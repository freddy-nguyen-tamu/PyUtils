# tiktok.py
#
# One-file TikTok profile scraper/downloader.
#
# Requires:
#   pip install -U selenium browser-cookie3 yt-dlp gallery-dl
#
# Usage:
#   python tiktok.py https://www.tiktok.com/@nhobell07
#
# What it does:
#   1) Opens the TikTok URL in Firefox using a persistent Selenium profile.
#   2) First tries the saved Selenium profile headless.
#   3) If not logged in, tries importing cookies from normal Firefox.
#   4) If still not logged in, opens visible Firefox so you can log in.
#   5) Waits for you to press ENTER after login/page load.
#   6) Injects the scrolling URL-collector JavaScript and waits until it finishes.
#   7) Saves the console-style output to tiktok_urls_found.txt.
#   8) Extracts /video/ and /photo/ URLs from that text.
#   9) Downloads videos with yt-dlp and photos with gallery-dl.
#  10) Skips already downloaded items when the TikTok ID exists in downloads.
#
# Notes:
#   - The visible Selenium login is saved in selenium_tiktok_profile.
#   - The script also exports Selenium TikTok cookies to tiktok_selenium_cookies.txt
#     so yt-dlp/gallery-dl can use the same login you just confirmed.
#   - If exported cookies fail, it falls back to --cookies-from-browser firefox.

import argparse
import base64
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

try:
    import browser_cookie3
except Exception:
    browser_cookie3 = None

try:
    from selenium import webdriver
    from selenium.webdriver.firefox.options import Options
    from selenium.common.exceptions import TimeoutException
except Exception:
    webdriver = None
    Options = None
    TimeoutException = Exception


# ================== DEFAULT CONFIGURATION ==================
SELENIUM_PROFILE_DIR = Path.cwd() / "selenium_tiktok_profile"

SCROLL_SCRIPT_FILE = Path(__file__).with_name("tiktok_scroll.js")
URL_TXT_FILE = Path.cwd() / "tiktok_urls_found.txt"
COOKIE_FILE = Path.cwd() / "tiktok_selenium_cookies.txt"

DOWNLOAD_DIR = Path.cwd() / "downloads"
TMP_DIR = Path.cwd() / "_tmp_tiktok_downloads"

FAILED_FILE = Path.cwd() / "failed_urls.txt"
SKIPPED_FILE = Path.cwd() / "skipped_urls.txt"
SUCCESS_FILE = Path.cwd() / "successful_urls.txt"

VIDEO_TOOL = "yt-dlp"
PHOTO_TOOL = "gallery-dl"

COOKIES_BROWSER = "firefox"

# Optional fbalbum.py-style override if browser_cookie3 picks the wrong Firefox
# profile. Set this env var to the full cookies.sqlite path.
FIREFOX_COOKIES_SQLITE = os.environ.get("TIKTOK_FIREFOX_COOKIES_SQLITE") or None

# Same behavior as your TikTok downloader: skip a URL when any existing file
# in downloads contains the TikTok video/photo ID in its filename.
SKIP_IF_ID_EXISTS_IN_DESTINATION = True

# TikTok can show "Something went wrong" with a Refresh button. The injected
# scraper and the page opener both try clicking Refresh until content loads.
REFRESH_RETRY_LIMIT = 10
PAGE_LOAD_TIMEOUT_SECONDS = 35
VISIBLE_AUTO_WAIT_SECONDS = 25
NORMAL_FIREFOX_RENDER_WAIT_SECONDS = 7
NORMAL_FIREFOX_CLIPBOARD_TIMEOUT_SECONDS = 240

HUMAN_CLIPBOARD_WAITING = "__TIKTOK_SCROLL_WAITING__"
HUMAN_CLIPBOARD_BEGIN = "__TIKTOK_SCROLL_OUTPUT_BEGIN__"
HUMAN_CLIPBOARD_END = "__TIKTOK_SCROLL_OUTPUT_END__"
HUMAN_CLIPBOARD_ERROR = "__TIKTOK_SCROLL_ERROR__"
NORMAL_FIREFOX_RESULT_PREFIX = "tiktok_urls_from_firefox_"

# Same scroll logic as the uploaded JS: stop after stable height 3 times.
DEFAULT_STABLE_SCROLLS = 3
DEFAULT_SCROLL_DELAY_MS = 1500

# Firefox/Selenium page dimensions used for headless stability.
BROWSER_WIDTH = 1280
BROWSER_HEIGHT = 900


# ================== LOGGING ==================
def log(message=""):
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def write_line(path, text):
    with open(path, "a", encoding="utf-8") as f:
        f.write(text + "\n")


def reset_log_files():
    FAILED_FILE.write_text("", encoding="utf-8")
    SKIPPED_FILE.write_text("", encoding="utf-8")
    SUCCESS_FILE.write_text("", encoding="utf-8")


# ================== TOOL CHECKS ==================
def check_python_deps():
    missing = []

    if webdriver is None or Options is None:
        missing.append("selenium")

    if browser_cookie3 is None:
        # Not fatal if your Selenium profile is already logged in, but it is
        # needed for the fbalbum.py-style normal-Firefox cookie import step.
        log("Optional warning: browser-cookie3 is not available.")
        log("Cookie import from normal Firefox will be skipped.")
        log("Install it with: pip install browser-cookie3")
        log("")

    if missing:
        log("Missing required Python package(s):")
        for name in missing:
            log(f"  - {name}")
        log("")
        log("Install missing packages, then run again:")
        log("  pip install -U selenium browser-cookie3")
        sys.exit(1)


def check_required_download_tools():
    missing = []

    if shutil.which(VIDEO_TOOL) is None:
        missing.append(VIDEO_TOOL)

    if shutil.which(PHOTO_TOOL) is None:
        missing.append(PHOTO_TOOL)

    if missing:
        log("Missing required command-line tools:")
        for tool in missing:
            log(f"  - {tool}")

        log("")
        log("Install missing tools, then run again:")
        log("  pip install -U yt-dlp gallery-dl")
        sys.exit(1)


# ================== FIREFOX / SELENIUM LOGIN HANDLING ==================
TIKTOK_COOKIE_DOMAINS = (
    "tiktok.com",
    ".tiktok.com",
    "www.tiktok.com",
)

TIKTOK_LOGIN_COOKIE_NAMES = {
    "sessionid",
    "sessionid_ss",
    "sid_tt",
    "sid_guard",
    "uid_tt",
    "uid_tt_ss",
    "sid_ucp_v1",
    "multi_sids",
}


