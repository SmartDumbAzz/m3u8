import subprocess
import sys
import os
import time
import re
import requests
from urllib.parse import urlparse, urljoin, quote
from playwright.sync_api import sync_playwright




# PyInstaller runtime fix
if getattr(sys, 'frozen', False):
    # Set browser path for packaged executable
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = os.path.join(
        sys._MEIPASS,  # PyInstaller temp directory
        "playwright-browsers"
    )
    
    # First-run browser installation
    from playwright.__main__ import main
    main(["install", "firefox"])
    main(["install", "chromium"])

# ===== CONFIGURATION =====
BASE_URL = "https://hianime.to"
SEARCH_URL = "https://hianime.to/search?keyword={query}"
M3U8_FILENAME_SUB = "newfile_subbed.m3u8"
M3U8_FILENAME_DUB = "newfile_dubbed.m3u8"
BUTTON_SELECTOR = '.servers-dub .item.server-item[data-type="dub"] a.btn'

# MITMProxy Add-on (from older script)
MITM_ADDON = """
from mitmproxy import http
from urllib.parse import urlparse, urlunparse

def response(flow: http.HTTPFlow):
    url = flow.request.url
    if "jwplayer6" not in url:
        parsed = urlparse(url)

        # Capture only master.m3u8 links
        if parsed.path.endswith("master.m3u8"):
            new_path = parsed.path.replace("master.m3u8", "index-f1-v1-a1.m3u8")
            modified_url = urlunparse(parsed._replace(path=new_path))

            with open("captured_links.txt", "a") as f:
                f.write(modified_url + "\\n")
"""

def setup_mitmproxy():
    """Starts mitmproxy with the capture script (from older script)."""
    with open("mitm_addon.py", "w") as f:
        f.write(MITM_ADDON)

    return subprocess.Popen([
        "mitmdump",
        "-s", "mitm_addon.py",
        "--quiet",
        "--set", "stream_large_bodies=1",
        "--set", "connection_strategy=lazy"
    ])

def get_captured_link():
    """Extracts the first captured .m3u8 link from the file"""
    if not os.path.exists("captured_links.txt"):
        print("No captured links found.")
        return None

    with open("captured_links.txt", "r") as f:
        links = [line.strip() for line in f if line.strip()]
        
    print(f"Found {len(links)} links in capture file")  # <-- Add this line
    if os.path.exists("captured_links.txt"):
        os.remove("captured_links.txt")

    return links[0] if links else None

def replace_domain_in_m3u8(m3u8_url):
    """Replace all segment domains with the main M3U8 domain"""
    try:
        # Extract base domain from the original M3U8 URL
        original_domain = urlparse(m3u8_url).netloc
        print(f"Original domain: {original_domain}")
        
        response = requests.get(m3u8_url)
        response.raise_for_status()
        
        modified_lines = []
        replaced_domains = set()  # Track domains we've already logged
        
        for line in response.text.split('\n'):
            # Only process segment lines
            if line.startswith('https://') and '/seg-' in line:
                parsed = urlparse(line)
                if parsed.netloc != original_domain:
                    # Log replacement only once per unique domain
                    if parsed.netloc not in replaced_domains:
                        print(f"Replacing domain: {parsed.netloc} -> {original_domain}")
                        replaced_domains.add(parsed.netloc)
                    
                    # Rebuild URL with original domain
                    new_url = parsed._replace(netloc=original_domain).geturl()
                    modified_lines.append(new_url)
                    continue
                modified_lines.append(line)
            else:
                modified_lines.append(line)
        
        return '\n'.join(modified_lines)
        
    except requests.exceptions.RequestException as e:
        print(f"Error fetching M3U8: {e}")
        return None

def save_new_file(content, filepath):
    """Save content to a new file"""
    try:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"Saved modified M3U8 to: {filepath}")
    except Exception as e:
        print(f"Error saving file: {e}")

def search_series(query):
    """Search for anime series and return results"""
    with sync_playwright() as p:
        browser = p.firefox.launch(headless=False)
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
    """Get all episodes from series page"""
    with sync_playwright() as p:
        browser = p.firefox.launch(headless=False)
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

def create_strm_for_series(sub_folder, dub_folder, github_username="smartdumbazz", github_repo_name="m3u8"):
    """Generate .strm files for the current series' m3u8 files."""
    base_github_pages_url = f"https://{github_username}.github.io/{github_repo_name}/"
    destination_root_dir = r"C:\Media\Anime"

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


def process_series(series_name, episodes):
    """Process all episodes for a series"""
    # Sanitize series name
    sanitized_name = re.sub(r'[^\w\-_ .]', '', series_name).strip().replace(' ', '_')
    
    # Define folders
    base_sub_folder = "Anime - Sub"
    base_dub_folder = "Anime - Dub"
    sub_folder = os.path.join(base_sub_folder, sanitized_name)
    dub_folder = os.path.join(base_dub_folder, sanitized_name)

    os.makedirs(sub_folder, exist_ok=True)
    os.makedirs(dub_folder, exist_ok=True)

    for ep_number, ep_url in episodes:
        print(f"Processing Episode {ep_number}...")
        capture_episode_links(ep_url, sanitized_name, ep_number, sub_folder, dub_folder)

    # Generate .strm files after all episodes are processed
    create_strm_for_series(sub_folder, dub_folder)

