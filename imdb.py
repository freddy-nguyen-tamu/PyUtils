# imdb.py
#
# Requires:
#   python -m pip install selenium requests pillow beautifulsoup4 browser-cookie3
#
# Usage:
#   python imdb.py
#   python imdb.py 90.txt
#   python -u imdb.py 90.txt
#
# Behavior:
#   - Reads one IMDb URL per line from 90.txt.
#   - Skips URLs whose IMDb title id is already listed in done_imdb.txt.
#   - Uses Firefox Selenium with a persistent profile.
#   - First tries headless Firefox.
#   - If IMDb returns AWS WAF / bot challenge, opens visible Firefox.
#   - You solve/let the page load once, then press ENTER in terminal.
#   - Future runs should reuse selenium_imdb_profile.
#   - Creates only final PNG files in imdb_output.
#   - Filename format: [Title] Content.png
#   - Runs tierlistmaker_limit.py after image creation.
#   - Adds TierMaker-processed URLs to done_imdb.txt, then removes them from 90.txt.
#   - Prints progress constantly with flush=True so it does not look frozen.

import argparse
import csv
import html
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List, Optional, Set, Tuple

import browser_cookie3
import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont

from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.common.by import By
from selenium.common.exceptions import WebDriverException


DEFAULT_INPUT_FILE = "90.txt"
DEFAULT_OUTPUT_DIR = "imdb_output"
DEFAULT_DONE_FILE = "done_imdb.txt"
TIERLISTMAKER_SCRIPT = "tierlistmaker_limit.py"
SELENIUM_PROFILE_DIR = "selenium_imdb_profile"

# Optional: set this if browser_cookie3 picks the wrong Firefox profile.
# Example:
# FIREFOX_COOKIES_SQLITE = r"C:\Users\qacer\AppData\Roaming\Mozilla\Firefox\Profiles\xxxx.default-release\cookies.sqlite"
FIREFOX_COOKIES_SQLITE = None

DELAY_SECONDS = 0.8
MAX_FILENAME_CHARS = 190

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) "
        "Gecko/20100101 Firefox/126.0"
    ),
    "Accept": "image/avif,image/webp,image/png,image/jpeg,image/*,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.imdb.com/",
}


# ----------------------------- logging -----------------------------

def log(message: str = "") -> None:
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def log_step(message: str) -> None:
    log(f"-- {message}")


def log_ok(message: str) -> None:
    log(f"[OK] {message}")


def log_warn(message: str) -> None:
    log(f"[WARN] {message}")


def log_fail(message: str) -> None:
    log(f"[FAILED] {message}")


# ----------------------------- text/url helpers -----------------------------

def clean_text(value) -> str:
    if value is None:
        return ""
    value = str(value)
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def extract_tt_id(url: str) -> str:
    match = re.search(r"/title/(tt\d+)", url)
    if not match:
        raise ValueError(f"Could not extract IMDb title id from URL: {url}")
    return match.group(1)


def canonical_url(url: str) -> str:
    return f"https://www.imdb.com/title/{extract_tt_id(url)}/"


def reference_url(url: str) -> str:
    return f"https://www.imdb.com/title/{extract_tt_id(url)}/reference/"


def read_urls(path: Path) -> List[str]:
    urls = []
    log_step(f"Reading URL list: {path}")

    for line_number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        if not line.startswith("http"):
            log_warn(f"Skipping line {line_number}; not a URL: {line}")
            continue
        urls.append(line)

    log_ok(f"Loaded {len(urls)} URLs")
    return urls


def url_done_key(url: str) -> str:
    try:
        return extract_tt_id(url)
    except ValueError:
        return clean_text(url).lower()


def read_done_keys(path: Path) -> Set[str]:
    if not path.exists():
        log_warn(f"Done file not found yet: {path}")
        return set()

    keys = set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or not line.startswith("http"):
            continue
        keys.add(url_done_key(line))

    log_ok(f"Loaded {len(keys)} done IMDb URLs from {path}")
    return keys


def filter_done_urls(urls: List[str], done_keys: Set[str]) -> Tuple[List[str], int]:
    pending = []
    skipped = 0

    for url in urls:
        if url_done_key(url) in done_keys:
            skipped += 1
            log_step(f"Skipping URL already in done_imdb.txt: {url}")
            continue
        pending.append(url)

    if skipped:
        log_ok(f"Skipped {skipped} URLs already listed in done_imdb.txt")

    return pending, skipped


def is_waf_html(page_html: str) -> bool:
    lowered = page_html.lower()
    signals = [
        "window.awswafcookiedomainlist",
        "window.gokuprops",
        "challenge-container",
        "token.awswaf.com",
        "awswaf",
        "verify that you're not a robot",
        "request blocked",
        "captcha",
    ]
    return any(signal in lowered for signal in signals)


def is_real_imdb_html(page_html: str) -> bool:
    lowered = page_html.lower()
    if is_waf_html(page_html):
        return False
    good_signals = [
        "application/ld+json",
        "hero__primary-text",
        "ipc-lockup-overlay",
        "og:image",
        "twitter:image",
        "title",
    ]
    return any(signal in lowered for signal in good_signals)


# ----------------------------- Firefox / Selenium -----------------------------

