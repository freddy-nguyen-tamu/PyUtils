import os
import time
import re
import requests
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

# Configure Chrome options
chrome_options = Options()
chrome_options.add_argument("--headless")  # Set to False to see the browser
chrome_options.add_argument("--no-sandbox")
chrome_options.add_argument("--disable-dev-shm-usage")
chrome_options.add_argument("--disable-gpu")
chrome_options.add_experimental_option("excludeSwitches", ["enable-logging"])

# Initialize driver
driver = webdriver.Chrome(
    service=Service(ChromeDriverManager().install()),
    options=chrome_options
)

# Create a requests session and copy cookies from driver
session = requests.Session()
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Referer": "https://cosplaythots.com/",
}
session.headers.update(headers)

def get_cookies_from_driver(driver):
    """Extract cookies from Selenium driver and add to requests session."""
    for cookie in driver.get_cookies():
        session.cookies.set(cookie['name'], cookie['value'])

def wait_for_images(driver, timeout=10):
    """Wait for at least one image to load."""
    WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, ".photo-item img"))
    )

def scroll_and_collect_image_urls(driver, max_attempts=3):
    """
    Scroll to bottom, wait for loading, collect all image src URLs.
    Returns a list of URLs (the 604 versions).
    """
    previous_count = 0
    same_count_attempts = 0

    while True:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)

        # Wait for spinner to disappear if present
        try:
            spinner = driver.find_element(By.ID, "loading-spinner")
            if spinner.is_displayed():
                WebDriverWait(driver, 10).until(
                    EC.invisibility_of_element_located((By.ID, "loading-spinner"))
                )
        except:
            pass

        # Get current image URLs
        imgs = driver.find_elements(By.CSS_SELECTOR, ".photo-item img")
        urls = [img.get_attribute("src") for img in imgs if img.get_attribute("src")]

        current_count = len(urls)
        if current_count == previous_count:
            same_count_attempts += 1
            if same_count_attempts >= max_attempts:
                break
        else:
            same_count_attempts = 0
            previous_count = current_count

    # Remove duplicates
    return list(dict.fromkeys(urls))

def download_image(url, save_path, session, retries=3):
    """Download a single image with retries."""
    for attempt in range(retries):
        try:
            resp = session.get(url, stream=True, timeout=30)
            if resp.status_code == 200:
                with open(save_path, 'wb') as f:
                    for chunk in resp.iter_content(chunk_size=8192):
                        f.write(chunk)
                return True
            else:
                print(f"    Attempt {attempt+1}: {resp.status_code} for {url}")
        except Exception as e:
            print(f"    Attempt {attempt+1} error: {e}")
        time.sleep(1)
    return False

def get_highest_quality_url(base_url):
    """
    Given a URL like .../604/... try to construct a higher quality URL.
    Known sizes: 604, 1280. We'll try 1280 first.
    """
    # Replace /604/ with /1280/
    high_url = base_url.replace("/604/", "/1280/")
    return high_url

def process_post_url(post_url, driver, session, base_download_dir="cosplay"):
    """Process a single post URL: extract images and download highest quality."""
    print(f"\nProcessing: {post_url}")
    driver.get(post_url)
    wait_for_images(driver)

    # Update session cookies
    get_cookies_from_driver(driver)

    # Get all image URLs (604 thumbnails)
    image_urls = scroll_and_collect_image_urls(driver)
    if not image_urls:
        print("  No images found.")
        return

    print(f"  Found {len(image_urls)} images.")

    # Extract post ID from URL
    match = re.search(r'/p/(\d+)', post_url)
    if not match:
        print("  Could not extract post ID.")
        return
    post_id = match.group(1)

    # Create post directory
    post_dir = os.path.join(base_download_dir, post_id)
    os.makedirs(post_dir, exist_ok=True)

    # Download each image
    for idx, thumb_url in enumerate(image_urls, start=1):
        # Try high quality first
        high_url = get_highest_quality_url(thumb_url)
        # Determine file extension from thumb_url
        ext = thumb_url.split('.')[-1].split('?')[0]
        filename = f"image_{idx:03d}.{ext}"
        save_path = os.path.join(post_dir, filename)

        # Skip if already exists
        if os.path.exists(save_path):
            print(f"  Skipping {filename} (already exists)")
            continue

        # Try high quality
        print(f"  Downloading {filename} (trying high quality)...")
        if download_image(high_url, save_path, session):
            print(f"    Downloaded high quality.")
            continue

        # Fallback to thumbnail
        print(f"  Falling back to thumbnail for {filename}...")
        if download_image(thumb_url, save_path, session):
            print(f"    Downloaded thumbnail.")
        else:
            print(f"  Failed to download {filename}.")

def main():
    # Read URLs from file
    url_file = "cosplay_urls.txt"
    if not os.path.exists(url_file):
        print(f"File {url_file} not found.")
        return

    with open(url_file, 'r') as f:
        urls = [line.strip() for line in f if line.strip()]

    if not urls:
        print("No URLs found in file.")
        return

    print(f"Total URLs to process: {len(urls)}")

    # Process each URL
    for url in urls:
        try:
            process_post_url(url, driver, session)
        except Exception as e:
            print(f"Error processing {url}: {e}")
            continue

    driver.quit()
    print("\nAll downloads complete.")

if __name__ == "__main__":
    main()