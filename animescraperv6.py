import os
import re
import sys
import time
import requests
import subprocess
from urllib.parse import urlparse, urljoin, quote
from playwright.sync_api import sync_playwright

# PyInstaller runtime fix
if getattr(sys, 'frozen', False):
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = os.path.join(
        sys._MEIPASS, "playwright-browsers"
    )
    from playwright.__main__ import main
    main(["install", "firefox"])

# ===== GLOBAL SETUP =====
BASE_URL = "https://hianime.to"
SEARCH_URL = "https://hianime.to/search?keyword={query}"
BUTTON_SELECTOR = '.servers-dub .item.server-item[data-type="dub"] a.btn'
MEDIA_ROOT = r"C:\Media\Anime"
SESSION = requests.Session()

# ===== MITMPROXY HANDLER =====
class MitmProxyManager:
    def __enter__(self):
        self.process = setup_mitmproxy()
        time.sleep(2)
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.process.terminate()
        time.sleep(1)

def setup_mitmproxy():
    mitm_script = """from mitmproxy import http
from urllib.parse import urlparse, urlunparse

def response(flow: http.HTTPFlow):
    url = flow.request.url
    if "jwplayer6" not in url and url.endswith("master.m3u8"):
        modified = urlunparse(urlparse(url)._replace(
            path=urlparse(url).path.replace("master.m3u8", "index-f1-v1-a1.m3u8")
        ))
        with open("captured_links.txt", "a") as f:
            f.write(modified + "\\n")
    """
    with open("mitm_addon.py", "w") as f:
        f.write(mitm_script)
    return subprocess.Popen([
        "mitmdump", "-s", "mitm_addon.py", "--quiet",
        "--set", "stream_large_bodies=1", "--set", "connection_strategy=lazy",
        "--ssl-insecure"
    ])

def get_captured_link():
    if not os.path.exists("captured_links.txt"):
        return None
    
    with open("captured_links.txt", "r") as f:
        links = [line.strip() for line in f if line.strip()]
    
    if os.path.exists("captured_links.txt"):
        os.remove("captured_links.txt")
    
    return links[0] if links else None

# ===== CORE FUNCTIONS =====
def get_season_info():
    use_seasons = input("Is this anime divided into seasons? (y/n): ").lower() == 'y'
    if not use_seasons:
        return {'use_seasons': False}
    
    season_number = int(input("Enter season number: "))
    return {
        'use_seasons': True,
        'number': season_number,
        'name': f"Season{season_number}"
    }

def process_m3u8(url):
    try:
        domain = urlparse(url).netloc
        content = SESSION.get(url).text
        return re.sub(
            r'(https://)(.*?)(/seg-.*?/)', 
            fr'\1{domain}\3', 
            content
        )
    except Exception as e:
        print(f"M3U8 Error: {str(e)}")
        return None

def rename_subtitles(folder):
    for fname in os.listdir(folder):
        if fname.endswith(".vtt"):
            base_name = os.path.splitext(fname)[0]
            vtt_src = os.path.join(folder, fname)
            vtt_dest = os.path.join(folder, f"{base_name}.vtt")
            if not os.path.exists(vtt_dest):
                os.rename(vtt_src, vtt_dest)
                print(f"Renamed subtitle: {vtt_dest}")

# ===== SERIES PROCESSING =====
def search_series(query, browser):
    page = browser.new_page()
    page.goto(SEARCH_URL.format(query=query))
    page.wait_for_selector('.flw-item', timeout=20000)
    
    results = []
    items = page.query_selector_all('.flw-item')
    for idx, item in enumerate(items, 1):
        title_element = item.query_selector('h3.film-name a')
        title = title_element.inner_text()
        url = urljoin(BASE_URL, "/watch") + title_element.get_attribute('href')
        results.append((idx, title, url))
    
    page.close()
    return results