def build_driver(headless: bool) -> webdriver.Firefox:
    log_step(f"Starting Firefox Selenium driver. headless={headless}")

    options = Options()
    profile_path = os.path.abspath(SELENIUM_PROFILE_DIR)
    os.makedirs(profile_path, exist_ok=True)

    log_step(f"Using persistent Firefox profile: {profile_path}")

    options.add_argument("-profile")
    options.add_argument(profile_path)

    if headless:
        options.add_argument("-headless")

    options.add_argument("--width=1400")
    options.add_argument("--height=1200")

    options.set_preference("browser.shell.checkDefaultBrowser", False)
    options.set_preference("browser.startup.homepage_override.mstone", "ignore")
    options.set_preference("startup.homepage_welcome_url", "about:blank")
    options.set_preference("startup.homepage_welcome_url.additional", "about:blank")
    options.set_preference("intl.accept_languages", "en-US,en")
    options.set_preference("dom.webdriver.enabled", False)
    options.set_preference("useAutomationExtension", False)

    driver = webdriver.Firefox(options=options)

    try:
        driver.set_window_size(1400, 1200)
    except Exception as exc:
        log_warn(f"Could not set Firefox window size: {exc}")

    log_ok("Firefox driver started")
    return driver


def load_firefox_imdb_cookies(required: bool = False):
    log_step("Reading IMDb cookies from normal Firefox")

    tries = []

    if FIREFOX_COOKIES_SQLITE:
        tries.append(("explicit cookies.sqlite", lambda: browser_cookie3.firefox(cookie_file=FIREFOX_COOKIES_SQLITE)))

    tries.append(("domain .imdb.com", lambda: browser_cookie3.firefox(domain_name=".imdb.com")))
    tries.append(("domain imdb.com", lambda: browser_cookie3.firefox(domain_name="imdb.com")))
    tries.append(("all Firefox cookies then filter", lambda: browser_cookie3.firefox()))

    best = []

    for label, fn in tries:
        try:
            log_step(f"Trying Firefox cookie source: {label}")
            cj = fn()
            cookies = []

            for c in cj:
                domain = (c.domain or "").lower()
                if "imdb.com" in domain or "amazon.com" in domain:
                    cookies.append(c)

            names = sorted(set(c.name for c in cookies))
            log(f"Cookie source {label}: {len(cookies)} cookies")
            log(f"Cookie names: {names}")

            if len(cookies) > len(best):
                best = cookies

            if cookies:
                return cookies

        except Exception as exc:
            log_warn(f"Cookie source {label} failed: {exc}")

    if required and not best:
        raise RuntimeError("Could not find IMDb cookies from normal Firefox.")

    return best


def make_selenium_cookie_variants(c):
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

    for domain_guess in [".imdb.com", "www.imdb.com"]:
        d2 = dict(base)
        d2["domain"] = domain_guess
        variants.append(d2)

    d3 = dict(base)
    variants.append(d3)
    return variants


def inject_cookies_into_driver(driver: webdriver.Firefox, cookies) -> None:
    if not cookies:
        log_warn("No normal Firefox IMDb cookies to inject")
        return

    log_step("Opening imdb.com before cookie injection")
    driver.get("https://www.imdb.com/")
    time.sleep(2)

    added = 0
    failed = 0

    log_step(f"Injecting {len(cookies)} cookies into Selenium Firefox")

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

    log(f"Injected cookies: added={added}, failed={failed}")

    log_step("Reloading imdb.com after cookie injection")
    driver.get("https://www.imdb.com/")
    time.sleep(2)


def selenium_cookies_to_requests_cookies(driver: webdriver.Firefox):
    cookies = []
    try:
        selenium_cookies = driver.get_cookies()
    except Exception:
        selenium_cookies = []

    for c in selenium_cookies:
        name = c.get("name")
        value = c.get("value")
        domain = c.get("domain") or ".imdb.com"
        path = c.get("path") or "/"

        if not name or not value:
            continue

        cookies.append(SimpleNamespace(name=name, value=value, domain=domain, path=path))

    return cookies


def wait_for_real_imdb_page(driver: webdriver.Firefox, timeout: int = 60, label: str = "page") -> bool:
    log_step(f"Waiting up to {timeout}s for real IMDb {label}")
    start = time.time()
    last_report = -1

    while time.time() - start < timeout:
        elapsed = int(time.time() - start)

        try:
            page_html = driver.page_source or ""
            title = driver.title or ""
            current = driver.current_url or ""
        except Exception:
            page_html = ""
            title = ""
            current = ""

        waf = is_waf_html(page_html)
        real = is_real_imdb_html(page_html)
        html_len = len(page_html)

        if elapsed != last_report and (elapsed == 0 or elapsed % 3 == 0):
            log(
                f"Waiting... elapsed={elapsed}s waf={waf} real={real} "
                f"html_len={html_len} title={title!r} url={current}"
            )
            last_report = elapsed

        if real:
            log_ok(f"Real IMDb {label} detected after {elapsed}s")
            return True

        time.sleep(1)

    try:
        page_html = driver.page_source or ""
    except Exception:
        page_html = ""

    log_warn(f"Timed out waiting for real IMDb {label}. waf={is_waf_html(page_html)} html_len={len(page_html)}")
    return False


def open_url_and_check(driver: webdriver.Firefox, url: str, timeout: int = 60) -> bool:
    log_step(f"Opening URL in Firefox: {url}")
    driver.get(url)
    ok = wait_for_real_imdb_page(driver, timeout=timeout, label=url)

    try:
        page_html = driver.page_source or ""
        log(f"Current URL: {driver.current_url}")
        log(f"Page title : {driver.title}")
        log(f"HTML bytes : {len(page_html)}")
        log(f"WAF page   : {is_waf_html(page_html)}")
        log(f"Real IMDb  : {is_real_imdb_html(page_html)}")
    except Exception as exc:
        log_warn(f"Could not print browser status: {exc}")

    return ok