def build_driver(headless):
    options = Options()

    profile_path = str(SELENIUM_PROFILE_DIR.resolve())
    SELENIUM_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    # Reuse the same Selenium Firefox profile every run.
    # This mirrors the fbalbum.py technique and saves login after the first run.
    options.add_argument("-profile")
    options.add_argument(profile_path)

    firefox_binary = Path(r"C:\Program Files\Mozilla Firefox\firefox.exe")
    if firefox_binary.exists():
        options.binary_location = str(firefox_binary)

    try:
        options.page_load_strategy = "eager"
    except Exception:
        pass

    if headless:
        options.add_argument("-headless")

    options.add_argument(f"--width={BROWSER_WIDTH}")
    options.add_argument(f"--height={BROWSER_HEIGHT}")

    # Reduce Firefox first-run/default-browser nags.
    options.set_preference("browser.shell.checkDefaultBrowser", False)
    options.set_preference("browser.startup.homepage_override.mstone", "ignore")
    options.set_preference("startup.homepage_welcome_url", "about:blank")
    options.set_preference("startup.homepage_welcome_url.additional", "about:blank")

    # Normal-ish Firefox, but with enough stability for automation.
    options.set_preference("media.volume_scale", "0.0")
    options.set_preference("dom.webdriver.enabled", False)

    driver = webdriver.Firefox(options=options)

    try:
        driver.set_window_size(BROWSER_WIDTH, BROWSER_HEIGHT)
    except Exception:
        pass

    # The scrolling collector can take a long time on large profiles.
    try:
        driver.set_script_timeout(60 * 60)
        driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT_SECONDS)
    except Exception:
        pass

    return driver


def safe_get(driver, url, settle_seconds=3):
    """
    TikTok often keeps network activity open long after the profile shell exists.
    Use a bounded load and stop the spinner so Selenium can continue scraping.
    """
    try:
        driver.get(url)
    except TimeoutException:
        log(f"Page load timed out after {PAGE_LOAD_TIMEOUT_SECONDS}s, stopping load and continuing.")
        try:
            driver.execute_script("window.stop();")
        except Exception:
            pass
    except Exception as e:
        log(f"Page load raised {type(e).__name__}: {e}")

    time.sleep(settle_seconds)


def load_firefox_tiktok_cookies(required=False):
    """
    Try to read TikTok cookies from normal Firefox.

    This is copied in spirit from fbalbum.py:
      - try specific TikTok domains
      - try all Firefox cookies then filter
      - prefer the cookie set containing login/session cookies
    """
    if browser_cookie3 is None:
        if required:
            raise RuntimeError("browser-cookie3 is not installed.")
        return []

    log("Reading TikTok cookies from normal Firefox...")

    tries = []

    if FIREFOX_COOKIES_SQLITE:
        tries.append(
            (
                "explicit cookies.sqlite",
                lambda: browser_cookie3.firefox(cookie_file=FIREFOX_COOKIES_SQLITE),
            )
        )

    tries.extend(
        [
            ("domain .tiktok.com", lambda: browser_cookie3.firefox(domain_name=".tiktok.com")),
            ("domain tiktok.com", lambda: browser_cookie3.firefox(domain_name="tiktok.com")),
            ("all Firefox cookies then filter", lambda: browser_cookie3.firefox()),
        ]
    )

    best = []

    for label, fn in tries:
        try:
            cj = fn()
            cookies = []

            for c in cj:
                domain = (c.domain or "").lower()
                if "tiktok.com" in domain:
                    cookies.append(c)

            names = sorted(set(c.name for c in cookies))
            log(f"{label}: {len(cookies)} cookies")
            log("cookie names: " + ", ".join(names))

            if len(cookies) > len(best):
                best = cookies

            if any(name in TIKTOK_LOGIN_COOKIE_NAMES for name in names):
                log("Found logged-in TikTok cookies in normal Firefox.")
                return cookies

        except Exception as e:
            log(f"{label} failed: {e}")

    names = sorted(set(c.name for c in best))
    log(f"Best Firefox TikTok cookie set: {len(best)} cookies")
    log("Best Firefox TikTok cookie names: " + ", ".join(names))

    if required and not any(name in TIKTOK_LOGIN_COOKIE_NAMES for name in names):
        raise RuntimeError(
            "Could not find logged-in TikTok cookies from normal Firefox. "
            "You may be using the wrong Firefox profile, private browsing, "
            "a container, or you are not logged into TikTok in normal Firefox."
        )

    return best


def make_selenium_cookie_variants(c):
    """
    Selenium is picky about cookie domains.

    Try:
      1) original cookie domain
      2) .tiktok.com
      3) no explicit domain
    """
    base = {
        "name": c.name,
        "value": c.value,
        "path": c.path or "/",
        "secure": bool(getattr(c, "secure", False)),
    }

    expires = getattr(c, "expires", None)
    if expires:
        try:
            expiry = int(expires)
            if expiry > int(time.time()):
                base["expiry"] = expiry
        except Exception:
            pass

    variants = []

    domain = getattr(c, "domain", None)
    if domain:
        d1 = dict(base)
        d1["domain"] = domain
        variants.append(d1)

    d2 = dict(base)
    d2["domain"] = ".tiktok.com"
    variants.append(d2)

    d3 = dict(base)
    variants.append(d3)

    return variants


def inject_cookies_into_firefox(driver, cookies):
    """
    Opens tiktok.com, injects cookies into Selenium Firefox, then reloads.
    """
    if not cookies:
        log("No external Firefox cookies to inject.")
        return

    safe_get(driver, "https://www.tiktok.com/", settle_seconds=2)

    added = 0
    failed = 0

    for c in cookies:
        ok = False

        for cookie_dict in make_selenium_cookie_variants(c):
            try:
                driver.add_cookie(cookie_dict)
                ok = True
                added += 1
                break
            except Exception:
                continue

        if not ok:
            failed += 1

    log(f"Injected cookies into Selenium Firefox: added={added}, failed={failed}")

    safe_get(driver, "https://www.tiktok.com/", settle_seconds=2)


def has_tiktok_login_cookie(driver):
    """
    Checks whether Selenium Firefox has important TikTok login cookies.
    """
    names = set()

    try:
        for c in driver.get_cookies():
            name = c.get("name")
            if name:
                names.add(name)
    except Exception:
        pass

    return any(name in TIKTOK_LOGIN_COOKIE_NAMES for name in names)


def is_tiktok_login_wall(driver):
    """
    Rough check for login/checkpoint wall.

    TikTok public profile pages may contain the word "Log in" even when the
    page is usable. For automation mode, we primarily trust cookies.
    """
    try:
        url = (driver.current_url or "").lower()
    except Exception:
        url = ""

    try:
        title = (driver.title or "").lower()
    except Exception:
        title = ""

    try:
        html = (driver.page_source or "").lower()
    except Exception:
        html = ""

    if "/login" in url or "login?" in url:
        return True

    if "log in | tiktok" in title or "login | tiktok" in title:
        return True

    bad_signs = [
        "log in to tiktok",
        "sign up for tiktok",
        "continue with facebook",
        "continue with google",
        "use phone / email / username",
        "use phone, email or username",
    ]

    bad_count = sum(1 for s in bad_signs if s in html)

    # If we have a login cookie, do not overreact to footer/header login text.
    if has_tiktok_login_cookie(driver) and "/login" not in url:
        return False

    return bad_count >= 2


def page_text_contains_error(driver):
    try:
        text = driver.execute_script("return document.body ? document.body.innerText : '';") or ""
    except Exception:
        try:
            text = driver.page_source or ""
        except Exception:
            text = ""

    low = text.lower()
    return (
        "something went wrong" in low
        or "try again" in low
        or "couldn't load" in low
        or "could not load" in low
    )


