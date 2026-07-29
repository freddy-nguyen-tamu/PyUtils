from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Iterable

from selenium import webdriver
from selenium.common.exceptions import (
    JavascriptException,
    NoSuchElementException,
    SessionNotCreatedException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
from urllib3.exceptions import MaxRetryError, ReadTimeoutError


# =============================================================================
# CONFIGURATION
# =============================================================================
SCRIPT_VERSION = "NEW-ADOBE-SOURCES-v3-MINIMAL-HIDDEN-HEADED-2026-07-22"

SOURCE_DIR = Path(r"C:\Users\qacer\Downloads\adobe")
ADOBE_URL = "https://www.adobe.com/express/feature/image/remove-background"
HEADLESS_NAVIGATION_ATTEMPTS = 4
HEADLESS_NAVIGATION_SETTLE_SECONDS = 2.0
HIDDEN_PAGE_READY_TIMEOUT_SECONDS = 20

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_RETRIES_PER_FILE = 3

PAGE_LOAD_TIMEOUT_SECONDS = 120
UPLOAD_CONTROL_TIMEOUT_SECONDS = 120
PROCESSING_TIMEOUT_SECONDS = 900
DOWNLOAD_TIMEOUT_SECONDS = 180
EXPRESS_EXPORT_PANEL_TIMEOUT_SECONDS = 30
PROCESSING_POLL_SECONDS = 1.0
WEBDRIVER_COMMAND_TIMEOUT_SECONDS = 35
BROWSER_STARTUP_RETRIES = 2

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

LOGIN_CONFIRMATION = "LOGGED_IN"
START_CONFIRMATION = "START"

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

# Current Adobe Express editor selectors. The readiness marker becomes active
# only after Remove Background has finished. The header button merely opens the
# export panel; the second selector is the actual final Download button.
EXPRESS_EDITOR_READY_SELECTORS = [
    "x-property-button[data-testid='restore-bg-image-btn'][active]",
    "[data-testid='restore-bg-image-btn'][active]",
]

EXPRESS_EDITOR_DOWNLOAD_SELECTORS = [
    "sp-button[data-testid='editor-download-button']",
    "sp-button#download-btn[data-testid='editor-download-button']",
]

EXPRESS_FINAL_DOWNLOAD_DEEP_SELECTORS = [
    "sp-button[data-testid='download-commit-button']",
    "sp-button#dialog-download-btn.download-button",
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


PROFILE_LOCK_FILENAMES = (
    "SingletonLock",
    "SingletonCookie",
    "SingletonSocket",
    "DevToolsActivePort",
)


def remove_stale_profile_locks() -> None:
    """Remove lock markers left after a crashed Selenium Chrome process."""
    for filename in PROFILE_LOCK_FILENAMES:
        path = PROFILE_DIR / filename
        try:
            if path.is_file() or path.is_symlink():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
        except OSError:
            pass


def terminate_profile_chrome_processes() -> None:
    """
    Stop only Chrome processes whose command line contains this script's
    dedicated Selenium profile path.

    This does not target the user's ordinary Chrome profile.
    """
    if os.name != "nt":
        return

    profile_text = str(PROFILE_DIR.resolve()).replace("'", "''")
    command = (
        "$needle = '" + profile_text + "'; "
        "Get-CimInstance Win32_Process | "
        "Where-Object { "
        "$_.Name -ieq 'chrome.exe' -and "
        "$_.CommandLine -and "
        "$_.CommandLine.Contains($needle) "
        "} | ForEach-Object { "
        "Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue "
        "}"
    )

    try:
        subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        pass


def prepare_profile_for_start() -> None:
    """
    Clear stale processes and locks belonging only to the dedicated Selenium
    profile. This is safe for both visible and headless modes and prevents
    DevToolsActivePort/profile-in-use startup failures.
    """
    terminate_profile_chrome_processes()
    time.sleep(0.75)
    remove_stale_profile_locks()


def configure_driver_command_timeout(driver: webdriver.Chrome) -> None:
    """Prevent one frozen Chrome command from blocking the batch for 120 seconds."""
    try:
        driver.command_executor._client_config.timeout = (
            WEBDRIVER_COMMAND_TIMEOUT_SECONDS
        )
        return
    except Exception:
        pass

    # Compatibility fallback for older Selenium releases.
    try:
        driver.command_executor.set_timeout(WEBDRIVER_COMMAND_TIMEOUT_SECONDS)
    except Exception:
        pass


def is_driver_transport_failure(exc: BaseException) -> bool:
    """Return True when the ChromeDriver connection/session is no longer usable."""
    if isinstance(
        exc,
        (
            ReadTimeoutError,
            MaxRetryError,
            SessionNotCreatedException,
            ConnectionError,
        ),
    ):
        return True

    message = str(exc).lower()
    indicators = (
        "httpconnectionpool(host='localhost'",
        "read timed out",
        "max retries exceeded",
        "invalid session id",
        "chrome not reachable",
        "disconnected: not connected to devtools",
        "devtoolsactiveport",
        "session deleted because of page crash",
        "target frame detached",
        "tab crashed",
    )
    return any(indicator in message for indicator in indicators)


def should_restart_browser_after_error(exc: BaseException) -> bool:
    """Return True when retrying inside the same browser is unlikely to help."""
    if is_driver_transport_failure(exc):
        return True

    message = str(exc).lower()
    restart_indicators = (
        "could not find adobe's new upload input",
        "chrome network error",
        "could not load adobe before any ui automation ran",
        "page application did not initialize",
        "adobe bootstrap stayed incomplete",
        "err_http2_protocol_error",
        "err_quic_protocol_error",
        "chrome failed to start",
        "devtoolsactiveport",
        "session not created",
    )
    return any(indicator in message for indicator in restart_indicators)


def stop_driver(
    driver: webdriver.Chrome | None,
    *,
    force: bool = False,
    clean_profile: bool = False,
) -> None:
    """Stop ChromeDriver without letting a dead session block cleanup."""
    if driver is not None:
        service = getattr(driver, "service", None)
        process = getattr(service, "process", None)

        if not force:
            try:
                driver.quit()
                driver = None
            except Exception:
                force = True

        if force and process is not None:
            try:
                if process.poll() is None:
                    process.kill()
            except Exception:
                pass

    if clean_profile:
        terminate_profile_chrome_processes()
        time.sleep(0.5)
        remove_stale_profile_locks()


# =============================================================================
# SELENIUM / CHROME SETUP
# =============================================================================
def get_driver(headless: bool = False) -> webdriver.Chrome:
    """
    Open Chrome with the same persistent Selenium profile.

    Both visible and headless startup receive one cleanup/retry because a
    previously crashed Selenium Chrome can lock this dedicated profile in
    either mode.
    """
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    DOWNLOAD_STAGING_DIR.mkdir(parents=True, exist_ok=True)

    prepare_profile_for_start()

    last_error: Exception | None = None
    attempts = BROWSER_STARTUP_RETRIES

    for startup_attempt in range(1, attempts + 1):
        chrome_options = Options()
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")

        if headless:
            # Adobe currently leaves its page at `body { display: none; }` and
            # never creates the uploader under true --headless=new Chrome.
            #
            # Therefore the user's no-window choice runs ordinary headed Chrome
            # far off-screen. Adobe receives the same renderer and page
            # lifecycle as working VISIBLE mode, while the window stays outside
            # the normal desktop area.
            chrome_options.add_argument("--window-size=1920,1080")
            chrome_options.add_argument("--window-position=-32000,-32000")
            chrome_options.add_argument("--no-first-run")
            chrome_options.add_argument("--no-default-browser-check")
            chrome_options.add_argument("--disable-background-timer-throttling")
            chrome_options.add_argument("--disable-renderer-backgrounding")
            chrome_options.add_argument("--disable-backgrounding-occluded-windows")
            chrome_options.add_argument(
                "--disable-features=CalculateNativeWinOcclusion"
            )
        else:
            chrome_options.add_argument("--start-maximized")

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

        chrome_options.add_experimental_option(
            "prefs",
            {
                "download.default_directory": str(
                    DOWNLOAD_STAGING_DIR.resolve()
                ),
                "download.prompt_for_download": False,
                "download.directory_upgrade": True,
                "safebrowsing.enabled": True,
                "profile.default_content_setting_values.automatic_downloads": 1,
            },
        )

        service = Service(ChromeDriverManager().install())

        try:
            driver = webdriver.Chrome(service=service, options=chrome_options)
        except SessionNotCreatedException as exc:
            last_error = exc

            if startup_attempt >= attempts:
                raise

            mode_name = "Hidden" if headless else "Visible"
            print(
                f"{mode_name} Chrome did not start. Clearing the dedicated "
                "Selenium profile lock and retrying once..."
            )
            prepare_profile_for_start()
            time.sleep(1)
            continue

        configure_driver_command_timeout(driver)
        driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT_SECONDS)
        setattr(driver, "_adobe_headless_mode", headless)

        if headless:
            # Repeat the requested geometry through WebDriver because some
            # Chrome versions ignore command-line window placement.
            try:
                driver.set_window_size(1920, 1080)
                driver.set_window_position(-32000, -32000)
            except WebDriverException:
                pass

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

    assert last_error is not None
    raise last_error


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
    SessionNotCreatedException,
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


# One lightweight query for the current Express editor. It traverses open
# shadow roots once, finds both the completed Remove Background state and the
# exact header Download button, and returns the button only when both exist.
# No MutationObserver, persistent cache, broad button scan, or page injection.
EXPRESS_READY_DOWNLOAD_JS = r"""
const readySelectors = arguments[0];
const downloadSelectors = arguments[1];

function isVisibleAndEnabled(el) {
    if (!el) return false;

    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();

    if (style.display === "none" || style.visibility === "hidden") return false;
    if (rect.width <= 0 || rect.height <= 0) return false;
    if (el.disabled || el.getAttribute("aria-disabled") === "true") return false;

    return true;
}

const stack = [document];
let ready = null;
let downloadButton = null;

while (stack.length) {
    const root = stack.pop();

    if (!ready) {
        for (const selector of readySelectors) {
            try {
                const match = root.querySelector(selector);
                if (match && isVisibleAndEnabled(match)) {
                    ready = match;
                    break;
                }
            } catch (_) {}
        }
    }

    if (!downloadButton) {
        for (const selector of downloadSelectors) {
            try {
                const match = root.querySelector(selector);
                if (match && isVisibleAndEnabled(match)) {
                    downloadButton = match;
                    break;
                }
            } catch (_) {}
        }
    }

    let all = [];
    try {
        all = root.querySelectorAll("*");
    } catch (_) {
        all = [];
    }

    for (const node of all) {
        if (node.shadowRoot) stack.push(node.shadowRoot);
    }
}

return ready && downloadButton ? downloadButton : null;
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


def express_ready_download_in_current_context(driver: webdriver.Chrome):
    try:
        return driver.execute_script(
            EXPRESS_READY_DOWNLOAD_JS,
            EXPRESS_EDITOR_READY_SELECTORS,
            EXPRESS_EDITOR_DOWNLOAD_SELECTORS,
        )
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
    SessionNotCreatedException,
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
    SessionNotCreatedException,
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
# BROWSER MODE
# =============================================================================
def choose_headless_mode() -> bool:
    """
    Ask whether Chrome should be visible.

    HEADLESS remains accepted for compatibility, but Adobe cannot currently
    initialize its uploader in true Chrome headless mode. The no-window choice
    therefore uses normal headed Chrome positioned far off-screen.
    """
    print("\n" + "=" * 78)
    print("BROWSER MODE")
    print("VISIBLE: opens Chrome and keeps the manual LOGGED_IN / START gate.")
    print(
        "HIDDEN: uses normal Chrome off-screen with the saved Selenium profile."
    )
    print(
        "HEADLESS is accepted as an alias for HIDDEN. True --headless Chrome "
        "is not used because Adobe leaves its page blank."
    )
    print(
        "Only choose HIDDEN/HEADLESS when that Selenium profile is already "
        "signed in."
    )

    while True:
        choice = input(
            "\nChoose VISIBLE or HIDDEN "
            "[HEADLESS also works; Enter = VISIBLE]: "
        ).strip().upper()

        if choice in {"", "V", "VISIBLE"}:
            print("Visible browser mode selected.")
            return False

        if choice in {"H", "HIDDEN", "HEADLESS"}:
            print(
                "Adobe-compatible hidden mode selected "
                "(regular Chrome positioned off-screen)."
            )
            return True

        print("Please type VISIBLE, HIDDEN, or HEADLESS.")


def detect_chrome_network_error(driver: webdriver.Chrome) -> str | None:
    """
    Detect Chrome's own error page even when current_url still shows Adobe.

    The rendered error document contains the net error code, such as
    ERR_HTTP2_PROTOCOL_ERROR. Returning it immediately prevents the script from
    waiting for an Adobe element that cannot exist.
    """
    try:
        current_url = (driver.current_url or "").lower()
    except WebDriverException:
        current_url = ""

    try:
        title = driver.title or ""
    except WebDriverException:
        title = ""

    try:
        body_text = driver.execute_script(
            "return document.body ? document.body.innerText : '';"
        ) or ""
    except WebDriverException:
        body_text = ""

    try:
        page_source = driver.page_source or ""
    except WebDriverException:
        page_source = ""

    combined = "\n".join(
        (current_url, title, body_text[:20000], page_source[:50000])
    ).upper()

    known_errors = (
        "ERR_HTTP2_PROTOCOL_ERROR",
        "ERR_QUIC_PROTOCOL_ERROR",
        "ERR_CONNECTION_RESET",
        "ERR_CONNECTION_CLOSED",
        "ERR_CONNECTION_TIMED_OUT",
        "ERR_TIMED_OUT",
        "ERR_NETWORK_CHANGED",
        "ERR_INTERNET_DISCONNECTED",
        "ERR_PROXY_CONNECTION_FAILED",
        "ERR_NAME_NOT_RESOLVED",
        "ERR_SSL_PROTOCOL_ERROR",
    )

    for error_code in known_errors:
        if error_code in combined:
            return error_code

    if current_url.startswith("chrome-error://"):
        return "CHROME_NETWORK_ERROR_PAGE"

    if "THIS SITE CAN'T BE REACHED" in combined:
        return "CHROME_NETWORK_ERROR_PAGE"

    return None


def adobe_bootstrap_state(driver: webdriver.Chrome) -> dict:
    """
    Return enough state to distinguish a loaded Adobe app from its hidden shell.

    Adobe initially ships `body { display: none; }`. Its JavaScript must remove
    or override that state and decorate the frictionless quick-action block.
    True headless Chrome was receiving the HTML but never completing that step.
    """
    try:
        state = driver.execute_script(
            """
            const body = document.body;
            const main = document.querySelector('main');
            const fqa = document.querySelector('.frictionless-quick-action');
            const fallbackLink = document.querySelector(
                '.frictionless-quick-action a.same-fcta'
            );
            const fileInput = document.querySelector(
                "input.ia-file-input[name='file-upload'][type='file'], "
                + ".ia-dropzone-shell input[type='file'], "
                + ".upload-action-container input[type='file'], "
                + "input[type='file']"
            );

            return {
                readyState: document.readyState,
                title: document.title || '',
                bodyExists: Boolean(body),
                bodyDisplay: body
                    ? window.getComputedStyle(body).display
                    : null,
                bodyTextLength: body
                    ? (body.innerText || '').trim().length
                    : 0,
                mainExists: Boolean(main),
                quickActionExists: Boolean(fqa),
                fallbackLinkExists: Boolean(fallbackLink),
                fileInputExists: Boolean(fileInput),
                scriptCount: document.scripts.length,
            };
            """
        )
        return state if isinstance(state, dict) else {}
    except WebDriverException:
        return {}


def wait_for_adobe_bootstrap(
    driver: webdriver.Chrome,
    timeout_seconds: float,
) -> tuple[bool, dict]:
    """
    Wait until Adobe has made its body visible.

    A visible body is the minimum proof that Adobe's page initializer ran.
    Upload-control discovery remains in find_upload_control(), where shadow
    roots and alternate Adobe variants are already handled.
    """
    deadline = time.monotonic() + timeout_seconds
    last_state: dict = {}

    while time.monotonic() < deadline:
        network_error = detect_chrome_network_error(driver)
        if network_error is not None:
            return False, {"networkError": network_error}

        last_state = adobe_bootstrap_state(driver)

        body_visible = (
            last_state.get("bodyExists")
            and last_state.get("bodyDisplay") != "none"
        )
        meaningful_document = (
            last_state.get("mainExists")
            or last_state.get("fileInputExists")
            or last_state.get("bodyTextLength", 0) > 20
        )

        if body_visible and meaningful_document:
            return True, last_state

        time.sleep(0.5)

    return False, last_state


def navigate_to_adobe(driver: webdriver.Chrome) -> None:
    """
    Open Adobe and verify that its JavaScript initializer made the page visible.

    The no-window mode is ordinary headed Chrome positioned off-screen, but the
    retry logic remains useful for transient network failures or an Adobe shell
    that does not finish initializing.
    """
    hidden_mode = bool(getattr(driver, "_adobe_headless_mode", False))
    attempts = HEADLESS_NAVIGATION_ATTEMPTS if hidden_mode else 1
    last_problem = "unknown startup failure"

    for attempt in range(1, attempts + 1):
        try:
            driver.get(ADOBE_URL)
        except TimeoutException:
            # A page-load timeout may still leave a usable rendered document.
            pass

        time.sleep(HEADLESS_NAVIGATION_SETTLE_SECONDS if hidden_mode else 0.5)

        network_error = detect_chrome_network_error(driver)
        if network_error is not None:
            last_problem = network_error
        else:
            ready, state = wait_for_adobe_bootstrap(
                driver,
                timeout_seconds=HIDDEN_PAGE_READY_TIMEOUT_SECONDS,
            )
            if ready:
                return

            if state.get("networkError"):
                last_problem = str(state["networkError"])
            else:
                last_problem = (
                    "Adobe bootstrap stayed incomplete: "
                    f"readyState={state.get('readyState')!r}, "
                    f"bodyDisplay={state.get('bodyDisplay')!r}, "
                    f"bodyTextLength={state.get('bodyTextLength')!r}, "
                    f"quickActionExists={state.get('quickActionExists')!r}, "
                    f"fallbackLinkExists={state.get('fallbackLinkExists')!r}, "
                    f"fileInputExists={state.get('fileInputExists')!r}, "
                    f"scriptCount={state.get('scriptCount')!r}"
                )

        if attempt >= attempts:
            break

        delay = min(2 * attempt, 6)
        mode_label = "Hidden Chrome" if hidden_mode else "Chrome"
        print(
            f"{mode_label} did not initialize Adobe correctly "
            f"({last_problem}). Retrying in {delay} seconds "
            f"({attempt}/{attempts})..."
        )

        try:
            driver.get("about:blank")
        except WebDriverException:
            pass

        try:
            driver.execute_cdp_cmd("Network.clearBrowserCache", {})
        except WebDriverException:
            pass

        time.sleep(delay)

    raise WebDriverException(
        "Chrome reached Adobe but its page application did not initialize. "
        f"Last observed problem: {last_problem}"
    )


def prepare_headless_session(driver: webdriver.Chrome) -> None:
    """
    Open Adobe with the saved profile in an off-screen headed Chrome window.
    """
    print("\n" + "=" * 78)
    print("HIDDEN ADOBE SESSION")
    print(f"Persistent Chrome profile: {PROFILE_DIR}")
    print(
        "Regular Chrome is running off-screen so Adobe receives the same "
        "renderer as VISIBLE mode."
    )
    print(
        "A Chrome taskbar item may exist, but the browser window is positioned "
        "outside the normal desktop."
    )
    print("The saved login from this profile will be reused.")

    navigate_to_adobe(driver)

    try:
        dismiss_common_overlays(driver)
    except WebDriverException:
        pass

    detected = detect_logged_in_account(driver)
    if detected:
        print(f"Saved Adobe account control detected: {detected}")
    else:
        print(
            "Adobe account control was not readable in hidden mode. "
            "Continuing because HIDDEN/HEADLESS was explicitly selected. "
            "Rerun in VISIBLE mode if Adobe requires authentication."
        )


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


def wait_for_manual_login(driver: webdriver.Chrome) -> None:
    """
    Open Adobe and do absolutely no upload until both LOGGED_IN and START are
    entered manually. This function does not refresh the page during login.
    """
    print("\n" + "=" * 78)
    print("MANUAL ADOBE LOGIN")
    print(f"Script version: {SCRIPT_VERSION}")
    print(f"Running file: {Path(__file__).resolve()}")
    print(f"Persistent Chrome profile: {PROFILE_DIR}")
    print("\nNo image has been uploaded.")
    print("Sign in to Adobe in the opened Chrome window.")
    print("Complete every verification/account-selection screen.")
    print("Wait until your Adobe avatar or account control is visible.")

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
            f"\nAfter login is fully complete, type exactly "
            f"{LOGIN_CONFIRMATION}: "
        ).strip()

        if confirmation.upper() != LOGIN_CONFIRMATION:
            print(
                f"Not accepted. Type exactly {LOGIN_CONFIRMATION}. "
                "The batch remains paused."
            )
            continue

        detected = detect_logged_in_account(driver)

        if detected:
            print(f"Adobe account control detected: {detected}")
        else:
            print(
                "Adobe's dynamic account control was not readable, but the "
                "manual confirmation remains authoritative."
            )

        start = input(
            f"\nReview the open browser. Type exactly {START_CONFIRMATION} "
            "to begin uploading, or press Enter to keep waiting: "
        ).strip()

        if start.upper() == START_CONFIRMATION:
            print("\nManual login confirmed. Starting the batch.")
            return

        print("The batch remains paused. No image has been uploaded.")


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
    navigate_to_adobe(driver)

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
        network_error = detect_chrome_network_error(driver)
        if network_error is not None:
            raise WebDriverException(
                "Chrome network error before Adobe uploader initialization: "
                f"{network_error}"
            )

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


def find_express_editor_download_once(driver: webdriver.Chrome):
    """Return the current Express Download button only after processing ends."""
    for handle in prioritize_window_handles(driver):
        try:
            driver.switch_to.window(handle)
            driver.switch_to.default_content()
            url = driver.current_url.lower()
        except WebDriverException:
            continue

        if "express.adobe.com/id/" not in url:
            continue

        button = express_ready_download_in_current_context(driver)
        if button is not None:
            return (
                "express-editor-shadow",
                handle,
                button,
                current_context_url(driver),
            )

    return None


def current_adobe_route(driver: webdriver.Chrome) -> str:
    """Identify the active workflow so only one result detector runs."""
    route = "inline"

    for handle in prioritize_window_handles(driver):
        try:
            driver.switch_to.window(handle)
            url = driver.current_url.lower()
        except WebDriverException:
            continue

        if url.startswith("devtools://"):
            continue
        if "express.adobe.com/id/" in url:
            return "express"
        if "firefly.adobe.com" in url:
            route = "firefly"

    return route


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
    Wait for the processed result without continuously running every detector.

    The current Express editor is checked with one exact, single-pass shadow-DOM
    query per polling cycle. Firefly and inline checks retain the old behavior.
    """
    deadline = time.monotonic() + PROCESSING_TIMEOUT_SECONDS
    last_status_time = 0.0

    stable_key: tuple[str, str, str] | None = None
    stable_since: float | None = None
    last_reported_second: int | None = None

    while time.monotonic() < deadline:
        route = current_adobe_route(driver)

        if route == "express":
            candidate = find_express_editor_download_once(driver)
        elif route == "firefly":
            candidate = find_firefly_download_once(driver)
        else:
            # The current workflow is either still on the upload page or in
            # the middle of redirecting. Do not run the old recursive legacy
            # selector scan here; it is unnecessary for the current Adobe UI
            # and can block while the renderer is transitioning.
            candidate = find_inline_ready_download_once(driver)

        now = time.monotonic()

        if candidate is not None:
            mode, handle, button, context_url = candidate

            # The active restore-bg marker is Adobe's completion state, so the
            # Express route needs no arbitrary 12-second settling delay.
            if mode == "express-editor-shadow":
                print(
                    "Express reports that Remove Background is complete. "
                    "Using the current Download control."
                )
                return candidate

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
                print(
                    "Result page remained stable. "
                    "Using the freshly reacquired Download control."
                )
                return candidate

        else:
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

        time.sleep(PROCESSING_POLL_SECONDS)

    raise TimeoutException(
        "Adobe never exposed a completed inline result, Firefly result, or "
        "completed Express Remove Background state. "
        f"Open pages: {list_open_urls(driver)}"
    )


# =============================================================================
# DOWNLOAD HANDLING
# =============================================================================
def download_staging_has_activity() -> bool:
    try:
        return any(path.is_file() for path in DOWNLOAD_STAGING_DIR.iterdir())
    except OSError:
        return False


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

    # Preserve the original click behavior. The only new step is that the
    # current Express header button opens an export panel instead of downloading
    # immediately, so click its exact final Download button afterward.
    time.sleep(0.75)

    print(f"Clicking Download using mode: {mode}")
    click_element(driver, button)

    if mode == "express-editor-shadow":
        try:
            _, final_button, context_url = wait_for_deep_element(
                driver,
                EXPRESS_FINAL_DOWNLOAD_DEEP_SELECTORS,
                timeout_seconds=EXPRESS_EXPORT_PANEL_TIMEOUT_SECONDS,
                description="the Express export panel's final Download button",
            )
            print(
                "Adobe opened the export panel. Clicking the final Download "
                f"button inside: {context_url}"
            )
            click_element(driver, final_button)
        except TimeoutException:
            # Some Adobe variants may download directly from the header click.
            if not download_staging_has_activity():
                raise

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
        "Waiting for the inline result, Firefly result, or completed "
        "Express Remove Background state..."
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

    try:
        handles = prioritize_window_handles(driver)
    except Exception:
        return saved

    for index, handle in enumerate(handles, start=1):
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
    headless = choose_headless_mode()

    try:
        # Preserve the original script's startup order: Chrome opens and the
        # selected session is prepared before checking whether images remain.
        # This makes VISIBLE mode reliably open the browser and login gate.
        driver = get_driver(headless=headless)

        if headless:
            prepare_headless_session(driver)
        else:
            wait_for_manual_login(driver)

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
                    assert driver is not None
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
                    transport_failure = is_driver_transport_failure(exc)
                    restart_browser = should_restart_browser_after_error(exc)

                    print(
                        f"Attempt failed: "
                        f"{type(exc).__name__}: {exc}"
                    )

                    if transport_failure:
                        print(
                            "ChromeDriver stopped responding. Skipping browser "
                            "diagnostics and preparing a clean restart."
                        )
                    else:
                        artifacts = save_error_artifacts(driver, source)
                        for artifact in artifacts:
                            print(f"Error artifact: {artifact}")

                    if restart_browser:
                        stop_driver(
                            driver,
                            force=transport_failure,
                            clean_profile=True,
                        )
                        driver = None

                    clear_download_staging()

                    if attempt < MAX_RETRIES_PER_FILE:
                        if driver is None:
                            print(
                                "Restarting Chrome before retrying this file..."
                            )
                            driver = get_driver(headless=headless)

                            if headless:
                                prepare_headless_session(driver)
                            else:
                                # A visible restart must remain visible. Reopen
                                # the manual gate so login/challenge screens can
                                # be completed instead of silently continuing.
                                wait_for_manual_login(driver)
                        else:
                            print(
                                "Returning to the Adobe upload page and "
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
        stop_driver(
            driver,
            force=False,
            clean_profile=False,
        )

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