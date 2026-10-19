import os
import re
import time
import json
import logging
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs, unquote, urlunparse

import requests
from requests.adapters import HTTPAdapter
from requests.exceptions import ConnectTimeout, ReadTimeout, Timeout, RequestException
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# ==================== Configuration ====================
BASE_URL = "https://kemono.cr"

SERVICE = "patreon"
USER_ID = "21871223"

ARTIST_PATH = f"/{SERVICE}/user/{USER_ID}"
DOWNLOAD_DIR = Path("downloads/byoru")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

MANIFEST_PATH = DOWNLOAD_DIR / "_download_manifest.json"
POST_URLS_PATH = DOWNLOAD_DIR / "all_post_urls.txt"

# Reuse all_post_urls.txt on next run so it skips collecting artist pages.
REUSE_POST_URLS_TXT = True

SKIP_MP4 = True
HEADLESS = True

PAGE_SIZE = 50
PAGE_DELAY = 0.75
POST_DELAY = 0.20
FILE_DELAY = 0.10

# Important:
# This version does NOT try n1/n2/n3/n4/n5/n6 alternate hosts.
# It tries only:
#   1. the exact full-size URL shown on the page
#   2. the exact thumbnail URL shown on the page / derived thumbnail URL
FULL_SIZE_CONNECT_TIMEOUT = 7
FULL_SIZE_READ_TIMEOUT = 20

THUMBNAIL_CONNECT_TIMEOUT = 7
THUMBNAIL_READ_TIMEOUT = 60

SKIP_EXISTING_FILENAMES = True

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# ==================== Sessions ====================
def get_page_session():
    session = requests.Session()

    retries = Retry(
        total=4,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "HEAD"],
        raise_on_status=False,
    )

    adapter = HTTPAdapter(
        max_retries=retries,
        pool_connections=20,
        pool_maxsize=20,
        pool_block=False,
    )

    session.mount("http://", adapter)
    session.mount("https://", adapter)

    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
    })

    return session


def get_file_session():
    session = requests.Session()

    no_retry = Retry(
        total=0,
        connect=0,
        read=0,
        redirect=0,
        status=0,
        raise_on_status=False,
    )

    adapter = HTTPAdapter(
        max_retries=no_retry,
        pool_connections=20,
        pool_maxsize=20,
        pool_block=False,
    )

    session.mount("http://", adapter)
    session.mount("https://", adapter)

    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Referer": BASE_URL + "/",
    })

    return session


page_session = get_page_session()
file_session = get_file_session()


# ==================== General helpers ====================
def fetch_text(url, timeout=45):
    try:
        resp = page_session.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp.text
    except KeyboardInterrupt:
        raise
    except Exception as e:
        logger.warning(f"Requests fetch failed: {url} -> {e}")
        return None


def fetch_json(url, timeout=30):
    try:
        resp = page_session.get(
            url,
            timeout=timeout,
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        return resp.json()
    except KeyboardInterrupt:
        raise
    except Exception:
        return None


def normalize_url(href):
    if not href:
        return None

    href = href.strip()

    if href.startswith("//"):
        return "https:" + href

    if href.startswith("/"):
        return urljoin(BASE_URL, href)

    if href.startswith("http://") or href.startswith("https://"):
        return href

    return urljoin(BASE_URL, href)


def clean_filename(name):
    if not name:
        return None

    # Convert URL-style plus signs into spaces too.
    name = str(name).replace("+", "%20")
    name = unquote(name).strip()

    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = name.strip(" .")

    if not name:
        return None

    if len(name) > 180:
        stem, ext = os.path.splitext(name)
        name = stem[:160] + ext

    return name


def filename_from_url(url):
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)

    if "f" in qs and qs["f"]:
        name = clean_filename(qs["f"][0])
        if name:
            return name

    name = clean_filename(os.path.basename(parsed.path))
    if name:
        return name

    return f"file_{abs(hash(url))}.bin"


def post_id_from_url(url):
    m = re.search(r"/post/(\d+)", url)
    return int(m.group(1)) if m else 0


def is_mp4_url(url):
    parsed = urlparse(url)
    return parsed.path.lower().endswith(".mp4")


