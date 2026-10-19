#!/usr/bin/env python3
"""
Extract post URLs from a noisy text file, scrape each post for images,
and download all images.
"""

import os
import re
import time
import requests
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup

# ---------------------------- CONFIGURATION ----------------------------
TEXT_FILE = "byorunet.txt"               # file containing post URLs (may contain noise)
OUTPUT_DIR = "byoru_images"              # root folder for downloaded images
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
DELAY_BETWEEN_POSTS = 1.0                # seconds to wait between post requests
DELAY_BETWEEN_IMAGES = 0.3               # seconds between image downloads
MAX_RETRIES = 3
TIMEOUT = 30
# ---------------------------------------------------------------------

def extract_post_urls_from_file(filepath):
    """
    Read the text file and extract all URLs that look like a Byoru post.
    Returns a list of unique URLs.
    """
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()

    # Regular expression to find HTTP/HTTPS URLs
    # We specifically look for byoru.net URLs that contain a post slug.
    # The pattern matches: https://byoru.net/ followed by a path (not including query strings)
    # We'll also allow variations.
    url_pattern = re.compile(
        r'https?://byoru\.net/[^\s<>"\'\)]+', re.IGNORECASE
    )
    raw_urls = url_pattern.findall(content)

    # Filter out non-post URLs (like home, category, etc.)
    # Post URLs often contain a date-like segment or a slug with dashes.
    # For safety, we accept any URL that is not just the domain or a category.
    post_urls = []
    for url in raw_urls:
        parsed = urlparse(url)
        path = parsed.path
        # Exclude root, category pages, tag pages, etc.
        if path in ('/', ''):
            continue
        if path.startswith('/category/') or path.startswith('/tag/'):
            continue
        if path.startswith('/page/') or path.startswith('/author/'):
            continue
        # Accept anything else as a potential post
        post_urls.append(url)

    # Remove duplicates while preserving order
    seen = set()
    unique_urls = []
    for url in post_urls:
        if url not in seen:
            seen.add(url)
            unique_urls.append(url)

    return unique_urls


def get_article_images(soup):
    """
    Given a BeautifulSoup object, find all img tags within the article body.
    Return a list of absolute image URLs.
    """
    # The images are inside <div itemprop="articleBody">, but we can fallback to article or main content.
    article_div = soup.find('div', itemprop='articleBody')
    if not article_div:
        # fallback: try to find the main content area (often .s-post-content)
        article_div = soup.find('div', class_='s-post-content')
    if not article_div:
        # fallback: just search all img tags inside the page
        article_div = soup

    images = []
    for img in article_div.find_all('img'):
        src = img.get('src')
        if src:
            # make absolute URL if relative
            absolute = urljoin('https://byoru.net/', src)
            images.append(absolute)
    return images


def download_image(url, folder, filename=None):
    """
    Download a single image and save it to the specified folder.
    Returns the local file path or None on failure.
    """
    if not filename:
        # extract filename from URL
        filename = os.path.basename(urlparse(url).path)
        if not filename:
            filename = f"img_{int(time.time())}.jpg"

    # Ensure the folder exists
    os.makedirs(folder, exist_ok=True)

    local_path = os.path.join(folder, filename)

    # Skip if already exists
    if os.path.exists(local_path):
        print(f"  [SKIP] {filename} already exists")
        return local_path

    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(url, headers={'User-Agent': USER_AGENT}, timeout=TIMEOUT)
            if resp.status_code == 200:
                with open(local_path, 'wb') as f:
                    f.write(resp.content)
                print(f"  [OK] Downloaded {filename}")
                return local_path
            else:
                print(f"  [WARN] Failed to download {url} (status {resp.status_code})")
        except Exception as e:
            print(f"  [ERROR] Attempt {attempt+1} for {url}: {e}")
            time.sleep(1)
    return None


def process_post(post_url, session, output_root):
    """
    Fetch a single post page, extract image URLs, and download them.
    """
    print(f"\nProcessing: {post_url}")
    for attempt in range(MAX_RETRIES):
        try:
            resp = session.get(post_url, timeout=TIMEOUT)
            if resp.status_code != 200:
                print(f"  Failed to fetch post (status {resp.status_code})")
                return
            break
        except Exception as e:
            print(f"  Error fetching post (attempt {attempt+1}): {e}")
            time.sleep(2)
            if attempt == MAX_RETRIES - 1:
                return

    soup = BeautifulSoup(resp.text, 'html.parser')
    image_urls = get_article_images(soup)

    if not image_urls:
        print("  No images found in this post.")
        return

    # Create a subfolder for this post using a sanitized slug from the URL
    # e.g., extract the last part of the path
    path_parts = urlparse(post_url).path.strip('/').split('/')
    if path_parts:
        slug = path_parts[-1]
    else:
        slug = 'post'
    # sanitize slug to avoid filesystem issues
    slug = re.sub(r'[\\/*?:"<>|]', '_', slug)
    post_folder = os.path.join(output_root, slug)

    print(f"  Found {len(image_urls)} image(s). Downloading to {post_folder}/")

    for img_url in image_urls:
        download_image(img_url, post_folder)
        time.sleep(DELAY_BETWEEN_IMAGES)


def main():
    # Check if text file exists
    if not os.path.isfile(TEXT_FILE):
        print(f"ERROR: File '{TEXT_FILE}' not found in current directory.")
        return

    print(f"Extracting post URLs from '{TEXT_FILE}'...")
    post_urls = extract_post_urls_from_file(TEXT_FILE)

    if not post_urls:
        print("No post URLs found. Exiting.")
        return

    print(f"Found {len(post_urls)} unique post URLs.")

    # Create output root
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Use a session for persistent headers
    session = requests.Session()
    session.headers.update({'User-Agent': USER_AGENT})

    for idx, url in enumerate(post_urls, 1):
        print(f"\n[{idx}/{len(post_urls)}] {url}")
        process_post(url, session, OUTPUT_DIR)
        time.sleep(DELAY_BETWEEN_POSTS)

    print("\nAll done!")


if __name__ == "__main__":
    main()