def get_ready_driver(first_url: str) -> Tuple[webdriver.Firefox, bool]:
    target = canonical_url(first_url)
    firefox_cookies = []

    log("")
    log_step("Trying saved Selenium Firefox profile in headless mode")
    driver = build_driver(headless=True)

    try:
        if open_url_and_check(driver, target, timeout=30):
            log_ok("Saved Selenium Firefox profile works headless")
            return driver, False

        log("")
        log_warn("Headless profile did not get the real IMDb page")
        log_step("Trying to import IMDb cookies from normal Firefox")

        firefox_cookies = load_firefox_imdb_cookies(required=False)

        if firefox_cookies:
            inject_cookies_into_driver(driver, firefox_cookies)
            if open_url_and_check(driver, target, timeout=30):
                log_ok("Imported normal Firefox cookies worked; continuing headless")
                return driver, False

        log("")
        log_warn("Headless still failed")
        log_step("Closing headless Firefox and opening visible Firefox")

    except Exception as exc:
        log_warn(f"Headless attempt had an error: {exc}")

    try:
        driver.quit()
    except Exception:
        pass

    time.sleep(1)

    driver = build_driver(headless=False)

    try:
        if firefox_cookies:
            inject_cookies_into_driver(driver, firefox_cookies)

        log_step(f"Opening first IMDb page visibly: {target}")
        driver.get(target)
        time.sleep(3)

        if not wait_for_real_imdb_page(driver, timeout=12, label="visible first page"):
            log("")
            log("=" * 80)
            log("Visible Firefox is open.")
            log("Let IMDb finish loading, solve any challenge if shown,")
            log("and make sure the real IMDb title page is visible.")
            input("After the real IMDb page is visible, press ENTER here to continue...")
            log("=" * 80)
            log("")

            log_step("Reloading first page after manual confirmation")
            driver.get(target)
            time.sleep(3)

        if not wait_for_real_imdb_page(driver, timeout=30, label="visible first page after manual step"):
            log_warn("IMDb still does not look like the real title page; extraction may fail")

        log_ok("Visible Selenium Firefox profile is ready")
        log_ok("This session is saved in selenium_imdb_profile for future headless runs")
        return driver, True

    except Exception:
        try:
            driver.quit()
        except Exception:
            pass
        raise


# ----------------------------- extraction helpers -----------------------------

def normalize_imdb_image_url(url: str) -> str:
    url = clean_text(url)
    if not url:
        return ""

    url = url.replace("\\/", "/")
    url = url.replace("&amp;", "&")

    match = re.match(
        r"^(https?://m\.media-amazon\.com/images/.*?@\.)_V1_.*?(\.(?:jpg|jpeg|png|webp))(?:\?.*)?$",
        url,
        flags=re.IGNORECASE,
    )
    if match:
        return f"{match.group(1)}_V1_{match.group(2)}"

    return url


def get_meta(soup: BeautifulSoup, selector: str) -> str:
    tag = soup.select_one(selector)
    if not tag:
        return ""
    return clean_text(tag.get("content") or tag.get_text(" ", strip=True))


def choose_best_srcset(srcset: str) -> str:
    best_url = ""
    best_width = -1

    for part in srcset.split(","):
        part = clean_text(part)
        if not part:
            continue

        pieces = part.split()
        candidate_url = pieces[0]
        candidate_width = 0

        if len(pieces) > 1:
            width_match = re.search(r"(\d+)w", pieces[1])
            if width_match:
                candidate_width = int(width_match.group(1))

        if candidate_width > best_width:
            best_width = candidate_width
            best_url = candidate_url

    return best_url


def image_url_from_img_tag(img) -> str:
    if not img:
        return ""

    srcset = img.get("srcset") or img.get("data-srcset")
    if srcset:
        best = choose_best_srcset(srcset)
        if best:
            return normalize_imdb_image_url(best)

    for attr in ["src", "data-src", "loadlate"]:
        value = img.get(attr)
        if value:
            return normalize_imdb_image_url(value)

    return ""


def parse_json_ld_objects(page_html: str) -> List[dict]:
    objects = []
    scripts = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        page_html,
        flags=re.IGNORECASE | re.DOTALL,
    )

    log(f"Found {len(scripts)} JSON-LD script blocks")

    for raw in scripts:
        raw = html.unescape(raw).strip()
        try:
            data = json.loads(raw)
        except Exception:
            continue

        if isinstance(data, dict):
            objects.append(data)
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    objects.append(item)

    return objects


def extract_from_json_ld(page_html: str) -> Dict[str, str]:
    result = {"title": "", "content": "", "poster": ""}
    preferred_types = {"Movie", "TVSeries", "TVEpisode", "CreativeWork", "VideoObject"}

    objects = parse_json_ld_objects(page_html)
    log(f"Parsed {len(objects)} JSON-LD objects")

    for obj in objects:
        obj_type = obj.get("@type")
        if isinstance(obj_type, list):
            type_ok = any(t in preferred_types for t in obj_type)
        else:
            type_ok = obj_type in preferred_types

        if not type_ok and not obj.get("image"):
            continue

        title = clean_text(obj.get("alternateName") or obj.get("name"))
        content = clean_text(obj.get("description"))

        image_value = obj.get("image")
        poster = ""

        if isinstance(image_value, str):
            poster = image_value
        elif isinstance(image_value, dict):
            poster = image_value.get("url") or image_value.get("contentUrl") or image_value.get("@id") or ""
        elif isinstance(image_value, list):
            for item in image_value:
                if isinstance(item, str):
                    poster = item
                    break
                if isinstance(item, dict):
                    poster = item.get("url") or item.get("contentUrl") or item.get("@id") or ""
                    if poster:
                        break

        if title and not result["title"]:
            result["title"] = title
        if content and not result["content"]:
            result["content"] = content
        if poster and not result["poster"]:
            result["poster"] = normalize_imdb_image_url(poster)

    log(f"JSON-LD extraction: title={bool(result['title'])} content={bool(result['content'])} poster={bool(result['poster'])}")
    return result