def is_original_file_url(url):
    if not url:
        return False

    lower = url.lower()

    if "/thumbnail/" in lower:
        return False

    if "/data/" in lower:
        return True

    return bool(re.search(
        r"\.(jpg|jpeg|png|gif|webp|bmp|tif|tiff|avif|zip|rar|7z|pdf)(\?|#|$)",
        lower,
    ))


def is_thumbnail_url(url):
    if not url:
        return False

    lower = url.lower()

    return "/thumbnail/" in lower and bool(re.search(
        r"\.(jpg|jpeg|png|gif|webp|bmp|tif|tiff|avif)(\?|#|$)",
        lower,
    ))


def thumbnail_url_from_original(original_url):
    """
    Convert:
      https://n4.kemono.cr/data/aa/bb/file.jpg?f=name.jpg

    Into:
      https://img.kemono.cr/thumbnail/data/aa/bb/file.jpg
    """
    original_url = normalize_url(original_url)

    if not original_url:
        return None

    parsed = urlparse(original_url)

    if "/thumbnail/" in parsed.path:
        return original_url

    if "/data/" not in parsed.path:
        return None

    thumb_path = parsed.path.replace("/data/", "/thumbnail/data/", 1)

    new_parsed = parsed._replace(
        scheme="https",
        netloc="img.kemono.cr",
        path=thumb_path,
        query="",
        fragment="",
    )

    return urlunparse(new_parsed)


def load_manifest():
    if not MANIFEST_PATH.exists():
        return {}

    try:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_manifest(manifest):
    tmp_path = MANIFEST_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    tmp_path.replace(MANIFEST_PATH)


def manifest_record_is_done(manifest, file_url):
    """
    Skip already-downloaded URLs only when the manifest says it was downloaded
    and the file still exists with nonzero size.
    """
    old_record = manifest.get(file_url)

    if not old_record:
        return False

    old_filename = old_record.get("filename", "")

    if not old_filename:
        return False

    old_path = DOWNLOAD_DIR / old_filename

    return old_path.exists() and old_path.stat().st_size > 0


def write_list_file(filename, lines):
    path = DOWNLOAD_DIR / filename
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def save_all_post_urls(posts):
    lines = [p["url"] for p in posts]
    POST_URLS_PATH.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"Saved {len(lines)} post URLs to: {POST_URLS_PATH}")
    return POST_URLS_PATH


def load_posts_from_txt():
    if not REUSE_POST_URLS_TXT:
        return []

    if not POST_URLS_PATH.exists():
        return []

    lines = POST_URLS_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()

    seen = set()
    posts = []

    for line in lines:
        url = line.strip()

        if not url:
            continue

        if url in seen:
            continue

        if f"/{SERVICE}/user/{USER_ID}/post/" not in url:
            continue

        seen.add(url)

        posts.append({
            "url": url,
            "id": post_id_from_url(url),
            "title": "",
            "attachment_count": None,
            "from_txt": True,
        })

    if posts:
        logger.info(f"Loaded {len(posts)} post URLs from existing TXT: {POST_URLS_PATH}")

    return posts


def looks_like_html_error(content_type, local_path):
    if "text/html" in content_type.lower():
        return True

    try:
        if local_path.stat().st_size < 4096:
            data = local_path.read_bytes()[:512].lower()
            if b"<html" in data or b"<!doctype html" in data:
                return True
    except Exception:
        pass

    return False


# ==================== Playwright renderer ====================
class BrowserRenderer:
    def __init__(self, headless=True):
        self.headless = headless
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

    def __enter__(self):
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(headless=self.headless)

        self.context = self.browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )

        def block_heavy(route):
            resource_type = route.request.resource_type

            # Do not block scripts/fetch/xhr. Kemono needs those.
            # Images can be blocked because the HTML usually already contains thumbnail src/data-src.
            if resource_type in {"image", "media", "font", "stylesheet"}:
                route.abort()
            else:
                route.continue_()

        self.context.route("**/*", block_heavy)
        self.page = self.context.new_page()
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if self.context:
                self.context.close()
        finally:
            try:
                if self.browser:
                    self.browser.close()
            finally:
                if self.playwright:
                    self.playwright.stop()

    def render(self, url, wait_selector=None, timeout_ms=45000, settle_ms=500):
        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

            if wait_selector:
                try:
                    self.page.wait_for_selector(wait_selector, timeout=timeout_ms)
                except PlaywrightTimeout:
                    logger.warning(f"Timeout waiting for selector {wait_selector!r} on {url}")

            if settle_ms:
                self.page.wait_for_timeout(settle_ms)

            return self.page.content()

        except Exception as e:
            logger.warning(f"Playwright render failed: {url} -> {e}")
            return None