def click_refresh_if_error(driver):
    """
    TikTok sometimes renders a "Something went wrong" page with a Refresh/Try again
    button. Click that button if present.
    """
    script = r"""
        const bodyText = (document.body && document.body.innerText || "").toLowerCase();
        const hasError =
            bodyText.includes("something went wrong") ||
            bodyText.includes("try again") ||
            bodyText.includes("couldn't load") ||
            bodyText.includes("could not load");

        if (!hasError) return {clicked: false, reason: "no error text"};

        const buttons = Array.from(document.querySelectorAll("button, div[role='button'], a"));
        const candidates = buttons.filter(el => {
            const t = (el.innerText || el.textContent || "").trim().toLowerCase();
            return t === "refresh" ||
                   t.includes("refresh") ||
                   t.includes("try again") ||
                   t.includes("reload");
        });

        if (!candidates.length) {
            return {clicked: false, reason: "error text but no button"};
        }

        candidates[0].click();
        return {clicked: true, text: (candidates[0].innerText || candidates[0].textContent || "").trim()};
    """

    try:
        result = driver.execute_script(script)
        if isinstance(result, dict) and result.get("clicked"):
            log(f"Clicked TikTok refresh/try-again button: {result.get('text')}")
            return True
    except Exception as e:
        log(f"Refresh-button JS check failed: {e}")

    return False


def refresh_until_loaded(driver, max_clicks=REFRESH_RETRY_LIMIT):
    clicked_any = False

    for attempt in range(1, max_clicks + 1):
        if not page_text_contains_error(driver):
            return clicked_any

        log(f"TikTok error page detected. Refresh attempt {attempt}/{max_clicks}...")
        clicked = click_refresh_if_error(driver)
        clicked_any = clicked_any or clicked

        if not clicked:
            try:
                driver.refresh()
                clicked_any = True
                log("No Refresh button clicked, used browser refresh instead.")
            except Exception:
                pass

        time.sleep(3)

    return clicked_any


def count_collectable_tiktok_links(driver):
    try:
        result = driver.execute_script(
            r"""
            const links = Array.from(document.querySelectorAll('a[href*="/video/"], a[href*="/photo/"]'));
            const unique = new Set(links.map(a => a.href).filter(Boolean));
            return unique.size;
            """
        )
        return int(result or 0)
    except Exception:
        return 0


def is_public_profile_page_usable(driver):
    """
    A TikTok profile can be downloadable without login. Treat a non-error,
    non-login TikTok page as usable even when session cookies are absent.
    """
    if is_tiktok_login_wall(driver):
        return False

    if page_text_contains_error(driver):
        return False

    try:
        current_url = (driver.current_url or "").lower()
    except Exception:
        current_url = ""

    if "tiktok.com" not in current_url:
        return False

    link_count = count_collectable_tiktok_links(driver)
    if link_count > 0:
        return True

    try:
        text = driver.execute_script("return document.body ? document.body.innerText : '';") or ""
    except Exception:
        text = ""

    lowered = text.lower()
    profile_signs = ("following" in lowered and "followers" in lowered) or "@" in current_url
    return profile_signs and len(text.strip()) > 80


def open_start_and_check(driver, start_url):
    safe_get(driver, start_url, settle_seconds=4)

    refresh_until_loaded(driver, max_clicks=2)

    if has_tiktok_login_cookie(driver) and page_text_contains_error(driver):
        refresh_until_loaded(driver, max_clicks=3)

    logged_cookie = has_tiktok_login_cookie(driver)
    login_wall = is_tiktok_login_wall(driver)
    error_page = page_text_contains_error(driver)
    link_count = count_collectable_tiktok_links(driver)
    page_usable = is_public_profile_page_usable(driver)

    log(f"Current URL: {driver.current_url}")
    log(f"Has TikTok login cookie: {logged_cookie}")
    log(f"Login wall detected: {login_wall}")
    log(f"TikTok error page detected: {error_page}")
    log(f"Visible TikTok post links: {link_count}")
    log(f"Public profile usable: {page_usable}")

    return (logged_cookie and not login_wall and not error_page) or page_usable


def wait_for_visible_page_to_settle(driver, max_seconds=VISIBLE_AUTO_WAIT_SECONDS):
    """
    Visible Firefox is useful when headless rendering is empty, but the default
    command should only wait when TikTok still needs a first-run login.
    """
    deadline = time.time() + max_seconds

    while time.time() < deadline:
        refresh_until_loaded(driver, max_clicks=2)

        error_page = page_text_contains_error(driver)
        logged_cookie = has_tiktok_login_cookie(driver)

        if is_public_profile_page_usable(driver) or (logged_cookie and not error_page and not is_tiktok_login_wall(driver)):
            return True

        time.sleep(2)

    error_page = page_text_contains_error(driver)
    logged_cookie = has_tiktok_login_cookie(driver)
    return is_public_profile_page_usable(driver) or (logged_cookie and not error_page and not is_tiktok_login_wall(driver))


def get_logged_in_driver(start_url, force_visible=False, interactive_login=True):
    """
    Returns a Selenium Firefox driver that is logged in or user-confirmed.

    Order:
      1) Try persistent Selenium profile headless.
      2) If failed, try importing normal Firefox cookies.
      3) If failed, open visible Firefox and ask for one manual login.
    """
    firefox_cookies = []

    if not force_visible:
        log("")
        log("Trying saved Selenium Firefox profile in headless mode...")
        driver = build_driver(headless=True)

        try:
            if open_start_and_check(driver, start_url):
                if has_tiktok_login_cookie(driver):
                    log("Saved Selenium profile is already logged in. Continuing headless.")
                else:
                    log("Public TikTok profile loaded without login. Continuing headless.")
                return driver, True

            log("")
            log("Headless saved profile was not logged in.")
            log("Trying to import TikTok cookies from normal Firefox...")

            firefox_cookies = load_firefox_tiktok_cookies(required=False)

            if firefox_cookies:
                inject_cookies_into_firefox(driver, firefox_cookies)

                if open_start_and_check(driver, start_url):
                    if has_tiktok_login_cookie(driver):
                        log("Imported normal Firefox cookies worked. Continuing headless.")
                    else:
                        log("Profile is usable after cookie import attempt. Continuing headless.")
                    return driver, True

            log("")
            log("Headless login still failed.")
            log("Closing headless browser and reopening visible Firefox...")

        except Exception as e:
            log(f"Headless login attempt had an error: {type(e).__name__}: {e}")

        try:
            driver.quit()
        except Exception:
            pass

        time.sleep(1)

    driver = build_driver(headless=False)

    try:
        # Try imported Firefox cookies again in visible mode if we already found them.
        if firefox_cookies:
            inject_cookies_into_firefox(driver, firefox_cookies)

        safe_get(driver, start_url, settle_seconds=4)
        refresh_until_loaded(driver)

        logged_cookie = has_tiktok_login_cookie(driver)
        login_wall = is_tiktok_login_wall(driver)
        error_page = page_text_contains_error(driver)
        link_count = count_collectable_tiktok_links(driver)

        log(f"Visible Firefox URL: {driver.current_url}")
        log(f"Has TikTok login cookie: {logged_cookie}")
        log(f"Login wall detected: {login_wall}")
        log(f"TikTok error page detected: {error_page}")
        log(f"Visible TikTok post links: {link_count}")

        if not is_public_profile_page_usable(driver) and (login_wall or error_page or not logged_cookie):
            print()
            print("=" * 78)
            print("Visible Firefox is open.")
            print("Log in to TikTok manually in that Firefox window.")
            print("Make sure the target TikTok profile loaded normally, not the Something went wrong page.")

            if interactive_login and sys.stdin.isatty():
                input("After the profile is visible, press ENTER here to continue...")
            else:
                log("No interactive terminal is attached; waiting briefly, then continuing.")
                time.sleep(VISIBLE_AUTO_WAIT_SECONDS)

            print("=" * 78)
            print()
        else:
            log(f"Waiting up to {VISIBLE_AUTO_WAIT_SECONDS}s for visible Firefox to settle...")
            wait_for_visible_page_to_settle(driver)

        safe_get(driver, start_url, settle_seconds=4)
        refresh_until_loaded(driver)

        if is_tiktok_login_wall(driver):
            log("Warning: TikTok still looks like a login wall.")
            log("The script will continue anyway, but it may find/download nothing.")
        else:
            log("Visible Selenium profile is confirmed.")
            log("This session should be saved for future headless runs.")

        return driver, False

    except Exception:
        try:
            driver.quit()
        except Exception:
            pass
        raise


