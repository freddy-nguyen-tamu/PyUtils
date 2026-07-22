# fb_album_downloader_firefox_cookies.py
#
# Requires:
#   pip install selenium requests browser-cookie3
#
# Usage:
#   1) Make sure you are already logged into Facebook in normal Firefox, OR
#      let this script open visible Firefox and log in there once.
#   2) Set START_URL below
#   3) Run:
#        python fb_album_downloader_firefox_cookies.py
#
# Behavior:
#   - Uses installed Firefox through Selenium.
#   - Reuses a persistent Selenium Firefox profile folder.
#   - First tries headless using the saved Selenium profile.
#   - If not logged in, tries importing cookies from normal Firefox.
#   - If still not logged in, opens visible Firefox for manual login.
#   - After manual login, future runs should work headless.

import os
import re
import time
from types import SimpleNamespace
from urllib.parse import urlparse, parse_qs

import requests
import browser_cookie3

from selenium import webdriver
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.firefox.options import Options


# START_URL = ""
# START_URL = "https://www.facebook.com/photo/?fbid=35422885987298672&set=pb.100000121598043.-2207520000"
# START_URL = "https://www.facebook.com/photo.php?fbid=1534937381333570&set=pb.100044519675957.-2207520000&type=3"
# START_URL = "https://www.facebook.com/photo/?fbid=834567576715674&set=pb.100044519675957.-2207520000"
# START_URL = "https://www.facebook.com/photo.php?fbid=2057196528551210&set=pb.100027826494002.-2207520000&type=3"
# START_URL = "https://www.facebook.com/photo/?fbid=2616326998381328&set=g.282008595742131"
# START_URL = "https://www.facebook.com/photo.php?fbid=875480885505867&set=pb.100091318902138.-2207520000&type=3"
# START_URL = "https://www.facebook.com/photo.php?fbid=3118425821674301&set=pb.100005207405108.-2207520000&type=3"
# START_URL = "https://www.facebook.com/photo.php?fbid=1240071477683460&set=pb.100050418582886.-2207520000&type=3"
# START_URL = "https://www.facebook.com/photo.php?fbid=1019245767517376&set=pb.100082958877264.-2207520000&type=3"
# START_URL = "https://www.facebook.com/photo.php?fbid=122124195411177269&set=pb.61585318081733.-2207520000&type=3"
# START_URL = "https://www.facebook.com/photo.php?fbid=1316297517345506&set=pb.100068958100534.-2207520000&type=3"
# START_URL = "https://www.facebook.com/photo/?fbid=122174818106691405&set=g.469040726197914"
# START_URL = "https://www.facebook.com/photo.php?fbid=1514271553672347&set=pb.100052685491871.-2207520000&type=3"
# START_URL = "https://www.facebook.com/photo.php?fbid=2085505352012211&set=pb.100016582998481.-2207520000&type=3"
# START_URL = "https://www.facebook.com/photo.php?fbid=27693852626923966&set=pb.100001778033019.-2207520000&type=3"
# START_URL = "https://www.facebook.com/photo.php?fbid=10113942486931311&set=pb.9010646.-2207520000&type=3"
START_URL = "https://www.facebook.com/photo.php?fbid=122291538656165504&set=pb.61554965143709.-2207520000&type=3"

OUT_DIR = "fb_photos"

# This folder stores the Selenium Firefox login session.
# Do not delete it after logging in, or you will have to log in again.
SELENIUM_PROFILE_DIR = "selenium_fb_profile"

# Safety cap. Set to None for no cap.
MAX_PHOTOS = None

# Optional: if browser_cookie3 picks the wrong Firefox profile, set this to
# the full path of cookies.sqlite from your real Firefox profile.
#
# Windows example:
# FIREFOX_COOKIES_SQLITE = r"C:\Users\YOURNAME\AppData\Roaming\Mozilla\Firefox\Profiles\xxxx.default-release\cookies.sqlite"
#
FIREFOX_COOKIES_SQLITE = None