# ==================== Artist page parsing ====================
def parse_artist_posts(html):
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    posts = []

    for article in soup.select("article.post-card"):
        a_tag = article.find("a", href=True)

        if not a_tag:
            continue

        href = a_tag["href"]

        if f"/{SERVICE}/user/{USER_ID}/post/" not in href:
            continue

        post_url = urljoin(BASE_URL, href)
        post_id = post_id_from_url(post_url)

        title_tag = article.select_one(".post-card__header")
        title = title_tag.get_text(" ", strip=True) if title_tag else ""

        text = article.get_text(" ", strip=True)

        if re.search(r"\bNo attachments\b", text, re.IGNORECASE):
            attachment_count = 0
        else:
            m = re.search(r"(\d+)\s+attachments?", text, re.IGNORECASE)
            attachment_count = int(m.group(1)) if m else None

        posts.append({
            "url": post_url,
            "id": post_id,
            "title": title,
            "attachment_count": attachment_count,
            "from_txt": False,
        })

    return posts


def get_artist_page(offset, browser):
    url = urljoin(BASE_URL, ARTIST_PATH)

    if offset:
        url += f"?o={offset}"

    logger.info(f"Fetching artist page offset={offset}: {url}")

    html = fetch_text(url, timeout=30)
    posts = parse_artist_posts(html)

    if posts:
        return html, posts

    logger.info("Requests page had no post cards. Falling back to Playwright render...")
    html = browser.render(url, wait_selector="article.post-card", timeout_ms=45000)
    posts = parse_artist_posts(html)

    return html, posts


def collect_all_posts(browser):
    all_posts = {}
    offset = 0

    while True:
        html, posts = get_artist_page(offset, browser)

        if not posts:
            logger.info(f"No posts found at offset={offset}. Stopping pagination.")
            break

        new_count = 0

        for post in posts:
            if post["url"] not in all_posts:
                all_posts[post["url"]] = post
                new_count += 1

        logger.info(
            f"Found {len(posts)} posts on page, "
            f"{new_count} new, total {len(all_posts)}"
        )

        if new_count == 0:
            break

        if len(posts) < PAGE_SIZE:
            break

        offset += PAGE_SIZE
        time.sleep(PAGE_DELAY)

    final_posts = sorted(all_posts.values(), key=lambda p: p["id"], reverse=True)

    save_all_post_urls(final_posts)

    return final_posts


# ==================== Post file parsing ====================
def thumbnail_from_anchor(a):
    """
    Gets the actual visible page thumbnail from:
      <a href="full-size-url">
        <img data-src="//img.kemono.cr/thumbnail/data/..." src="...">
      </a>
    """
    if not a:
        return None

    img = a.find("img")

    if not img:
        return None

    return (
        img.get("data-src")
        or img.get("src")
        or img.get("data-lazy-src")
        or img.get("data-original")
    )


def add_file(files, seen_urls, href, name=None, thumbnail_href=None):
    url = normalize_url(href)

    if not url:
        return

    if not is_original_file_url(url):
        return

    if url in seen_urls:
        return

    seen_urls.add(url)

    thumbnail_url = normalize_url(thumbnail_href) if thumbnail_href else None

    if not thumbnail_url:
        thumbnail_url = thumbnail_url_from_original(url)

    files.append({
        "url": url,
        "name": clean_filename(name) or filename_from_url(url),
        "thumbnail_url": thumbnail_url,
    })