def capture_episode_links(episode_url, series_name, ep_number, sub_folder, dub_folder):
    """Capture episode links using MITM proxy"""
    mitm_process = setup_mitmproxy()
    time.sleep(2)

    try:
        with sync_playwright() as p:
            browser = p.firefox.launch(
                proxy={"server": "http://localhost:8080"},
                headless=False
            )
            context = browser.new_context(ignore_https_errors=True)
            context.add_init_script("delete Object.getPrototypeOf(navigator).webdriver;")
            page = context.new_page()
            
            # Capture subbed version
            print(f"\n=== Capturing subbed version for Episode {ep_number} ===")
            page.goto(episode_url, timeout=60000)
            time.sleep(5)
            subbed_link = get_captured_link()
            
            if subbed_link:
                print(f"Subbed link found: {subbed_link}")  # <-- Add this line
                modified_m3u8 = replace_domain_in_m3u8(subbed_link)
                filename = f"{series_name}_Ep_{ep_number}.m3u8"
                save_new_file(modified_m3u8, os.path.join(sub_folder, filename))
            
            # Capture dubbed version
            print(f"\n=== Capturing dubbed version for Episode {ep_number} ===")
            try:
                page.click(BUTTON_SELECTOR)
                time.sleep(5)
                dubbed_link = get_captured_link()
                
                if dubbed_link:
                    print(f"Dubbed link found: {dubbed_link}")  # <-- Add this line
                    modified_m3u8 = replace_domain_in_m3u8(dubbed_link)
                    filename = f"{series_name}_Ep_{ep_number}.m3u8"
                    save_new_file(modified_m3u8, os.path.join(dub_folder, filename))
            except Exception as e:
                print(f"Error capturing dubbed version: {str(e)}")
            
            context.close()
            browser.close()
    finally:
        mitm_process.terminate()
        mitm_process.wait(timeout=10)

def upload_to_github(folder_name):
    """Upload files to GitHub repository"""
    try:
        subprocess.run(["git", "add", "Anime - Dub", "Anime - Sub"], check=True)
        subprocess.run(["git", "commit", "-m", f"Add {folder_name} Anime folders"], check=True)
        subprocess.run(["git", "push"], check=True)
        print("Successfully uploaded to GitHub")
    except subprocess.CalledProcessError as e:
        print(f"GitHub upload error: {str(e)}")

if __name__ == "__main__":
    # New initial prompt
    print("1. Download new anime")
    print("2. Refresh all existing links")
    mode = input("Choose mode (1/2): ")

    if mode == "1":
        # Existing download flow
        search_query = input("Enter anime name to search: ")
        results = search_series(search_query)
        
        print("\nSearch Results:")
        for idx, title, url in results:
            print(f"{idx}. {title}")
        
        choice = int(input("\nEnter the number of the series: "))
        selected_idx, selected_title, selected_url = results[choice-1]
        
        episodes = get_episodes(selected_url)
        print(f"\nFound {len(episodes)} episodes for {selected_title}")

        # Episode selection logic
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

        process_series(selected_title, selected_episodes)
        upload_to_github(selected_title)

    elif mode == "2":
        # New refresh all functionality
        def find_existing_series():
            series_map = {}
            for base_folder in ["Anime - Sub", "Anime - Dub"]:
                if not os.path.exists(base_folder):
                    continue
                for series_folder in os.listdir(base_folder):
                    series_path = os.path.join(base_folder, series_folder)
                    if os.path.isdir(series_path):
                        # Reverse sanitization: _ -> space
                        clean_name = series_folder.replace('_', ' ').strip()
                        series_map[clean_name] = {
                            'sanitized': series_folder,
                            'base_folder': base_folder
                        }
            return series_map

        print("\nScanning existing series...")
        existing_series = find_existing_series()
        
        if not existing_series:
            print("No existing series found in Anime-Sub/Anime-Dub folders!")
            sys.exit()

        print("\nFound existing series:")
        for idx, name in enumerate(existing_series.keys(), 1):
            print(f"{idx}. {name}")

        # Get fresh data for all series
        for series_name, data in existing_series.items():
            print(f"\nRefreshing: {series_name}")
            results = search_series(series_name)
            
            if not results:
                print(f"Warning: {series_name} not found on site, skipping")
                continue
                
            # Take first search result
            selected_url = results[0][2]
            episodes = get_episodes(selected_url)
            
            if not episodes:
                print(f"Warning: No episodes found for {series_name}, skipping")
                continue
                
            # Process all episodes
            process_series(series_name, episodes)
        
        print("\nRefresh complete!")
        upload_to_github("all_series_refresh")

    else:
        print("Invalid mode selection")