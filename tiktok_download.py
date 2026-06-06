import re
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service

# ================== CONFIGURATION ==================
TXT_FILE = "ndhang.txt"
DOWNLOAD_DIR = Path.cwd() / "downloads"
HEADLESS = True
WAIT_TIMEOUT = 30
DELAY_BETWEEN_VIDEOS = 2

DOWNLOAD_DIR.mkdir(exist_ok=True)

# ================== CHROME OPTIONS ==================
chrome_options = Options()
if HEADLESS:
    chrome_options.add_argument("--headless")
chrome_options.add_experimental_option("prefs", {
    "download.default_directory": str(DOWNLOAD_DIR.resolve()),
    "download.prompt_for_download": False,
    "download.directory_upgrade": True,
    "safebrowsing.enabled": True
})
chrome_options.add_argument("--disable-blink-features=AutomationControlled")
chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# ================== EXTRACT VIDEO URLs ==================
def extract_video_urls(file_path):
    urls = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            cleaned = re.sub(r"^webmssdk\.js:1\s+", "", line.strip())
            if "/video/" in cleaned and cleaned.startswith("https://"):
                urls.append(cleaned)
    return urls

# ================== HELPER: JavaScript Click ==================
def js_click(driver, element):
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
    time.sleep(0.3)
    driver.execute_script("arguments[0].click();", element)

# ================== MAIN ==================
def main():
    video_urls = extract_video_urls(TXT_FILE)
    if not video_urls:
        print("No video URLs found.")
        return

    print(f"Found {len(video_urls)} videos. Starting...\n")

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    driver.maximize_window()

    try:
        driver.get("https://musicaldown.com/en")
        wait = WebDriverWait(driver, WAIT_TIMEOUT)

        for idx, tiktok_url in enumerate(video_urls, start=1):
            print(f"\n[{idx}/{len(video_urls)}] Processing: {tiktok_url}")

            # 1. Paste URL
            input_field = wait.until(EC.presence_of_element_located((By.ID, "link_url")))
            input_field.clear()
            input_field.send_keys(tiktok_url)

            # 2. Click Download button (JavaScript click to avoid overlays)
            submit_btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button[type='submit'].orange")))
            js_click(driver, submit_btn)
            print("Submitted form.")

            # 3. Wait for HD download link and click it
            hd_link = wait.until(EC.presence_of_element_located(
                (By.CSS_SELECTOR, "a.download[data-event='hd_download_click']")
            ))
            js_click(driver, hd_link)
            print("Clicked HD download (no watermark).")

            # 4. Close the ad modal if it appears
            try:
                modal = WebDriverWait(driver, 5).until(
                    EC.presence_of_element_located((By.ID, "modal1"))
                )
                if modal.is_displayed():
                    close_btn = modal.find_element(By.CSS_SELECTOR, "a.modal-close.btn")
                    js_click(driver, close_btn)
                    print("Closed ad modal.")
            except:
                pass  # No modal, continue

            # 5. Click "Download Another Video" to reset the form
            reset_btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "a.btn[href='/en']")))
            js_click(driver, reset_btn)
            print("Reset form for next video.")

            time.sleep(DELAY_BETWEEN_VIDEOS)

    except Exception as e:
        print(f"Error: {e}")
    finally:
        print("\nClosing browser.")
        driver.quit()

if __name__ == "__main__":
    main()