def extract_file_links_from_html(post_html):
    files = []
    seen_urls = set()

    if not post_html:
        return files

    soup = BeautifulSoup(post_html, "html.parser")

    # Best path: file links inside page files area, with their actual visible thumbnail.
    for a in soup.select("div.post__files a.fileThumb[href]"):
        add_file(
            files,
            seen_urls,
            a.get("href"),
            a.get("download"),
            thumbnail_href=thumbnail_from_anchor(a),
        )

    # Other file links in post__files.
    for a in soup.select("div.post__files a[href]"):
        add_file(
            files,
            seen_urls,
            a.get("href"),
            a.get("download"),
            thumbnail_href=thumbnail_from_anchor(a),
        )

    # Download links outside post__files.
    for a in soup.select("a[download][href]"):
        add_file(
            files,
            seen_urls,
            a.get("href"),
            a.get("download"),
            thumbnail_href=thumbnail_from_anchor(a),
        )

    # Any direct /data/ link.
    for a in soup.select("a[href]"):
        href = a.get("href")

        if href and "/data/" in href.lower():
            add_file(
                files,
                seen_urls,
                href,
                a.get("download"),
                thumbnail_href=thumbnail_from_anchor(a),
            )

    # Regex fallback for full-size /data/ URLs.
    for m in re.finditer(
        r'https?://[^"\']*?kemono\.cr/data/[^"\']+',
        post_html,
        flags=re.IGNORECASE,
    ):
        raw_url = m.group(0).replace("&amp;", "&")
        add_file(files, seen_urls, raw_url, filename_from_url(raw_url))

    return files


def extract_file_links_from_api(post_url):
    post_id = post_id_from_url(post_url)

    if not post_id:
        return []

    api_url = f"{BASE_URL}/api/v1/{SERVICE}/user/{USER_ID}/post/{post_id}"
    data = fetch_json(api_url, timeout=30)

    files = []
    seen_urls = set()

    def walk(obj):
        if isinstance(obj, dict):
            path = obj.get("path")
            name = obj.get("name") or obj.get("filename")

            if path:
                add_file(files, seen_urls, path, name)

            for value in obj.values():
                walk(value)

        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    if data:
        walk(data)

    return files


def get_post_files(post_url, browser, expected_attachment_count=None):
    html = fetch_text(post_url, timeout=45)
    files = extract_file_links_from_html(html)

    if files:
        return files

    files = extract_file_links_from_api(post_url)

    if files:
        return files

    if expected_attachment_count is None or expected_attachment_count > 0:
        logger.info("  Requests/API found no files. Rendering post page with Playwright...")

        rendered_html = browser.render(
            post_url,
            wait_selector="h1.post__title, div.post__files, div.post__body",
            timeout_ms=45000,
            settle_ms=1000,
        )

        files = extract_file_links_from_html(rendered_html)

    return files


# ==================== Downloading ====================
def try_download_one(url, local_path, label, connect_timeout, read_timeout):
    if not url:
        return "failed", None, "empty URL"

    tmp_path = local_path.with_name(local_path.name + ".part")
    parsed = urlparse(url)
    host = parsed.netloc

    try:
        logger.info(f"  Trying {label}: {url}")

        with file_session.get(
            url,
            stream=True,
            timeout=(connect_timeout, read_timeout),
            allow_redirects=True,
        ) as resp:
            status = resp.status_code

            if status == 404:
                return "failed", None, f"404 on {url}"

            if status in {403, 429, 500, 502, 503, 504}:
                return "failed", None, f"HTTP {status} on {url}"

            resp.raise_for_status()

            content_type = resp.headers.get("content-type", "").lower()

            if SKIP_MP4 and "video/mp4" in content_type:
                return "mp4", url, None

            bytes_written = 0

            with open(tmp_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1024 * 128):
                    if chunk:
                        f.write(chunk)
                        bytes_written += len(chunk)

            if bytes_written == 0:
                try:
                    tmp_path.unlink()
                except Exception:
                    pass

                return "failed", None, f"0 bytes from {url}"

            if looks_like_html_error(content_type, tmp_path):
                try:
                    tmp_path.unlink()
                except Exception:
                    pass

                return "failed", None, f"HTML error page from {url}"

            tmp_path.replace(local_path)
            return "downloaded", url, None

    except KeyboardInterrupt:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass

        raise

    except (ConnectTimeout, ReadTimeout, Timeout) as e:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass

        return "failed", None, f"timeout on {host}: {e}"

    except RequestException as e:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass

        return "failed", None, f"request error on {host}: {e}"

    except Exception as e:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass

        return "failed", None, f"unexpected error on {host}: {e}"


