api_key = "b1c733f37ef88d4a69208f2f6861cbd4"
import os
import sys
import time
import requests
import subprocess
from urllib.parse import urlparse, urljoin, quote
from playwright.sync_api import sync_playwright

# ===== CONFIGURATION =====
BASE_URL = "https://vidlink.pro/tv"
MEDIA_ROOT = r"C:\Media\TV"

TMDB_SEARCH_URL = "https://api.themoviedb.org/3/search/tv"
TMDB_TV_DETAILS_URL = "https://api.themoviedb.org/3/tv/{tv_id}"
TMDB_SEASON_DETAILS_URL = "https://api.themoviedb.org/3/tv/{tv_id}/season/{season_number}"

# ===== TMDB API FUNCTIONS =====
def search_tv(query, api_key):
    params = {"api_key": api_key, "query": query}
    response = requests.get(TMDB_SEARCH_URL, params=params)
    data = response.json()
    return data.get("results", [])

def get_tv_details(tv_id, api_key):
    url = TMDB_TV_DETAILS_URL.format(tv_id=tv_id)
    params = {"api_key": api_key}
    response = requests.get(url, params=params)
    return response.json()

def get_season_details(tv_id, season_number, api_key):
    url = TMDB_SEASON_DETAILS_URL.format(tv_id=tv_id, season_number=season_number)
    params = {"api_key": api_key}
    response = requests.get(url, params=params)
    return response.json()

# ===== MITMPROXY SETUP =====
def setup_mitmproxy():
    # Launch mitmdump with our custom addon (tv_mitm_addon.py)
    addon_file = "tv_mitm_addon.py"
    proc = subprocess.Popen([
        "mitmdump", "-s", addon_file, "--quiet",
        "--set", "stream_large_bodies=1", "--set", "connection_strategy=lazy",
        "--ssl-insecure"
    ])
    return proc

def get_captured_link():
    """Retrieve the captured m3u8 link (if any) from the mitmproxy addon."""
    if not os.path.exists("captured_links.txt"):
        return None
    link = None
    with open("captured_links.txt", "r") as f:
        for line in f:
            line = line.strip()
            if line.startswith("m3u8:"):
                link = line[5:]
                break
    if os.path.exists("captured_links.txt"):
        os.remove("captured_links.txt")
    return link

# ===== UTILITY FUNCTIONS =====
def get_episode_prefix(season, episode):
    return f"S{int(season):02d}E{int(episode):02d}"

def create_strm_file(series_folder, prefix, m3u8_url):
    """Generate a .strm file that directly contains the m3u8 link."""
    os.makedirs(series_folder, exist_ok=True)
    strm_file = os.path.join(series_folder, f"{prefix}.strm")
    with open(strm_file, 'w') as f:
        f.write(m3u8_url)
    print(f"Created STRM: {strm_file}")

# ===== EPISODE PROCESSING =====
def process_episode(tv_id, season, episode, series_name):
    episode_url = f"{BASE_URL}/{tv_id}/{season}/{episode}"
    print(f"\nProcessing Episode URL: {episode_url}")
    mitm = setup_mitmproxy()
    time.sleep(2)  # Allow mitmproxy time to start
    try:
        with sync_playwright() as p:
            browser = p.firefox.launch(
                proxy={"server": "http://localhost:8080"},
                headless=True
            )
            context = browser.new_context(ignore_https_errors=True)
            page = context.new_page()
            page.goto(episode_url, timeout=60000)
            time.sleep(5)  # Allow time for the page to load and mitmproxy to capture the request
            captured = get_captured_link()
            if captured:
                series_folder = os.path.join(MEDIA_ROOT, series_name, f"Season {season}")
                prefix = get_episode_prefix(season, episode)
                create_strm_file(series_folder, prefix, captured)
            else:
                print("No m3u8 link captured.")
            context.close()
            browser.close()
    finally:
        mitm.terminate()

# ===== MAIN FLOW =====
def main():
    query = input("Search TV Show: ").strip()
    results = search_tv(query, api_key)
    if not results:
        print("No TV shows found.")
        sys.exit(1)
    print("\nResults:")
    for idx, show in enumerate(results, 1):
        print(f"{idx}. {show.get('name')} (ID: {show.get('id')})")
    choice = int(input("Select TV show: "))
    selected = results[choice - 1]
    tv_id = selected.get("id")
    series_name = selected.get("name")
    
    # Get TV details to list available seasons (ignoring season 0 if present)
    details = get_tv_details(tv_id, api_key)
    seasons = details.get("seasons", [])
    available_seasons = [s for s in seasons if s.get("season_number", 0) > 0]
    print("\nAvailable Seasons:")
    for s in available_seasons:
        print(f"Season {s.get('season_number')}")
    season_number = int(input("Enter season number to download: ").strip())
    
    season_details = get_season_details(tv_id, season_number, api_key)
    episodes = season_details.get("episodes", [])
    print(f"\nFound {len(episodes)} episodes in Season {season_number}:")
    for ep in episodes:
        print(f"Episode {ep.get('episode_number')}: {ep.get('name')}")
    
    start = int(input("Enter starting episode number: ").strip())
    end = int(input("Enter ending episode number: ").strip())
    
    for ep in episodes:
        ep_number = ep.get("episode_number")
        if start <= ep_number <= end:
            process_episode(tv_id, season_number, ep_number, series_name)

if __name__ == "__main__":
    main()
