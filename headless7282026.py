from __future__ import annotations

import base64
import ctypes
import os
import re
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from ctypes import wintypes
from dataclasses import dataclass
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
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.file_detector import UselessFileDetector
from webdriver_manager.chrome import ChromeDriverManager


# =============================================================================
# CONFIGURATION
# =============================================================================
SCRIPT_VERSION = "ADOBE-TRUE-HEADLESS-v8-DIRECT-FILEPATH-2026-07-28"

SOURCE_DIR = Path(r"C:\Users\qacer\Downloads\adobe")
ADOBE_URL = "https://www.adobe.com/express/feature/image/remove-background"

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_RETRIES_PER_FILE = 3

PAGE_LOAD_TIMEOUT_SECONDS = 120
UPLOAD_CONTROL_TIMEOUT_SECONDS = 120
PROCESSING_TIMEOUT_SECONDS = 240
DOWNLOAD_TIMEOUT_SECONDS = 180

# The current Adobe Express editor opens a download panel after the first
# header click. Wait this long for either that panel's final Download
# button or evidence that a direct download has already started.
SECOND_STAGE_DOWNLOAD_TIMEOUT_SECONDS = 20

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

# After START, the visible login Chrome is closed and a second *headed* Chrome
# is launched on a private Windows desktop. It is not headless and not merely
# minimized, so Adobe still receives a normal visible browser environment while
# Windows does not composite that browser onto the desktop you are using.
BACKGROUND_DESKTOP_PREFIX = "AdobeAutomation"
BACKGROUND_WINDOW_WIDTH = 1100
BACKGROUND_WINDOW_HEIGHT = 800
BACKGROUND_DRIVER_START_TIMEOUT_SECONDS = 45
BACKGROUND_START_RETRIES = 3
PROFILE_HANDOFF_DELAY_SECONDS = 3

# Chrome remains internally active even though its private desktop is not shown.
# These flags prevent Adobe from freezing timers merely because the browser's
# desktop is not the interactive desktop.
KEEP_BACKGROUND_PAGE_ACTIVE = True

BACKGROUND_CHROMEDRIVER_LOG = ERROR_DIR / "_headless_chromedriver.log"

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

# Adobe's July 2026 Express editor changed the processed-result UI. The new
# header control is x-editor-download-button, and its real clickable sp-button
# lives inside that component's open shadow root. These selectors are queried
# recursively across every open shadow root and iframe.
EXPRESS_EDITOR_DOWNLOAD_DEEP_SELECTORS = [
    "sp-button#download-btn[data-testid='editor-download-button']",
    "sp-button[data-testid='editor-download-button']",
    "x-editor-download-button sp-button#download-btn",
    "x-editor-download-button",
]

# If the workflow opens the older embedded Express editor without shadow DOM,
# retain ordinary Selenium selectors as a final fallback.
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
# Win32 constants used to create a private desktop and launch ChromeDriver on it.
DESKTOP_READOBJECTS = 0x0001
DESKTOP_CREATEWINDOW = 0x0002
DESKTOP_CREATEMENU = 0x0004
DESKTOP_HOOKCONTROL = 0x0008
DESKTOP_JOURNALRECORD = 0x0010
DESKTOP_JOURNALPLAYBACK = 0x0020
DESKTOP_ENUMERATE = 0x0040
DESKTOP_WRITEOBJECTS = 0x0080
DESKTOP_SWITCHDESKTOP = 0x0100
STANDARD_RIGHTS_REQUIRED = 0x000F0000
DESKTOP_ALL_ACCESS = STANDARD_RIGHTS_REQUIRED | (
    DESKTOP_READOBJECTS
    | DESKTOP_CREATEWINDOW
    | DESKTOP_CREATEMENU
    | DESKTOP_HOOKCONTROL
    | DESKTOP_JOURNALRECORD
    | DESKTOP_JOURNALPLAYBACK
    | DESKTOP_ENUMERATE
    | DESKTOP_WRITEOBJECTS
    | DESKTOP_SWITCHDESKTOP
)