def get_episodes(url, browser):
    page = browser.new_page()
    page.goto(url)
    page.wait_for_selector('#detail-ss-list .ssl-item', timeout=20000)
    
    episodes = []
    items = page.query_selector_all('#detail-ss-list .ssl-item')
    for item in items:
        ep_number = item.get_attribute('data-number')
        ep_url = urljoin(BASE_URL, item.get_attribute('href'))
        episodes.append((ep_number, ep_url))
    
    page.close()
    return episodes

def process_series(title, episodes, season_info, browser):
    sanitized = re.sub(r'[^\w\-_ ]', '', title).strip().replace(' ', '_')
    sub_base = os.path.join("Anime - Sub", sanitized)
    dub_base = os.path.join("Anime - Dub", sanitized)
    
    if season_info['use_seasons']:
        sub_base = os.path.join(sub_base, season_info['name'])
        dub_base = os.path.join(dub_base, season_info['name'])
    
    os.makedirs(sub_base, exist_ok=True)
    os.makedirs(dub_base, exist_ok=True)

    with MitmProxyManager():
        context = browser.new_context(
            proxy={"server": "http://localhost:8080"},
            ignore_https_errors=True
        )
        context.add_init_script("delete Object.getPrototypeOf(navigator).webdriver;")

        try:
            # Process episodes sequentially to avoid threading issues
            for idx, (ep_num, url) in enumerate(episodes, 1):
                process_episode(context, url, sub_base, dub_base, season_info, idx)
        finally:
            context.close()

    rename_subtitles(sub_base)
    rename_subtitles(dub_base)
    create_strm_for_series(sub_base, dub_base)

def process_episode(context, url, sub_dir, dub_dir, season_info, ep_idx):
    page = context.new_page()
    
    try:
        # Subbed version
        page.goto(url, timeout=60000)
        page.wait_for_selector('.video-wrapper', state='attached')
        sub_link = get_captured_link()
        if sub_link:
            save_media(sub_link, sub_dir, season_info, ep_idx, "sub")

        # Dub version
        if page.is_visible(BUTTON_SELECTOR):
            page.click(BUTTON_SELECTOR)
            page.wait_for_load_state('networkidle')
            dub_link = get_captured_link()
            if dub_link:
                save_media(dub_link, dub_dir, season_info, ep_idx, "dub")
    except Exception as e:
        print(f"Episode processing error: {str(e)}")
    finally:
        page.close()

def save_media(m3u8_url, base_dir, season_info, ep_idx, version):
    if season_info['use_seasons']:
        prefix = f"S{season_info['number']:02d}E{ep_idx:02d}"
    else:
        prefix = f"E{ep_idx:03d}"
    
    m3u8_path = os.path.join(base_dir, f"{prefix}.m3u8")
    if os.path.exists(m3u8_path):
        print(f"Skipping existing: {m3u8_path}")
        return
    
    content = process_m3u8(m3u8_url)
    if not content:
        return
    
    with open(m3u8_path, 'w') as f:
        f.write(content)
    print(f"Saved {version} M3U8: {m3u8_path}")

def create_strm_for_series(sub_folder, dub_folder, github_username="smartdumbazz", github_repo_name="m3u8"):
    base_github_pages_url = f"https://{github_username}.github.io/{github_repo_name}/"
    destination_root_dir = MEDIA_ROOT

    for folder in [sub_folder, dub_folder]:
        if not os.path.exists(folder):
            continue

        base_source_folder = os.path.dirname(folder)
        sanitized_series = os.path.basename(folder)

        for filename in os.listdir(folder):
            if filename.endswith(".m3u8"):
                rel_path = f"{sanitized_series}/{filename}"
                url_segments = [os.path.basename(base_source_folder), rel_path]
                encoded_url = base_github_pages_url + quote("/".join(url_segments))

                strm_filename = filename.replace(".m3u8", ".strm")
                dest_folder = os.path.join(
                    destination_root_dir,
                    os.path.basename(base_source_folder),
                    sanitized_series
                )
                os.makedirs(dest_folder, exist_ok=True)
                dest_path = os.path.join(dest_folder, strm_filename)

                with open(dest_path, 'w') as f:
                    f.write(encoded_url)
                print(f"Created STRM: {dest_path}")

