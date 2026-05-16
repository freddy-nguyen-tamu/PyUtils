from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
import time

# --- Setup Chrome ---
options = Options()
options.add_argument("--start-maximized")
# DO NOT use headless mode (Facebook login will fail)
# options.add_argument("--headless")

driver = webdriver.Chrome(
    service=Service(ChromeDriverManager().install()),
    options=options
)

# --- Open Facebook Friends page ---
url = "https://www.facebook.com/quan.nguyen.235036/friends"
driver.get(url)

print("\n Please sign in to Facebook manually.")
input(" After you are fully logged in and the Friends page loads, press ENTER...")

# --- Scroll to load more friends ---
def scroll_page(times=8, pause=2):
    for _ in range(times):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(pause)

scroll_page(times=8)

# --- Collect all clickable links ---
links = set()
elements = driver.find_elements(By.XPATH, "//a[@href]")

for el in elements:
    href = el.get_attribute("href")
    if href:
        links.add(href)

# --- Write links to text file ---
output_file = "facebook_friends_clickable_links.txt"
with open(output_file, "w", encoding="utf-8") as f:
    for link in sorted(links):
        f.write(link + "\n")

print(f"\n Saved {len(links)} clickable links to '{output_file}'")

# --- Cleanup ---
# driver.quit()