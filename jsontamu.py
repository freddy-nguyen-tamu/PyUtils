import json
import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager

def main():
    # --- HEADLESS SELENIUM SETUP ---
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")  # headless mode
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)

    results = {}  # dictionary for JSON output

    try:
        base_url = "https://engineering.tamu.edu/cse/profiles/index.html#Faculty"
        driver.get(base_url)
        time.sleep(2)

        # find all profile links
        profile_links = driver.find_elements(By.CSS_SELECTOR, "a[href*='/cse/profiles/']")
        profile_urls = list({link.get_attribute("href") for link in profile_links})

        print(f"[INFO] Found {len(profile_urls)} profile URLs.\n")

        for profile_url in profile_urls:
            print(f"[PROFILE] Visiting: {profile_url}")
            driver.get(profile_url)
            time.sleep(2)

            # fetch professor name
            try:
                name_elem = driver.find_element(By.TAG_NAME, "h1")
                prof_name = name_elem.text.strip()
            except:
                prof_name = "Unknown"
                print("[WARN] Could not extract name.")

            # find Google Scholar link
            scholar_links = driver.find_elements(By.CSS_SELECTOR, "a[href*='scholar.google.com/citations']")
            if not scholar_links:
                print(f"[WARN] No Google Scholar link for {prof_name}\n")
                continue

            scholar_url = scholar_links[0].get_attribute("href")
            print(f"  └── [SCHOLAR] {scholar_url}")

            # open google scholar page
            driver.get(scholar_url)
            time.sleep(3)

            try:
                scholar_text = driver.find_element(By.TAG_NAME, "body").text
            except:
                scholar_text = ""
                print("[WARN] Could not read Google Scholar content.")

            # store data in JSON-friendly structure
            results[prof_name] = {
                "name": prof_name,
                "profile_url": profile_url,
                "scholar_url": scholar_url,
                "scholar_text": scholar_text
            }

            print(f"[OK] Captured Google Scholar for {prof_name}\n")

        # write to JSON file
        with open("professors_scholar.json", "w", encoding="utf-8") as f:
            json.dump(results, f, indent=4, ensure_ascii=False)

        print("[DONE] JSON written to professors_scholar.json")

    finally:
        driver.quit()


if __name__ == "__main__":
    main()