def write_netscape_cookie_file_from_selenium(driver, out_file):
    """
    Export Selenium cookies to Netscape cookie format so download tools can use
    the same session from the visible/confirmed Selenium browser.
    """
    try:
        # Ensure TikTok cookies are available for this domain.
        if "tiktok.com" not in (driver.current_url or ""):
            safe_get(driver, "https://www.tiktok.com/", settle_seconds=2)
    except Exception:
        pass

    try:
        cookies = driver.get_cookies()
    except Exception as e:
        log(f"Could not read Selenium cookies for export: {e}")
        return False

    lines = [
        "# Netscape HTTP Cookie File",
        "# This file was generated by tiktok.py from the Selenium Firefox session.",
        "# It lets yt-dlp/gallery-dl use the same login you confirmed.",
    ]

    count = 0

    for c in cookies:
        name = c.get("name")
        value = c.get("value")
        domain = c.get("domain") or ".tiktok.com"
        path = c.get("path") or "/"
        secure = "TRUE" if c.get("secure") else "FALSE"
        expiry = c.get("expiry")

        if not name or value is None:
            continue

        # Keep this cookie file focused on TikTok.
        if "tiktok.com" not in domain.lower():
            continue

        include_subdomains = "TRUE" if domain.startswith(".") else "FALSE"

        try:
            expiry = int(expiry) if expiry else 0
        except Exception:
            expiry = 0

        # Netscape format:
        # domain include_subdomains path secure expiry name value
        lines.append(
            f"{domain}\t{include_subdomains}\t{path}\t{secure}\t{expiry}\t{name}\t{value}"
        )
        count += 1

    out_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(f"Exported {count} TikTok Selenium cookie(s) to: {out_file.resolve()}")

    return count > 0


# ================== JAVASCRIPT URL COLLECTION ==================
def load_tiktok_scroll_script(stable_scrolls, delay_ms):
    if not SCROLL_SCRIPT_FILE.exists():
        raise FileNotFoundError(f"Missing TikTok scroll script: {SCROLL_SCRIPT_FILE}")

    source = SCROLL_SCRIPT_FILE.read_text(encoding="utf-8")

    # Keep the checked-in script as the source of truth, but let CLI knobs tune
    # the two constants it already exposes.
    source = re.sub(r"const\s+maxStable\s*=\s*\d+\s*;", f"const maxStable = {int(stable_scrolls)};", source)
    source = re.sub(r"const\s+delay\s*=\s*\d+\s*;", f"const delay = {int(delay_ms)};", source)

    return source