CREATE_NO_WINDOW = 0x08000000
CREATE_UNICODE_ENVIRONMENT = 0x00000400
STILL_ACTIVE = 259
WAIT_TIMEOUT = 258
INFINITE = 0xFFFFFFFF


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_ubyte)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


def _windows_api_error(action: str) -> OSError:
    code = ctypes.get_last_error()
    return OSError(code, f"{action} failed: {ctypes.FormatError(code)}")


def _user32():
    if os.name != "nt":
        raise RuntimeError(
            "The hidden-desktop mode requires native Windows Python, not WSL."
        )

    dll = ctypes.WinDLL("user32", use_last_error=True)
    dll.CreateDesktopW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    dll.CreateDesktopW.restype = wintypes.HANDLE
    dll.CloseDesktop.argtypes = [wintypes.HANDLE]
    dll.CloseDesktop.restype = wintypes.BOOL
    return dll


def _kernel32():
    if os.name != "nt":
        raise RuntimeError(
            "The hidden-desktop mode requires native Windows Python, not WSL."
        )

    dll = ctypes.WinDLL("kernel32", use_last_error=True)
    dll.CreateProcessW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.POINTER(STARTUPINFOW),
        ctypes.POINTER(PROCESS_INFORMATION),
    ]
    dll.CreateProcessW.restype = wintypes.BOOL
    dll.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    dll.GetExitCodeProcess.restype = wintypes.BOOL
    dll.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    dll.TerminateProcess.restype = wintypes.BOOL
    dll.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    dll.WaitForSingleObject.restype = wintypes.DWORD
    dll.CloseHandle.argtypes = [wintypes.HANDLE]
    dll.CloseHandle.restype = wintypes.BOOL
    return dll


@dataclass
class HiddenDesktopBrowser:
    """Own the true-headless Chrome session used after manual login."""

    driver: webdriver.Remote
    desktop_handle: int = 0
    process_handle: int = 0
    process_id: int = 0
    desktop_name: str = "true-headless"

    def close(self) -> None:
        try:
            self.driver.quit()
        except WebDriverException:
            pass


def build_chrome_options(*, background: bool) -> Options:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    DOWNLOAD_STAGING_DIR.mkdir(parents=True, exist_ok=True)

    chrome_options = Options()
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--mute-audio")
    chrome_options.add_argument("--no-first-run")
    chrome_options.add_argument("--no-default-browser-check")

    if background:
        # True Chromium headless mode. This avoids creating or compositing a
        # visible browser window and is substantially lighter than headful mode.
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument(
            f"--window-size={BACKGROUND_WINDOW_WIDTH},{BACKGROUND_WINDOW_HEIGHT}"
        )
        chrome_options.add_argument("--hide-scrollbars")
        chrome_options.add_argument("--disable-extensions")
        chrome_options.add_argument("--disable-notifications")
        chrome_options.add_argument("--disable-component-update")
        chrome_options.add_argument("--disable-sync")
        chrome_options.add_argument("--metrics-recording-only")
        chrome_options.add_argument("--disable-default-apps")
        chrome_options.add_argument("--disable-features=Translate,MediaRouter,OptimizationHints")

        # Do not freeze Adobe's timers merely because no visible window exists.
        if KEEP_BACKGROUND_PAGE_ACTIVE:
            chrome_options.add_argument("--disable-background-timer-throttling")
            chrome_options.add_argument("--disable-renderer-backgrounding")
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
            "download.default_directory": str(DOWNLOAD_STAGING_DIR.resolve()),
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": True,
            "profile.default_content_setting_values.automatic_downloads": 1,
        },
    )

    return chrome_options


def execute_cdp_command(
    driver: webdriver.Remote,
    command: str,
    parameters: dict,
):
    """Run a Chrome CDP command for both local and directly attached sessions."""
    native_method = getattr(driver, "execute_cdp_cmd", None)

    if callable(native_method):
        return native_method(command, parameters)

    # webdriver.Remote does not always register ChromeDriver's vendor command.
    driver.command_executor._commands.setdefault(
        "executeCdpCommand",
        ("POST", "/session/$sessionId/goog/cdp/execute"),
    )
    response = driver.execute(
        "executeCdpCommand",
        {"cmd": command, "params": parameters},
    )
    return response.get("value") if isinstance(response, dict) else response


