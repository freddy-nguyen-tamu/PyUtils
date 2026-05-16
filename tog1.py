import re
import time
import requests
from pathlib import Path
from playwright.sync_api import sync_playwright

# ---------- CONFIGURATION ----------
START_URL = "https://www.webtoons.com/en/fantasy/tower-of-god/season-3-ep-235-season-3-finale/viewer?title_no=95&episode_no=653"

# Put the Season 3 episode numbers you want here
TARGET_SEASON_EPS = {235}

DOWNLOAD_DIR = Path("./tower_of_god_images")
DOWNLOAD_DIR.mkdir(exist_ok=True)


# ---------- HELPER FUNCTIONS ----------

def dismiss_cookie_banner(page):
    """Try to close cookie consent popups."""
    selectors = [
        "button:has-text('Accept All')",
        "button:has-text('Allow All')",
        "button:has-text('I Agree')",
        "button:has-text('Accept')",
        "button[aria-label='Close']",
        ".cookie_banner button",
        "#onetrust-accept-btn-handler",
    ]

    for sel in selectors:
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click()
                page.wait_for_timeout(800)
                print("Cookie banner dismissed.")
                return
        except:
            continue


def get_current_season_ep(page):
    """
    Parses the Season 3 episode number from Webtoon URLs.

    Handles both cases:

    Normal:
    /season-3-ep-235/viewer

    Latest/finale/special slug:
    /season-3-ep-235-season-3-finale/viewer
    /season-3-ep-235-something-else/viewer
    """
    url = page.url

    # Match:
    # /season-3-ep-235/
    # /season-3-ep-235-season-3-finale/
    # /season-3-ep-235-anything/
    match = re.search(r"/season-3-ep-(\d+)(?=[-/])", url, re.IGNORECASE)
    if match:
        return int(match.group(1))

    # Backup parser from visible page text, in case Webtoon changes URL style later
    try:
        text = page.evaluate("document.body.innerText")
        match = re.search(r"Season\s*3\s*Ep\.?\s*(\d+)", text, re.IGNORECASE)
        if match:
            return int(match.group(1))
    except:
        pass

    return None


def scroll_until_all_loaded(page):
    """
    Slowly scroll step-by-step from top to bottom to trigger lazy-loading.
    """
    page.wait_for_selector("#_imageList", state="attached", timeout=10000)

    viewport = page.viewport_size
    if viewport:
        scroll_step = viewport["height"]
    else:
        scroll_step = 900

    current_scroll = 0
    page_height = page.evaluate("document.body.scrollHeight")

    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(500)

    while current_scroll < page_height:
        current_scroll += scroll_step
        page.evaluate(f"window.scrollTo(0, {current_scroll})")
        page.wait_for_timeout(1500)
        page_height = page.evaluate("document.body.scrollHeight")

    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_timeout(2000)

    page.wait_for_function("""
        () => {
            const imgs = document.querySelectorAll('#_imageList img._images');
            return imgs.length > 0 && Array.from(imgs).every(img => img.src && !img.src.startsWith('data:'));
        }
    """, timeout=30000)


def get_image_urls(page):
    urls = page.evaluate("""
        () => {
            const imgs = document.querySelectorAll('#_imageList img._images');
            return Array.from(imgs).map(img => img.src);
        }
    """)

    return [u for u in urls if u and not u.startswith("data:")]


def create_authenticated_session(page):
    sess = requests.Session()

    for cookie in page.context.cookies():
        sess.cookies.set(
            name=cookie["name"],
            value=cookie["value"],
            domain=cookie["domain"],
            path=cookie["path"],
            secure=cookie.get("secure", False),
            rest={"HttpOnly": cookie.get("httpOnly", False)},
            expires=cookie.get("expires", None)
        )

    sess.headers.update({
        "User-Agent": page.evaluate("navigator.userAgent"),
        "Referer": page.url,
    })

    return sess