def download_file(original_url, local_path, thumbnail_url=None):
    if SKIP_MP4 and is_mp4_url(original_url):
        return "mp4", None

    original_url = normalize_url(original_url)

    if not thumbnail_url:
        thumbnail_url = thumbnail_url_from_original(original_url)

    thumbnail_url = normalize_url(thumbnail_url) if thumbnail_url else None

    logger.info("  Trying real full-size file URL shown on page first...")

    result, downloaded_url, full_error = try_download_one(
        original_url,
        local_path,
        label="full-size",
        connect_timeout=FULL_SIZE_CONNECT_TIMEOUT,
        read_timeout=FULL_SIZE_READ_TIMEOUT,
    )

    if result == "downloaded":
        return "downloaded", downloaded_url

    if result == "mp4":
        return "mp4", downloaded_url

    logger.warning(f"  Full-size not available. Reason: {full_error}")

    if not thumbnail_url:
        logger.warning("  No thumbnail fallback URL available.")
        return "failed", None

    logger.warning(f"  Falling back to thumbnail: {thumbnail_url}")

    result, downloaded_url, thumb_error = try_download_one(
        thumbnail_url,
        local_path,
        label="thumbnail",
        connect_timeout=THUMBNAIL_CONNECT_TIMEOUT,
        read_timeout=THUMBNAIL_READ_TIMEOUT,
    )

    if result == "downloaded":
        return "downloaded_thumbnail", downloaded_url

    if result == "mp4":
        return "mp4", downloaded_url

    logger.warning(f"  Thumbnail fallback failed too. Reason: {thumb_error}")
    return "failed", None