def extract_title_from_soup(soup: BeautifulSoup) -> str:
    selectors = [
        'span.hero__primary-text[data-testid="hero__primary-text"]',
        "span.hero__primary-text",
        'h1[data-testid="hero__pageTitle"]',
        "h1",
        'h3[itemprop="name"] a',
        'h3[itemprop="name"]',
    ]

    for selector in selectors:
        tag = soup.select_one(selector)
        if tag:
            title = clean_text(tag.get_text(" ", strip=True))
            if title:
                title = re.sub(r"\s*\([^)]*\)\s*-\s*IMDb\s*$", "", title)
                title = re.sub(r"\s*⭐.*$", "", title)
                return title.strip()

    for selector in ['meta[property="og:title"]', 'meta[name="title"]', 'meta[property="twitter:title"]']:
        title = get_meta(soup, selector)
        if title:
            title = re.sub(r"\s*\([^)]*\)\s*-\s*IMDb\s*$", "", title)
            title = re.sub(r"\s*⭐.*$", "", title)
            return title.strip()

    return ""


def extract_content_from_soup(soup: BeautifulSoup) -> str:
    selectors = [
        'span[data-testid="plot-xl"]',
        'span[data-testid="plot-l"]',
        'span[data-testid="plot-xs_to_m"]',
        '[data-testid="plot"] span',
        '[data-testid="storyline-plot-summary"]',
        'span[itemprop="description"]',
    ]

    for selector in selectors:
        tag = soup.select_one(selector)
        if tag:
            text = clean_text(tag.get_text(" ", strip=True))
            if text:
                return text

    meta_description = get_meta(soup, 'meta[name="description"]')
    if meta_description:
        if ": With " in meta_description and ". " in meta_description:
            return clean_text(meta_description.split(". ", 1)[1])
        return meta_description

    return ""


def extract_poster_from_soup(soup: BeautifulSoup) -> str:
    overlay = (
        soup.select_one('a.ipc-lockup-overlay.ipc-focusable.ipc-focusable--constrained[href*="/mediaviewer/"]')
        or soup.select_one('a.ipc-lockup-overlay[href*="/mediaviewer/"]')
        or soup.select_one('a[href*="/mediaviewer/"]')
    )

    if overlay:
        current = overlay
        for _ in range(12):
            current = current.parent
            if not current:
                break
            img = current.select_one("img")
            poster = image_url_from_img_tag(img)
            if poster:
                return poster

    poster_img = soup.select_one("div.poster img") or soup.select_one(".poster img") or soup.select_one('img[itemprop="image"]')
    poster = image_url_from_img_tag(poster_img)
    if poster:
        return poster

    for selector in ['meta[property="og:image"]', 'meta[property="twitter:image"]', 'meta[name="twitter:image"]']:
        poster = get_meta(soup, selector)
        if poster:
            return normalize_imdb_image_url(poster)

    return ""


def extract_poster_by_regex(page_html: str) -> str:
    raw = html.unescape(page_html)
    raw = raw.replace("\\/", "/")

    patterns = [
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+property=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'"image"\s*:\s*"([^"]+m\.media-amazon\.com/images/[^"]+)"',
        r'"url"\s*:\s*"([^"]+m\.media-amazon\.com/images/[^"]+)"',
    ]

    for pattern in patterns:
        match = re.search(pattern, raw, flags=re.IGNORECASE | re.DOTALL)
        if match:
            poster = normalize_imdb_image_url(match.group(1))
            if poster:
                return poster

    urls = re.findall(
        r"https?://m\.media-amazon\.com/images/M/[^\"'<>\s\\]+?\.(?:jpg|jpeg|png|webp)",
        raw,
        flags=re.IGNORECASE,
    )

    cleaned = []
    seen = set()
    for url in urls:
        url = normalize_imdb_image_url(url)
        if not url or url in seen:
            continue
        seen.add(url)
        cleaned.append(url)

    if not cleaned:
        return ""

    def score(url: str) -> int:
        value = 0
        if "MV5B" in url:
            value += 10
        if "_V1_" in url:
            value += 8
        if "UX" in url or "UY" in url:
            value += 3
        if "RM" not in url:
            value += 1
        return value

    cleaned.sort(key=score, reverse=True)
    return cleaned[0]