def download_images(urls, folder: Path, session):
    folder.mkdir(parents=True, exist_ok=True)

    for i, url in enumerate(urls, start=1):
        ext = url.split(".")[-1].split("?")[0].lower()

        if ext not in ("jpg", "jpeg", "png", "webp", "gif"):
            ext = "jpg"

        filename = folder / f"{i:03d}.{ext}"

        if filename.exists() and filename.stat().st_size > 0:
            print(f"  Already exists, skipping {filename.name}")
            continue

        try:
            resp = session.get(url, timeout=30)
            resp.raise_for_status()
            filename.write_bytes(resp.content)
            print(f"  Saved {filename.name}")
        except Exception as e:
            print(f"  Failed to download {url}: {e}")


def click_nav_button(page, direction):
    """
    Click the Previous or Next episode button via JavaScript.
    Returns True if the button existed and was clicked, False otherwise.
    """
    selector = "a.pg_prev._prevEpisode" if direction == "prev" else "a.pg_next._nextEpisode"
    name = "Previous Episode" if direction == "prev" else "Next Episode"

    exists = page.evaluate(f"!!document.querySelector('{selector}')")

    if not exists:
        print(f"    {name} button not found.")
        return False

    clicked = page.evaluate(f"""
        () => {{
            const btn = document.querySelector('{selector}');
            if (!btn) return false;
            btn.scrollIntoView({{ block: 'center', inline: 'center' }});
            btn.click();
            return true;
        }}
    """)

    if clicked:
        print(f"    Clicked {name} button via JS.")
    else:
        print(f"    Failed to click {name} button via JS.")

    return clicked


def wait_for_episode_change(page, old_url):
    """
    Waits until the URL changes after clicking Previous/Next.
    """
    try:
        page.wait_for_function(
            "(oldUrl) => window.location.href !== oldUrl",
            arg=old_url,
            timeout=15000
        )
    except:
        print("    URL did not change quickly, continuing anyway...")

    page.wait_for_selector("#_imageList", state="attached", timeout=15000)
    page.wait_for_timeout(1500)


# ---------- MAIN AUTOMATION ----------

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()

        print("Navigating to start episode...")
        page.goto(START_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_selector("#_imageList", state="attached", timeout=15000)
        print("Viewer loaded successfully.")

        dismiss_cookie_banner(page)

        collected = set()
        pending = TARGET_SEASON_EPS.copy()

        while pending:
            current_ep = get_current_season_ep(page)

            if current_ep is None:
                print(f"Could not parse episode number from URL:")
                print(page.url)
                print("Stopping.")
                break

            print(f"\n=== Current episode: season 3 ep {current_ep} ===")

            if current_ep in pending:
                folder = DOWNLOAD_DIR / f"S3_Ep_{current_ep}"

                if folder.exists() and any(folder.iterdir()):
                    print(f"Episode {current_ep} folder already exists and is not empty – skipping.")
                    pending.discard(current_ep)
                    collected.add(current_ep)
                else:
                    print("Slow-scrolling to load all panels...")
                    scroll_until_all_loaded(page)

                    img_urls = get_image_urls(page)

                    if not img_urls:
                        print("No comic panels found – something went wrong.")
                    else:
                        session = create_authenticated_session(page)
                        print(f"Downloading {len(img_urls)} panel images to {folder}...")
                        download_images(img_urls, folder, session)
                        collected.add(current_ep)

                pending.discard(current_ep)

            else:
                print("Skipping – not in target list.")
                pending.discard(current_ep)

            if not pending:
                print("\nAll target episodes collected!")
                break

            old_url = page.url

            # Navigate toward the closest remaining target episode
            if any(ep < current_ep for ep in pending):
                success = click_nav_button(page, "prev")
                if not success:
                    print("Cannot go further back. Stopping.")
                    break
            else:
                success = click_nav_button(page, "next")
                if not success:
                    print("Cannot go further forward. Stopping.")
                    break

            print("    Waiting for new episode to load...")
            wait_for_episode_change(page, old_url)
            dismiss_cookie_banner(page)

        browser.close()
        print(f"Done. Downloaded episodes: {sorted(collected)}")


if __name__ == "__main__":
    main()