def upload_to_github(folder_name):
    try:
        subprocess.run(["git", "add", "Anime - Dub/*", "Anime - Sub/*"], check=True)
        result = subprocess.run(["git", "diff", "--cached", "--quiet"], capture_output=True)
        if result.returncode != 0:
            subprocess.run(["git", "commit", "-m", f"Add {folder_name} Anime folders"], check=True)
            subprocess.run(["git", "push"], check=True)
            print("Successfully uploaded to GitHub")
        else:
            print("No changes to commit")
    except subprocess.CalledProcessError as e:
        print(f"GitHub upload error: {str(e)}")

def find_existing_series():
    series_map = {}
    for base_folder in ["Anime - Sub", "Anime - Dub"]:
        if not os.path.exists(base_folder):
            continue
        for series_folder in os.listdir(base_folder):
            series_path = os.path.join(base_folder, series_folder)
            if os.path.isdir(series_path):
                clean_name = series_folder.replace('_', ' ').strip()
                series_map[clean_name] = {
                    'sanitized': series_folder,
                    'path': series_path
                }
    return series_map

def refresh_all_series(browser):
    print("\nScanning existing series...")
    existing_series = find_existing_series()
    
    if not existing_series:
        print("No existing series found!")
        return
    
    print("\nFound existing series:")
    for idx, name in enumerate(existing_series.keys(), 1):
        print(f"{idx}. {name}")
    
    for series_name, data in existing_series.items():
        print(f"\nRefreshing: {series_name}")
        results = search_series(series_name, browser)
        
        if not results:
            print(f"Warning: {series_name} not found, skipping")
            continue
            
        selected_idx, selected_title, selected_url = results[0]
        episodes = get_episodes(selected_url, browser)
        
        if not episodes:
            print(f"Warning: No episodes found, skipping")
            continue
        
        season_info = {'use_seasons': False}
        series_path = data['path']
        if any(os.path.isdir(os.path.join(series_path, d)) for d in os.listdir(series_path) if d.startswith('Season')):
            for season_dir in sorted([d for d in os.listdir(series_path) if d.startswith('Season')]):
                season_num = int(season_dir.replace('Season', ''))
                season_info = {
                    'use_seasons': True,
                    'number': season_num,
                    'name': season_dir
                }
                print(f"Processing {series_name} {season_dir}")
                process_series(series_name, episodes, season_info, browser)
        else:
            process_series(series_name, episodes, season_info, browser)
    
    print("\nRefresh complete!")
    upload_to_github("all_series_refresh")

# ===== MAIN FLOW =====
if __name__ == "__main__":
    print("Anime Manager v2 (Optimized)")
    print("1. Download new anime")
    print("2. Refresh all existing links")
    mode = input("Choice: ").strip()

    with sync_playwright() as playwright:
        browser = playwright.firefox.launch(headless=True)
        try:
            if mode == "1":
                query = input("Search Anime: ")
                results = search_series(query, browser)
                
                print("\nResults:")
                for idx, title, _ in results:
                    print(f"{idx}. {title}")
                
                choice = int(input("\nSelect: "))
                if choice < 1 or choice > len(results):
                    sys.exit("Invalid selection")
                
                selected_idx, selected_title, selected_url = results[choice-1]
                
                print(f"\nSelected title: {selected_title}")
                new_name = input("Enter new name (or press Enter to keep current): ").strip()
                if new_name:
                    selected_title = new_name
                
                episodes = get_episodes(selected_url, browser)
                if not episodes:
                    sys.exit("No episodes found")
                
                season = get_season_info()
                process_series(selected_title, episodes, season, browser)
                
                upload_to_github(selected_title)
                
            elif mode == "2":
                refresh_all_series(browser)
                
            else:
                print("Invalid option")
        finally:
            browser.close()