def load_firefox_facebook_cookies(required=False):
    """
    Try to read Facebook cookies from your normal Firefox profile.

    This is only used as a helper. The main long-term login storage is
    SELENIUM_PROFILE_DIR.
    """
    print("Reading Facebook cookies from normal Firefox...")

    tries = []

    if FIREFOX_COOKIES_SQLITE:
        tries.append(
            (
                "explicit cookies.sqlite",
                lambda: browser_cookie3.firefox(cookie_file=FIREFOX_COOKIES_SQLITE),
            )
        )

    tries.append(
        (
            "domain .facebook.com",
            lambda: browser_cookie3.firefox(domain_name=".facebook.com"),
        )
    )

    tries.append(
        (
            "domain facebook.com",
            lambda: browser_cookie3.firefox(domain_name="facebook.com"),
        )
    )

    tries.append(
        (
            "all Firefox cookies then filter",
            lambda: browser_cookie3.firefox(),
        )
    )

    best = []

    for label, fn in tries:
        try:
            cj = fn()
            cookies = []

            for c in cj:
                domain = (c.domain or "").lower()
                if "facebook.com" in domain or "fbcdn.net" in domain:
                    cookies.append(c)

            names = sorted(set(c.name for c in cookies))
            print(f"{label}: {len(cookies)} cookies")
            print("cookie names:", names)

            if len(cookies) > len(best):
                best = cookies

            if "c_user" in names and "xs" in names:
                print("Found logged-in Facebook cookies in normal Firefox.")
                return cookies

        except Exception as e:
            print(f"{label} failed:", e)

    names = sorted(set(c.name for c in best))
    print("Best Firefox cookie set:", len(best), "cookies")
    print("Best Firefox cookie names:", names)

    if required and ("c_user" not in names or "xs" not in names):
        raise RuntimeError(
            "Could not find logged-in Facebook cookies from normal Firefox. "
            "You may be using the wrong Firefox profile, private browsing, "
            "a container, or you are not logged into Facebook in normal Firefox."
        )

    return best


def get_fbid_and_set(url):
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)

    fbid = qs.get("fbid", [None])[0]
    set_ = qs.get("set", [None])[0]

    if not fbid:
        m = re.search(r"[?&]fbid=(\d+)", url)
        if m:
            fbid = m.group(1)

    return fbid, set_


def is_start_loop_url(url, start_fbid, start_set):
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


