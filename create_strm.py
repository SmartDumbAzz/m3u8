import os
from urllib.parse import quote

def create_strm_files(github_username="smartdumbazz", github_repo_name="m3u8"):
    base_github_pages_url = f"https://{github_username}.github.io/{github_repo_name}/"
    source_folders = ["Anime - Dub", "Anime - Sub"]
    destination_root_dir = r"C:\Media\Anime"

    for source_folder_name in source_folders:
        source_folder_path = os.path.join(source_folder_name)
        if not os.path.exists(source_folder_path):
            continue

        for root, _, files in os.walk(source_folder_path):
            for filename in files:
                if filename.endswith(".m3u8"):
                    rel_path = os.path.relpath(os.path.join(root, filename), source_folder_path)
                    strm_filename = filename.replace(".m3u8", ".strm")
                    dest_path = os.path.join(destination_root_dir, source_folder_name, os.path.dirname(rel_path))
                    os.makedirs(dest_path, exist_ok=True)
                    
                    # Build URL with proper encoding
                    url_segments = [source_folder_name, rel_path.replace("\\", "/")]
                    encoded_url = base_github_pages_url + quote("/".join(url_segments))
                    
                    with open(os.path.join(dest_path, strm_filename), 'w') as f:
                        f.write(encoded_url)
                    print(f"Created: {os.path.join(dest_path, strm_filename)}")

if __name__ == "__main__":
    create_strm_files()
    print("STRM file creation process completed.")