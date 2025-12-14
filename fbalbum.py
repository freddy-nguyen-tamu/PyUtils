# fb_album_downloader_pause_login.py
#
# Requires:
#   pip install playwright requests
#   playwright install
#
# Usage:
#   1) Set START_URL below
#   2) Run: python fb_album_downloader_pause_login.py
#   3) Log in manually in the opened browser
#   4) Come back to terminal and press ENTER to start downloading

import os
import re
import time
from urllib.parse import urlparse, parse_qs

import requests
from playwright.sync_api import sync_playwright


START_URL = ""
OUT_DIR = "fb_photos"
USER_DATA_DIR = "playwright_fb_profile"  # persistent profile folder to keeps login


def wait_for_manual_login():
    print("\n" + "=" * 70)
    print("A browser window is open.")
    print("Please log in to Facebook manually (if prompted).")
    print("Make sure you can see the photo normally (not a login wall).")
    input("When you're fully logged in and ready, press ENTER here to start...")
    print("=" * 70 + "\n")


def get_fbid_and_set(url: str):
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)

    fbid = qs.get("fbid", [None])[0]
    set_ = qs.get("set", [None])[0]

    if not fbid:
        m = re.search(r"[?&]fbid=(\d+)", url)
        if m:
            fbid = m.group(1)

    return fbid, set_


def is_start_loop_url(url: str, start_fbid: str, start_set: str | None) -> bool:
    """
    Stop condition per your request:
    Stop when we reach the canonical:
      https://www.facebook.com/photo/?fbid=<start>&set=<start_set>
    again.
    """
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")

    if path != "/photo":
        return False

    qs = parse_qs(parsed.query)
    fbid = qs.get("fbid", [None])[0]
    set_ = qs.get("set", [None])[0]

    if fbid != start_fbid:
        return False
    if start_set is not None and set_ != start_set:
        return False

    return True


def pick_best_image_src(page) -> str:
    """
    Heuristic: pick the largest visible <img>.
    Facebook viewer usually includes a big image; we prefer large natural size.
    """
    imgs = page.locator("img:visible")
    count = imgs.count()
    if count == 0:
        return ""

    best_src = ""
    best_area = 0

    # scan up to 40 visible imgs; tweak if needed
    for i in range(min(count, 40)):
        img = imgs.nth(i)
        try:
            info = img.evaluate(
                """(el) => ({
                    src: el.currentSrc || el.src || "",
                    w: el.naturalWidth || 0,
                    h: el.naturalHeight || 0
                })"""
            )
        except Exception:
            continue

        src = (info.get("src") or "").strip()
        w = int(info.get("w") or 0)
        h = int(info.get("h") or 0)
        area = w * h

        # Avoid obvious tiny UI icons
        if not src or area < 50_000:
            continue

        # Prefer FB CDN images; still allow others if large
        if area > best_area and ("scontent" in src or area > 250_000):
            best_area = area
            best_src = src

    return best_src


def download_image(url: str, out_path: str, cookies: list):
    """
    Download image using requests with cookies copied from Playwright context.
    Helps when the image requires authentication.
    """
    session = requests.Session()
    for c in cookies:
        name = c.get("name")
        value = c.get("value")
        domain = c.get("domain")
        if name and value and domain:
            session.cookies.set(name, value, domain=domain)

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://www.facebook.com/",
    }

    r = session.get(url, headers=headers, stream=True, timeout=60)
    r.raise_for_status()

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=1024 * 256):
            if chunk:
                f.write(chunk)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    with sync_playwright() as p:
        # Persistent context keeps login between runs
        context = p.chromium.launch_persistent_context(
            USER_DATA_DIR,
            headless=False,
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()

        # Go to the starting photo link
        page.goto(START_URL, wait_until="domcontentloaded")
        time.sleep(2)

        # HARD PAUSE until you confirm login is complete
        wait_for_manual_login()

        # Light stabilization after login
        time.sleep(2)

        start_url_now = page.url
        start_fbid, start_set = get_fbid_and_set(start_url_now)
        if not start_fbid:
            print("❌ Could not extract starting fbid from URL:", start_url_now)
            context.close()
            return

        print("Start URL :", start_url_now)
        print("Start FBID:", start_fbid)
        print("Start SET :", start_set)
        print()

        visited_fbids = set()
        saved_count = 0

        # To avoid an immediate stop if the first page is already the canonical /photo/? form,
        # we only stop when we've progressed at least 2 unique fbids and then see the start again.
        while True:
            current_url = page.url
            cur_fbid, _ = get_fbid_and_set(current_url)

            # Try to find the displayed photo
            img_src = pick_best_image_src(page)

            if cur_fbid and img_src and cur_fbid not in visited_fbids:
                filename = f"{saved_count + 1:04d}_fbid_{cur_fbid}.jpg"
                out_path = os.path.join(OUT_DIR, filename)

                try:
                    cookies = context.cookies()
                    download_image(img_src, out_path, cookies)
                    visited_fbids.add(cur_fbid)
                    saved_count += 1
                    print(f"[{saved_count}] Saved: {out_path}")
                except Exception as e:
                    print("Download failed:", e)
            else:
                # If you see repeated skips, FB may have changed the viewer layout.
                print("Skipped (no image found or already saved). URL:", current_url)

            # Stop condition: looped back to the starting canonical URL again
            if len(visited_fbids) >= 2 and is_start_loop_url(current_url, start_fbid, start_set):
                print("\nDetected loop back to the starting canonical URL. Stopping.")
                break

            # Move to next (older) photo
            # Ensure viewer focus
            try:
                page.mouse.click(640, 400)
            except Exception:
                pass

            page.keyboard.press("ArrowRight")
            page.wait_for_timeout(1200)

        print(f"\nDone. Total saved: {saved_count}")
        context.close()


if __name__ == "__main__":
    main()