def extract_runtime_dom_info(driver: webdriver.Firefox) -> Dict[str, str]:
    log_step("Extracting title/content/poster from live browser DOM")

    script = r'''
        function clean(s) {
            return (s || "").replace(/\s+/g, " ").trim();
        }
        function text(selector) {
            const el = document.querySelector(selector);
            return el ? clean(el.textContent) : "";
        }
        function meta(selector) {
            const el = document.querySelector(selector);
            return el ? clean(el.getAttribute("content") || el.textContent || "") : "";
        }
        function imgSrc(img) {
            if (!img) return "";
            return clean(img.currentSrc || img.src || img.getAttribute("src") || img.getAttribute("data-src") || "");
        }

        let title =
            text('span.hero__primary-text[data-testid="hero__primary-text"]') ||
            text('span.hero__primary-text') ||
            text('h1[data-testid="hero__pageTitle"]') ||
            text('h1') ||
            meta('meta[property="og:title"]') ||
            meta('meta[property="twitter:title"]');

        let content =
            text('span[data-testid="plot-xl"]') ||
            text('span[data-testid="plot-l"]') ||
            text('span[data-testid="plot-xs_to_m"]') ||
            text('[data-testid="plot"] span') ||
            text('[data-testid="storyline-plot-summary"]') ||
            meta('meta[name="description"]');

        let poster = "";
        const overlay =
            document.querySelector('a.ipc-lockup-overlay.ipc-focusable.ipc-focusable--constrained[href*="/mediaviewer/"]') ||
            document.querySelector('a.ipc-lockup-overlay[href*="/mediaviewer/"]') ||
            document.querySelector('a[href*="/mediaviewer/"]');

        if (overlay) {
            let cur = overlay;
            for (let i = 0; i < 12 && cur; i++) {
                cur = cur.parentElement;
                if (!cur) break;
                const img = cur.querySelector("img");
                const src = imgSrc(img);
                if (src) {
                    poster = src;
                    break;
                }
            }
        }

        if (!poster) {
            const img =
                document.querySelector("div.poster img") ||
                document.querySelector(".poster img") ||
                document.querySelector('img[itemprop="image"]');
            poster = imgSrc(img);
        }

        if (!poster) {
            poster =
                meta('meta[property="og:image"]') ||
                meta('meta[property="twitter:image"]') ||
                meta('meta[name="twitter:image"]');
        }

        const scripts = Array.from(document.querySelectorAll('script[type="application/ld+json"]'));
        for (const script of scripts) {
            try {
                const parsed = JSON.parse(script.textContent);
                const arr = Array.isArray(parsed) ? parsed : [parsed];
                for (const obj of arr) {
                    if (!obj || typeof obj !== "object") continue;
                    if (!title) title = clean(obj.alternateName || obj.name || "");
                    if (!content) content = clean(obj.description || "");
                    if (!poster && obj.image) {
                        if (typeof obj.image === "string") {
                            poster = clean(obj.image);
                        } else if (Array.isArray(obj.image)) {
                            for (const item of obj.image) {
                                if (typeof item === "string") {
                                    poster = clean(item);
                                    break;
                                }
                                if (item && typeof item === "object") {
                                    poster = clean(item.url || item.contentUrl || item["@id"] || "");
                                    if (poster) break;
                                }
                            }
                        } else if (typeof obj.image === "object") {
                            poster = clean(obj.image.url || obj.image.contentUrl || obj.image["@id"] || "");
                        }
                    }
                }
            } catch (e) {}
        }
        return {title: title || "", content: content || "", poster: poster || ""};
    '''

    try:
        result = driver.execute_script(script) or {}
    except Exception as exc:
        log_warn(f"Runtime DOM extraction failed: {exc}")
        result = {}

    info = {
        "title": clean_text(result.get("title", "")),
        "content": clean_text(result.get("content", "")),
        "poster": normalize_imdb_image_url(result.get("poster", "")),
    }

    log(f"DOM extraction result: title={bool(info['title'])} content={bool(info['content'])} poster={bool(info['poster'])}")
    return info


def extract_info_from_html(page_html: str) -> Dict[str, str]:
    log_step("Extracting title/content/poster from page HTML")
    log(f"HTML length: {len(page_html)}")

    soup = BeautifulSoup(page_html, "html.parser")
    json_ld_info = extract_from_json_ld(page_html)

    title = json_ld_info["title"] or extract_title_from_soup(soup)
    content = json_ld_info["content"] or extract_content_from_soup(soup)
    poster = json_ld_info["poster"] or extract_poster_from_soup(soup) or extract_poster_by_regex(page_html)

    info = {
        "title": clean_text(title),
        "content": clean_text(content),
        "poster": normalize_imdb_image_url(poster),
    }

    log(f"HTML extraction result: title={bool(info['title'])} content={bool(info['content'])} poster={bool(info['poster'])}")
    return info


def merge_info(*infos: Dict[str, str]) -> Dict[str, str]:
    merged = {"title": "", "content": "", "poster": ""}
    for info in infos:
        for key in merged:
            if not merged[key] and info.get(key):
                merged[key] = info[key]
    return merged


def save_debug_html(tt_id: str, label: str, page_html: str) -> None:
    debug_dir = Path("imdb_debug_sources")
    debug_dir.mkdir(exist_ok=True)
    path = debug_dir / f"{tt_id}_{label}.html"
    path.write_text(page_html, encoding="utf-8", errors="replace")
    log_warn(f"Saved debug HTML: {path}")


def open_real_page_or_manual(driver_holder: Dict[str, object], url: str) -> None:
    driver = driver_holder["driver"]

    log_step(f"Navigating browser to: {url}")
    driver.get(url)

    if wait_for_real_imdb_page(driver, timeout=45, label=url):
        return

    try:
        page_html = driver.page_source or ""
    except Exception:
        page_html = ""

    if not is_waf_html(page_html):
        log_warn("Page did not pass real IMDb check, but it also does not look like WAF; continuing")
        return

    log("")
    log_warn("IMDb WAF/challenge appeared during processing")

    if driver_holder["visible"]:
        log("Visible Firefox is already open")
        input("Solve/let IMDb load, then press ENTER here to continue...")
        log_step("Reloading URL after manual confirmation")
        driver.get(url)
        time.sleep(3)
        wait_for_real_imdb_page(driver, timeout=30, label="manual reload")
        return

    log_warn("Current browser is headless. Reopening visible Firefox with the same profile")

    try:
        driver.quit()
    except Exception:
        pass

    driver = build_driver(headless=False)
    driver_holder["driver"] = driver
    driver_holder["visible"] = True

    log_step(f"Opening visible Firefox at: {url}")
    driver.get(url)
    time.sleep(3)

    log("")
    log("=" * 80)
    log("Visible Firefox is open.")
    log("Solve/let IMDb load until the real title page is visible.")
    input("After the real IMDb page is visible, press ENTER here to continue...")
    log("=" * 80)
    log("")

    log_step("Reloading URL after manual confirmation")
    driver.get(url)
    time.sleep(3)
    wait_for_real_imdb_page(driver, timeout=30, label="manual visible reload")


