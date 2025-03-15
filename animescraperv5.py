import os
import re
import sys
import time
import requests
import subprocess
import shutil
from urllib.parse import urlparse, urljoin, quote
from playwright.sync_api import sync_playwright

# ===== CONFIGURATION =====
BASE_URL = "https://hianime.to"
SEARCH_URL = "https://hianime.to/search?keyword={query}"
BUTTON_SELECTOR = '.servers-dub .item.server-item[data-type="dub"] a.btn'
MEDIA_ROOT = r"C:\Media\Anime"

# ===== CORE FUNCTIONS =====
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
            f.write("m3u8:{}\\n".format(modified))
    elif url.endswith(".vtt"):
        with open("captured_links.txt", "a") as f:
            f.write("vtt:{}\\n".format(url))
    """
    with open("mitm_addon.py", "w") as f:
        f.write(mitm_script)
    return subprocess.Popen([
        "mitmdump", "-s", "mitm_addon.py", "--quiet",
        "--set", "stream_large_bodies=1", "--set", "connection_strategy=lazy",
        "--ssl-insecure"
    ])

def get_captured_links():
    """Retrieve captured links from the MITM proxy"""
    if not os.path.exists("captured_links.txt"):
        return {"m3u8": None, "vtt": None}
    
    links = {"m3u8": None, "vtt": None}
    with open("captured_links.txt", "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.startswith("m3u8:"):
                links["m3u8"] = line[5:]
            elif line.startswith("vtt:"):
                links["vtt"] = line[4:]
    
    if os.path.exists("captured_links.txt"):
        os.remove("captured_links.txt")
    
    return links

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

def get_episode_prefix(season_info, ep_idx, human_readable=False):
    """Generate a consistent episode prefix with optional human-readable formatting."""
    if season_info['use_seasons']:
        base = f"S{season_info['number']:02d}E{ep_idx:02d}"
    else:
        base = f"E{ep_idx:03d}"
    
    if human_readable:
        return base.replace('_', ' ')
    return base

def process_m3u8(url):
    try:
        domain = urlparse(url).netloc
        content = requests.get(url).text
        replacements = set()
        
        lines = []
        for line in content.split('\n'):
            if line.startswith('https://') and '/seg-' in line:
                parsed = urlparse(line)
                if parsed.netloc != domain:
                    if parsed.netloc not in replacements:
                        print(f"Domain fix: {parsed.netloc} -> {domain}")
                        replacements.add(parsed.netloc)
                    line = parsed._replace(netloc=domain).geturl()
            lines.append(line)
        
        return '\n'.join(lines)
    except Exception as e:
        print(f"M3U8 Error: {str(e)}")
        return None

# ===== SERIES PROCESSING =====
def search_series(query):
    """Search for anime series on the website"""
    with sync_playwright() as p:
        browser = p.firefox.launch(headless=True)
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
        
        browser.close()
        return results

def get_episodes(url):
    """Get all episodes from a series (season) page"""
    with sync_playwright() as p:
        browser = p.firefox.launch(headless=True)
        page = browser.new_page()
        page.goto(url)
        page.wait_for_selector('#detail-ss-list .ssl-item', timeout=20000)
        
        episodes = []
        items = page.query_selector_all('#detail-ss-list .ssl-item')
        for item in items:
            ep_number = item.get_attribute('data-number')
            ep_url = urljoin(BASE_URL, item.get_attribute('href'))
            episodes.append((ep_number, ep_url))
        
        browser.close()
        return episodes

def process_series(title, episodes, season_info, season_link=None):
    sanitized = re.sub(r'[^\w\-_ ]', '', title).strip().replace(' ', '_')
    sub_base = os.path.join("Anime - Sub", sanitized)
    dub_base = os.path.join("Anime - Dub", sanitized)
    
    if season_info['use_seasons']:
        sub_base = os.path.join(sub_base, season_info['name'])
        dub_base = os.path.join(dub_base, season_info['name'])
    
    os.makedirs(sub_base, exist_ok=True)
    os.makedirs(dub_base, exist_ok=True)

    # Record the season link if provided so that later refreshes use the correct season page
    if season_link:
        season_link_path_sub = os.path.join(sub_base, "season_link.txt")
        with open(season_link_path_sub, "w") as f:
            f.write(season_link)
        season_link_path_dub = os.path.join(dub_base, "season_link.txt")
        with open(season_link_path_dub, "w") as f:
            f.write(season_link)
    
    for idx, (ep_num, url) in enumerate(episodes, 1):
        print(f"\nProcessing Episode {ep_num} ({idx}/{len(episodes)})")
        try:
            ep_number = int(ep_num)
        except ValueError:
            # Fallback if the episode number isn’t an integer
            ep_number = idx
        process_episode(url, sub_base, dub_base, sanitized, season_info, ep_number)

    
    create_strm_for_series(sub_base, dub_base)

def process_episode(url, sub_dir, dub_dir, series_name, season_info, ep_idx):
    mitm = setup_mitmproxy()
    time.sleep(2)
    
    try:
        with sync_playwright() as p:
            browser = p.firefox.launch(
                proxy={"server": "http://localhost:8080"},
                headless=True
            )
            context = browser.new_context(ignore_https_errors=True)
            context.add_init_script("delete Object.getPrototypeOf(navigator).webdriver;")
            page = context.new_page()
            
            # Subbed version
            page.goto(url, timeout=60000)
            time.sleep(5)
            captured = get_captured_links()
            
            if captured["m3u8"]:
                save_media(captured["m3u8"], sub_dir, season_info, ep_idx, "sub")
                
            if captured["vtt"]:
                download_vtt(captured["vtt"], sub_dir, season_info, ep_idx)
            
            # Dub version
            try:
                page.click(BUTTON_SELECTOR)
                time.sleep(5)
                captured = get_captured_links()
                
                if captured["m3u8"]:
                    save_media(captured["m3u8"], dub_dir, season_info, ep_idx, "dub")
                
                prefix = get_episode_prefix(season_info, ep_idx)
                src_vtt = os.path.join(sub_dir, f"{prefix}.vtt")
                if os.path.exists(src_vtt):
                    dst_vtt = os.path.join(dub_dir, f"{prefix}.vtt")
                    with open(src_vtt, 'rb') as src, open(dst_vtt, 'wb') as dst:
                        dst.write(src.read())
                    print(f"Copied subtitle to dub folder: {dst_vtt}")
            except Exception as e:
                print(f"Dub error: {str(e)}")
            
            context.close()
            browser.close()
    finally:
        mitm.terminate()

def download_vtt(url, directory, season_info, ep_idx):
    """Download VTT subtitle file with human-readable name"""
    try:
        response = requests.get(url)
        if response.status_code == 200:
            prefix = get_episode_prefix(season_info, ep_idx, human_readable=True)
            vtt_path = os.path.join(directory, f"{prefix}.vtt")
            with open(vtt_path, 'wb') as f:
                f.write(response.content)
            print(f"Downloaded subtitle: {vtt_path}")
    except Exception as e:
        print(f"VTT download error: {str(e)}")

def save_media(m3u8_url, base_dir, season_info, ep_idx, version):
    content = process_m3u8(m3u8_url)
    if not content:
        return
    
    # Generate both sanitized and human-readable prefixes
    sanitized_prefix = get_episode_prefix(season_info, ep_idx)
    human_prefix = get_episode_prefix(season_info, ep_idx, human_readable=True)
    
    # Save M3U8 with sanitized name
    m3u8_path = os.path.join(base_dir, f"{sanitized_prefix}.m3u8")
    with open(m3u8_path, 'w') as f:
        f.write(content)
    print(f"Saved {version} M3U8: {m3u8_path}")
    
    # Rename VTT to human-readable format if exists
    vtt_src = os.path.join(base_dir, f"{sanitized_prefix}.vtt")
    vtt_dest = os.path.join(base_dir, f"{human_prefix}.vtt")
    if os.path.exists(vtt_src):
        os.rename(vtt_src, vtt_dest)
        print(f"Renamed subtitle: {vtt_dest}")

def create_strm_for_series(sub_folder, dub_folder, github_username="smartdumbazz", github_repo_name="m3u8"):
    """Generate .strm files with proper naming and VTT handling"""
    base_github_pages_url = f"https://{github_username}.github.io/{github_repo_name}/"
    destination_root_dir = MEDIA_ROOT

    for folder in [sub_folder, dub_folder]:
        if not os.path.exists(folder):
            continue

        path_parts = os.path.normpath(folder).split(os.sep)
        if len(path_parts) < 2:
            print(f"Invalid folder structure: {folder}")
            continue
            
        base_type = path_parts[0]  # "Anime - Sub" or "Anime - Dub"
        series_parts = [p.replace('_', ' ') for p in path_parts[1:]]  # Human-readable paths

        dest_folder = os.path.join(
            destination_root_dir,
            base_type,
            *series_parts
        )
        os.makedirs(dest_folder, exist_ok=True)

        for filename in os.listdir(folder):
            src_path = os.path.join(folder, filename)
            
            if filename.endswith(".m3u8"):
                # Create human-readable STRM name
                clean_name = filename.replace('_', ' ').replace('.m3u8', '')
                strm_filename = f"{clean_name}.strm"
                
                # Build GitHub URL with original encoding
                encoded_parts = [
                    quote(base_type),
                    *[quote(p) for p in path_parts[1:]],
                    quote(filename)
                ]
                encoded_url = f"{base_github_pages_url}{'/'.join(encoded_parts)}"
                
                # Write STRM file
                dest_path = os.path.join(dest_folder, strm_filename)
                with open(dest_path, 'w') as f:
                    f.write(encoded_url)
                print(f"Created STRM: {dest_path}")
            
            elif filename.endswith(".vtt"):
                # Create human-readable VTT name
                clean_vtt_name = filename.replace('_', ' ')
                dest_path = os.path.join(dest_folder, clean_vtt_name)
                
                if not os.path.exists(dest_path):
                    shutil.copy2(src_path, dest_path)
                    print(f"Copied subtitle: {dest_path}")

def upload_to_github(folder_name, github_username="smartdumbazz", github_repo_name="m3u8"):
    """Upload files to GitHub repository"""
    try:
        result = subprocess.run(["git", "remote", "-v"], capture_output=True, text=True)
        if "origin" not in result.stdout:
            remote_url = f"https://github.com/{github_username}/{github_repo_name}.git"
            subprocess.run(["git", "remote", "add", "origin", remote_url], check=True)
            print(f"Added remote: {remote_url}")
        
        subprocess.run(["git", "add", "Anime - Dub", "Anime - Sub", "."], check=True)
        
        result = subprocess.run(["git", "diff", "--cached", "--quiet"], capture_output=True)
        if result.returncode != 0:
            subprocess.run(["git", "commit", "-m", f"Add {folder_name} Anime folders"], check=True)
            subprocess.run(["git", "push", "origin", "main"], check=True)
            print("Successfully uploaded to GitHub")
        else:
            print("No changes to commit")
    except subprocess.CalledProcessError as e:
        print(f"GitHub upload error: {str(e)}")

def find_existing_series():
    """Find all existing series folders"""
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

def refresh_all_series():
    """Refresh all existing series"""
    print("\nScanning existing series...")
    existing_series = find_existing_series()
    
    if not existing_series:
        print("No existing series found in Anime-Sub/Anime-Dub folders!")
        return
    
    print("\nFound existing series:")
    for idx, name in enumerate(existing_series.keys(), 1):
        print(f"{idx}. {name}")
    
    for series_name, data in existing_series.items():
        print(f"\nRefreshing: {series_name}")
        series_path = data['path']
        # Check if there are season directories
        season_dirs = [d for d in os.listdir(series_path) if os.path.isdir(os.path.join(series_path, d)) and d.startswith('Season')]
        if season_dirs:
            for season_dir in sorted(season_dirs):
                season_link_file = os.path.join(series_path, season_dir, "season_link.txt")
                if os.path.exists(season_link_file):
                    with open(season_link_file, "r") as f:
                        season_link = f.read().strip()
                else:
                    results = search_series(series_name)
                    if not results:
                        print(f"Warning: {series_name} not found on site, skipping")
                        continue
                    selected_idx, selected_title, selected_url = results[0]
                    season_link = selected_url
                season_info = {
                    'use_seasons': True,
                    'number': int(season_dir.replace('Season', '')),
                    'name': season_dir
                }
                print(f"Processing {series_name} {season_dir}")
                episodes = get_episodes(season_link)
                if not episodes:
                    print(f"Warning: No episodes found for {series_name} {season_dir}, skipping")
                    continue
                process_series(series_name, episodes, season_info, season_link=season_link)
        else:
            season_link_file = os.path.join(series_path, "season_link.txt")
            if os.path.exists(season_link_file):
                with open(season_link_file, "r") as f:
                    season_link = f.read().strip()
            else:
                results = search_series(series_name)
                if not results:
                    print(f"Warning: {series_name} not found on site, skipping")
                    continue
                selected_idx, selected_title, selected_url = results[0]
                season_link = selected_url
            season_info = {'use_seasons': False}
            episodes = get_episodes(season_link)
            if not episodes:
                print(f"Warning: No episodes found for {series_name}, skipping")
                continue
            process_series(series_name, episodes, season_info, season_link=season_link)
    
    print("\nRefresh complete!")
    upload_to_github("all_series_refresh")

# ===== MAIN FLOW =====
if __name__ == "__main__":
    print("Anime Manager v2")
    print("1. Download new anime")
    print("2. Refresh all existing links")
    print("3. Add specific episodes to existing anime")
    mode = input("Choice: ").strip()

    if mode == "1":
        query = input("Search Anime: ")
        results = search_series(query)
        
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
        
        episodes = get_episodes(selected_url)
        if not episodes:
            sys.exit("No episodes found")
        
        while True:
            ep_choice = input("\nDownload options:\n1. All episodes\n2. Specific episode\nChoose (1/2): ")
            if ep_choice in ['1', '2']:
                break
            print("Invalid choice. Please enter 1 or 2")
        
        selected_episodes = []
        if ep_choice == '2':
            while True:
                try:
                    ep_num = int(input("Enter episode number to download: "))
                    if 1 <= ep_num <= len(episodes):
                        selected_episodes = [episodes[ep_num-1]]
                        break
                    print(f"Invalid number. Must be between 1-{len(episodes)}")
                except ValueError:
                    print("Please enter a valid number")
        else:
            selected_episodes = episodes
        
        season = get_season_info()
        process_series(selected_title, selected_episodes, season, season_link=selected_url)
        upload_to_github(selected_title)
        
    elif mode == "2":
        refresh_all_series()
        
    elif mode == "3":
        existing_series = find_existing_series()
        
        if not existing_series:
            sys.exit("No existing series found in Anime-Sub/Anime-Dub folders!")
        
        print("\nFound existing series:")
        series_list = list(existing_series.keys())
        for idx, name in enumerate(series_list, 1):
            print(f"{idx}. {name}")
        
        choice = int(input("\nSelect series: "))
        if choice < 1 or choice > len(series_list):
            sys.exit("Invalid selection")
        
        selected_series = series_list[choice-1]
        print(f"\nSelected: {selected_series}")
        
        results = search_series(selected_series)
        if not results:
            sys.exit(f"Series '{selected_series}' not found on site")
        
        selected_idx, selected_title, _ = results[0]
        series_path = existing_series[selected_series]['path']
        # Determine season link either from recorded file or fallback to search
        if any(os.path.isdir(os.path.join(series_path, d)) for d in os.listdir(series_path) if d.startswith('Season')):
            seasons = sorted([d for d in os.listdir(series_path) if os.path.isdir(os.path.join(series_path, d)) and d.startswith('Season')])
            print("\nAvailable seasons:")
            for idx, season in enumerate(seasons, 1):
                print(f"{idx}. {season}")
            
            season_choice = int(input("\nSelect season: "))
            if season_choice < 1 or season_choice > len(seasons):
                sys.exit("Invalid selection")
            
            selected_season = seasons[season_choice-1]
            season_link_file = os.path.join(series_path, selected_season, "season_link.txt")
            if os.path.exists(season_link_file):
                with open(season_link_file, "r") as f:
                    season_link = f.read().strip()
            else:
                results = search_series(selected_series)
                if not results:
                    sys.exit(f"Series '{selected_series}' not found on site")
                _, _, selected_url = results[0]
                season_link = selected_url
            season_info = {
                'use_seasons': True,
                'number': int(selected_season.replace('Season','')),
                'name': selected_season
            }
        else:
            season_link_file = os.path.join(series_path, "season_link.txt")
            if os.path.exists(season_link_file):
                with open(season_link_file, "r") as f:
                    season_link = f.read().strip()
            else:
                results = search_series(selected_series)
                if not results:
                    sys.exit(f"Series '{selected_series}' not found on site")
                _, _, selected_url = results[0]
                season_link = selected_url
            season_info = {'use_seasons': False}
        
        episodes = get_episodes(season_link)
        if not episodes:
            sys.exit("No episodes found")
        print(f"\nFound {len(episodes)} episodes for {selected_series}")
        
        while True:
            try:
                ep_num = int(input("Enter episode number to download: "))
                if 1 <= ep_num <= len(episodes):
                    selected_episodes = [episodes[ep_num-1]]
                    break
                print(f"Invalid number. Must be between 1-{len(episodes)}")
            except ValueError:
                print("Please enter a valid number")
        
        process_series(selected_series, selected_episodes, season_info, season_link=season_link)
        upload_to_github(f"{selected_series}_episode_{ep_num}")
    
    else:
        print("Invalid option")
