import os
import time
import base64
import re
from datetime import datetime, timedelta
from collections import deque
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service

# ---------- CONFIGURATION ----------
LOCAL_DIR = r"C:\Users\qacer\Downloads\nl"
PROMPT = "Make her smile and change outfit to red dress"
HEADLESS = False
IMAGE_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.webp')
MAX_RETRIES = 3
PROCESS_PER_HOUR = 2

# ---------- SELENIUM SETUP ----------
def get_driver():
    chrome_options = Options()
    if HEADLESS:
        chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    return driver

# ---------- POPUP HANDLING ----------
def handle_age_verification(driver):
    """
    Detect and click the age verification popup (e.g., 'I Confirm & Agree')
    """
    # Wait a moment for any modal to appear
    time.sleep(2)
    # Common selectors for the age confirmation button
    selectors = [
        "//button[contains(text(), 'I Confirm & Agree')]",
        "//button[contains(text(), 'I Confirm')]",
        "//button[contains(text(), 'Confirm & Agree')]",
        "//button[contains(text(), 'Agree')]",
        "//button[contains(text(), 'Yes')]",
        "//button[contains(text(), 'I am 18')]",
        "//button[contains(text(), 'Confirm')]",
        "//button[contains(text(), 'Enter')]",
        "//button[contains(text(), 'Continue')]",
        "//*[@role='button' and contains(text(), 'I Confirm')]",
        "//*[contains(@class, 'age')]//button",
        "//*[contains(@class, 'modal')]//button[contains(text(), 'Confirm')]",
    ]
    for xpath in selectors:
        try:
            btn = WebDriverWait(driver, 2).until(
                EC.element_to_be_clickable((By.XPATH, xpath))
            )
            btn.click()
            print("Age verification popup handled.")
            time.sleep(1)  # allow modal to close
            return True
        except:
            continue
    # If no popup found, just continue
    return False

def refresh_and_reinit(driver):
    """Refresh the page and re-initialize state."""
    driver.refresh()
    time.sleep(3)
    handle_age_verification(driver)
    ensure_model_selected(driver)

# ---------- CORE FUNCTIONS ----------
def ensure_model_selected(driver):
    """Ensure that 'FLUX.2 Klein 4B' is selected in the dropdown."""
    select_btn = WebDriverWait(driver, 10).until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, 'button[role="combobox"]'))
    )
    current_text = select_btn.text.strip()
    if "FLUX.2 Klein 4B" in current_text:
        return
    select_btn.click()
    option_selectors = [
        (By.XPATH, "//div[@role='option' and text()='FLUX.2 Klein 4B']"),
        (By.XPATH, "//span[text()='FLUX.2 Klein 4B']"),
        (By.XPATH, "//div[contains(text(), 'FLUX.2 Klein 4B')]"),
        (By.XPATH, "//li[contains(text(), 'FLUX.2 Klein 4B')]"),
        (By.XPATH, "//*[contains(@class, 'option') and contains(text(), 'FLUX.2 Klein 4B')]"),
        (By.XPATH, "//*[contains(@class, 'item') and contains(text(), 'FLUX.2 Klein 4B')]"),
    ]
    for by, selector in option_selectors:
        try:
            option = WebDriverWait(driver, 2).until(
                EC.element_to_be_clickable((by, selector))
            )
            option.click()
            return
        except:
            continue
    raise Exception("Could not find option 'FLUX.2 Klein 4B' in dropdown")

def upload_primary_image(driver, file_path):
    """Upload an image using the primary file input."""
    # Wait for any file input to be present
    WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.XPATH, "//input[@type='file']"))
    )
    file_inputs = driver.find_elements(By.XPATH, "//input[@type='file']")
    primary_input = None
    for inp in file_inputs:
        aria_label = inp.get_attribute("aria-label")
        if aria_label and "second" in aria_label.lower():
            continue
        primary_input = inp
        break
    if not primary_input:
        # If not found, try a more general approach: find the first file input that is not hidden
        for inp in file_inputs:
            if inp.is_displayed() and inp.is_enabled():
                primary_input = inp
                break
        if not primary_input:
            raise Exception("Primary file input not found")
    
    # Now upload the file
    primary_input.send_keys(file_path)
    
    # Wait for the image to appear (placeholder image changes)
    try:
        img_container = driver.find_element(By.XPATH, "//div[contains(@class, 'space-y-3')]//div[contains(@class, 'relative') and contains(@class, 'h-48')]")
        img = img_container.find_element(By.XPATH, ".//img")
        initial_src = img.get_attribute("src")
        WebDriverWait(driver, 15).until(
            lambda d: img.get_attribute("src") != initial_src
        )
    except:
        # If we can't find the image container, just wait a moment
        time.sleep(2)
    time.sleep(1)

