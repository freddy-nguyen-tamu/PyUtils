r'''
first run: python indown.py --profile "https://www.instagram.com/aaliyah.brianni/" --user-data-dir "%USERPROFILE%\playwright_ig_profile2" --out "downloads" --max-posts 1000 --max-scroll-pages 100
second run: python indown.py --profile "https://www.instagram.com/aaliyah.brianni/" --user-data-dir "%USERPROFILE%\playwright_ig_profile2" --out "downloads" --headless
'''
import argparse
import os
import re
import time
import hashlib
from urllib.parse import urlparse

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

POST_OR_REEL_RE = re.compile(r"/(p|reel)/([^/]+)/")


def normalize_post_url(href: str) -> str | None:
    if not href:
        return None
    m = POST_OR_REEL_RE.search(href)
    if not m:
        return None
    kind, code = m.group(1), m.group(2)
    return f"https://www.instagram.com/{kind}/{code}/"


def safe_filename(url: str, content_type: str | None) -> str:
    h = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    ext = None
    if content_type:
        ct = content_type.split(";")[0].strip().lower()
        if ct == "image/jpeg":
            ext = ".jpg"
        elif ct == "image/png":
            ext = ".png"
        elif ct == "image/webp":
            ext = ".webp"
        elif ct == "video/mp4":
            ext = ".mp4"

    if not ext:
        path = urlparse(url).path
        _, maybe_ext = os.path.splitext(path)
        ext = maybe_ext if maybe_ext else ".bin"
    return f"{h}{ext}"