def make_selenium_cookie_variants(c):
    """
    Selenium is picky about cookie domains.

    We try:
      1) the original cookie domain
      2) .facebook.com
      3) no explicit domain, for __Host-* cookies
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
    d2["domain"] = ".facebook.com"
    variants.append(d2)

    d3 = dict(base)
    variants.append(d3)

    return variants


def build_driver(headless):
    options = Options()

    profile_path = os.path.abspath(SELENIUM_PROFILE_DIR)
    os.makedirs(profile_path, exist_ok=True)

    # Reuse the same Selenium Firefox profile every run.
    # This is what saves your login after the first manual login.
    options.add_argument("-profile")
    options.add_argument(profile_path)

    if headless:
        options.add_argument("-headless")

    # Make headless layout more stable.
    options.add_argument("--width=1280")
    options.add_argument("--height=900")

    # Reduce first-run/default-browser nags.
    options.set_preference("browser.shell.checkDefaultBrowser", False)
    options.set_preference("browser.startup.homepage_override.mstone", "ignore")
    options.set_preference("startup.homepage_welcome_url", "about:blank")
    options.set_preference("startup.homepage_welcome_url.additional", "about:blank")

    driver = webdriver.Firefox(options=options)

    try:
        driver.set_window_size(1280, 900)
    except Exception:
        pass

    return driver


def inject_cookies_into_firefox(driver, cookies):
    """
    Opens facebook.com, injects cookies into the Selenium Firefox session,
    then reloads Facebook.
    """
    if not cookies:
        print("No external Firefox cookies to inject.")
        return

    driver.get("https://www.facebook.com/")
    time.sleep(2)

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

    print(f"Injected cookies into Selenium Firefox: added={added}, failed={failed}")

    driver.get("https://www.facebook.com/")
    time.sleep(2)


def selenium_cookies_to_requests_cookies(driver):
    """
    Convert current Selenium cookies to a requests-friendly cookie list.
    """
    cookies = []

    for c in driver.get_cookies():
        name = c.get("name")
        value = c.get("value")
        domain = c.get("domain")
        path = c.get("path") or "/"

        if not name or not value:
            continue

        if not domain:
            domain = ".facebook.com"

        cookies.append(
            SimpleNamespace(
                name=name,
                value=value,
                domain=domain,
                path=path,
            )
        )

    return cookies


def has_facebook_login_cookie(driver):
    """
    Checks whether Selenium Firefox has the important Facebook login cookies.
    """
    names = set()

    try:
        for c in driver.get_cookies():
            names.add(c.get("name"))
    except Exception:
        pass

    return "c_user" in names and "xs" in names


def is_login_wall(driver):
    """
    Rough check for whether Facebook is showing a login wall.
    """
    try:
        url = driver.current_url.lower()
    except Exception:
        url = ""

    try:
        title = (driver.title or "").lower()
    except Exception:
        title = ""

    try:
        html = driver.page_source.lower()
    except Exception:
        html = ""

    if "facebook.com/login" in url:
        return True

    if "checkpoint" in url:
        return True

    if "log in" in title and "facebook" in title:
        return True

    bad_signs = [
        "log into facebook",
        "log in to facebook",
        "you must log in",
        "create new account",
        "forgot password",
    ]

    bad_count = sum(1 for s in bad_signs if s in html)

    # If we have c_user/xs cookies, do not overreact to random login text
    # that can appear in the page footer.
    if has_facebook_login_cookie(driver) and "facebook.com/login" not in url:
        return False

    return bad_count >= 2


def pick_best_image_src(driver):
    """
    Heuristic: pick the largest visible <img>.
    Facebook viewer usually includes a big image; we prefer large natural size.
    """
    script = r"""
        const imgs = Array.from(document.querySelectorAll("img"));

        let bestSrc = "";
        let bestArea = 0;

        for (const el of imgs.slice(0, 120)) {
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);

            const visible =
                rect.width > 0 &&
                rect.height > 0 &&
                style.display !== "none" &&
                style.visibility !== "hidden" &&
                style.opacity !== "0";

            if (!visible) continue;

            const src = (el.currentSrc || el.src || "").trim();
            const w = el.naturalWidth || 0;
            const h = el.naturalHeight || 0;
            const area = w * h;

            if (!src || area < 50000) continue;

            // Avoid UI icons, profile thumbnails, etc.
            if (area > bestArea && (src.includes("scontent") || src.includes("fbcdn") || area > 250000)) {
                bestArea = area;
                bestSrc = src;
            }
        }

        return bestSrc;
    """

    try:
        return driver.execute_script(script) or ""
    except Exception:
        return ""


def download_image(url, out_path, cookies):
    """
    Download image using requests with cookies copied from Selenium Firefox.
    """
    session = requests.Session()

    for c in cookies:
        try:
            session.cookies.set(
                c.name,
                c.value,
                domain=c.domain,
                path=c.path or "/",
            )
        except Exception:
            pass

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) "
            "Gecko/20100101 Firefox/126.0"
        ),
        "Referer": "https://www.facebook.com/",
        "Accept": "image/avif,image/webp,image/png,image/svg+xml,image/*,*/*;q=0.8",
    }

    r = session.get(url, headers=headers, stream=True, timeout=60)
    r.raise_for_status()

    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    with open(out_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=1024 * 256):
            if chunk:
                f.write(chunk)


def open_start_and_check(driver):
    driver.get(START_URL)
    time.sleep(3)

    login_wall = is_login_wall(driver)
    logged_cookie = has_facebook_login_cookie(driver)

    print("Current URL:", driver.current_url)
    print("Has c_user/xs cookies:", logged_cookie)
    print("Login wall detected:", login_wall)

    return not login_wall


def get_logged_in_driver():
    """
    Returns a Selenium Firefox driver that is logged in.

    Order:
      1) Try persistent Selenium profile headless.
      2) If failed, try importing normal Firefox cookies into that profile.
      3) If failed, open the same persistent Selenium profile visibly and let
         you log in manually.
    """
    firefox_cookies = []

    print()
    print("Trying saved Selenium Firefox profile in headless mode...")
    driver = build_driver(headless=True)

    try:
        if open_start_and_check(driver):
            print("Saved Selenium profile is already logged in. Continuing headless.")
            return driver

        print()
        print("Headless saved profile was not logged in.")
        print("Trying to import cookies from normal Firefox...")

        firefox_cookies = load_firefox_facebook_cookies(required=False)

        if firefox_cookies:
            inject_cookies_into_firefox(driver, firefox_cookies)

            if open_start_and_check(driver):
                print("Imported normal Firefox cookies worked. Continuing headless.")
                return driver

        print()
        print("Headless login still failed.")
        print("Closing headless browser and reopening visible Firefox...")

    except Exception as e:
        print("Headless login attempt had an error:", e)

    try:
        driver.quit()
    except Exception:
        pass

    time.sleep(1)

    driver = build_driver(headless=False)

    try:
        # Try imported Firefox cookies again in visible mode if available.
        if firefox_cookies:
            inject_cookies_into_firefox(driver, firefox_cookies)

        driver.get(START_URL)
        time.sleep(3)

        if is_login_wall(driver):
            print()
            print("=" * 70)
            print("Visible Firefox is open.")
            print("Log in to Facebook manually in that window.")
            print("Make sure the target photo is visible normally.")
            input("After the photo is visible, press ENTER here to continue...")
            print("=" * 70)
            print()

            # After login, reload START_URL just in case login redirected elsewhere.
            driver.get(START_URL)
            time.sleep(3)

        if is_login_wall(driver):
            print()
            print("Warning: Facebook still looks like a login wall.")
            print("The script will continue anyway, but it may not find/download photos.")
        else:
            print("Visible Selenium profile is logged in.")
            print("This login should be saved for future headless runs.")

        return driver

    except Exception:
        try:
            driver.quit()
        except Exception:
            pass
        raise


def press_next_photo(driver):
    """
    Move to next/older photo.
    """
    try:
        ActionChains(driver).move_by_offset(10, 10).click().perform()
    except Exception:
        pass

    try:
        ActionChains(driver).send_keys(Keys.ARROW_RIGHT).perform()
        return True
    except Exception:
        pass

    try:
        body = driver.find_element("tag name", "body")
        body.send_keys(Keys.ARROW_RIGHT)
        return True
    except Exception as e:
        print("Could not press ArrowRight:", e)
        return False


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    driver = None

    try:
        driver = get_logged_in_driver()

        active_download_cookies = selenium_cookies_to_requests_cookies(driver)
        print(f"Using {len(active_download_cookies)} cookies from Selenium Firefox.")

        start_url_now = driver.current_url
        start_fbid, start_set = get_fbid_and_set(start_url_now)

        if not start_fbid:
            print("Could not extract starting fbid from URL:", start_url_now)
            return

        print()
        print("Start URL :", start_url_now)
        print("Start FBID:", start_fbid)
        print("Start SET :", start_set)
        print()

        visited_fbids = set()
        saved_count = 0
        same_url_count = 0
        previous_url = None

        while True:
            current_url = driver.current_url
            cur_fbid, _ = get_fbid_and_set(current_url)

            if current_url == previous_url:
                same_url_count += 1
            else:
                same_url_count = 0

            previous_url = current_url

            img_src = pick_best_image_src(driver)

            if cur_fbid and img_src and cur_fbid not in visited_fbids:
                filename = f"{saved_count + 1:04d}_fbid_{cur_fbid}.jpg"
                out_path = os.path.join(OUT_DIR, filename)

                try:
                    # Refresh cookies occasionally in case Facebook updates them.
                    active_download_cookies = selenium_cookies_to_requests_cookies(driver)

                    download_image(img_src, out_path, active_download_cookies)
                    visited_fbids.add(cur_fbid)
                    saved_count += 1
                    print(f"[{saved_count}] Saved: {out_path}")
                except Exception as e:
                    print("Download failed:", e)
                    print("Image URL:", img_src)
            else:
                print("Skipped (no image found or already saved). URL:", current_url)

            if MAX_PHOTOS is not None and saved_count >= MAX_PHOTOS:
                print(f"\nReached MAX_PHOTOS={MAX_PHOTOS}. Stopping.")
                break

            if len(visited_fbids) >= 2 and is_start_loop_url(current_url, start_fbid, start_set):
                print("\nDetected loop back to the starting canonical URL. Stopping.")
                break

            if same_url_count >= 8:
                print()
                print("The URL has not changed after several attempts.")
                print("Stopping to avoid an infinite loop.")
                break

            if not press_next_photo(driver):
                break

            time.sleep(1.2)

        print(f"\nDone. Total saved: {saved_count}")

    finally:
        if driver is not None:
            try:
                # Give Firefox a moment to flush profile/cookies.
                driver.get("about:blank")
                time.sleep(1)
            except Exception:
                pass

            driver.quit()


if __name__ == "__main__":
    main()