def get_imdb_info(driver_holder: Dict[str, object], original_url: str) -> Dict[str, str]:
    tt_id = extract_tt_id(original_url)
    main_url = canonical_url(original_url)
    ref_url = reference_url(original_url)

    log_step(f"Processing IMDb id: {tt_id}")
    log_step(f"Canonical URL: {main_url}")

    open_real_page_or_manual(driver_holder, main_url)
    driver = driver_holder["driver"]

    main_html = driver.page_source or ""
    main_dom_info = extract_runtime_dom_info(driver)
    main_html_info = extract_info_from_html(main_html)
    info = merge_info(main_dom_info, main_html_info)

    log(f"Merged main page info: title={bool(info['title'])} content={bool(info['content'])} poster={bool(info['poster'])}")

    if info["title"] and info["content"] and info["poster"]:
        log_ok("Main page had all needed data")
        return info

    log_warn("Main page missing some data; trying reference page fallback")
    log_step(f"Reference URL: {ref_url}")

    open_real_page_or_manual(driver_holder, ref_url)
    driver = driver_holder["driver"]

    ref_html = driver.page_source or ""
    ref_dom_info = extract_runtime_dom_info(driver)
    ref_html_info = extract_info_from_html(ref_html)
    info = merge_info(info, ref_dom_info, ref_html_info)

    log(f"Merged final info: title={bool(info['title'])} content={bool(info['content'])} poster={bool(info['poster'])}")

    if not info["title"]:
        save_debug_html(tt_id, "main", main_html)
        save_debug_html(tt_id, "reference", ref_html)
        raise ValueError("Could not find title. Saved debug HTML.")

    if not info["content"]:
        info["content"] = "No description found"
        log_warn("No content/description found; using fallback text")

    if not info["poster"]:
        save_debug_html(tt_id, "main", main_html)
        save_debug_html(tt_id, "reference", ref_html)
        raise ValueError("Could not find poster image. Saved debug HTML.")

    return info


# ----------------------------- image download / rendering -----------------------------

def download_image_with_driver_cookies(driver: webdriver.Firefox, image_url: str) -> Image.Image:
    log_step(f"Downloading poster image: {image_url}")

    session = requests.Session()
    session.headers.update(REQUEST_HEADERS)

    cookies = selenium_cookies_to_requests_cookies(driver)
    log(f"Copying {len(cookies)} Selenium cookies into requests image session")

    for c in cookies:
        try:
            session.cookies.set(c.name, c.value, domain=c.domain, path=c.path or "/")
        except Exception:
            pass

    response = session.get(image_url, timeout=60)
    log(f"Image HTTP status: {response.status_code}; bytes={len(response.content)}")

    if response.status_code >= 400:
        raise RuntimeError(f"HTTP {response.status_code} while downloading image: {image_url}")

    img = Image.open(BytesIO(response.content))
    img.load()
    img = img.convert("RGB")
    log_ok(f"Poster image loaded: {img.size[0]}x{img.size[1]}")
    return img


def find_font(size: int) -> ImageFont.ImageFont:
    candidates = [
        os.environ.get("FONT_PATH", ""),
        r"C:\Windows\Fonts\arialbd.ttf",
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\calibrib.ttf",
        r"C:\Windows\Fonts\calibri.ttf",
        r"C:\Windows\Fonts\segoeuib.ttf",
        r"C:\Windows\Fonts\segoeui.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "arialbd.ttf",
        "arial.ttf",
    ]

    for candidate in candidates:
        if not candidate:
            continue
        try:
            return ImageFont.truetype(candidate, size=size)
        except Exception:
            pass

    return ImageFont.load_default()


def text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0]


def split_long_word(draw: ImageDraw.ImageDraw, word: str, font: ImageFont.ImageFont, max_width: int) -> List[str]:
    pieces = []
    current = ""

    for char in word:
        test = current + char
        if text_width(draw, test, font) <= max_width:
            current = test
        else:
            if current:
                pieces.append(current)
            current = char

    if current:
        pieces.append(current)

    return pieces


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> List[str]:
    words = text.split()
    lines = []
    current = ""

    for word in words:
        test = word if not current else f"{current} {word}"

        if text_width(draw, test, font) <= max_width:
            current = test
            continue

        if current:
            lines.append(current)
            current = ""

        if text_width(draw, word, font) <= max_width:
            current = word
        else:
            pieces = split_long_word(draw, word, font, max_width)
            if pieces:
                lines.extend(pieces[:-1])
                current = pieces[-1]

    if current:
        lines.append(current)

    return lines