def find_firefox_executable():
    candidates = [
        Path(r"C:\Program Files\Mozilla Firefox\firefox.exe"),
        Path(r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe"),
    ]

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    found = shutil.which("firefox")
    if found:
        return found

    raise FileNotFoundError("Could not find firefox.exe.")


def powershell_clipboard_set(text):
    command = (
        "[Console]::InputEncoding = [Text.UTF8Encoding]::new(); "
        "Set-Clipboard -Value ([Console]::In.ReadToEnd())"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        input=text,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )


def powershell_clipboard_get():
    command = (
        "[Console]::OutputEncoding = [Text.UTF8Encoding]::new(); "
        "Get-Clipboard -Raw"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    return result.stdout or ""


def normal_firefox_download_dirs():
    dirs = []

    home_downloads = Path.home() / "Downloads"
    if home_downloads.exists():
        dirs.append(home_downloads)

    cwd = Path.cwd()
    if cwd.exists():
        dirs.append(cwd)

    parent = cwd.parent
    if parent.exists():
        dirs.append(parent)

    unique_dirs = []
    seen = set()

    for directory in dirs:
        resolved = str(directory.resolve()).lower()
        if resolved not in seen:
            seen.add(resolved)
            unique_dirs.append(directory)

    return unique_dirs


def find_normal_firefox_download_result(started_at):
    candidates = []

    for directory in normal_firefox_download_dirs():
        try:
            for path in directory.glob(f"{NORMAL_FIREFOX_RESULT_PREFIX}*.txt"):
                if not path.is_file():
                    continue

                if path.stat().st_mtime + 2 < started_at:
                    continue

                part_path = path.with_name(path.name + ".part")
                if part_path.exists():
                    continue

                candidates.append(path)
        except Exception:
            continue

    if not candidates:
        return None

    candidates.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    return candidates[0]


def parse_normal_firefox_result_text(text):
    if HUMAN_CLIPBOARD_ERROR in text:
        error = text.split(HUMAN_CLIPBOARD_ERROR, 1)[1].strip()
        raise RuntimeError("Normal Firefox console script failed: " + error)

    start = text.find(HUMAN_CLIPBOARD_BEGIN)
    end = text.find(HUMAN_CLIPBOARD_END, start + len(HUMAN_CLIPBOARD_BEGIN))

    if start != -1 and end != -1:
        output = text[start + len(HUMAN_CLIPBOARD_BEGIN):end]
        return output.strip("\r\n") + "\n"

    return None


def build_normal_firefox_console_script(stable_scrolls, delay_ms):
    source = load_tiktok_scroll_script(stable_scrolls, delay_ms)
    encoded_source = base64.b64encode(source.encode("utf-8")).decode("ascii")
    encoded_begin = base64.b64encode(HUMAN_CLIPBOARD_BEGIN.encode("utf-8")).decode("ascii")
    encoded_end = base64.b64encode(HUMAN_CLIPBOARD_END.encode("utf-8")).decode("ascii")
    encoded_error = base64.b64encode(HUMAN_CLIPBOARD_ERROR.encode("utf-8")).decode("ascii")
    encoded_download_prefix = base64.b64encode(NORMAL_FIREFOX_RESULT_PREFIX.encode("utf-8")).decode("ascii")

    js = f"""
(async () => {{
    const marker = value => new TextDecoder().decode(Uint8Array.from(atob(value), ch => ch.charCodeAt(0)));
    const begin = marker({encoded_begin!r});
    const end = marker({encoded_end!r});
    const errorMarker = marker({encoded_error!r});
    const downloadPrefix = marker({encoded_download_prefix!r});
    const encodedSource = {encoded_source!r};

    const status = (message) => {{
        const text = `[tiktok.py] ${{message}}`;
        console.log(text);

        let box = document.getElementById("__codex_tiktok_status");
        if (!box) {{
            box = document.createElement("div");
            box.id = "__codex_tiktok_status";
            box.style.cssText = [
                "position:fixed",
                "z-index:2147483647",
                "left:12px",
                "bottom:12px",
                "max-width:520px",
                "padding:10px 12px",
                "background:#111",
                "color:#fff",
                "font:13px/1.35 monospace",
                "border:1px solid #555",
                "border-radius:6px",
                "white-space:pre-wrap",
                "box-shadow:0 4px 18px rgba(0,0,0,.35)"
            ].join(";");
            document.documentElement.appendChild(box);
        }}

        box.textContent = text;
    }};

    const copyText = async (text) => {{
        if (typeof copy === "function") {{
            copy(text);
            return;
        }}

        try {{
            await navigator.clipboard.writeText(text);
            return;
        }} catch (firstError) {{
            const textarea = document.createElement("textarea");
            textarea.value = text;
            textarea.setAttribute("readonly", "");
            textarea.style.position = "fixed";
            textarea.style.left = "-9999px";
            textarea.style.top = "0";
            document.body.appendChild(textarea);
            textarea.focus();
            textarea.select();
            const ok = document.execCommand("copy");
            textarea.remove();
            if (!ok) {{
                throw firstError;
            }}
        }}
    }};

    const downloadText = (text) => {{
        const filename = `${{downloadPrefix}}${{Date.now()}}.txt`;
        const blob = new Blob([text], {{ type: "text/plain;charset=utf-8" }});
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = filename;
        link.style.display = "none";
        document.body.appendChild(link);
        link.click();
        setTimeout(() => {{
            URL.revokeObjectURL(url);
            link.remove();
        }}, 30000);
        return filename;
    }};

    const deliverResult = async (text, isError = false) => {{
        const payload = isError ? `${{errorMarker}}\\n${{text}}` : `${{begin}}\\n${{text}}${{end}}`;
        let copied = false;

        try {{
            await copyText(payload);
            copied = true;
        }} catch (copyError) {{
            console.warn("[tiktok.py] Clipboard copy failed, using download fallback.", copyError);
        }}

        let filename = "";
        try {{
            filename = downloadText(payload);
        }} catch (downloadError) {{
            console.error("[tiktok.py] Download fallback failed.", downloadError);
            if (!copied) {{
                throw downloadError;
            }}
        }}

        status(`${{isError ? "Failed" : "Finished"}}. ${{copied ? "Copied clipboard." : "Clipboard blocked."}} ${{filename ? "Downloaded " + filename + "." : ""}} Return to terminal.`);
        return {{ copied, filename }};
    }};

    const decodeSource = () => {{
        const binary = atob(encodedSource);
        const bytes = Uint8Array.from(binary, ch => ch.charCodeAt(0));
        return new TextDecoder().decode(bytes);
    }};

    const collectLinks = () => {{
        const urls = new Set();

        document.querySelectorAll('a[href*="/video/"], a[href*="/photo/"]').forEach(link => {{
            const href = link.href || "";
            if (href.includes("/video/") || href.includes("/photo/")) {{
                urls.add(href);
            }}
        }});

        const html = document.documentElement ? document.documentElement.innerHTML : "";
        const re = /https?:\\/\\/[^"'<>\\s]+tiktok\\.com\\/[^"'<>\\s]+\\/(?:video|photo)\\/\\d+/g;
        for (const match of html.matchAll(re)) {{
            urls.add(match[0].replace(/\\\\u002F/g, "/").replace(/&amp;/g, "&"));
        }}

        const videoUrls = [];
        const photoUrls = [];

        for (const url of urls) {{
            if (url.includes("/video/")) {{
                videoUrls.push(url);
            }} else if (url.includes("/photo/")) {{
                photoUrls.push(url);
            }}
        }}

        return {{ videoUrls, photoUrls }};
    }};

    const lines = [];
    const originalConsoleLog = console.log.bind(console);

    console.log = (...args) => {{
        lines.push(args.map(value => String(value)).join(" "));
        originalConsoleLog(...args);
    }};

    try {{
        status("Console script started. Scrolling profile...");
        const maybePromise = window.eval(decodeSource());
        if (maybePromise && typeof maybePromise.then === "function") {{
            await maybePromise;
        }}

        status("Collecting loaded TikTok post links...");
        const extraLinks = collectLinks();
        let output = lines.join("\\n");

        if (output && !output.endsWith("\\n")) {{
            output += "\\n";
        }}

        const hasUrl = /https?:\\/\\/[^\\s"'<>]+tiktok\\.com\\/[^\\s"'<>]+\\/(video|photo)\\/\\d+/.test(output);
        if (!hasUrl) {{
            output += "\\nvideo:\\n";
            output += extraLinks.videoUrls.length ? extraLinks.videoUrls.join("\\n") + "\\n" : "(no videos found)\\n";
            output += "\\nimage:\\n";
            output += extraLinks.photoUrls.length ? extraLinks.photoUrls.join("\\n") + "\\n" : "(no images found)\\n";
            output += `\\nSummary: ${{extraLinks.videoUrls.length}} video(s), ${{extraLinks.photoUrls.length}} image(s).\\n`;
        }}

        await deliverResult(output, false);
    }} catch (err) {{
        const message = String(err && (err.stack || err.message) || err);
        try {{
            await deliverResult(message, true);
        }} catch (deliverError) {{
            status("Failed, and could not return result. See Web Console error.");
            console.error("[tiktok.py] Could not return failure details.", deliverError);
        }}
    }} finally {{
        console.log = originalConsoleLog;
    }}
}})();
"""

    return " ".join(line.strip() for line in js.splitlines()).strip()


def focus_firefox_and_run_console_script():
    command = r"""
$wshell = New-Object -ComObject WScript.Shell
$activated = $false
foreach ($title in @("TikTok", "Mozilla Firefox", "Firefox")) {
    if ($wshell.AppActivate($title)) {
        $activated = $true
        break
    }
}
Start-Sleep -Milliseconds 500
if (-not $activated) {
    Write-Output "APP_ACTIVATE_FAILED"
    exit 2
}
$wshell.SendKeys("^+k")
Start-Sleep -Milliseconds 3000
$wshell.SendKeys("allow pasting")
Start-Sleep -Milliseconds 250
$wshell.SendKeys("{ENTER}")
Start-Sleep -Milliseconds 500
$wshell.SendKeys("^v")
Start-Sleep -Milliseconds 500
$wshell.SendKeys("{ENTER}")
"""

    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    if result.returncode != 0:
        log("Could not automatically focus Firefox and run the console script.")
        if result.stdout.strip():
            log(result.stdout.strip())
        return False

    return True


def wait_for_normal_firefox_result(started_at, timeout_seconds=NORMAL_FIREFOX_CLIPBOARD_TIMEOUT_SECONDS):
    deadline = time.time() + timeout_seconds
    last_preview = ""
    next_progress_log = time.time() + 10

    while time.time() < deadline:
        downloaded = find_normal_firefox_download_result(started_at)
        if downloaded is not None:
            text = downloaded.read_text(encoding="utf-8", errors="replace")
            parsed = parse_normal_firefox_result_text(text)
            if parsed is not None:
                log(f"Read Firefox-downloaded result file: {downloaded}")
                return parsed

        text = powershell_clipboard_get()
        parsed = parse_normal_firefox_result_text(text)
        if parsed is not None:
            return parsed

        preview = text.strip().replace("\r", " ").replace("\n", " ")[:80]
        if preview and preview != last_preview and preview != HUMAN_CLIPBOARD_WAITING:
            last_preview = preview

        if time.time() >= next_progress_log:
            remaining = max(0, int(deadline - time.time()))
            log(f"Still waiting for Firefox script output by clipboard or download... {remaining}s left")
            next_progress_log = time.time() + 10

        time.sleep(1)

    raise TimeoutError(
        "Timed out waiting for TikTok scroll output by clipboard or downloaded txt. "
        "Firefox may not have focused, DevTools may not have opened, or the console script may not have run."
    )


def run_normal_firefox_scroll_script(start_url, stable_scrolls=DEFAULT_STABLE_SCROLLS, delay_ms=DEFAULT_SCROLL_DELAY_MS):
    """
    Opens real Firefox with no Selenium/Marionette flags, runs tiktok_scroll.js
    in the Web Console, and reads the console-style output from the clipboard.
    """
    firefox_exe = find_firefox_executable()
    console_script = build_normal_firefox_console_script(stable_scrolls, delay_ms)
    started_at = time.time()

    log("Opening normal Firefox, not Selenium/WebDriver.")
    log(f"Firefox: {firefox_exe}")
    subprocess.Popen(
        [firefox_exe, "-new-window", start_url],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    log(f"Waiting {NORMAL_FIREFOX_RENDER_WAIT_SECONDS}s for the real Firefox page to render.")
    time.sleep(NORMAL_FIREFOX_RENDER_WAIT_SECONDS)

    powershell_clipboard_set(HUMAN_CLIPBOARD_WAITING)
    powershell_clipboard_set(console_script)

    log("Opening Firefox Web Console and pasting the TikTok scroll script.")
    log("Keep the keyboard and mouse still for a moment.")
    if not focus_firefox_and_run_console_script():
        log("The JS script is on the clipboard. Click Firefox, press Ctrl+Shift+K, type allow pasting, press Enter, press Ctrl+V, then Enter.")

    text = wait_for_normal_firefox_result(started_at)
    URL_TXT_FILE.write_text(text, encoding="utf-8")

    urls = extract_tiktok_urls(URL_TXT_FILE)
    video_urls = []
    photo_urls = []

    for url in urls:
        kind, _ = get_tiktok_kind_and_id(url)
        if kind == "video":
            video_urls.append(url)
        elif kind == "photo":
            photo_urls.append(url)

    log(f"Saved normal-Firefox JS output to: {URL_TXT_FILE.resolve()}")
    log(f"Collected {len(video_urls)} video URL(s), {len(photo_urls)} photo URL(s).")

    return video_urls, photo_urls, text


def run_tiktok_scroll_script(driver, stable_scrolls=DEFAULT_STABLE_SCROLLS, delay_ms=DEFAULT_SCROLL_DELAY_MS):
    """
    Injects the repository's tiktok_scroll.js script, captures its console
    output, and returns structured URLs for the downloader stage.
    """
    scroll_source = load_tiktok_scroll_script(stable_scrolls, delay_ms)
    log(f"Injecting TikTok scrolling script: {SCROLL_SCRIPT_FILE.resolve()}")

    script = r"""
        const done = arguments[arguments.length - 1];
        const scrollSource = arguments[0];
        const refreshRetryLimit = arguments[1];

        (async () => {
            const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
            const lines = [];
            const originalConsoleLog = console.log.bind(console);

            console.log = (...args) => {
                const text = args.map(value => String(value)).join(" ");
                lines.push(text);
                originalConsoleLog(...args);
            };

            const collectLinks = () => {
                const videoUrls = new Set();
                const photoUrls = new Set();
                const allLinks = document.querySelectorAll('a[href*="/video/"], a[href*="/photo/"]');

                allLinks.forEach(link => {
                    const href = link.href;
                    if (!href) return;

                    if (href.includes("/video/")) {
                        videoUrls.add(href);
                    } else if (href.includes("/photo/")) {
                        photoUrls.add(href);
                    }
                });

                return {
                    videoUrls: Array.from(videoUrls),
                    photoUrls: Array.from(photoUrls)
                };
            };

            const clickRefreshIfNeeded = async () => {
                const bodyText = (document.body && document.body.innerText || "").toLowerCase();
                const hasError =
                    bodyText.includes("something went wrong") ||
                    bodyText.includes("try again") ||
                    bodyText.includes("couldn't load") ||
                    bodyText.includes("could not load");

                if (!hasError) return false;

                const buttons = Array.from(document.querySelectorAll("button, div[role='button'], a"));
                const candidates = buttons.filter(el => {
                    const t = (el.innerText || el.textContent || "").trim().toLowerCase();
                    return t === "refresh" ||
                           t.includes("refresh") ||
                           t.includes("try again") ||
                           t.includes("reload");
                });

                if (candidates.length) {
                    const label = (candidates[0].innerText || candidates[0].textContent || "").trim();
                    console.log("TikTok error page detected. Clicking:", label || "Refresh");
                    candidates[0].click();
                    await sleep(3000);
                    return true;
                }

                console.log("TikTok error page detected but no Refresh button was found.");
                return false;
            };

            try {
                for (let i = 0; i < refreshRetryLimit; i++) {
                    const clicked = await clickRefreshIfNeeded();
                    if (!clicked) break;
                }

                const maybePromise = window.eval(scrollSource);
                if (maybePromise && typeof maybePromise.then === "function") {
                    await maybePromise;
                }

                const finalLinks = collectLinks();
                let text = lines.join("\n");

                if (text && !text.endsWith("\n")) {
                    text += "\n";
                }

                if (!text.includes("\nvideo:") && !text.startsWith("video:")) {
                    text += "\nvideo:\n";
                    text += finalLinks.videoUrls.length ? finalLinks.videoUrls.join("\n") + "\n" : "(no videos found)\n";
                    text += "\nimage:\n";
                    text += finalLinks.photoUrls.length ? finalLinks.photoUrls.join("\n") + "\n" : "(no images found)\n";
                    text += `\nSummary: ${finalLinks.videoUrls.length} video(s), ${finalLinks.photoUrls.length} image(s).\n`;
                }

                window.__CHATGPT_TIKTOK_SCRAPE_RESULT__ = {
                    ok: true,
                    videoUrls: finalLinks.videoUrls,
                    photoUrls: finalLinks.photoUrls,
                    text
                };
            } finally {
                console.log = originalConsoleLog;
            }

            done(window.__CHATGPT_TIKTOK_SCRAPE_RESULT__);
        })().catch(err => {
            try {
                console.log = console.log;
            } catch (_) {}

            const errorResult = {
                ok: false,
                error: String(err && (err.stack || err.message) || err)
            };
            window.__CHATGPT_TIKTOK_SCRAPE_RESULT__ = errorResult;
            done(errorResult);
        });
    """

    result = driver.execute_async_script(script, scroll_source, REFRESH_RETRY_LIMIT)

    if not isinstance(result, dict):
        raise RuntimeError(f"Unexpected JS result: {result!r}")

    if not result.get("ok"):
        raise RuntimeError("Injected TikTok scroll script failed: " + str(result.get("error")))

    text = result.get("text") or ""
    video_urls = result.get("videoUrls") or []
    photo_urls = result.get("photoUrls") or []

    URL_TXT_FILE.write_text(text, encoding="utf-8")
    log(f"Saved injected JS output to: {URL_TXT_FILE.resolve()}")
    log(f"Collected {len(video_urls)} video URL(s), {len(photo_urls)} photo URL(s).")

    return video_urls, photo_urls, text


# ================== URL EXTRACTION: SAME AS TIKTOK DOWNLOADER ==================
def extract_tiktok_urls(file_path):
    urls = []

    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            cleaned = re.sub(r"^webmssdk\.js:1\s+", "", line.strip())

            found_urls = re.findall(r"https://[^\s\"'<>]+", cleaned)

            for url in found_urls:
                url = url.strip().rstrip("),.;]'\"")

                if "tiktok.com" not in url:
                    continue

                if "/video/" not in url and "/photo/" not in url:
                    continue

                if url not in urls:
                    urls.append(url)

    return urls


def get_tiktok_kind_and_id(url):
    match = re.search(r"/(video|photo)/(\d+)", url)

    if not match:
        return None, None

    kind = match.group(1)
    tiktok_id = match.group(2)

    return kind, tiktok_id


# ================== DESTINATION SKIP CHECK: SAME AS TIKTOK DOWNLOADER ==================
def find_existing_files_for_id(tiktok_id):
    matches = []

    if not DOWNLOAD_DIR.exists():
        return matches

    for path in DOWNLOAD_DIR.rglob("*"):
        if not path.is_file():
            continue

        if path.name.endswith(".part"):
            continue

        if path.name.endswith(".crdownload"):
            continue

        if tiktok_id in path.name:
            matches.append(path)

    return matches


def should_skip_url(tiktok_id):
    if not SKIP_IF_ID_EXISTS_IN_DESTINATION:
        return False, []

    existing = find_existing_files_for_id(tiktok_id)

    return len(existing) > 0, existing


# ================== COMMAND RUNNER: SAME STYLE AS TIKTOK DOWNLOADER ==================
def command_to_printable(command):
    return " ".join(f'"{x}"' if " " in str(x) else str(x) for x in command)


def run_command(command):
    log("Running command:")
    log(command_to_printable(command))

    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )

        for line in process.stdout:
            print(line, end="", flush=True)

        process.wait()

        return process.returncode

    except FileNotFoundError:
        log(f"ERROR: Command not found: {command[0]}")
        return 127

    except Exception as e:
        log(f"ERROR while running command: {type(e).__name__}: {e}")
        return 1


# ================== FILE MOVING: SAME AS TIKTOK DOWNLOADER ==================
def make_unique_destination_path(dest_path):
    if not dest_path.exists():
        return dest_path

    stem = dest_path.stem
    suffix = dest_path.suffix
    parent = dest_path.parent

    counter = 1

    while True:
        candidate = parent / f"{stem} ({counter}){suffix}"

        if not candidate.exists():
            return candidate

        counter += 1


def move_completed_files_from_tmp(tmp_item_dir):
    moved_files = []

    for path in tmp_item_dir.rglob("*"):
        if not path.is_file():
            continue

        lower_name = path.name.lower()

        if lower_name.endswith(".part"):
            log(f"Leaving unfinished temp file: {path}")
            continue

        if lower_name.endswith(".crdownload"):
            log(f"Leaving unfinished Chrome temp file: {path}")
            continue

        if lower_name.endswith(".ytdl"):
            log(f"Leaving yt-dlp temp file: {path}")
            continue

        dest_path = DOWNLOAD_DIR / path.name

        if dest_path.exists():
            log(f"Destination already has file with same name, skipping move: {dest_path.name}")
            continue

        final_dest = make_unique_destination_path(dest_path)

        shutil.move(str(path), str(final_dest))
        moved_files.append(final_dest)

        log(f"Moved to downloads: {final_dest.name}")

    return moved_files


def clear_tmp_item_dir(tmp_item_dir):
    if tmp_item_dir.exists():
        shutil.rmtree(tmp_item_dir, ignore_errors=True)

    tmp_item_dir.mkdir(parents=True, exist_ok=True)


# ================== DOWNLOADERS: YT-DLP FOR VIDEOS, GALLERY-DL FOR PHOTOS ==================
def exported_cookie_args():
    if COOKIE_FILE.exists() and COOKIE_FILE.stat().st_size > 0:
        return ["--cookies", str(COOKIE_FILE)]
    return []


def firefox_cookie_args():
    # Exact style used by your TikTok downloader.
    return ["--cookies-from-browser", COOKIES_BROWSER]


def build_cookie_arg_attempts():
    """
    First use the exported Selenium cookies so manual login in the visible
    Selenium window actually works for downloads. If that fails, fallback to
    the original downloader technique: --cookies-from-browser firefox.
    """
    attempts = []

    exported = exported_cookie_args()
    if exported:
        attempts.append(("exported Selenium cookies", exported))

    attempts.append(("normal Firefox cookies", firefox_cookie_args()))

    return attempts


def download_video_with_ytdlp(url, tiktok_id, tmp_item_dir, cookie_args):
    output_template = str(tmp_item_dir / "%(uploader)s_%(id)s.%(ext)s")

    command = [
        VIDEO_TOOL,
        url,
        *cookie_args,
        "--windows-filenames",
        "--no-overwrites",
        "--restrict-filenames",
        "-o",
        output_template,
    ]

    return run_command(command)


def download_photo_with_gallerydl(url, tiktok_id, tmp_item_dir, cookie_args):
    command = [
        PHOTO_TOOL,
        *cookie_args,
        "--directory",
        str(tmp_item_dir),
        url,
    ]

    return run_command(command)


def download_one_url(url, kind, tiktok_id, tmp_item_dir):
    attempts = build_cookie_arg_attempts()
    last_code = 1

    for attempt_index, (label, cookie_args) in enumerate(attempts, start=1):
        if attempt_index > 1:
            log("")
            log(f"Retrying with {label}...")

        log(f"Cookie source: {label}")

        if kind == "video":
            last_code = download_video_with_ytdlp(url, tiktok_id, tmp_item_dir, cookie_args)
        elif kind == "photo":
            last_code = download_photo_with_gallerydl(url, tiktok_id, tmp_item_dir, cookie_args)
        else:
            return 1

        if last_code == 0:
            return 0

        log(f"Download attempt using {label} failed with exit code: {last_code}")

    return last_code


# ================== MAIN DOWNLOAD LOOP: SAME AS TIKTOK DOWNLOADER ==================
def process_url_txt_and_download_all(txt_file):
    DOWNLOAD_DIR.mkdir(exist_ok=True)
    TMP_DIR.mkdir(exist_ok=True)
    reset_log_files()

    urls = extract_tiktok_urls(txt_file)

    if not urls:
        log("No TikTok /video/ or /photo/ URLs found.")
        return

    log(f"Found {len(urls)} TikTok URLs.")
    log(f"Final download folder: {DOWNLOAD_DIR.resolve()}")
    log(f"Temporary folder: {TMP_DIR.resolve()}")
    log("")

    success_count = 0
    skipped_count = 0
    failed_count = 0

    for index, url in enumerate(urls, start=1):
        log("=" * 80)
        log(f"[{index}/{len(urls)}] URL: {url}")

        kind, tiktok_id = get_tiktok_kind_and_id(url)

        if not kind or not tiktok_id:
            log("Could not detect TikTok type or ID. Marking failed.")
            write_line(FAILED_FILE, f"{url} | Could not detect type or ID")
            failed_count += 1
            continue

        log(f"Detected type: {kind}")
        log(f"Detected ID: {tiktok_id}")

        skip, existing_files = should_skip_url(tiktok_id)

        if skip:
            log("Skipping because destination already contains file(s) with this TikTok ID:")

            for existing in existing_files:
                log(f"  Existing: {existing}")

            write_line(SKIPPED_FILE, f"{url} | ID already exists in downloads: {tiktok_id}")
            skipped_count += 1
            continue

        tmp_item_dir = TMP_DIR / f"{kind}_{tiktok_id}"
        clear_tmp_item_dir(tmp_item_dir)

        if kind not in ("video", "photo"):
            log(f"Unknown TikTok type: {kind}")
            write_line(FAILED_FILE, f"{url} | Unknown TikTok type: {kind}")
            failed_count += 1
            continue

        return_code = download_one_url(url, kind, tiktok_id, tmp_item_dir)

        if return_code != 0:
            log(f"Download command failed with exit code: {return_code}")
            write_line(FAILED_FILE, f"{url} | Command failed with exit code {return_code}")
            failed_count += 1
            continue

        moved_files = move_completed_files_from_tmp(tmp_item_dir)

        if not moved_files:
            log("Command finished, but no completed files were found to move.")
            write_line(FAILED_FILE, f"{url} | No completed files found after download")
            failed_count += 1
            continue

        log("Download completed successfully.")
        write_line(SUCCESS_FILE, f"{url} | {tiktok_id} | {len(moved_files)} file(s)")
        success_count += 1

    log("=" * 80)
    log("SUMMARY")
    log(f"Successful downloads: {success_count}")
    log(f"Skipped existing:      {skipped_count}")
    log(f"Failed:                {failed_count}")
    log("")
    log(f"URL text:    {URL_TXT_FILE.resolve()}")
    log(f"Cookie file: {COOKIE_FILE.resolve()}")
    log(f"Success log: {SUCCESS_FILE.resolve()}")
    log(f"Skipped log: {SKIPPED_FILE.resolve()}")
    log(f"Failed log:  {FAILED_FILE.resolve()}")
    log(f"Downloads:   {DOWNLOAD_DIR.resolve()}")


# ================== ARGUMENTS ==================
def normalize_tiktok_start_url(url):
    url = url.strip()

    if not url:
        raise ValueError("Empty TikTok URL.")

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    parsed = urlparse(url)
    host = parsed.netloc.lower()

    if "tiktok.com" not in host:
        raise ValueError("URL does not look like a TikTok URL: " + url)

    return url


def parse_args():
    parser = argparse.ArgumentParser(
        description="Open TikTok in Firefox, collect all video/photo URLs, then download all."
    )

    parser.add_argument(
        "url",
        help="TikTok profile URL, for example: https://www.tiktok.com/@nhobell07",
    )

    parser.add_argument(
        "--visible",
        action="store_true",
        help="Selenium mode only: skip headless check and open visible automated Firefox immediately.",
    )

    parser.add_argument(
        "--selenium",
        action="store_true",
        help="Use the old Selenium/WebDriver Firefox path instead of normal Firefox Web Console mode.",
    )

    parser.add_argument(
        "--stable-scrolls",
        type=int,
        default=DEFAULT_STABLE_SCROLLS,
        help="Stop after this many scrolls with no new page height/link count. Default: 3",
    )

    parser.add_argument(
        "--scroll-delay-ms",
        type=int,
        default=DEFAULT_SCROLL_DELAY_MS,
        help="Delay between scrolls in milliseconds. Default: 1500",
    )

    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Only collect URLs into tiktok_urls_found.txt; do not download.",
    )

    parser.add_argument(
        "--no-login-prompt",
        action="store_true",
        help="Do not wait for ENTER during the first-run visible TikTok login fallback.",
    )

    parser.add_argument(
        "--from-txt",
        default=None,
        help="Skip browser scraping and download URLs from an existing txt file.",
    )

    return parser.parse_args()


def main():
    args = parse_args()

    check_required_download_tools()

    start_url = normalize_tiktok_start_url(args.url)

    driver = None

    try:
        if args.from_txt:
            txt_path = Path(args.from_txt)
            if not txt_path.exists():
                raise FileNotFoundError(f"TXT file does not exist: {txt_path}")
            process_url_txt_and_download_all(txt_path)
            return

        log(f"Target TikTok URL: {start_url}")

        if not args.selenium:
            video_urls, photo_urls, _ = run_normal_firefox_scroll_script(
                start_url,
                stable_scrolls=max(1, args.stable_scrolls),
                delay_ms=max(500, args.scroll_delay_ms),
            )

            if args.skip_download:
                log("Skipping download because --skip-download was used.")
                log(f"Collected URLs are saved at: {URL_TXT_FILE.resolve()}")
                return

            process_url_txt_and_download_all(URL_TXT_FILE)
            return

        check_python_deps()

        driver, was_headless = get_logged_in_driver(
            start_url,
            force_visible=args.visible,
            interactive_login=not args.no_login_prompt,
        )

        # Export the current Selenium cookies before scraping/downloading, so
        # the download tools can use the same session.
        write_netscape_cookie_file_from_selenium(driver, COOKIE_FILE)

        video_urls, photo_urls, _ = run_tiktok_scroll_script(
            driver,
            stable_scrolls=max(1, args.stable_scrolls),
            delay_ms=max(500, args.scroll_delay_ms),
        )

        # If headless is logged in but TikTok returned nothing, reopen visible
        # Firefox and let the user confirm the page. This catches headless render
        # failures without asking unnecessary questions up front.
        if was_headless and len(video_urls) + len(photo_urls) == 0:
            log("")
            log("Headless run found zero URLs. Reopening visible Firefox for confirmation...")
            try:
                driver.quit()
            except Exception:
                pass

            driver, was_headless = get_logged_in_driver(
                start_url,
                force_visible=True,
                interactive_login=not args.no_login_prompt,
            )
            write_netscape_cookie_file_from_selenium(driver, COOKIE_FILE)
            video_urls, photo_urls, _ = run_tiktok_scroll_script(
                driver,
                stable_scrolls=max(1, args.stable_scrolls),
                delay_ms=max(500, args.scroll_delay_ms),
            )

        if args.skip_download:
            log("Skipping download because --skip-download was used.")
            log(f"Collected URLs are saved at: {URL_TXT_FILE.resolve()}")
            return

        process_url_txt_and_download_all(URL_TXT_FILE)

    finally:
        if driver is not None:
            try:
                driver.get("about:blank")
                time.sleep(1)
            except Exception:
                pass

            try:
                driver.quit()
            except Exception:
                pass


if __name__ == "__main__":
    main()