def download(url: str, out_dir: str, referer: str, user_agent: str, timeout=60) -> str:
    os.makedirs(out_dir, exist_ok=True)
    headers = {"User-Agent": user_agent, "Referer": referer}
    with requests.get(url, headers=headers, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        fn = safe_filename(url, r.headers.get("content-type"))
        path = os.path.join(out_dir, fn)
        if os.path.exists(path):
            return path
        with open(path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
        return path


def is_login_or_challenge_url(url: str) -> bool:
    return any(x in (url or "") for x in [
        "/accounts/login",
        "/challenge/",
        "/two_factor",
        "/accounts/onetap",
    ])


def page_looks_like_login_wall(page) -> bool:
    js = r"""
    () => {
      const t = document.body?.innerText?.toLowerCase() || "";
      const hasLoginText =
        t.includes("log in") ||
        t.includes("sign up") ||
        t.includes("login") ||
        t.includes("create an account");

      const hasPasswordField = !!document.querySelector('input[name="password"], input[type="password"]');
      const hasLoginButton = !!document.querySelector('button[type="submit"]');

      return { hasLoginText, hasPasswordField, hasLoginButton, url: location.href };
    }
    """
    try:
        d = page.evaluate(js)
        return bool(d.get("hasPasswordField") or (d.get("hasLoginButton") and d.get("hasLoginText")))
    except Exception:
        return False


def wait_for_manual_login(page) -> None:
    """
    Pause when Instagram asks for login/checkpoint or shows a login wall without changing URL.
    """
    page.wait_for_timeout(1200)

    if is_login_or_challenge_url(page.url) or page_looks_like_login_wall(page):
        print(f"\n[!] Manual action needed (login/checkpoint/wall). Current URL: {page.url}")
        print("    Complete it in the opened Playwright browser window.")
        input("    Press ENTER here when finished... ")
        page.wait_for_timeout(1200)

    cookies = page.context.cookies()
    cookie_names = {c["name"] for c in cookies}
    if "sessionid" not in cookie_names:
        print("\n[!] sessionid cookie not found yet.")
        print("    If you're still on a login/checkpoint screen, finish it.")
        input("    Press ENTER to continue... ")
        page.wait_for_timeout(1200)


def collect_post_urls(page, max_scroll_pages: int, max_posts: int, scroll_pause: float) -> list[str]:
    seen: list[str] = []
    seen_set: set[str] = set()

    try:
        page.wait_for_selector('a[href*="/p/"], a[href*="/reel/"]', timeout=20000)
    except PWTimeoutError:
        pass

    for _ in range(max_scroll_pages):
        hrefs = page.eval_on_selector_all(
            'a[href*="/p/"], a[href*="/reel/"]',
            "els => els.map(e => e.href)"
        )
        for h in hrefs:
            u = normalize_post_url(h)
            if u and u not in seen_set:
                seen_set.add(u)
                seen.append(u)
                if len(seen) >= max_posts:
                    return seen

        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(int(scroll_pause * 1000))

    return seen


def wait_post_ready(page, timeout_ms: int = 25000) -> None:
    """
    Wait until at least one large img/video is visible in the viewport.
    """
    js = r"""
    () => {
      for (const el of document.querySelectorAll("img, video")) {
        const r = el.getBoundingClientRect();
        const area = Math.max(0, r.width) * Math.max(0, r.height);
        const inView = r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth;
        if (inView && area > 80000) return true;
      }
      return false;
    }
    """
    start = time.time()
    while (time.time() - start) * 1000 < timeout_ms:
        if page_looks_like_login_wall(page):
            return
        try:
            if page.evaluate(js):
                return
        except Exception:
            pass
        page.wait_for_timeout(250)


def extract_main_media_url_anywhere(page) -> str | None:
    """
    Picks the most likely "current carousel item" by scoring:
      score = area - distance_from_center * 2000
    Then:
      - if IMG: pick best srcset candidate (largest width)
      - if VIDEO: currentSrc/src/source.src
    """
    js = r"""
    () => {
      function bestFromSrcset(srcset) {
        if (!srcset) return null;
        try {
          const parts = srcset.split(",").map(s => s.trim()).filter(Boolean);
          let best = null;
          for (const p of parts) {
            const m = p.match(/^(.*)\s+(\d+)w$/);
            if (!m) continue;
            const url = m[1].trim();
            const w = parseInt(m[2], 10);
            if (!best || w > best.w) best = { url, w };
          }
          return best ? best.url : null;
        } catch (e) { return null; }
      }

      const cx = innerWidth / 2;
      const cy = innerHeight / 2;

      const candidates = [];
      for (const el of document.querySelectorAll("img, video")) {
        const r = el.getBoundingClientRect();
        const area = Math.max(0, r.width) * Math.max(0, r.height);
        if (area < 80000) continue;

        const inView = r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth;
        if (!inView) continue;

        const ex = r.left + r.width / 2;
        const ey = r.top + r.height / 2;
        const dist = Math.hypot(ex - cx, ey - cy);

        const score = area - dist * 2000;
        candidates.push({ el, score });
      }

      if (!candidates.length) return null;
      candidates.sort((a, b) => b.score - a.score);
      const top = candidates[0].el;

      if (top.tagName === "VIDEO") {
        const src = top.currentSrc || top.src || top.querySelector("source")?.src || null;
        if (!src || src.startsWith("blob:")) return null;
        return src;
      }

      if (top.tagName === "IMG") {
        const best = bestFromSrcset(top.getAttribute("srcset"));
        const src = best || top.getAttribute("src") || null;
        return src;
      }

      return null;
    }
    """
    u = page.evaluate(js)
    if not u or not isinstance(u, str):
        return None
    if ("scontent" in u) or ("cdninstagram" in u):
        return u
    return None


def click_next_if_present(page) -> bool:
    selectors = [
        'button[aria-label="Next"]',
        'button[aria-label="Next page"]',
        'div[role="button"][aria-label="Next"]',
        'button:has(svg[aria-label="Next"])',
    ]
    for sel in selectors:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click()
                return True
        except Exception:
            pass

    # Keyboard fallback
    try:
        page.keyboard.press("ArrowRight")
        return True
    except Exception:
        return False


def wait_for_main_change_stable(page, previous: str | None, timeout_s: float = 10.0) -> str | None:
    """
    Wait for the main media URL to change AND stabilize
    (seen the same new URL at least twice).
    """
    end = time.time() + timeout_s
    last = None
    stable = 0

    while time.time() < end:
        cur = extract_main_media_url_anywhere(page)
        if cur and cur != previous:
            if cur == last:
                stable += 1
            else:
                last = cur
                stable = 1
            if stable >= 2:
                return cur
        page.wait_for_timeout(200)

    return None


def debug_state(page) -> None:
    js = r"""
    () => {
      const bigEls = Array.from(document.querySelectorAll("img, video")).filter(el => {
        const r = el.getBoundingClientRect();
        const area = Math.max(0, r.width) * Math.max(0, r.height);
        const inView = r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth;
        return inView && area > 80000;
      }).length;
      const url = location.href;
      const hasArticle = !!document.querySelector("article");
      return { url, hasArticle, bigEls };
    }
    """
    try:
        print("  [debug]", page.evaluate(js))
    except Exception as e:
        print("  [debug] failed:", e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--user-data-dir", required=True)
    ap.add_argument("--out", default="ig_downloads")

    # Limiters
    ap.add_argument("--max-posts", type=int, default=25)
    ap.add_argument("--max-scroll-pages", type=int, default=5)
    ap.add_argument("--max-carousel-items", type=int, default=10)

    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--scroll-pause", type=float, default=1.5)
    ap.add_argument("--per-post-pause", type=float, default=1.0)
    args = ap.parse_args()

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=args.user_data_dir,
            headless=args.headless,
        )

        page = context.new_page()
        page.goto(args.profile, wait_until="domcontentloaded")
        wait_for_manual_login(page)

        user_agent = page.evaluate("navigator.userAgent")

        post_urls = collect_post_urls(
            page,
            max_scroll_pages=args.max_scroll_pages,
            max_posts=args.max_posts,
            scroll_pause=args.scroll_pause,
        )

        print(f"\nCollected {len(post_urls)} post/reel URLs")
        for i, u in enumerate(post_urls, 1):
            print(f"[{i}/{len(post_urls)}] {u}")

        for idx, post_url in enumerate(post_urls, 1):
            print(f"\n=== ({idx}/{len(post_urls)}) Open: {post_url}")
            post_page = context.new_page()
            try:
                post_page.goto(post_url, wait_until="domcontentloaded")
                wait_for_manual_login(post_page)
                wait_post_ready(post_page, timeout_ms=25000)

                if page_looks_like_login_wall(post_page):
                    print("  [!] Login wall detected on post page.")
                    debug_state(post_page)
                    continue

                cur = extract_main_media_url_anywhere(post_page)
                if not cur:
                    print("  [!] No main media detected.")
                    debug_state(post_page)
                    continue

                downloaded: set[str] = set()
                steps = 0
                no_progress_cycles = 0

                while steps < args.max_carousel_items:
                    steps += 1

                    if cur not in downloaded:
                        try:
                            path = download(cur, args.out, referer=post_url, user_agent=user_agent)
                            downloaded.add(cur)
                            print(f"  downloaded ({len(downloaded)}): {path}")
                        except Exception as e:
                            print(f"  download failed: {cur} ({e})")

                    clicked = click_next_if_present(post_page)
                    if not clicked:
                        break

                    post_page.wait_for_timeout(int(args.per_post_pause * 1000))

                    nxt = wait_for_main_change_stable(post_page, previous=cur, timeout_s=10.0)
                    if not nxt:
                        # One extra wait cycle helps with the *last* carousel item
                        post_page.wait_for_timeout(600)
                        nxt = wait_for_main_change_stable(post_page, previous=cur, timeout_s=10.0)

                    if not nxt:
                        no_progress_cycles += 1
                        if no_progress_cycles >= 2:
                            break
                        continue

                    if nxt == cur:
                        no_progress_cycles += 1
                        if no_progress_cycles >= 2:
                            break
                    else:
                        no_progress_cycles = 0
                        cur = nxt

                print(f"Post done. Unique main carousel media: {len(downloaded)}")

            except PWTimeoutError as e:
                print(f"Timeout loading {post_url}: {e}")
            finally:
                post_page.close()

        context.close()


if __name__ == "__main__":
    main()