def configure_driver(driver: webdriver.Remote) -> webdriver.Remote:
    driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT_SECONDS)

    stealth_script = """
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        });
    """

    try:
        execute_cdp_command(
            driver,
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
        execute_cdp_command(
            driver,
            "Page.setDownloadBehavior",
            {
                "behavior": "allow",
                "downloadPath": str(DOWNLOAD_STAGING_DIR.resolve()),
            },
        )
    except WebDriverException:
        pass

    return driver


def get_visible_login_driver() -> webdriver.Chrome:
    """Open ordinary visible Chrome solely for manual Adobe login and review."""
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(
        service=service,
        options=build_chrome_options(background=False),
    )
    return configure_driver(driver)  # type: ignore[return-value]


def _reserve_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _create_private_desktop(name: str) -> int:
    user32 = _user32()
    handle = user32.CreateDesktopW(
        name,
        None,
        None,
        0,
        DESKTOP_ALL_ACCESS,
        None,
    )

    if not handle:
        raise _windows_api_error("CreateDesktopW")

    return int(handle)


def _close_private_desktop(handle: int) -> None:
    if not handle:
        return

    try:
        _user32().CloseDesktop(wintypes.HANDLE(handle))
    except Exception:
        pass


def _start_process_on_desktop(
    executable: Path,
    arguments: list[str],
    desktop_name: str,
) -> tuple[int, int]:
    kernel32 = _kernel32()

    command_line = subprocess.list2cmdline([str(executable), *arguments])
    mutable_command = ctypes.create_unicode_buffer(command_line)

    startup = STARTUPINFOW()
    startup.cb = ctypes.sizeof(STARTUPINFOW)
    startup.lpDesktop = f"Winsta0\\{desktop_name}"

    process_info = PROCESS_INFORMATION()

    created = kernel32.CreateProcessW(
        str(executable),
        mutable_command,
        None,
        None,
        False,
        CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT,
        None,
        str(executable.parent),
        ctypes.byref(startup),
        ctypes.byref(process_info),
    )

    if not created:
        raise _windows_api_error("CreateProcessW for ChromeDriver")

    # The primary-thread handle is not needed after process creation.
    kernel32.CloseHandle(process_info.hThread)

    return int(process_info.hProcess), int(process_info.dwProcessId)


def _native_process_is_running(process_handle: int) -> bool:
    if not process_handle:
        return False

    kernel32 = _kernel32()
    exit_code = wintypes.DWORD()

    if not kernel32.GetExitCodeProcess(
        wintypes.HANDLE(process_handle),
        ctypes.byref(exit_code),
    ):
        return False

    return int(exit_code.value) == STILL_ACTIVE


def _stop_native_process(process_handle: int) -> None:
    if not process_handle:
        return

    kernel32 = _kernel32()
    handle = wintypes.HANDLE(process_handle)

    try:
        if _native_process_is_running(process_handle):
            kernel32.TerminateProcess(handle, 0)
            result = kernel32.WaitForSingleObject(handle, 5000)

            if result == WAIT_TIMEOUT:
                kernel32.TerminateProcess(handle, 1)
                kernel32.WaitForSingleObject(handle, 2000)
    finally:
        kernel32.CloseHandle(handle)


def _wait_for_chromedriver(port: int, process_handle: int) -> None:
    deadline = time.monotonic() + BACKGROUND_DRIVER_START_TIMEOUT_SECONDS
    status_url = f"http://127.0.0.1:{port}/status"
    last_error: Exception | None = None

    while time.monotonic() < deadline:
        if not _native_process_is_running(process_handle):
            raise RuntimeError(
                "Hidden-desktop ChromeDriver exited before accepting connections. "
                f"See: {BACKGROUND_CHROMEDRIVER_LOG}"
            )

        try:
            with urllib.request.urlopen(status_url, timeout=0.75) as response:
                if 200 <= int(response.status) < 300:
                    return
        except (OSError, urllib.error.URLError) as exc:
            last_error = exc

        time.sleep(0.25)

    raise TimeoutError(
        "Timed out waiting for hidden-desktop ChromeDriver. "
        f"Last connection error: {last_error}. "
        f"See: {BACKGROUND_CHROMEDRIVER_LOG}"
    )


def _wait_for_profile_handoff() -> None:
    """Give visible Chrome time to flush cookies and release the profile."""
    print(
        "Closing visible login Chrome and flushing its persistent Adobe session..."
    )
    time.sleep(PROFILE_HANDOFF_DELAY_SECONDS)


def start_hidden_desktop_browser() -> HiddenDesktopBrowser:
    """Restart Chrome in true headless mode using the logged-in profile."""
    ERROR_DIR.mkdir(parents=True, exist_ok=True)
    last_error: Exception | None = None

    for attempt in range(1, BACKGROUND_START_RETRIES + 1):
        driver: webdriver.Chrome | None = None
        try:
            service = Service(
                ChromeDriverManager().install(),
                log_output=str(BACKGROUND_CHROMEDRIVER_LOG.resolve()),
            )
            driver = webdriver.Chrome(
                service=service,
                options=build_chrome_options(background=True),
            )

            # ChromeDriver is local, so pass absolute Windows paths directly.
            driver.file_detector = UselessFileDetector()
            configure_driver(driver)

            try:
                driver.get(ADOBE_URL)
            except TimeoutException:
                pass

            try:
                visibility = driver.execute_script(
                    "return document.visibilityState || 'unknown';"
                )
                user_agent = driver.execute_script("return navigator.userAgent;")
            except WebDriverException:
                visibility = "unknown"
                user_agent = "unavailable"

            print(
                "Background browser started in TRUE HEADLESS mode "
                f"(visibility={visibility})."
            )
            print(f"Headless user agent: {user_agent}")
            print(
                "No Chrome window is created or rendered on the desktop. "
                "Keep this console open to see progress."
            )

            return HiddenDesktopBrowser(driver=driver)

        except Exception as exc:
            last_error = exc
            if driver is not None:
                try:
                    driver.quit()
                except WebDriverException:
                    pass

            if attempt < BACKGROUND_START_RETRIES:
                print(
                    f"Headless launch attempt {attempt}/"
                    f"{BACKGROUND_START_RETRIES} failed: "
                    f"{type(exc).__name__}: {exc}"
                )
                time.sleep(PROFILE_HANDOFF_DELAY_SECONDS)

    raise RuntimeError(
        "Could not start true-headless Chrome after "
        f"{BACKGROUND_START_RETRIES} attempts. Last error: {last_error}. "
        f"See: {BACKGROUND_CHROMEDRIVER_LOG}"
    )


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
    """
    Click a normal or Shadow-DOM control using a real pointer action first.

    Adobe Spectrum custom elements sometimes ignore WebElement.click() or a
    synthetic JavaScript click when an overlay is involved. Moving the browser
    pointer to the element and clicking its center is the most reliable path.
    """
    last_error: Exception | None = None

    try:
        driver.execute_script(
            """
            const el = arguments[0];
            if (el && el.scrollIntoView) {
                el.scrollIntoView({
                    block: "center",
                    inline: "center",
                    behavior: "instant"
                });
            }
            """,
            element,
        )
        time.sleep(0.15)
    except WebDriverException as exc:
        last_error = exc

    try:
        ActionChains(driver).move_to_element(element).pause(0.15).click().perform()
        return
    except WebDriverException as exc:
        last_error = exc

    try:
        element.click()
        return
    except WebDriverException as exc:
        last_error = exc

    try:
        clicked = driver.execute_script(
            """
            const el = arguments[0];
            if (!el) return false;

            el.focus && el.focus();

            const options = {
                bubbles: true,
                cancelable: true,
                composed: true,
                view: window,
                button: 0,
                buttons: 1
            };

            try {
                el.dispatchEvent(new PointerEvent("pointerdown", options));
                el.dispatchEvent(new MouseEvent("mousedown", options));
                el.dispatchEvent(new PointerEvent("pointerup", {
                    ...options,
                    buttons: 0
                }));
                el.dispatchEvent(new MouseEvent("mouseup", {
                    ...options,
                    buttons: 0
                }));
            } catch (_) {
                // Older browser builds may not support PointerEvent here.
            }

            el.click();
            return true;
            """,
            element,
        )
        if clicked:
            return
    except WebDriverException as exc:
        last_error = exc

    if last_error is not None:
        raise last_error

    raise WebDriverException("Unable to click the supplied element.")


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


# After the new Express header button is clicked, Adobe may open a panel or
# dialog containing a second, final Download button. This JavaScript finds that
# final control across open shadow roots while deliberately excluding the
# header buttons that opened the panel.
SECONDARY_DOWNLOAD_BUTTON_JS = r"""
function isVisibleAndEnabled(el) {
    if (!el) return false;

    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();

    if (
        style.display === "none" ||
        style.visibility === "hidden" ||
        Number(style.opacity || "1") <= 0
    ) {
        return false;
    }

    if (rect.width <= 0 || rect.height <= 0) return false;

    if (
        el.disabled ||
        el.hasAttribute("disabled") ||
        el.getAttribute("aria-disabled") === "true"
    ) {
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
            if (node.shadowRoot) stack.push(node.shadowRoot);
        }
    }

    return roots;
}

function normalized(value) {
    return String(value || "").replace(/\s+/g, " ").trim().toLowerCase();
}

const roots = collectRoots(document);

// Adobe Express currently uses this exact final CTA. It is inside an overlay
// that itself lives below x-editor-download-button, so it must NOT be rejected
// merely because x-editor-download-button is in its composed ancestor chain.
const exactSelectors = [
    "sp-button[data-testid='download-commit-button']",
    "sp-button#dialog-download-btn.download-button",
    "x-export-popover-download-button",
    "[data-testid='download-commit-button']"
];

for (const selector of exactSelectors) {
    for (const root of roots) {
        let matches = [];

        try {
            matches = root.querySelectorAll(selector);
        } catch (_) {
            matches = [];
        }

        for (const match of matches) {
            if (isVisibleAndEnabled(match)) return match;
        }
    }
}

// Fallback for future Adobe variants.
const candidates = [];

for (const root of roots) {
    let elements = [];

    try {
        elements = root.querySelectorAll(
            "button, sp-button, sp-action-button, [role='button']"
        );
    } catch (_) {
        elements = [];
    }

    for (const el of elements) {
        if (!isVisibleAndEnabled(el)) continue;

        const visibleText = normalized(el.innerText || el.textContent);
        const aria = normalized(el.getAttribute("aria-label"));
        const label = normalized(el.getAttribute("label"));
        const title = normalized(el.getAttribute("title"));
        const testId = normalized(el.getAttribute("data-testid"));
        const id = normalized(el.id);
        const className = normalized(el.className);
        const combined = normalized([
            visibleText,
            aria,
            label,
            title,
            testId,
            id,
            className
        ].join(" "));

        if (!combined.includes("download")) continue;

        // Exclude only the header opener itself. Do not exclude its overlay
        // descendants, because Adobe mounts the final CTA inside that tree.
        if (
            testId === "editor-download-button" ||
            (
                id === "download-btn" &&
                testId !== "download-commit-button"
            )
        ) {
            continue;
        }

        if (
            combined.includes("download app") ||
            combined.includes("download mobile") ||
            combined.includes("app store") ||
            combined.includes("google play") ||
            testId === "download-print-button"
        ) {
            continue;
        }

        let score = 0;

        if (testId === "download-commit-button") score += 2000;
        if (id === "dialog-download-btn") score += 1200;
        if (className.includes("download-button")) score += 500;
        if (visibleText === "download") score += 400;
        else if (visibleText.includes("download")) score += 180;
        if (aria === "download" || label === "download" || title === "download") {
            score += 300;
        }
        if (testId.includes("download")) score += 100;
        if (id.includes("download")) score += 80;

        if (score >= 300) candidates.push({el, score});
    }
}

candidates.sort((a, b) => b.score - a.score);
return candidates.length ? candidates[0].el : null;
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


def secondary_download_button_in_current_context(
    driver: webdriver.Chrome,
):
    try:
        return driver.execute_script(SECONDARY_DOWNLOAD_BUTTON_JS)
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


def secondary_download_in_frames(
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

            element = secondary_download_button_in_current_context(driver)
            if element is not None:
                return element, current_context_url(driver)

            nested = secondary_download_in_frames(
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


def find_secondary_download_across_windows_once(
    driver: webdriver.Chrome,
):
    for handle in prioritize_window_handles(driver):
        try:
            driver.switch_to.window(handle)
            driver.switch_to.default_content()
        except WebDriverException:
            continue

        element = secondary_download_button_in_current_context(driver)
        if element is not None:
            return handle, element, current_context_url(driver)

        found = secondary_download_in_frames(driver)
        if found is not None:
            element, context_url = found
            return handle, element, context_url

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

    absolute_source = str(source.resolve())
    print(f"Upload input found inside: {context_url}")
    print(f"Sending local file path directly to ChromeDriver: {absolute_source}")

    if not source.is_file():
        raise FileNotFoundError(f"Upload source disappeared: {absolute_source}")

    # IMPORTANT: the background session is webdriver.Remote connected directly
    # to standalone ChromeDriver on this same PC. Its file detector is set to
    # UselessFileDetector so this sends the native Windows path directly, rather
    # than calling the Selenium Grid-only /session/.../se/file endpoint.
    file_input.send_keys(absolute_source)


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


def find_express_editor_download_once(driver: webdriver.Chrome):
    found = deep_find_across_windows_once(
        driver,
        EXPRESS_EDITOR_DOWNLOAD_DEEP_SELECTORS,
    )

    if found is None:
        return None

    handle, button, context_url = found
    return "express-editor-shadow", handle, button, context_url


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
            candidate = find_express_editor_download_once(driver)

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
        "Download button, current Express shadow-DOM Download button, or "
        "legacy editor Download button. "
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


def wait_for_download_activity_or_secondary_button(
    driver: webdriver.Chrome,
    timeout_seconds: float,
):
    """
    After clicking an editor header Download button, return either:

    - ("download-started", None) when Chrome creates a staging file, or
    - ("secondary-button", found_tuple) when Adobe opens a panel/dialog with
      another enabled Download control.

    A None result means neither signal appeared during the short UI wait; the
    normal full download timeout still runs afterward and produces diagnostics.
    """
    deadline = time.monotonic() + timeout_seconds

    while time.monotonic() < deadline:
        if download_staging_has_activity():
            return "download-started", None

        found = find_secondary_download_across_windows_once(driver)
        if found is not None:
            return "secondary-button", found

        time.sleep(0.25)

    return None


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

    # The current Express editor opens a panel after the header click. Its
    # final CTA is:
    #   x-export-popover-download-button
    #     -> sp-button[data-testid='download-commit-button']
    if mode in {"express-editor-shadow", "legacy"}:
        next_step = wait_for_download_activity_or_secondary_button(
            driver,
            timeout_seconds=SECOND_STAGE_DOWNLOAD_TIMEOUT_SECONDS,
        )

        # A rare missed header click can happen while Adobe is finishing a
        # repaint. Reacquire the current header control and click it once more.
        if next_step is None and mode == "express-editor-shadow":
            print(
                "The download panel did not appear. Reacquiring and clicking "
                "the editor header Download button one more time..."
            )
            reacquired = find_express_editor_download_once(driver)

            if reacquired is not None:
                _, _, current_header_button, _ = reacquired
                click_element(driver, current_header_button)
                next_step = wait_for_download_activity_or_secondary_button(
                    driver,
                    timeout_seconds=SECOND_STAGE_DOWNLOAD_TIMEOUT_SECONDS,
                )

        if next_step is not None:
            kind, payload = next_step

            if kind == "download-started":
                print("Direct download started after the editor header click.")

            elif kind == "secondary-button" and payload is not None:
                _, final_button, context_url = payload
                print(
                    "Adobe opened the export panel. Clicking the actual final "
                    f"Download CTA inside: {context_url}"
                )
                click_element(driver, final_button)

                # Confirm that the final click started a download promptly.
                activity_deadline = time.monotonic() + 20

                while time.monotonic() < activity_deadline:
                    if download_staging_has_activity():
                        print("Final Download click started the local file.")
                        break
                    time.sleep(0.25)
                else:
                    raise TimeoutException(
                        "The Adobe export panel was found and its final "
                        "Download CTA was clicked, but Chrome created no "
                        "download file."
                    )

        elif mode == "express-editor-shadow":
            raise TimeoutException(
                "The Express header Download control was clicked twice, but "
                "Adobe did not open its export panel and no download began."
            )

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
        "Waiting for the inline result, Firefly Download button, or the "
        "current Express editor shadow-DOM Download button..."
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

    # Save session-wide diagnostics even when no window can be inspected.
    session_path = unique_path(ERROR_DIR, f"{base}_session.txt")
    try:
        capabilities = getattr(driver, "capabilities", {})
        session_path.write_text(
            "\n".join(
                [
                    f"script_version={SCRIPT_VERSION}",
                    f"source={source.resolve()}",
                    f"source_exists={source.exists()}",
                    f"source_size={source.stat().st_size if source.exists() else -1}",
                    f"session_id={getattr(driver, 'session_id', None)}",
                    f"capabilities={capabilities!r}",
                    f"open_urls={list_open_urls(driver)!r}",
                ]
            ),
            encoding="utf-8",
        )
        saved.append(session_path)
    except Exception:
        pass

    browser_log_path = unique_path(ERROR_DIR, f"{base}_browser_console.txt")
    try:
        entries = driver.get_log("browser")
        browser_log_path.write_text(
            "\n".join(repr(item) for item in entries) or "(no browser console entries)",
            encoding="utf-8",
        )
        saved.append(browser_log_path)
    except Exception as exc:
        try:
            browser_log_path.write_text(
                f"Browser log unavailable: {type(exc).__name__}: {exc}",
                encoding="utf-8",
            )
            saved.append(browser_log_path)
        except OSError:
            pass

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
        state_path = unique_path(
            ERROR_DIR,
            f"{base}_window{index}_state.txt",
        )

        try:
            state = driver.execute_script(
                """
                return {
                    href: location.href,
                    title: document.title,
                    readyState: document.readyState,
                    visibilityState: document.visibilityState,
                    innerWidth: innerWidth,
                    innerHeight: innerHeight,
                    devicePixelRatio: devicePixelRatio,
                    bodyText: (document.body && document.body.innerText || '').slice(0, 12000)
                };
                """
            )
            state_path.write_text(repr(state), encoding="utf-8")
            saved.append(state_path)
        except (OSError, WebDriverException):
            pass

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
    visible_login_driver: webdriver.Chrome | None = None
    background_browser: HiddenDesktopBrowser | None = None
    driver: webdriver.Remote | None = None

    try:
        # Phase 1: real on-screen Chrome for login and manual review.
        visible_login_driver = get_visible_login_driver()
        wait_for_manual_login(visible_login_driver)

        # Phase 2: after START, stop the on-screen browser so the persistent
        # profile can be reopened by the private-desktop Chrome.
        try:
            visible_login_driver.quit()
        except WebDriverException:
            pass
        visible_login_driver = None

        _wait_for_profile_handoff()

        # Phase 3: true headless Chrome using the same logged-in profile.
        background_browser = start_hidden_desktop_browser()
        driver = background_browser.driver

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
        if visible_login_driver is not None:
            try:
                visible_login_driver.quit()
            except WebDriverException:
                pass

        if background_browser is not None:
            background_browser.close()
        elif driver is not None:
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