def set_prompt(driver, prompt):
    textarea = WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.ID, "prompt"))
    )
    textarea.clear()
    textarea.send_keys(prompt)

def click_generate(driver):
    btn = WebDriverWait(driver, 10).until(
        EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Generate Edit')]"))
    )
    btn.click()

def check_for_error(driver):
    """Check if there's an error message on the page."""
    error_msgs = driver.find_elements(By.XPATH, "//*[contains(text(), 'Process failed') or contains(text(), 'error') or contains(text(), 'failed')]")
    for el in error_msgs:
        text = el.text.lower()
        if "process" in text or "failed" in text or "error" in text:
            return el.text
    toasts = driver.find_elements(By.XPATH, "//div[@role='alert' or contains(@class, 'toast') or contains(@class, 'notification')]")
    for toast in toasts:
        text = toast.text.lower()
        if "process" in text or "failed" in text or "error" in text:
            return toast.text
    return None

def wait_for_result(driver, timeout=180):
    """Wait for the result image to appear, checking for errors."""
    result_img = WebDriverWait(driver, 15).until(
        EC.presence_of_element_located((By.XPATH, "//img[@alt='Generated Edit']"))
    )
    initial_src = result_img.get_attribute("src")
    start = time.time()
    while time.time() - start < timeout:
        err = check_for_error(driver)
        if err:
            raise Exception(f"Generation failed with message: {err}")
        current_src = result_img.get_attribute("src")
        if current_src != initial_src:
            time.sleep(2)
            return
        time.sleep(1)
    raise Exception("Timeout waiting for result image")

def download_result(driver, output_path):
    img = driver.find_element(By.XPATH, "//img[@alt='Generated Edit']")
    src = img.get_attribute("src")
    if src.startswith("data:image"):
        base64_data = re.sub(r'^data:image/.+;base64,', '', src)
        image_bytes = base64.b64decode(base64_data)
        with open(output_path, "wb") as f:
            f.write(image_bytes)
        print(f"Downloaded: {output_path}")
    else:
        raise Exception("Result image is not a data URL; cannot download.")

def process_image(driver, image_path, prompt, output_dir):
    print(f"Processing: {image_path}")
    upload_primary_image(driver, image_path)
    set_prompt(driver, prompt)
    click_generate(driver)
    wait_for_result(driver)
    base = os.path.basename(image_path)
    name, ext = os.path.splitext(base)
    out_name = f"{name}_edited_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
    out_path = os.path.join(output_dir, out_name)
    download_result(driver, out_path)
    return True

# ---------- MAIN LOOP ----------
def main():
    driver = get_driver()
    driver.get("https://www.nsfwlover.com/nsfw-image-edit")
    print("Page loaded.")
    handle_age_verification(driver)
    ensure_model_selected(driver)

    image_files = [f for f in os.listdir(LOCAL_DIR) if f.lower().endswith(IMAGE_EXTENSIONS)]
    image_files.sort()
    if not image_files:
        print("No images found.")
        return

    output_dir = os.path.join(LOCAL_DIR, "output")
    os.makedirs(output_dir, exist_ok=True)

    pending = deque(image_files)
    retries = {f: 0 for f in image_files}

    print(f"Found {len(pending)} images. Processing up to {PROCESS_PER_HOUR} per hour.")

    while pending:
        processed_this_hour = 0

        while pending and processed_this_hour < PROCESS_PER_HOUR:
            img_file = pending.popleft()
            img_path = os.path.join(LOCAL_DIR, img_file)
            try:
                success = process_image(driver, img_path, PROMPT, output_dir)
                if success:
                    processed_this_hour += 1
                    del retries[img_file]
            except Exception as e:
                print(f"Error processing {img_file}: {e}")
                retries[img_file] = retries.get(img_file, 0) + 1
                if retries[img_file] >= MAX_RETRIES:
                    print(f"Max retries ({MAX_RETRIES}) exceeded for {img_file}. Skipping permanently.")
                else:
                    print("Refreshing page and reinitializing...")
                    refresh_and_reinit(driver)
                    pending.append(img_file)
                    print(f"Pushed {img_file} to back of queue (retry #{retries[img_file]})")

        if pending:
            now = datetime.now()
            next_hour = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
            wait_seconds = (next_hour - now).total_seconds()
            if wait_seconds < 60:
                wait_seconds = 3600
            print(f"Waiting {wait_seconds:.0f} seconds until next hour at {next_hour.strftime('%H:%M')}")
            time.sleep(wait_seconds)
        else:
            print("All images processed successfully.")
            break

    # Keep browser open for investigation
    print("\nAll done. Keeping browser open for investigation. Press Ctrl+C to exit.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Exiting...")
        driver.quit()

if __name__ == "__main__":
    main()