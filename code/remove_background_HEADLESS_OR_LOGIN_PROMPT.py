from __future__ import annotations

import base64
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Iterable

from selenium import webdriver
from selenium.common.exceptions import (
    JavascriptException,
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager


# =============================================================================
# CONFIGURATION
# =============================================================================
SCRIPT_VERSION = "HEADLESS-OR-LOGIN-PROMPT-v2-2026-07-09"

SOURCE_DIR = Path(r"C:\Users\qacer\Downloads\adobe")
ADOBE_URL = "https://www.adobe.com/express/feature/image/remove-background"

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_RETRIES_PER_FILE = 3

PAGE_LOAD_TIMEOUT_SECONDS = 120
UPLOAD_CONTROL_TIMEOUT_SECONDS = 120
PROCESSING_TIMEOUT_SECONDS = 240
DOWNLOAD_TIMEOUT_SECONDS = 180

# Adobe can briefly render a previously processed Firefly image immediately
# after redirecting. Do not click Download until the redirected result page and
# its Download control have remained stable for this long.
REDIRECT_SETTLE_SECONDS = 12

# The inline (non-redirected) result does not need as long, but still receives
# a short settling period to avoid clicking while Adobe is swapping previews.
INLINE_RESULT_SETTLE_SECONDS = 3

# Persistent Selenium Chrome profile. Adobe cookies, login, local storage, and
# account selection are retained between runs.
PROFILE_DIR = SOURCE_DIR / "_selenium_chrome_profile"

# Chrome is forced to put downloads here first. The script verifies the local
# file, then moves it to SOURCE_DIR as "Untitled - original_name.png".
DOWNLOAD_STAGING_DIR = SOURCE_DIR / "_download_staging"

PROCESSED_DIR = SOURCE_DIR / "_processed_originals"
FAILED_DIR = SOURCE_DIR / "_failed_originals"
ERROR_DIR = SOURCE_DIR / "_automation_errors"

# If the persistent Adobe profile is already signed in, run fully headless
# with no terminal prompt. If it is not signed in, open visible Chrome and wait
# for the user to type LOGGED_IN after completing Adobe login.
HEADLESS_IF_LOGGED_IN = True
LOGIN_CONFIRMATION = "LOGGED_IN"
LOGIN_VERIFY_TIMEOUT_SECONDS = 90
LOGIN_RECHECK_INTERVAL_SECONDS = 1.0

TEMP_DOWNLOAD_SUFFIXES = {".crdownload", ".tmp", ".part"}


# Exact selectors from the NEW initial Adobe page source.
NEW_UPLOAD_LOCATORS = [
    (
        By.CSS_SELECTOR,
        "input.ia-file-input[name='file-upload'][type='file']",
    ),
    (By.CSS_SELECTOR, ".ia-dropzone-shell input[type='file']"),
    (By.CSS_SELECTOR, ".upload-action-container input[type='file']"),
]

NEW_INLINE_RESULT_IMAGE_LOCATORS = [
    (By.CSS_SELECTOR, "img.ia-result-img[alt='Processed image']"),
    (By.CSS_SELECTOR, ".ia-result img.ia-result-img"),
]

NEW_INLINE_DOWNLOAD_LOCATORS = [
    (By.CSS_SELECTOR, "button.ia-download-btn"),
    (
        By.XPATH,
        "//button[contains(@class,'ia-download-btn')]"
        "[.//span[normalize-space()='Download']]",
    ),
]

# Old/alternate Adobe input selectors are retained only as fallback.
FALLBACK_UPLOAD_LOCATORS = [
    (By.CSS_SELECTOR, ".dropzone-container input[type='file']"),
    (By.CSS_SELECTOR, "input[type='file'][accept*='image']"),
    (By.CSS_SELECTOR, "input[type='file']"),
]

# Firefly's final processed page uses open Shadow DOM. Ordinary Selenium CSS
# selectors cannot reliably cross all those shadow roots, so the script uses
# recursive JavaScript queries for these selectors.
FIREFLY_DOWNLOAD_DEEP_SELECTORS = [
    (
        "firefly-image-editor-header-download-button "
        "sp-action-button[aria-label='Download']"
    ),
    "sp-action-button.download-button[aria-label='Download']",
    "sp-action-button[aria-label='Download'][label='Download']",
    "firefly-image-editor-header-download-button",
]

# If the workflow opens the older embedded Express editor, retain support.
LEGACY_DOWNLOAD_LOCATORS = [
    (By.CSS_SELECTOR, "sp-button#download-btn"),
    (By.CSS_SELECTOR, "sp-button[data-testid='editor-download-button']"),
    (By.CSS_SELECTOR, "#download-btn"),
    (By.CSS_SELECTOR, "[data-testid='editor-download-button']"),
]

AUTH_ACCOUNT_LOCATORS = [
    (
        By.CSS_SELECTOR,
        "pandora-react-mini-app-account-menu[user-id]:not([user-id=''])",
    ),
    (By.CSS_SELECTOR, "[data-testid*='account-menu']"),
    (By.CSS_SELECTOR, "button[aria-label*='Account']"),
    (By.CSS_SELECTOR, "button[aria-label*='Profile']"),
]


# =============================================================================
# FILE HELPERS
# =============================================================================
def pending_images() -> list[Path]:
    """Return eligible images not beginning with 'Untitled'."""
    return sorted(
        (
            path
            for path in SOURCE_DIR.iterdir()
            if path.is_file()
            and path.suffix.lower() in SUPPORTED_EXTENSIONS
            and not path.name.lower().startswith("untitled")
        ),
        key=lambda item: item.name.lower(),
    )


def safe_stem(text: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", text).strip(" .")
    return cleaned or "image"


def unique_path(folder: Path, filename: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    candidate = folder / filename
    if not candidate.exists():
        return candidate

    counter = 2
    while True:
        candidate = (
            folder
            / f"{Path(filename).stem} ({counter}){Path(filename).suffix}"
        )
        if not candidate.exists():
            return candidate
        counter += 1


def output_path_for(source: Path) -> Path:
    return unique_path(
        SOURCE_DIR,
        f"Untitled - {safe_stem(source.stem)}.png",
    )


def move_original(source: Path, destination_folder: Path) -> Path:
    destination = unique_path(destination_folder, source.name)
    shutil.move(str(source), str(destination))
    return destination


def clear_download_staging() -> None:
    DOWNLOAD_STAGING_DIR.mkdir(parents=True, exist_ok=True)
    for path in DOWNLOAD_STAGING_DIR.iterdir():
        try:
            if path.is_file() or path.is_symlink():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
        except OSError:
            pass


# =============================================================================
# SELENIUM / CHROME SETUP
# =============================================================================
def get_driver(headless: bool = False) -> webdriver.Chrome:
    """
    Use the same Selenium/Chrome opening method as the supplied working script,
    plus a persistent profile and an explicit download directory.

    When headless=True and the saved Adobe profile is already authenticated,
    the batch runs entirely in the background.
    """
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    DOWNLOAD_STAGING_DIR.mkdir(parents=True, exist_ok=True)

    chrome_options = Options()
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option(
        "excludeSwitches",
        ["enable-automation"],
    )
    chrome_options.add_experimental_option(
        "useAutomationExtension",
        False,
    )

    chrome_options.add_argument(f"--user-data-dir={PROFILE_DIR}")
    chrome_options.add_argument("--profile-directory=Default")

    if headless:
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--window-size=1440,960")
    else:
        chrome_options.add_argument("--start-maximized")

    chrome_options.add_experimental_option(
        "prefs",
        {
            "download.default_directory": str(DOWNLOAD_STAGING_DIR.resolve()),
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": True,
            "profile.default_content_setting_values.automatic_downloads": 1,
        },
    )

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT_SECONDS)

    stealth_script = """
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        });
    """

    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": stealth_script},
        )
    except WebDriverException:
        pass

    try:
        driver.execute_script(stealth_script)
    except JavascriptException:
        pass

    try:
        driver.execute_cdp_cmd(
            "Page.setDownloadBehavior",
            {
                "behavior": "allow",
                "downloadPath": str(DOWNLOAD_STAGING_DIR.resolve()),
            },
        )
    except WebDriverException:
        pass

    return driver


# =============================================================================
# GENERAL BROWSER HELPERS
# =============================================================================
def dismiss_common_overlays(driver: webdriver.Chrome) -> None:
    try:
        driver.switch_to.default_content()
    except WebDriverException:
        return

    selectors = [
        (By.CSS_SELECTOR, "#onetrust-accept-btn-handler"),
        (
            By.XPATH,
            "//button[contains(normalize-space(.), 'Accept all')]"
            " | //button[contains(normalize-space(.), 'Accept All')]",
        ),
        (
            By.XPATH,
            "//button[contains(normalize-space(.), 'I agree')]"
            " | //button[contains(normalize-space(.), 'Continue')]",
        ),
    ]

    for by, selector in selectors:
        try:
            for element in driver.find_elements(by, selector):
                if element.is_displayed() and element.is_enabled():
                    try:
                        element.click()
                    except WebDriverException:
                        driver.execute_script("arguments[0].click()", element)
                    time.sleep(0.4)
                    return
        except WebDriverException:
            continue


def element_is_usable(element, require_displayed: bool = True) -> bool:
    try:
        if require_displayed and not element.is_displayed():
            return False

        if (element.get_attribute("aria-disabled") or "").lower() == "true":
            return False

        if element.get_attribute("disabled") is not None:
            return False

        return element.is_enabled()
    except (StaleElementReferenceException, WebDriverException):
        return False


def current_context_url(driver: webdriver.Chrome) -> str:
    try:
        return str(driver.execute_script("return window.location.href"))
    except WebDriverException:
        try:
            return driver.current_url
        except WebDriverException:
            return "(URL unavailable)"


def click_element(driver: webdriver.Chrome, element) -> None:
    try:
        element.click()
    except WebDriverException:
        driver.execute_script("arguments[0].click()", element)


def prioritize_window_handles(driver: webdriver.Chrome) -> list[str]:
    try:
        handles = list(driver.window_handles)
    except WebDriverException:
        return []

    try:
        current = driver.current_window_handle
        handles = [current] + [item for item in handles if item != current]
    except WebDriverException:
        pass

    return handles


def find_in_current_context(
    driver: webdriver.Chrome,
    locators: Iterable[tuple[str, str]],
    require_displayed: bool = True,
):
    for by, selector in locators:
        try:
            elements = driver.find_elements(by, selector)
        except WebDriverException:
            continue

        for element in elements:
            if element_is_usable(
                element,
                require_displayed=require_displayed,
            ):
                return element

    return None


def search_frames_recursively(
    driver: webdriver.Chrome,
    locators: Iterable[tuple[str, str]],
    require_displayed: bool = True,
    depth: int = 0,
    max_depth: int = 6,
):
    """
    Search the current document and every nested iframe.

    On success Selenium remains inside the frame containing the element.
    """
    element = find_in_current_context(
        driver,
        locators,
        require_displayed=require_displayed,
    )
    if element is not None:
        return element, current_context_url(driver)

    if depth >= max_depth:
        return None

    try:
        frames = driver.find_elements(By.CSS_SELECTOR, "iframe, frame")
    except WebDriverException:
        return None

    def frame_priority(frame) -> int:
        try:
            src = (frame.get_attribute("src") or "").lower()
            if "firefly.adobe.com" in src:
                return 0
            if "quick-actions.express.adobe.com" in src:
                return 1
            if "express.adobe.com" in src:
                return 2
        except WebDriverException:
            pass
        return 3

    try:
        frames = sorted(frames, key=frame_priority)
    except Exception:
        pass

    for frame in frames:
        switched = False
        try:
            driver.switch_to.frame(frame)
            switched = True

            found = search_frames_recursively(
                driver,
                locators,
                require_displayed=require_displayed,
                depth=depth + 1,
                max_depth=max_depth,
            )
            if found is not None:
                return found

        except (
            NoSuchElementException,
            StaleElementReferenceException,
            WebDriverException,
        ):
            pass

        if switched:
            try:
                driver.switch_to.parent_frame()
            except WebDriverException:
                try:
                    driver.switch_to.default_content()
                except WebDriverException:
                    pass

    return None


def find_across_windows_and_frames_once(
    driver: webdriver.Chrome,
    locators: Iterable[tuple[str, str]],
    require_displayed: bool = True,
):
    for handle in prioritize_window_handles(driver):
        try:
            driver.switch_to.window(handle)
            driver.switch_to.default_content()
        except WebDriverException:
            continue

        found = search_frames_recursively(
            driver,
            locators,
            require_displayed=require_displayed,
        )
        if found is not None:
            element, context_url = found
            return handle, element, context_url

    return None


def wait_for_regular_element(
    driver: webdriver.Chrome,
    locators: Iterable[tuple[str, str]],
    timeout_seconds: float,
    description: str,
    require_displayed: bool = True,
):
    deadline = time.monotonic() + timeout_seconds
    last_urls: list[str] = []

    while time.monotonic() < deadline:
        found = find_across_windows_and_frames_once(
            driver,
            locators,
            require_displayed=require_displayed,
        )
        if found is not None:
            return found

        last_urls = list_open_urls(driver)
        time.sleep(0.35)

    raise TimeoutException(
        f"Timed out waiting for {description}. Open pages: {last_urls}"
    )


def list_open_urls(driver: webdriver.Chrome) -> list[str]:
    urls: list[str] = []

    for handle in prioritize_window_handles(driver):
        try:
            driver.switch_to.window(handle)
            urls.append(driver.current_url)
        except WebDriverException:
            continue

    return urls


# =============================================================================
# OPEN SHADOW-DOM HELPERS
# =============================================================================
DEEP_QUERY_FIRST_JS = r"""
const selectors = arguments[0];

function isVisibleAndEnabled(el) {
    if (!el) return false;

    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();

    if (style.display === "none" || style.visibility === "hidden") {
        return false;
    }

    if (rect.width <= 0 || rect.height <= 0) {
        return false;
    }

    if (el.disabled || el.getAttribute("aria-disabled") === "true") {
        return false;
    }

    return true;
}

function collectRoots(startRoot) {
    const roots = [];
    const stack = [startRoot];

    while (stack.length) {
        const root = stack.pop();
        roots.push(root);

        let all = [];
        try {
            all = root.querySelectorAll("*");
        } catch (_) {
            all = [];
        }

        for (const node of all) {
            if (node.shadowRoot) {
                stack.push(node.shadowRoot);
            }
        }
    }

    return roots;
}

const roots = collectRoots(document);

for (const selector of selectors) {
    for (const root of roots) {
        let matches = [];
        try {
            matches = root.querySelectorAll(selector);
        } catch (_) {
            matches = [];
        }

        for (const match of matches) {
            if (isVisibleAndEnabled(match)) {
                return match;
            }
        }
    }
}

return null;
"""


DEEP_UPLOAD_INPUT_JS = r"""
function collectRoots(startRoot) {
    const roots = [];
    const stack = [startRoot];

    while (stack.length) {
        const root = stack.pop();
        roots.push(root);

        let all = [];
        try {
            all = root.querySelectorAll("*");
        } catch (_) {
            all = [];
        }

        for (const node of all) {
            if (node.shadowRoot) {
                stack.push(node.shadowRoot);
            }
        }
    }

    return roots;
}

function surroundingUploadHost(input) {
    let node = input;

    while (node) {
        if (
            node.tagName &&
            node.tagName.toLowerCase() === "firefly-upload-new"
        ) {
            return node;
        }

        const root = node.getRootNode ? node.getRootNode() : null;
        if (root && root.host) {
            node = root.host;
        } else {
            node = node.parentElement;
        }
    }

    return null;
}

const candidates = [];

for (const root of collectRoots(document)) {
    let inputs = [];

    try {
        inputs = root.querySelectorAll("input[type='file']");
    } catch (_) {
        inputs = [];
    }

    for (const input of inputs) {
        const accept = (input.getAttribute("accept") || "").toLowerCase();

        if (
            accept &&
            !accept.includes("image") &&
            !accept.includes(".jpg") &&
            !accept.includes(".jpeg") &&
            !accept.includes(".png") &&
            !accept.includes(".webp")
        ) {
            continue;
        }

        let score = 0;

        if (input.classList.contains("ia-file-input")) score += 1000;
        if (input.getAttribute("name") === "file-upload") score += 900;
        if (input.id === "file-input") score += 100;
        if (accept.includes("image")) score += 40;

        const host = surroundingUploadHost(input);

        if (host) {
            if (host.classList.contains("add-new")) score += 300;
            if (!host.hasAttribute("fileref")) score += 200;
            if (host.hasAttribute("fileref")) score -= 500;
        }

        if (input.disabled) score -= 1000;

        candidates.push({input, score});
    }
}

candidates.sort((a, b) => b.score - a.score);
return candidates.length ? candidates[0].input : null;
"""


def deep_query_first_in_current_context(
    driver: webdriver.Chrome,
    selectors: list[str],
):
    try:
        return driver.execute_script(DEEP_QUERY_FIRST_JS, selectors)
    except (JavascriptException, WebDriverException):
        return None


def deep_upload_input_in_current_context(driver: webdriver.Chrome):
    try:
        return driver.execute_script(DEEP_UPLOAD_INPUT_JS)
    except (JavascriptException, WebDriverException):
        return None


def deep_search_frames_recursively(
    driver: webdriver.Chrome,
    selectors: list[str],
    depth: int = 0,
    max_depth: int = 5,
):
    element = deep_query_first_in_current_context(driver, selectors)
    if element is not None:
        return element, current_context_url(driver)

    if depth >= max_depth:
        return None

    try:
        frames = driver.find_elements(By.CSS_SELECTOR, "iframe, frame")
    except WebDriverException:
        return None

    for frame in frames:
        switched = False

        try:
            driver.switch_to.frame(frame)
            switched = True

            found = deep_search_frames_recursively(
                driver,
                selectors,
                depth=depth + 1,
                max_depth=max_depth,
            )
            if found is not None:
                return found

        except (
            NoSuchElementException,
            StaleElementReferenceException,
            WebDriverException,
        ):
            pass

        if switched:
            try:
                driver.switch_to.parent_frame()
            except WebDriverException:
                try:
                    driver.switch_to.default_content()
                except WebDriverException:
                    pass

    return None


def deep_find_across_windows_once(
    driver: webdriver.Chrome,
    selectors: list[str],
):
    for handle in prioritize_window_handles(driver):
        try:
            driver.switch_to.window(handle)
            driver.switch_to.default_content()
        except WebDriverException:
            continue

        found = deep_search_frames_recursively(driver, selectors)
        if found is not None:
            element, context_url = found
            return handle, element, context_url

    return None


def wait_for_deep_element(
    driver: webdriver.Chrome,
    selectors: list[str],
    timeout_seconds: float,
    description: str,
):
    deadline = time.monotonic() + timeout_seconds

    while time.monotonic() < deadline:
        found = deep_find_across_windows_once(driver, selectors)
        if found is not None:
            return found

        time.sleep(0.4)

    raise TimeoutException(
        f"Timed out waiting for {description}. "
        f"Open pages: {list_open_urls(driver)}"
    )


def find_deep_upload_across_windows_once(driver: webdriver.Chrome):
    for handle in prioritize_window_handles(driver):
        try:
            driver.switch_to.window(handle)
            driver.switch_to.default_content()
        except WebDriverException:
            continue

        # Search top document first.
        element = deep_upload_input_in_current_context(driver)
        if element is not None:
            return handle, element, current_context_url(driver)

        # Then search nested frames.
        found = deep_upload_in_frames(driver)
        if found is not None:
            element, context_url = found
            return handle, element, context_url

    return None


def deep_upload_in_frames(
    driver: webdriver.Chrome,
    depth: int = 0,
    max_depth: int = 5,
):
    if depth >= max_depth:
        return None

    try:
        frames = driver.find_elements(By.CSS_SELECTOR, "iframe, frame")
    except WebDriverException:
        return None

    for frame in frames:
        switched = False

        try:
            driver.switch_to.frame(frame)
            switched = True

            element = deep_upload_input_in_current_context(driver)
            if element is not None:
                return element, current_context_url(driver)

            nested = deep_upload_in_frames(
                driver,
                depth=depth + 1,
                max_depth=max_depth,
            )
            if nested is not None:
                return nested

        except (
            NoSuchElementException,
            StaleElementReferenceException,
            WebDriverException,
        ):
            pass

        if switched:
            try:
                driver.switch_to.parent_frame()
            except WebDriverException:
                try:
                    driver.switch_to.default_content()
                except WebDriverException:
                    pass

    return None


# =============================================================================
# MANUAL LOGIN GATE
# =============================================================================
def detect_logged_in_account(driver: webdriver.Chrome) -> str | None:
    found = find_across_windows_and_frames_once(
        driver,
        AUTH_ACCOUNT_LOCATORS,
        require_displayed=True,
    )

    if found is None:
        return None

    _, element, _ = found

    for attribute in ("user-id", "aria-label", "title"):
        try:
            value = (element.get_attribute(attribute) or "").strip()
            if value:
                return value
        except WebDriverException:
            pass

    return "account control detected"


def adobe_session_looks_authenticated(driver: webdriver.Chrome) -> str | None:
    """
    Detect whether the persistent Adobe profile already appears signed in and
    usable, without requiring any terminal input.
    """
    try:
        driver.get(ADOBE_URL)
    except TimeoutException:
        pass

    try:
        dismiss_common_overlays(driver)
    except WebDriverException:
        pass

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        detected = detect_logged_in_account(driver)
        if detected:
            return detected

        upload_found = find_across_windows_and_frames_once(
            driver,
            NEW_UPLOAD_LOCATORS,
            require_displayed=False,
        )
        if upload_found is not None:
            return "upload page accessible"

        deep_upload_found = find_deep_upload_across_windows_once(driver)
        if deep_upload_found is not None:
            return "firefly upload accessible"

        time.sleep(0.5)

    return None


def wait_for_manual_login_confirmation(
    driver: webdriver.Chrome,
) -> str:
    """
    Open visible Chrome for Adobe login and require only LOGGED_IN.

    There is intentionally no START prompt. After LOGGED_IN is entered, Adobe
    login is verified before the script relaunches headless.
    """
    print("\n" + "=" * 78)
    print("ADOBE LOGIN REQUIRED")
    print(f"Script version: {SCRIPT_VERSION}")
    print(f"Running file: {Path(__file__).resolve()}")
    print(f"Persistent Chrome profile: {PROFILE_DIR}")
    print("\nNo valid saved Adobe login was detected.")
    print("A visible Chrome window has been opened.")
    print("Log in to Adobe and complete all verification/account screens.")
    print("Wait until your Adobe account/avatar is visible.")
    print(f"Then return here and type exactly {LOGIN_CONFIRMATION}.")
    print("There is no START prompt.")

    try:
        driver.get(ADOBE_URL)
    except TimeoutException:
        pass

    try:
        dismiss_common_overlays(driver)
    except WebDriverException:
        pass

    while True:
        confirmation = input(
            f"\nType exactly {LOGIN_CONFIRMATION} after Adobe login is complete: "
        ).strip()

        if confirmation.upper() != LOGIN_CONFIRMATION:
            print(
                f"Not accepted. Type exactly {LOGIN_CONFIRMATION}. "
                "No image has been uploaded."
            )
            continue

        print("Verifying the Adobe login...")
        deadline = time.monotonic() + LOGIN_VERIFY_TIMEOUT_SECONDS

        while time.monotonic() < deadline:
            detected = adobe_session_looks_authenticated(driver)
            if detected:
                print(f"Adobe login verified: {detected}")
                return detected

            time.sleep(LOGIN_RECHECK_INTERVAL_SECONDS)

        print(
            "\nAdobe login could not be verified yet. "
            "The batch remains paused and nothing has been uploaded."
        )
        print(
            "Finish the login in the visible Chrome window, then type "
            f"{LOGIN_CONFIRMATION} again."
        )


def obtain_authenticated_driver() -> webdriver.Chrome:
    """
    Desired startup behavior:

    1. Try the persistent profile headlessly.
    2. If Adobe is already signed in, continue headlessly with no prompt.
    3. Otherwise open visible Chrome and require only LOGGED_IN.
    4. Verify the login, close visible Chrome, and relaunch headless.
    """
    if HEADLESS_IF_LOGGED_IN:
        print("Checking saved Adobe login in headless background mode...")
        driver = get_driver(headless=True)

        try:
            detected = adobe_session_looks_authenticated(driver)
            if detected:
                print(f"Saved Adobe login detected: {detected}")
                print("No login prompt is needed. Continuing headlessly.")
                return driver

            print("No valid saved Adobe login was detected.")
        except Exception as exc:
            print(f"Headless login check warning: {type(exc).__name__}: {exc}")

        try:
            driver.quit()
        except WebDriverException:
            pass

    print("Opening visible Chrome for Adobe login...")
    driver = get_driver(headless=False)

    try:
        detected = wait_for_manual_login_confirmation(driver)
        print(f"Login completed in visible Chrome: {detected}")
    finally:
        # Closing Chrome writes cookies/profile state before relaunching.
        try:
            driver.quit()
        except WebDriverException:
            pass

    print("Relaunching with the same profile in headless background mode...")
    driver = get_driver(headless=True)

    detected = adobe_session_looks_authenticated(driver)
    if not detected:
        try:
            driver.quit()
        except WebDriverException:
            pass

        raise RuntimeError(
            "Adobe login was verified in visible Chrome, but the persistent "
            "profile was not authenticated after the headless relaunch."
        )

    print(f"Headless Adobe session confirmed: {detected}")
    return driver


# =============================================================================
# NAVIGATION AND UPLOAD
# =============================================================================
def choose_main_window(driver: webdriver.Chrome) -> str:
    handles = prioritize_window_handles(driver)
    if not handles:
        raise WebDriverException("Chrome has no open windows.")

    scored: list[tuple[int, str]] = []

    for handle in handles:
        score = 0

        try:
            driver.switch_to.window(handle)
            url = driver.current_url.lower()
        except WebDriverException:
            continue

        if "www.adobe.com/express/feature/image/remove-background" in url:
            score += 100
        elif "www.adobe.com" in url:
            score += 50
        elif "firefly.adobe.com" in url:
            score += 40

        if "accounts.adobe.com" in url:
            score -= 100

        scored.append((score, handle))

    if not scored:
        return handles[0]

    scored.sort(reverse=True)
    return scored[0][1]


def normalize_to_one_window(driver: webdriver.Chrome) -> None:
    keep = choose_main_window(driver)

    for handle in list(driver.window_handles):
        if handle == keep:
            continue

        try:
            driver.switch_to.window(handle)
            driver.close()
        except WebDriverException:
            pass

    driver.switch_to.window(keep)
    driver.switch_to.default_content()


def open_clean_adobe_page(driver: webdriver.Chrome) -> None:
    normalize_to_one_window(driver)

    try:
        driver.get(ADOBE_URL)
    except TimeoutException:
        pass

    try:
        dismiss_common_overlays(driver)
    except WebDriverException:
        pass


def find_upload_control(
    driver: webdriver.Chrome,
    timeout_seconds: float,
):
    deadline = time.monotonic() + timeout_seconds

    while time.monotonic() < deadline:
        # Exact selector from the new initial page source.
        found = find_across_windows_and_frames_once(
            driver,
            NEW_UPLOAD_LOCATORS,
            require_displayed=False,
        )
        if found is not None:
            return found

        # Firefly input inside open Shadow DOM, if Adobe sends the account
        # directly into Firefly.
        found = find_deep_upload_across_windows_once(driver)
        if found is not None:
            return found

        # Legacy fallback.
        found = find_across_windows_and_frames_once(
            driver,
            FALLBACK_UPLOAD_LOCATORS,
            require_displayed=False,
        )
        if found is not None:
            return found

        time.sleep(0.35)

    raise TimeoutException(
        "Could not find Adobe's new upload input "
        "input.ia-file-input[name='file-upload'] or a Firefly image input. "
        f"Open pages: {list_open_urls(driver)}"
    )


def upload_image(driver: webdriver.Chrome, source: Path) -> None:
    _, file_input, context_url = find_upload_control(
        driver,
        timeout_seconds=UPLOAD_CONTROL_TIMEOUT_SECONDS,
    )

    print(f"Upload input found inside: {context_url}")

    # Hidden file inputs accept send_keys in Selenium.
    file_input.send_keys(str(source.resolve()))


# =============================================================================
# RESULT READINESS AND DOWNLOAD CONTROL DETECTION
# =============================================================================
def inline_result_is_ready(driver: webdriver.Chrome) -> bool:
    """
    The new initial page contains the result nodes before processing, so merely
    finding .ia-download-btn is not enough. Require a loaded, nonempty result
    image and a visible download button.
    """
    try:
        result = find_in_current_context(
            driver,
            NEW_INLINE_RESULT_IMAGE_LOCATORS,
            require_displayed=True,
        )
        button = find_in_current_context(
            driver,
            NEW_INLINE_DOWNLOAD_LOCATORS,
            require_displayed=True,
        )

        if result is None or button is None:
            return False

        src = (result.get_attribute("src") or "").strip()

        if not src:
            return False

        return bool(
            driver.execute_script(
                """
                const img = arguments[0];
                return Boolean(
                    img &&
                    img.src &&
                    img.complete &&
                    img.naturalWidth > 0 &&
                    img.naturalHeight > 0
                );
                """,
                result,
            )
        )

    except (
        JavascriptException,
        StaleElementReferenceException,
        WebDriverException,
    ):
        return False


def find_inline_ready_download_once(driver: webdriver.Chrome):
    for handle in prioritize_window_handles(driver):
        try:
            driver.switch_to.window(handle)
            driver.switch_to.default_content()
        except WebDriverException:
            continue

        if inline_result_is_ready(driver):
            button = find_in_current_context(
                driver,
                NEW_INLINE_DOWNLOAD_LOCATORS,
                require_displayed=True,
            )
            if button is not None:
                return (
                    "inline",
                    handle,
                    button,
                    current_context_url(driver),
                )

    return None


def find_firefly_download_once(driver: webdriver.Chrome):
    found = deep_find_across_windows_once(
        driver,
        FIREFLY_DOWNLOAD_DEEP_SELECTORS,
    )

    if found is None:
        return None

    handle, button, context_url = found
    return "firefly-shadow", handle, button, context_url


def find_legacy_download_once(driver: webdriver.Chrome):
    found = find_across_windows_and_frames_once(
        driver,
        LEGACY_DOWNLOAD_LOCATORS,
        require_displayed=True,
    )

    if found is None:
        return None

    handle, button, context_url = found
    return "legacy", handle, button, context_url


def wait_for_ready_download_control(driver: webdriver.Chrome):
    """
    Wait for a ready Download control, then require that the same result page
    and mode remain continuously available for a settling period.

    This prevents Adobe's briefly restored previous Firefly result from being
    downloaded immediately after a redirect.
    """
    deadline = time.monotonic() + PROCESSING_TIMEOUT_SECONDS
    last_status_time = 0.0

    stable_key: tuple[str, str, str] | None = None
    stable_since: float | None = None
    last_reported_second: int | None = None

    while time.monotonic() < deadline:
        candidate = find_inline_ready_download_once(driver)

        if candidate is None:
            candidate = find_firefly_download_once(driver)

        if candidate is None:
            candidate = find_legacy_download_once(driver)

        now = time.monotonic()

        if candidate is not None:
            mode, handle, button, context_url = candidate
            key = (mode, handle, context_url)

            settle_seconds = (
                INLINE_RESULT_SETTLE_SECONDS
                if mode == "inline"
                else REDIRECT_SETTLE_SECONDS
            )

            if key != stable_key:
                stable_key = key
                stable_since = now
                last_reported_second = None

                if mode == "inline":
                    print(
                        "Inline processed result detected. "
                        f"Waiting {settle_seconds} seconds for it to settle..."
                    )
                else:
                    print(
                        f"Redirected result detected via {mode}: {context_url}"
                    )
                    print(
                        "Waiting "
                        f"{settle_seconds} seconds before Download so Adobe "
                        "can replace any restored previous result..."
                    )

            assert stable_since is not None
            elapsed = now - stable_since
            remaining = max(0.0, settle_seconds - elapsed)
            rounded_remaining = int(remaining + 0.999)

            if (
                rounded_remaining > 0
                and rounded_remaining != last_reported_second
                and (
                    rounded_remaining == int(settle_seconds)
                    or rounded_remaining % 3 == 0
                    or rounded_remaining <= 2
                )
            ):
                print(
                    f"Result settling: {rounded_remaining} second(s) remaining..."
                )
                last_reported_second = rounded_remaining

            if elapsed >= settle_seconds:
                # Reacquire on the final iteration. The returned WebElement is
                # therefore current, rather than the one first seen when the
                # redirected page may still have shown an older result.
                print(
                    "Result page remained stable. "
                    "Using the freshly reacquired Download control."
                )
                return candidate

        else:
            # If the control or URL disappears while Adobe is rerendering,
            # restart the settling timer when it returns.
            if stable_key is not None:
                print(
                    "Result control changed while settling; restarting the "
                    "safety wait."
                )
            stable_key = None
            stable_since = None
            last_reported_second = None

        if now - last_status_time >= 10:
            print(
                "Still processing. Current pages: "
                + " | ".join(list_open_urls(driver))
            )
            last_status_time = now

        time.sleep(0.4)

    raise TimeoutException(
        "Adobe never exposed a ready and stable new inline result, Firefly "
        "Shadow-DOM Download button, or legacy editor Download button. "
        f"Open pages: {list_open_urls(driver)}"
    )


# =============================================================================
# DOWNLOAD HANDLING
# =============================================================================
def wait_for_completed_download_file(timeout_seconds: float) -> Path:
    """
    Wait for Chrome to finish a local download. UUID filenames and files with
    no extension are accepted.
    """
    deadline = time.monotonic() + timeout_seconds
    last_candidate: Path | None = None
    last_size = -1
    stable_checks = 0

    while time.monotonic() < deadline:
        try:
            all_files = [
                path
                for path in DOWNLOAD_STAGING_DIR.iterdir()
                if path.is_file()
            ]
        except OSError:
            all_files = []

        temporary_exists = any(
            path.suffix.lower() in TEMP_DOWNLOAD_SUFFIXES
            or path.name.lower().endswith(".crdownload")
            for path in all_files
        )

        completed = [
            path
            for path in all_files
            if path.suffix.lower() not in TEMP_DOWNLOAD_SUFFIXES
            and not path.name.lower().endswith(".crdownload")
        ]

        if completed and not temporary_exists:
            candidate = max(
                completed,
                key=lambda path: path.stat().st_mtime,
            )

            try:
                size = candidate.stat().st_size
            except OSError:
                size = -1

            if (
                candidate == last_candidate
                and size > 0
                and size == last_size
            ):
                stable_checks += 1
            else:
                last_candidate = candidate
                last_size = size
                stable_checks = 0

            if stable_checks >= 3:
                return candidate

        time.sleep(0.5)

    contents: list[str] = []

    try:
        contents = [
            f"{path.name} ({path.stat().st_size} bytes)"
            for path in DOWNLOAD_STAGING_DIR.iterdir()
        ]
    except OSError:
        pass

    raise TimeoutException(
        "Chrome did not finish a local download. "
        f"Staging contents: {contents}"
    )


def save_downloaded_file(downloaded: Path, source: Path) -> Path:
    destination = output_path_for(source)
    shutil.move(str(downloaded), str(destination))

    if not destination.exists() or destination.stat().st_size <= 0:
        raise RuntimeError(
            f"Downloaded file could not be verified locally: {destination}"
        )

    return destination


def save_inline_blob_fallback(
    driver: webdriver.Chrome,
    source: Path,
) -> Path | None:
    """
    If Chrome's download event fails on the new inline page, fetch the loaded
    .ia-result-img blob/data URL inside the page and save it directly.
    """
    try:
        driver.switch_to.default_content()

        result = find_in_current_context(
            driver,
            NEW_INLINE_RESULT_IMAGE_LOCATORS,
            require_displayed=True,
        )

        if result is None:
            return None

        data = driver.execute_async_script(
            """
            const img = arguments[0];
            const done = arguments[arguments.length - 1];

            (async () => {
                try {
                    const src = img && img.src;

                    if (!src) {
                        done({ok: false, error: "empty result image src"});
                        return;
                    }

                    if (src.startsWith("data:")) {
                        done({ok: true, dataUrl: src});
                        return;
                    }

                    const response = await fetch(src);
                    const blob = await response.blob();
                    const reader = new FileReader();

                    reader.onloadend = () => {
                        done({ok: true, dataUrl: reader.result});
                    };

                    reader.onerror = () => {
                        done({ok: false, error: "FileReader failed"});
                    };

                    reader.readAsDataURL(blob);
                } catch (error) {
                    done({
                        ok: false,
                        error: String(error && error.message || error)
                    });
                }
            })();
            """,
            result,
        )

        if not isinstance(data, dict) or not data.get("ok"):
            return None

        data_url = str(data.get("dataUrl") or "")
        match = re.match(
            r"^data:image/[^;]+;base64,(.+)$",
            data_url,
            flags=re.DOTALL,
        )

        if not match:
            return None

        raw = base64.b64decode(match.group(1))
        destination = output_path_for(source)
        destination.write_bytes(raw)

        if destination.stat().st_size <= 0:
            destination.unlink(missing_ok=True)
            return None

        return destination

    except (
        JavascriptException,
        StaleElementReferenceException,
        WebDriverException,
        OSError,
        ValueError,
    ):
        return None


def perform_download(
    driver: webdriver.Chrome,
    source: Path,
    mode: str,
    button,
) -> Path:
    clear_download_staging()

    # A final small pause lets the most recent paint/event loop finish after
    # the stable button was reacquired.
    time.sleep(0.75)

    print(f"Clicking Download using mode: {mode}")
    click_element(driver, button)

    try:
        downloaded = wait_for_completed_download_file(
            timeout_seconds=DOWNLOAD_TIMEOUT_SECONDS,
        )
        return save_downloaded_file(downloaded, source)

    except TimeoutException:
        if mode == "inline":
            print(
                "Chrome did not expose a completed file; attempting direct "
                "save from the new inline processed-image blob..."
            )
            fallback = save_inline_blob_fallback(driver, source)

            if fallback is not None:
                return fallback

        raise


# =============================================================================
# ONE FILE
# =============================================================================
def process_one(driver: webdriver.Chrome, source: Path) -> Path:
    open_clean_adobe_page(driver)
    clear_download_staging()

    print(f"Uploading: {source.name}")
    upload_image(driver, source)

    print(
        "Waiting for either the new inline result or the Firefly "
        "Shadow-DOM Download button..."
    )

    mode, _, button, context_url = wait_for_ready_download_control(driver)

    print(f"Processed image is ready via {mode}: {context_url}")
    print("Downloading...")

    saved = perform_download(
        driver,
        source,
        mode,
        button,
    )

    print(f"Downloaded completely: {saved}")
    return saved


# =============================================================================
# DIAGNOSTICS
# =============================================================================
def save_error_artifacts(
    driver: webdriver.Chrome,
    source: Path,
) -> list[Path]:
    ERROR_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = f"{safe_stem(source.stem)}_{stamp}"
    saved: list[Path] = []

    for index, handle in enumerate(prioritize_window_handles(driver), start=1):
        try:
            driver.switch_to.window(handle)
            driver.switch_to.default_content()
        except WebDriverException:
            continue

        screenshot = unique_path(
            ERROR_DIR,
            f"{base}_window{index}.png",
        )
        html_path = unique_path(
            ERROR_DIR,
            f"{base}_window{index}.html",
        )
        url_path = unique_path(
            ERROR_DIR,
            f"{base}_window{index}_url.txt",
        )

        try:
            driver.save_screenshot(str(screenshot))
            saved.append(screenshot)
        except WebDriverException:
            pass

        try:
            html_path.write_text(
                driver.page_source,
                encoding="utf-8",
            )
            saved.append(html_path)
        except (OSError, WebDriverException):
            pass

        try:
            url_path.write_text(
                driver.current_url,
                encoding="utf-8",
            )
            saved.append(url_path)
        except (OSError, WebDriverException):
            pass

    return saved


# =============================================================================
# MAIN
# =============================================================================
def main() -> int:
    print("\n" + "#" * 78)
    print(f"RUNNING SCRIPT VERSION: {SCRIPT_VERSION}")
    print(f"RUNNING FILE: {Path(__file__).resolve()}")
    print("#" * 78)
    print("Behavior: saved login = no prompt/headless; missing login = type LOGGED_IN once.")

    if not SOURCE_DIR.exists():
        print(f"ERROR: Folder does not exist: {SOURCE_DIR}")
        return 1

    for directory in (
        PROFILE_DIR,
        DOWNLOAD_STAGING_DIR,
        PROCESSED_DIR,
        FAILED_DIR,
        ERROR_DIR,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    completed = 0
    failed = 0
    driver: webdriver.Chrome | None = None

    try:
        driver = obtain_authenticated_driver()

        while True:
            files = pending_images()

            if not files:
                break

            source = files[0]
            succeeded = False
            last_error: Exception | None = None

            for attempt in range(1, MAX_RETRIES_PER_FILE + 1):
                print("\n" + "=" * 78)
                print(
                    f"File: {source.name} | "
                    f"attempt {attempt}/{MAX_RETRIES_PER_FILE}"
                )

                try:
                    process_one(driver, source)

                    archived = move_original(source, PROCESSED_DIR)
                    print(f"Moved original to: {archived}")

                    completed += 1
                    succeeded = True
                    break

                except KeyboardInterrupt:
                    raise

                except Exception as exc:
                    last_error = exc
                    print(
                        f"Attempt failed: "
                        f"{type(exc).__name__}: {exc}"
                    )

                    artifacts = save_error_artifacts(driver, source)

                    for artifact in artifacts:
                        print(f"Error artifact: {artifact}")

                    clear_download_staging()

                    if attempt < MAX_RETRIES_PER_FILE:
                        print(
                            "Returning to the new Adobe upload page and "
                            "retrying this file..."
                        )
                        time.sleep(2)

            if not succeeded:
                failed += 1
                moved = move_original(source, FAILED_DIR)

                print(
                    f"Giving up after {MAX_RETRIES_PER_FILE} attempts. "
                    f"Moved original to: {moved}"
                )

                if last_error:
                    print(
                        f"Last error: {type(last_error).__name__}: "
                        f"{last_error}"
                    )

    finally:
        if driver is not None:
            try:
                driver.quit()
            except WebDriverException:
                pass

    print("\n" + "=" * 78)
    print("Batch finished.")
    print(f"Successfully processed: {completed}")
    print(f"Failed and preserved in {FAILED_DIR.name}: {failed}")
    print(f"Remaining eligible files: {len(pending_images())}")

    return 0 if failed == 0 else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nStopped by user. The current original file was preserved.")
        raise SystemExit(130)