# ==================== Main ====================
def main():
    manifest = load_manifest()

    skipped_mp4 = []
    failed_downloads = []
    thumbnail_fallbacks = []
    skipped_already_downloaded_urls = []

    total_downloaded = 0
    total_downloaded_thumbnail = 0
    total_skipped_existing = 0
    total_no_files = 0
    total_skipped_no_attachment_posts = 0
    total_skipped_already_downloaded_urls = 0

    posts = load_posts_from_txt()

    with BrowserRenderer(headless=HEADLESS) as browser:
        if posts:
            logger.info("Skipping artist-page collection because all_post_urls.txt already exists.")
            logger.info(f"Using post URL list: {POST_URLS_PATH}")
        else:
            logger.info("No reusable all_post_urls.txt found. Collecting all artist pages first...")
            posts = collect_all_posts(browser)

        if not posts:
            logger.error("No posts found. Exiting.")
            return

        all_post_urls_path = save_all_post_urls(posts)

        print("\n" + "=" * 60)
        print(f"Saved {len(posts)} post URLs before downloading:")
        print(all_post_urls_path)
        print("=" * 60 + "\n")

        logger.info(f"Total posts to process: {len(posts)}")
        logger.info("Starting downloads now...")

        for idx, post in enumerate(posts, 1):
            post_url = post["url"]
            title = post.get("title") or ""
            attachment_count = post.get("attachment_count")

            if attachment_count == 0:
                total_skipped_no_attachment_posts += 1
                logger.info(
                    f"Skipping no-attachment post {idx}/{len(posts)}: "
                    f"{post_url} | {title}"
                )
                continue

            logger.info(
                f"Processing post {idx}/{len(posts)}: "
                f"{post_url} | attachments={attachment_count} | {title}"
            )

            try:
                file_items = get_post_files(
                    post_url,
                    browser,
                    expected_attachment_count=attachment_count,
                )
            except KeyboardInterrupt:
                raise
            except Exception as e:
                logger.error(f"Unexpected error while parsing {post_url}: {e}")
                failed_downloads.append(f"{post_url} -> PARSE ERROR: {e}")
                continue

            if not file_items:
                total_no_files += 1
                logger.info("  No file links found.")
                time.sleep(POST_DELAY)
                continue

            logger.info(f"  Found {len(file_items)} file(s).")

            for item in file_items:
                file_url = item["url"]
                thumbnail_url = item.get("thumbnail_url") or thumbnail_url_from_original(file_url)

                filename = item["name"] or filename_from_url(file_url)
                filename = clean_filename(filename) or filename_from_url(file_url)

                if manifest_record_is_done(manifest, file_url):
                    total_skipped_already_downloaded_urls += 1
                    total_skipped_existing += 1
                    skipped_already_downloaded_urls.append(file_url)
                    logger.info(f"  Skipped already-downloaded URL: {file_url}")
                    continue

                if SKIP_MP4 and is_mp4_url(file_url):
                    skipped_mp4.append(file_url)
                    logger.info(f"  Skipped MP4: {file_url}")
                    continue

                local_path = DOWNLOAD_DIR / filename

                if SKIP_EXISTING_FILENAMES and local_path.exists() and local_path.stat().st_size > 0:
                    total_skipped_existing += 1

                    manifest[file_url] = {
                        "filename": local_path.name,
                        "post_url": post_url,
                        "url": file_url,
                        "thumbnail_url": thumbnail_url,
                        "downloaded_url": file_url,
                        "download_kind": "existing_filename",
                    }

                    save_manifest(manifest)
                    logger.info(f"  Skipped existing filename: {local_path.name}")
                    continue

                logger.info(f"  Original URL: {file_url}")

                if thumbnail_url:
                    logger.info(f"  Thumbnail fallback URL: {thumbnail_url}")

                result, downloaded_url = download_file(
                    file_url,
                    local_path,
                    thumbnail_url=thumbnail_url,
                )

                if result in {"downloaded", "downloaded_thumbnail"}:
                    if result == "downloaded_thumbnail":
                        total_downloaded_thumbnail += 1
                        thumbnail_fallbacks.append(
                            f"{file_url} -> {downloaded_url} -> {local_path}"
                        )
                        logger.info(f"  Downloaded thumbnail fallback: {local_path.name}")
                    else:
                        total_downloaded += 1
                        logger.info(f"  Downloaded full-size: {local_path.name}")

                    manifest[file_url] = {
                        "filename": local_path.name,
                        "post_url": post_url,
                        "url": file_url,
                        "thumbnail_url": thumbnail_url,
                        "downloaded_url": downloaded_url or file_url,
                        "download_kind": (
                            "thumbnail_fallback"
                            if result == "downloaded_thumbnail"
                            else "full_size"
                        ),
                    }

                    save_manifest(manifest)

                elif result == "mp4":
                    skipped_mp4.append(file_url)
                    logger.info(f"  Skipped MP4: {file_url}")

                else:
                    failed_downloads.append(f"{file_url} -> {local_path}")
                    logger.warning(f"  Failed: {file_url}")

                time.sleep(FILE_DELAY)

            time.sleep(POST_DELAY)

    save_manifest(manifest)

    skipped_mp4_path = write_list_file("skipped_mp4_urls.txt", skipped_mp4)
    failed_path = write_list_file("failed_downloads.txt", failed_downloads)
    thumbnail_fallbacks_path = write_list_file("thumbnail_fallback_downloads.txt", thumbnail_fallbacks)
    skipped_already_downloaded_path = write_list_file(
        "skipped_already_downloaded_urls.txt",
        skipped_already_downloaded_urls,
    )

    print("\n" + "=" * 60)
    print(f"Total posts found: {len(posts)}")
    print(f"All post URLs saved to: {POST_URLS_PATH}")
    print(f"No-attachment posts skipped: {total_skipped_no_attachment_posts}")
    print(f"Full-size files downloaded: {total_downloaded}")
    print(f"Thumbnail fallback files downloaded: {total_downloaded_thumbnail}")
    print(f"Already-downloaded URLs skipped: {total_skipped_already_downloaded_urls}")
    print(f"Files already existed / already recorded: {total_skipped_existing}")
    print(f"Attachment posts with no file links after render: {total_no_files}")
    print(f"Files still failed: {len(failed_downloads)}")
    print(f"MP4 files skipped: {len(skipped_mp4)}")
    print(f"Skipped MP4 list: {skipped_mp4_path}")
    print(f"Failed downloads list: {failed_path}")
    print(f"Thumbnail fallback list: {thumbnail_fallbacks_path}")
    print(f"Skipped already-downloaded URLs list: {skipped_already_downloaded_path}")
    print(f"Manifest: {MANIFEST_PATH}")

    if skipped_mp4:
        print("\n=== Skipped MP4 URLs ===")
        for url in skipped_mp4:
            print(url)

    if thumbnail_fallbacks:
        print("\n=== Thumbnail Fallback Downloads ===")
        for line in thumbnail_fallbacks:
            print(line)

    if skipped_already_downloaded_urls:
        print("\n=== Skipped Already-Downloaded URLs ===")
        for url in skipped_already_downloaded_urls:
            print(url)

    if failed_downloads:
        print("\n=== Failed Downloads ===")
        for line in failed_downloads:
            print(line)


if __name__ == "__main__":
    main()