def fit_font(draw: ImageDraw.ImageDraw, title: str, box_width: int, box_height: int) -> Tuple[ImageFont.ImageFont, List[str], int]:
    log_step("Fitting title text to black right-half canvas")

    padding_x = max(24, int(box_width * 0.08))
    padding_y = max(24, int(box_height * 0.08))
    usable_width = box_width - padding_x * 2
    usable_height = box_height - padding_y * 2

    low = 10
    high = max(24, min(box_width, box_height) // 2)

    best_font = find_font(low)
    best_lines = [title]
    best_spacing = 4
    best_size = low

    while low <= high:
        mid = (low + high) // 2
        font = find_font(mid)
        spacing = max(4, int(mid * 0.22))
        lines = wrap_text(draw, title, font, usable_width)
        text_block = "\n".join(lines)

        bbox = draw.multiline_textbbox((0, 0), text_block, font=font, spacing=spacing, align="center")
        block_width = bbox[2] - bbox[0]
        block_height = bbox[3] - bbox[1]

        if block_width <= usable_width and block_height <= usable_height:
            best_font = font
            best_lines = lines
            best_spacing = spacing
            best_size = mid
            low = mid + 1
        else:
            high = mid - 1

    log_ok(f"Title font fitted: size={best_size}; lines={len(best_lines)}")
    return best_font, best_lines, best_spacing


def create_side_by_side_image(poster: Image.Image, title: str) -> Image.Image:
    log_step("Creating final side-by-side image")

    left_width, left_height = poster.size
    right = Image.new("RGB", (left_width, left_height), "black")
    draw = ImageDraw.Draw(right)

    font, lines, spacing = fit_font(draw, title, left_width, left_height)
    text_block = "\n".join(lines)

    bbox = draw.multiline_textbbox((0, 0), text_block, font=font, spacing=spacing, align="center")
    block_width = bbox[2] - bbox[0]
    block_height = bbox[3] - bbox[1]

    x = (left_width - block_width) / 2 - bbox[0]
    y = (left_height - block_height) / 2 - bbox[1]

    draw.multiline_text((x, y), text_block, fill="white", font=font, spacing=spacing, align="center")

    final = Image.new("RGB", (left_width * 2, left_height), "black")
    final.paste(poster, (0, 0))
    final.paste(right, (left_width, 0))

    log_ok(f"Final image created: {final.size[0]}x{final.size[1]}")
    return final


# ----------------------------- filename helpers -----------------------------

def sanitize_filename_part(value: str) -> str:
    value = clean_text(value)
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", value)
    value = re.sub(r"\s+", " ", value)
    value = value.strip(" .")
    return value or "Untitled"


def output_path_for(output_dir: Path, title: str, content: str) -> Path:
    safe_title = sanitize_filename_part(title)
    safe_content = sanitize_filename_part(content)

    # Requested format:
    # [Title] Content.png
    base = f"[{safe_title}] {safe_content}"

    if len(base) > MAX_FILENAME_CHARS:
        base = base[:MAX_FILENAME_CHARS].rstrip(" .")

    path = output_dir / f"{base}.png"

    counter = 2
    while path.exists():
        suffix = f" ({counter})"
        trimmed = base[: MAX_FILENAME_CHARS - len(suffix)].rstrip(" .")
        path = output_dir / f"{trimmed}{suffix}.png"
        counter += 1

    log_step(f"Output filename selected: {path.name}")
    return path


# ----------------------------- main processing -----------------------------

def process_one(driver_holder: Dict[str, object], url: str, output_dir: Path, index: int, total: int) -> Tuple[bool, Optional[Path]]:
    log("")
    log("=" * 90)
    log(f"[{index}/{total}] START")
    log(f"Original URL: {url}")

    try:
        info = get_imdb_info(driver_holder, url)

        title = info["title"]
        content = info["content"]
        poster_url = info["poster"]

        log_ok("Extracted IMDb data")
        log(f"Title  : {title}")
        log(f"Content: {content}")
        log(f"Poster : {poster_url}")

        driver = driver_holder["driver"]
        poster = download_image_with_driver_cookies(driver, poster_url)
        final_image = create_side_by_side_image(poster, title)

        output_path = output_path_for(output_dir, title, content)
        log_step(f"Saving final PNG: {output_path}")
        final_image.save(output_path, "PNG", optimize=True)

        log_ok(f"Saved final PNG: {output_path}")
        log(f"[{index}/{total}] DONE")
        return True, output_path

    except KeyboardInterrupt:
        raise

    except Exception as exc:
        log_fail(url)
        log(f"Reason: {exc}")
        log(f"[{index}/{total}] FAILED")
        return False, None


def tierlistmaker_output_dir(source_folder: Path) -> Path:
    return source_folder.with_name(source_folder.name + "_tiermaker_small")


def run_tierlistmaker_limit(source_folder: Path) -> Path:
    script_path = Path(__file__).with_name(TIERLISTMAKER_SCRIPT).resolve()
    if not script_path.exists():
        raise FileNotFoundError(f"TierMaker script not found: {script_path}")

    command = [sys.executable, str(script_path), str(source_folder)]
    log("")
    log_step(f"Running {TIERLISTMAKER_SCRIPT}")
    log(f"Command: {subprocess.list2cmdline(command)}")

    completed = subprocess.run(command, cwd=str(script_path.parent))
    if completed.returncode != 0:
        raise RuntimeError(f"{TIERLISTMAKER_SCRIPT} exited with code {completed.returncode}")

    report_path = tierlistmaker_output_dir(source_folder) / "compression_report.csv"
    log_ok(f"{TIERLISTMAKER_SCRIPT} finished")
    return report_path


def read_tierlistmaker_processed_names(report_path: Path) -> Set[str]:
    if not report_path.exists():
        log_warn(f"TierMaker report not found: {report_path}")
        return set()

    processed_names = set()
    with report_path.open("r", newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            original_file = clean_text(row.get("original_file", ""))
            if original_file:
                processed_names.add(original_file)

    log_ok(f"TierMaker report lists {len(processed_names)} processed source images")
    return processed_names


def unique_urls_by_done_key(urls: List[str]) -> List[str]:
    unique = []
    seen = set()

    for url in urls:
        key = url_done_key(url)
        if key in seen:
            continue
        seen.add(key)
        unique.append(url)

    return unique


def append_done_then_remove_from_input(input_path: Path, done_path: Path, urls: List[str]) -> Tuple[int, int]:
    urls = unique_urls_by_done_key(urls)
    if not urls:
        log_warn("No processed URLs to move into done_imdb.txt")
        return 0, 0

    existing_done_keys = read_done_keys(done_path)
    urls_to_append = [url for url in urls if url_done_key(url) not in existing_done_keys]

    if urls_to_append:
        existing_text = done_path.read_text(encoding="utf-8", errors="replace") if done_path.exists() else ""
        needs_separator = bool(existing_text and not existing_text.endswith(("\n", "\r")))

        with done_path.open("a", encoding="utf-8") as f:
            if needs_separator:
                f.write("\n")
            for url in urls_to_append:
                f.write(f"{url}\n")

        log_ok(f"Added {len(urls_to_append)} URLs to {done_path}")
    else:
        log_ok("All processed URLs were already present in done_imdb.txt")

    move_keys = {url_done_key(url) for url in urls}
    original_lines = input_path.read_text(encoding="utf-8", errors="replace").splitlines()
    kept_lines = []
    removed = 0

    for line in original_lines:
        stripped = line.strip()
        if stripped.startswith("http") and url_done_key(stripped) in move_keys:
            removed += 1
            continue
        kept_lines.append(line)

    new_text = "\n".join(kept_lines)
    if new_text:
        new_text += "\n"
    input_path.write_text(new_text, encoding="utf-8")

    log_ok(f"Removed {removed} URLs from {input_path}")
    return len(urls_to_append), removed


def move_tierlistmaker_processed_urls(
    input_path: Path,
    done_path: Path,
    output_dir: Path,
    completed_outputs: List[Tuple[str, Path]],
) -> int:
    if not completed_outputs:
        log_warn(f"No new IMDb images were saved, so {TIERLISTMAKER_SCRIPT} was not run")
        return 0

    report_path = run_tierlistmaker_limit(output_dir)
    processed_names = read_tierlistmaker_processed_names(report_path)

    processed_urls = [
        url
        for url, output_path in completed_outputs
        if output_path.name in processed_names
    ]

    missing_count = len(completed_outputs) - len(processed_urls)
    if missing_count:
        log_warn(f"{missing_count} newly saved images did not appear in the TierMaker report")

    append_done_then_remove_from_input(input_path, done_path, processed_urls)
    return len(processed_urls)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "input_file",
        nargs="?",
        default=DEFAULT_INPUT_FILE,
        help="Text file containing one IMDb URL per line. Default: 90.txt",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help="Output folder. Default: imdb_output",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DELAY_SECONDS,
        help="Delay between pages. Default: 0.8",
    )

    args = parser.parse_args()

    log("IMDb content card generator started")
    log(f"Python executable: {sys.executable}")
    log(f"Working folder: {Path.cwd()}")

    input_path = Path(args.input_file).expanduser().resolve()
    if not input_path.exists():
        log_fail(f"Input file not found: {input_path}")
        return 1

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    done_path = input_path.with_name(DEFAULT_DONE_FILE)
    all_urls = read_urls(input_path)
    done_keys = read_done_keys(done_path)
    urls, skipped_done_count = filter_done_urls(all_urls, done_keys)

    log(f"Input file   : {input_path}")
    log(f"URLs found   : {len(all_urls)}")
    log(f"Already done : {skipped_done_count}")
    log(f"Pending URLs : {len(urls)}")
    log(f"Done file    : {done_path}")
    log(f"Output folder: {output_dir}")
    log(f"Delay        : {args.delay}s")

    if not urls:
        log_ok("No pending URLs found")
        return 0

    driver = None
    driver_holder = None
    ok_count = 0
    fail_count = 0
    tierlistmaker_failed = False
    completed_outputs: List[Tuple[str, Path]] = []

    try:
        driver, visible = get_ready_driver(urls[0])
        driver_holder = {"driver": driver, "visible": visible}

        log_ok("Browser setup complete")
        log(f"Browser visible mode: {visible}")

        for index, url in enumerate(urls, start=1):
            ok, output_path = process_one(driver_holder, url, output_dir, index, len(urls))

            if ok:
                ok_count += 1
                if output_path is not None:
                    completed_outputs.append((url, output_path))
            else:
                fail_count += 1

            log(f"Progress summary: successful={ok_count}, failed={fail_count}, remaining={len(urls) - index}")

            if index < len(urls) and args.delay > 0:
                log_step(f"Sleeping {args.delay}s before next URL")
                time.sleep(args.delay)

    except KeyboardInterrupt:
        log_warn("Stopped by user")

    finally:
        log_step("Cleaning up browser")
        closed_ids = set()

        for maybe_driver in [driver, driver_holder.get("driver") if driver_holder else None]:
            if maybe_driver is None:
                continue
            if id(maybe_driver) in closed_ids:
                continue
            closed_ids.add(id(maybe_driver))

            try:
                maybe_driver.get("about:blank")
                time.sleep(1)
                maybe_driver.quit()
                log_ok("Firefox driver closed")
            except Exception as exc:
                log_warn(f"Could not close Firefox cleanly: {exc}")

    try:
        moved_count = move_tierlistmaker_processed_urls(input_path, done_path, output_dir, completed_outputs)
        log_ok(f"URLs confirmed by TierMaker processing: {moved_count}")
    except Exception as exc:
        tierlistmaker_failed = True
        log_fail(f"Post-processing failed: {exc}")

    log("")
    log("Done.")
    log(f"Successful    : {ok_count}")
    log(f"Failed        : {fail_count}")
    log(f"TierMaker ok  : {not tierlistmaker_failed}")
    log(f"Output folder : {output_dir}")

    return 0 if fail_count == 0 and not tierlistmaker_failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
