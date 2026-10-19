import os
import re
import time
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, unquote

# ---------- Configuration ----------
BASE_URL = 'https://www.erome.com'
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Referer': BASE_URL,
}
DOWNLOAD_DELAY = 1.0          # seconds between image downloads
PAGE_DELAY = 2.0              # seconds between album page requests
IMAGE_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff')

# ---------- Helper functions ----------
def extract_album_urls_from_file(filename):
    """Extract valid EroMe album URLs from a text file."""
    url_pattern = re.compile(r'https://www\.erome\.com/a/[A-Za-z0-9]+')
    found = []
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            for line in f:
                match = url_pattern.search(line)
                if match:
                    found.append(match.group(0))
    except FileNotFoundError:
        print(f"File '{filename}' not found.")
    return found

def get_album_id(url):
    """Extract album ID from URL."""
    match = re.search(r'/a/([^/?]+)', url)
    return match.group(1) if match else None

def download_image(img_url, save_path):
    """Download a single image. Returns True on success."""
    try:
        resp = requests.get(img_url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        with open(save_path, 'wb') as f:
            f.write(resp.content)
        return True
    except Exception as e:
        print(f"  Failed to download {img_url}: {e}")
        return False

def album_folder_is_nonempty(album_dir):
    """Return True if the album folder exists and contains at least one file."""
    return os.path.exists(album_dir) and any(os.listdir(album_dir))

# ---------- Main scraping ----------
def main():
    album_urls = extract_album_urls_from_file('erome.txt')
    if not album_urls:
        print("No valid album URLs found in erome.txt.")
        return

    print(f"Found {len(album_urls)} album URLs.")

    failed_urls = []      # URLs that caused errors
    skipped_urls = []     # URLs already downloaded (folder non‑empty)

    for idx, album_url in enumerate(album_urls, start=1):
        album_id = get_album_id(album_url)
        if not album_id:
            print(f"⚠️  Could not extract album ID from {album_url}, skipping.")
            failed_urls.append(album_url)
            continue

        album_dir = os.path.join('downloads', album_id)

        # Check if already downloaded
        if album_folder_is_nonempty(album_dir):
            print(f"\n[{idx}/{len(album_urls)}] Album {album_id} already downloaded, skipping.")
            skipped_urls.append(album_url)
            continue

        # Create folder (empty at this point)
        os.makedirs(album_dir, exist_ok=True)

        print(f"\n[{idx}/{len(album_urls)}] Processing album: {album_id}")

        # Fetch album page
        try:
            resp = requests.get(album_url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
        except Exception as e:
            print(f"  Failed to fetch page: {e}")
            failed_urls.append(album_url)
            time.sleep(PAGE_DELAY)
            continue

        soup = BeautifulSoup(resp.text, 'html.parser')

        # Find all image containers
        img_divs = soup.select('div.img[data-src]')
        if not img_divs:
            print("  No images found on this page.")
            # Not a failure; just no content
            time.sleep(PAGE_DELAY)
            continue

        print(f"  Found {len(img_divs)} images.")

        downloaded = 0
        for div in img_divs:
            img_url = div.get('data-src')
            if not img_url:
                continue

            parsed = urlparse(img_url)
            filename = os.path.basename(parsed.path)
            if not filename:
                filename = f"image_{downloaded+1}.jpg"
            filename = unquote(filename)

            ext = os.path.splitext(filename)[1].lower()
            if ext not in IMAGE_EXTENSIONS:
                print(f"    Skipping non-image: {filename}")
                continue

            save_path = os.path.join(album_dir, filename)

            if os.path.exists(save_path):
                print(f"    {filename} already exists, skipping.")
                continue

            print(f"    Downloading {filename} ...")
            if download_image(img_url, save_path):
                downloaded += 1
            time.sleep(DOWNLOAD_DELAY)

        print(f"  Downloaded {downloaded} new images for album {album_id}.")
        time.sleep(PAGE_DELAY)

    # ---------- Final summary ----------
    print("\n" + "=" * 50)
    print("Processing complete.")
    if skipped_urls:
        print(f"\n⏭️  Skipped {len(skipped_urls)} album(s) (already downloaded):")
        for url in skipped_urls:
            print(f"  {url}")
    if failed_urls:
        print(f"\n❌ The following {len(failed_urls)} album URL(s) failed:")
        for url in failed_urls:
            print(f"  {url}")
    if not skipped_urls and not failed_urls:
        print("All albums were processed successfully.")

if __name__ == '__main__':
    main()