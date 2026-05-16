"""
python ocr_translate.py "[URL]" --lang korean --force-redownload --headless --no-manual-confirm
"""
import os
import re
import cv2
import json
import time
import math
import glob
import hashlib
import argparse
import unicodedata
import requests
import numpy as np

from PIL import Image, ImageDraw, ImageFont, ImageOps

try:
    import fitz  # PyMuPDF
except Exception:
    fitz = None

from paddleocr import PaddleOCR
from playwright.sync_api import sync_playwright


BASE_DIR = os.path.dirname(os.path.abspath(__file__))

GROQ_API_KEYS = []

base_key = os.getenv("GROQ_API_KEY")
if base_key:
    GROQ_API_KEYS.append(("GROQ_API_KEY", base_key))

for i in range(1, 10):
    key_name = f"GROQ_API_KEY{i}"
    key_value = os.getenv(key_name)
    if key_value:
        GROQ_API_KEYS.append((key_name, key_value))

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.1-8b-instant"

if not GROQ_API_KEYS:
    raise SystemExit(
        "No Groq API keys found. Set GROQ_API_KEY and optionally "
        "GROQ_API_KEY1 through GROQ_API_KEY9 in the environment."
    )


def make_groq_headers(api_key):
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def norm_text(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFC", str(s))
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r"\n\s+", "\n", s)
    return s.strip()


def safe_name(s: str) -> str:
    s = re.sub(r"https?://", "", s)
    s = re.sub(r"[^a-zA-Z0-9._-]+", "_", s)
    s = s.strip("_")
    return s[:120] or "comic"


def parse_retry_after_seconds(resp: requests.Response) -> float:
    ra = resp.headers.get("retry-after")
    if ra:
        try:
            return float(ra)
        except ValueError:
            pass

    try:
        j = resp.json()
        msg = j.get("error", {}).get("message", "")
        m = re.search(r"try again in\s+([0-9]*\.?[0-9]+)s", msg, re.IGNORECASE)
        if m:
            return float(m.group(1))
    except Exception:
        pass

    return 2.0


def groq_chat(messages, max_tokens=1200, temperature=0, timeout=90, max_retries=8, json_mode=True):
    payload = {
        "model": GROQ_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    backoff = 1.0

    for attempt in range(1, max_retries + 1):
        rate_limit_waits = []
        network_errors = []

        for key_name, api_key in GROQ_API_KEYS:
            try:
                resp = requests.post(
                    GROQ_API_URL,
                    headers=make_groq_headers(api_key),
                    json=payload,
                    timeout=timeout,
                )
            except requests.RequestException as e:
                network_errors.append((key_name, e))
                print(
                    f"[NET] {key_name} network error. "
                    f"Trying next key if available. Attempt {attempt}/{max_retries}"
                )
                continue

            if resp.status_code == 200:
                return resp.json()

            if resp.status_code == 429:
                wait_s = max(parse_retry_after_seconds(resp), backoff) + 0.25
                rate_limit_waits.append(wait_s)
                print(
                    f"[429] {key_name} rate limited. "
                    f"Trying next key before sleeping. Attempt {attempt}/{max_retries}"
                )
                continue

            if resp.status_code == 400:
                try:
                    err = resp.json()
                except Exception:
                    err = {}

                code = err.get("error", {}).get("code", "")
                if code == "json_validate_failed" and json_mode:
                    print("[400] JSON validation failed. Retrying without JSON mode...")
                    return groq_chat(
                        messages=messages,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        timeout=timeout,
                        max_retries=max_retries,
                        json_mode=False,
                    )

            raise SystemExit(f"Groq API error using {key_name}: {resp.status_code} {resp.text}")

        if rate_limit_waits:
            wait_s = max(rate_limit_waits)
        else:
            wait_s = backoff + 0.25

        if attempt == max_retries:
            if network_errors and not rate_limit_waits:
                key_name, err = network_errors[-1]
                raise SystemExit(f"Network error calling Groq using {key_name}: {err}")
            raise SystemExit("Groq API error: too many retries across all API keys.")

        print(
            f"All Groq API keys failed or were rate limited. "
            f"Sleeping {wait_s:.2f}s, then retrying from GROQ_API_KEY. "
            f"Attempt {attempt}/{max_retries}"
        )

        time.sleep(wait_s)
        backoff = min(backoff * 1.6, 20.0)

    raise SystemExit("Groq API error: too many retries across all API keys.")

def extract_json_object(text: str):
    t = text.strip()

    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t).strip()

    decoder = json.JSONDecoder()

    start_obj = t.find("{")
    if start_obj != -1:
        candidate = t[start_obj:]
        try:
            obj, _ = decoder.raw_decode(candidate)
            return obj
        except json.JSONDecodeError:
            pass

    repaired = t
    repaired = repaired.replace("“", '"').replace("”", '"').replace("’", "'")
    repaired = re.sub(r",\s*([}\]])", r"\1", repaired)

    start_obj = repaired.find("{")
    if start_obj != -1:
        candidate = repaired[start_obj:]
        obj, _ = decoder.raw_decode(candidate)
        return obj

    raise ValueError("Could not extract valid JSON object from response.")


def call_and_parse(messages, max_tokens=1200, json_mode=True):
    data_json = groq_chat(messages, max_tokens=max_tokens, temperature=0, json_mode=json_mode)
    content = data_json["choices"][0]["message"]["content"]

    try:
        return extract_json_object(content)
    except Exception:
        fix_messages = [
            {"role": "system", "content": "You repair malformed JSON. Return only valid JSON."},
            {"role": "user", "content": "Fix this into valid JSON only:\n\n" + content},
        ]
        fixed = groq_chat(fix_messages, max_tokens=max_tokens, temperature=0, json_mode=False)
        fixed_content = fixed["choices"][0]["message"]["content"]
        return extract_json_object(fixed_content)


def get_existing_downloaded_pages(page_image_dir):
    paths = sorted(glob.glob(os.path.join(page_image_dir, "page_*.png")))

    good = []
    for p in paths:
        if os.path.isfile(p) and os.path.getsize(p) > 0:
            good.append(p)

    return good


def split_long_screenshot(full_path, page_image_dir, chunk_height=1800, overlap=0):
    print("Splitting full-page screenshot into page chunks...")

    img = Image.open(full_path).convert("RGB")
    w, h = img.size

    if w < 300 or h < 500:
        img.close()
        print("Full-page screenshot is too small. It may be a blocked/login/error page.")
        return []

    for old in glob.glob(os.path.join(page_image_dir, "page_*.png")):
        try:
            os.remove(old)
        except Exception:
            pass

    out_paths = []
    y = 0
    index = 1

    while y < h:
        bottom = min(h, y + chunk_height)
        crop = img.crop((0, y, w, bottom))

        out_path = os.path.join(page_image_dir, f"page_{index:04d}.png")
        crop.save(out_path)

        out_paths.append(out_path)
        print(f"  Saved fallback chunk {index:04d}: {out_path}")

        if bottom >= h:
            break

        y = bottom - overlap
        index += 1

    img.close()

    print(f"Split full-page screenshot into {len(out_paths)} image chunk(s).")
    return out_paths


def is_theblank_url(url):
    return "theblank.net" in url.lower()


def get_theblank_page_count(page):
    try:
        return page.evaluate(
            """
            () => {
                const app = document.querySelector("#app");
                if (!app || !app.dataset.page) return null;

                try {
                    const data = JSON.parse(app.dataset.page);
                    return data?.props?.data?.page_count || null;
                } catch (e) {
                    return null;
                }
            }
            """
        )
    except Exception:
        return None


def save_network_image_bytes(body, page_image_dir, index):
    tmp_path = None

    try:
        digest = hashlib.sha1(body).hexdigest()[:12]
        tmp_path = os.path.join(page_image_dir, f"_network_{index:04d}_{digest}.bin")

        with open(tmp_path, "wb") as f:
            f.write(body)

        img = Image.open(tmp_path).convert("RGB")
        w, h = img.size

        if w < 450 or h < 600:
            img.close()
            os.remove(tmp_path)
            return None

        aspect = h / max(1, w)

        if aspect < 0.75:
            img.close()
            os.remove(tmp_path)
            return None

        out_path = os.path.join(page_image_dir, f"page_{index:04d}.png")
        img.save(out_path)
        img.close()
        os.remove(tmp_path)

        return out_path

    except Exception:
        try:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        return None


def capture_theblank_network_images(page, page_image_dir, expected_count=None):
    print("TheBlank mode: capturing reader page images from network responses...")

    captured = []
    seen_hashes = set()

    def on_response(response):
        try:
            content_type = response.headers.get("content-type", "").lower()

            if not content_type.startswith("image/"):
                return

            url = response.url.lower()

            bad_markers = [
                "favicon",
                "logo",
                "avatar",
                "ads",
                "adservice",
                "doubleclick",
                "google",
                "analytics",
                "facebook",
                "discord",
                "tiktok",
                "gif",
                "sprite",
                "emoji",
                "banner",
                "icon",
            ]

            if any(x in url for x in bad_markers):
                return

            body = response.body()
            if not body or len(body) < 20_000:
                return

            digest = hashlib.sha1(body).hexdigest()
            if digest in seen_hashes:
                return

            seen_hashes.add(digest)
            captured.append(body)

        except Exception:
            pass

    page.on("response", on_response)

    page.wait_for_timeout(3000)

    last_y = -1
    stable_rounds = 0

    for _ in range(280):
        y = page.evaluate("window.scrollY")
        vh = page.evaluate("window.innerHeight")
        sh = page.evaluate("document.body.scrollHeight")

        page.evaluate("window.scrollBy(0, 900)")
        page.wait_for_timeout(700)

        new_y = page.evaluate("window.scrollY")
        new_sh = page.evaluate("document.body.scrollHeight")

        if abs(new_y - last_y) < 5:
            stable_rounds += 1
        else:
            stable_rounds = 0

        last_y = new_y

        if expected_count and len(captured) >= expected_count:
            page.wait_for_timeout(2500)
            break

        if stable_rounds >= 5 or new_y + vh >= new_sh - 10:
            page.wait_for_timeout(2500)
            break

    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(1000)

    for old in glob.glob(os.path.join(page_image_dir, "page_*.png")):
        try:
            os.remove(old)
        except Exception:
            pass

    saved = []
    temp_index = 1

    for body in captured:
        out_path = save_network_image_bytes(body, page_image_dir, temp_index)
        if out_path:
            saved.append(out_path)
            print(f"  Saved TheBlank network page {temp_index:04d}: {out_path}")
            temp_index += 1

    if expected_count and len(saved) > expected_count:
        extras = saved[expected_count:]
        saved = saved[:expected_count]

        for p in extras:
            try:
                os.remove(p)
            except Exception:
                pass

    print(f"TheBlank mode captured {len(saved)} page image(s).")
    return saved


def wait_for_manual_confirmation(page, url):
    print("\n" + "=" * 80)
    print("MANUAL BROWSER STEP")
    print("=" * 80)
    print("A Chromium browser window is open.")
    print("Use it normally now:")
    print("- solve Cloudflare / captcha / human check")
    print("- log in if needed")
    print("- close popups if needed")
    print("- make sure the chapter page is visible")
    print("")
    print(f"Current target URL: {url}")
    print("")
    print("When the page is ready, come back to this terminal and press Enter.")
    print("The script will start scrolling/capturing only after that.")
    print("=" * 80)

    input("Press Enter here after the page is ready... ")

    try:
        page.wait_for_timeout(1500)
    except Exception:
        pass


def lazy_load_page(
    url,
    output_dir,
    selector="img",
    min_width=300,
    min_height=250,
    scroll_step=900,
    max_scroll_rounds=350,
    force_redownload=False,
    fallback_chunk_height=1800,
    headless=False,
    manual_confirm=True,
):
    os.makedirs(output_dir, exist_ok=True)

    page_image_dir = os.path.join(output_dir, "downloaded_pages")
    os.makedirs(page_image_dir, exist_ok=True)

    existing_pages = get_existing_downloaded_pages(page_image_dir)

    if existing_pages and not force_redownload:
        print(f"Found {len(existing_pages)} already-downloaded original page image(s).")
        print("Skipping browser scrolling/downloading.")
        return existing_pages

    if force_redownload:
        old_pages = get_existing_downloaded_pages(page_image_dir)
        old_full = os.path.join(page_image_dir, "full_page_render.png")

        if old_pages:
            print(f"Force redownload enabled. Deleting {len(old_pages)} old page image(s)...")
            for p in old_pages:
                try:
                    os.remove(p)
                except Exception:
                    pass

        if os.path.isfile(old_full):
            try:
                os.remove(old_full)
            except Exception:
                pass

    print(f"Opening URL: {url}")
    print(f"Browser headless mode: {headless}")

    image_paths = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            slow_mo=60 if not headless else 0,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--start-maximized",
            ],
        )

        context = browser.new_context(
            viewport=None if not headless else {"width": 1400, "height": 2200},
            device_scale_factor=1,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )

        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=120000)
        page.wait_for_timeout(4000)

        if manual_confirm and not headless:
            wait_for_manual_confirmation(page, url)

        if is_theblank_url(url):
            expected_count = get_theblank_page_count(page)
            if expected_count:
                print(f"TheBlank detected. Expected page count: {expected_count}")
            else:
                print("TheBlank detected. Could not read expected page count.")

            network_pages = capture_theblank_network_images(
                page=page,
                page_image_dir=page_image_dir,
                expected_count=expected_count,
            )

            if network_pages:
                browser.close()
                return network_pages

            print("TheBlank network mode found no usable pages. Continuing to normal <img> mode...")

        print("Lazy loading images by scrolling...")

        stable_rounds = 0
        last_height = 0
        last_y = -1

        for _ in range(max_scroll_rounds):
            y = page.evaluate("window.scrollY")
            viewport_h = page.evaluate("window.innerHeight")

            page.evaluate(f"window.scrollBy(0, {scroll_step})")
            page.wait_for_timeout(900)

            new_height = page.evaluate("document.body.scrollHeight")
            new_y = page.evaluate("window.scrollY")

            if new_height == last_height and abs(new_y - last_y) < 5:
                stable_rounds += 1
            else:
                stable_rounds = 0

            last_height = new_height
            last_y = new_y

            if stable_rounds >= 5 or new_y + viewport_h >= new_height - 10:
                page.wait_for_timeout(2500)
                final_height = page.evaluate("document.body.scrollHeight")
                if final_height == new_height:
                    break

        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(1500)

        print("Finding reader images in top-to-bottom reading order...")

        image_infos = page.evaluate(
            """
            ({selector, minWidth, minHeight}) => {
                const els = Array.from(document.querySelectorAll(selector));
                return els.map((el, index) => {
                    const r = el.getBoundingClientRect();
                    const src = el.currentSrc || el.src || "";
                    return {
                        index,
                        top: r.top + window.scrollY,
                        left: r.left + window.scrollX,
                        width: r.width,
                        height: r.height,
                        src
                    };
                }).filter(x =>
                    x.width >= minWidth &&
                    x.height >= minHeight &&
                    x.src &&
                    !x.src.startsWith("data:")
                ).sort((a, b) => {
                    if (Math.abs(a.top - b.top) > 20) return a.top - b.top;
                    return a.left - b.left;
                });
            }
            """,
            {
                "selector": selector,
                "minWidth": min_width,
                "minHeight": min_height,
            }
        )

        if not image_infos:
            print("No normal large <img> elements found.")
            print("Falling back to full-page screenshot capture...")

            full_path = os.path.join(page_image_dir, "full_page_render.png")

            try:
                page.screenshot(path=full_path, full_page=True, timeout=120000)
                print(f"Saved full-page screenshot: {full_path}")
            except Exception as e:
                browser.close()
                raise SystemExit(f"Full-page screenshot fallback failed: {e}")

            browser.close()

            chunks = split_long_screenshot(
                full_path=full_path,
                page_image_dir=page_image_dir,
                chunk_height=fallback_chunk_height,
                overlap=0,
            )

            if chunks:
                return chunks

            raise SystemExit(
                "No reader images found and full-page screenshot fallback failed. "
                "The page may be blocked, empty, login-only, or rendered inside a protected viewer."
            )

        print(f"Found {len(image_infos)} likely comic images.")

        for out_index, info in enumerate(image_infos, start=1):
            locator = page.locator(selector).nth(info["index"])

            try:
                locator.scroll_into_view_if_needed(timeout=30000)
                page.wait_for_timeout(900)

                out_path = os.path.join(page_image_dir, f"page_{out_index:04d}.png")
                locator.screenshot(path=out_path, timeout=60000)

                if os.path.isfile(out_path) and os.path.getsize(out_path) > 0:
                    image_paths.append(out_path)
                    print(f"  Saved page {out_index:04d}: {out_path}")
            except Exception as e:
                print(f"  Failed screenshot for image {out_index}: {e}")

        browser.close()

    return image_paths


def polygon_to_rect(poly):
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))


def rect_union(rects):
    x1 = min(r[0] for r in rects)
    y1 = min(r[1] for r in rects)
    x2 = max(r[2] for r in rects)
    y2 = max(r[3] for r in rects)
    return x1, y1, x2, y2


def rect_center(r):
    return ((r[0] + r[2]) / 2.0, (r[1] + r[3]) / 2.0)


def rect_distance(a, b):
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b

    dx = max(bx1 - ax2, ax1 - bx2, 0)
    dy = max(by1 - ay2, ay1 - by2, 0)

    return math.sqrt(dx * dx + dy * dy)


def expand_rect(r, pad, w, h):
    x1, y1, x2, y2 = r
    return (
        max(0, x1 - pad),
        max(0, y1 - pad),
        min(w - 1, x2 + pad),
        min(h - 1, y2 + pad),
    )


def is_noise_text(text):
    t = norm_text(text)
    if not t:
        return True

    if re.fullmatch(r"[0-9:.\-_/ ]+", t):
        return True

    if re.fullmatch(r"[^\w가-힣ぁ-んァ-ン一-龯]+", t):
        return True

    return False


def should_merge_pair(a, b):
    ar = a["rect"]
    br = b["rect"]

    ax1, ay1, ax2, ay2 = ar
    bx1, by1, bx2, by2 = br

    aw = max(1, ax2 - ax1)
    ah = max(1, ay2 - ay1)
    bw = max(1, bx2 - bx1)
    bh = max(1, by2 - by1)

    avg_h = max(8, (ah + bh) / 2)
    avg_w = max(8, (aw + bw) / 2)

    acx, acy = rect_center(ar)
    bcx, bcy = rect_center(br)

    horizontal_overlap = max(0, min(ax2, bx2) - max(ax1, bx1))
    vertical_overlap = max(0, min(ay2, by2) - max(ay1, by1))

    min_width = max(1, min(aw, bw))
    min_height = max(1, min(ah, bh))

    horizontal_overlap_ratio = horizontal_overlap / min_width
    vertical_overlap_ratio = vertical_overlap / min_height

    vertical_gap = max(by1 - ay2, ay1 - by2, 0)
    horizontal_gap = max(bx1 - ax2, ax1 - bx2, 0)

    center_x_gap = abs(acx - bcx)
    center_y_gap = abs(acy - bcy)

    if horizontal_overlap_ratio >= 0.35 and vertical_gap <= max(20, avg_h * 1.35):
        return True

    if center_x_gap <= avg_w * 0.85 and vertical_gap <= max(18, avg_h * 1.15):
        return True

    if vertical_overlap_ratio >= 0.45 and horizontal_gap <= max(18, avg_h * 1.2):
        return True

    if rect_distance(ar, br) <= max(10, avg_h * 0.65) and center_y_gap <= avg_h * 1.4:
        return True

    return False


def group_ocr_items(items):
    clean_items = []
    for item in items:
        if is_noise_text(item["text"]):
            continue
        clean_items.append(item)

    items = sorted(clean_items, key=lambda x: (x["rect"][1], x["rect"][0]))

    n = len(items)
    if n == 0:
        return []

    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra = find(a)
        rb = find(b)
        if ra != rb:
            parent[rb] = ra

    for i in range(n):
        for j in range(i + 1, n):
            if items[j]["rect"][1] - items[i]["rect"][3] > 90:
                break

            if should_merge_pair(items[i], items[j]):
                union(i, j)

    grouped = {}
    for i, item in enumerate(items):
        r = find(i)
        grouped.setdefault(r, []).append(item)

    groups = []
    for parts in grouped.values():
        parts = sorted(parts, key=lambda x: (x["rect"][1], x["rect"][0]))
        rect = rect_union([p["rect"] for p in parts])
        raw_text = "\n".join(p["text"] for p in parts)

        groups.append({
            "items": parts,
            "rect": rect,
            "raw_text": raw_text,
        })

    groups = sorted(groups, key=lambda g: (g["rect"][1], g["rect"][0]))

    for idx, g in enumerate(groups, start=1):
        g["id"] = idx

    return groups


def make_ocr(lang):
    try:
        return PaddleOCR(
            use_angle_cls=True,
            lang=lang,
            show_log=False,
        )
    except TypeError:
        return PaddleOCR(
            use_angle_cls=True,
            lang=lang,
        )


def ocr_image(ocr, image_path, min_conf=0.35):
    result = ocr.ocr(image_path, cls=True)

    items = []
    if not result:
        return []

    lines = result[0] if len(result) == 1 and isinstance(result[0], list) else result

    for line in lines:
        try:
            poly = line[0]
            text = norm_text(line[1][0])
            conf = float(line[1][1])
        except Exception:
            continue

        if not text or conf < min_conf:
            continue

        rect = polygon_to_rect(poly)

        items.append({
            "poly": poly,
            "rect": rect,
            "text": text,
            "conf": conf,
        })

    return items


def build_single_translation_prompt(raw_text):
    return (
        "Translate this Korean/Japanese/Chinese comic OCR text to natural English.\n"
        "Return ONLY valid JSON.\n\n"
        "Input text:\n"
        f"{raw_text}\n\n"
        "Return exactly this JSON shape:\n"
        "{\n"
        '  "raw_text": "original text",\n'
        '  "translated_text": "English translation"\n'
        "}\n\n"
        "Rules:\n"
        "- Preserve the meaning and tone.\n"
        "- Do not add explanations.\n"
        "- Do not romanize; translate to English.\n"
        "- If the text is a sound effect, translate it as a short English sound effect.\n"
        "- Keep translation short enough to fit in the original bubble if possible.\n"
        "- Keep names as names.\n"
        "- If OCR text is fragmented by line breaks, understand it as one comic bubble or one text area.\n"
    )


def translate_one_group_with_groq(raw_text):
    messages = [
        {
            "role": "system",
            "content": "You are a comic translator. Return valid JSON only."
        },
        {
            "role": "user",
            "content": build_single_translation_prompt(raw_text)
        }
    ]

    data = call_and_parse(messages, max_tokens=800, json_mode=True)

    translated = norm_text(data.get("translated_text", ""))
    if not translated:
        translated = raw_text

    return {
        "raw_text": raw_text,
        "translated_text": translated,
    }


def translate_groups_with_groq_separately(groups, sleep_between_calls=0.25):
    translations = {}

    for g in groups:
        raw_text = g["raw_text"]
        print(f"    Groq region [{g['id']}]: {raw_text.replace(chr(10), ' / ')}")

        try:
            translations[g["id"]] = translate_one_group_with_groq(raw_text)
            print(f"      -> {translations[g['id']]['translated_text']}")
        except Exception as e:
            print(f"      Groq failed for region [{g['id']}]: {e}")
            translations[g["id"]] = {
                "raw_text": raw_text,
                "translated_text": raw_text,
            }

        time.sleep(sleep_between_calls)

    return translations


def get_default_font_path():
    candidates = [
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\arialbd.ttf",
        r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\calibri.ttf",
        r"C:\Windows\Fonts\malgun.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]

    for p in candidates:
        if os.path.isfile(p):
            return p

    return None


def wrap_text(draw, text, font, max_width):
    text = norm_text(text)
    words = text.split()

    if not words:
        return [""]

    lines = []
    current = words[0]

    for word in words[1:]:
        test = current + " " + word
        bbox = draw.textbbox((0, 0), test, font=font)
        width = bbox[2] - bbox[0]

        if width <= max_width:
            current = test
        else:
            lines.append(current)
            current = word

    lines.append(current)
    return lines


def fit_font(draw, text, font_path, box_w, box_h, max_size=46, min_size=8):
    if not font_path:
        return ImageFont.load_default(), [text]

    for size in range(max_size, min_size - 1, -1):
        font = ImageFont.truetype(font_path, size=size)
        lines = wrap_text(draw, text, font, max_width=max(10, box_w))

        line_bboxes = [draw.textbbox((0, 0), line, font=font) for line in lines]
        line_heights = [(b[3] - b[1]) for b in line_bboxes]
        line_widths = [(b[2] - b[0]) for b in line_bboxes]

        total_h = sum(line_heights) + max(0, len(lines) - 1) * int(size * 0.22)
        max_w = max(line_widths) if line_widths else 0

        if max_w <= box_w and total_h <= box_h:
            return font, lines

    font = ImageFont.truetype(font_path, size=min_size)
    lines = wrap_text(draw, text, font, max_width=max(10, box_w))
    return font, lines


def median_border_color(image_bgr, rect, pad=3, ring=5):
    h, w = image_bgr.shape[:2]
    x1, y1, x2, y2 = expand_rect(rect, pad, w, h)
    bx1, by1, bx2, by2 = expand_rect(rect, pad + ring, w, h)

    samples = []

    if by1 < y1:
        samples.append(image_bgr[by1:y1, bx1:bx2])

    if y2 < by2:
        samples.append(image_bgr[y2:by2, bx1:bx2])

    if bx1 < x1:
        samples.append(image_bgr[by1:by2, bx1:x1])

    if x2 < bx2:
        samples.append(image_bgr[by1:by2, x2:bx2])

    pixels = []
    for s in samples:
        if s.size:
            pixels.append(s.reshape(-1, 3))

    if not pixels:
        return (255, 255, 255)

    arr = np.vstack(pixels)
    med = np.median(arr, axis=0)
    brightness = float(np.mean(med))

    if brightness > 185:
        return (255, 255, 255)

    return tuple(int(x) for x in med)


def erase_text_safely(image_bgr, groups, pad=2):
    out = image_bgr.copy()
    h, w = out.shape[:2]

    for g in groups:
        for item in g["items"]:
            x1, y1, x2, y2 = expand_rect(item["rect"], pad, w, h)
            fill = median_border_color(out, item["rect"], pad=pad, ring=5)
            cv2.rectangle(out, (x1, y1), (x2, y2), fill, thickness=-1)

    return out


def compute_draw_box(group, image_w, image_h):
    x1, y1, x2, y2 = group["rect"]
    gw = x2 - x1
    gh = y2 - y1

    pad_x = max(8, int(gw * 0.25))
    pad_y = max(6, int(gh * 0.45))

    x1 = max(0, x1 - pad_x)
    y1 = max(0, y1 - pad_y)
    x2 = min(image_w - 1, x2 + pad_x)
    y2 = min(image_h - 1, y2 + pad_y)

    max_h = max(36, int(image_h * 0.18))
    if y2 - y1 > max_h:
        cy = (y1 + y2) // 2
        y1 = max(0, cy - max_h // 2)
        y2 = min(image_h - 1, cy + max_h // 2)

    return x1, y1, x2, y2


def draw_translations(image_bgr, groups, translations, font_path=None):
    h, w = image_bgr.shape[:2]

    cleaned = erase_text_safely(image_bgr, groups, pad=2)

    image_rgb = cv2.cvtColor(cleaned, cv2.COLOR_BGR2RGB)
    pil_img = Image.fromarray(image_rgb)
    draw = ImageDraw.Draw(pil_img)

    if font_path is None:
        font_path = get_default_font_path()

    for g in groups:
        item = translations.get(g["id"])
        if not item:
            continue

        text = item["translated_text"]
        if not text:
            continue

        x1, y1, x2, y2 = compute_draw_box(g, w, h)
        box_w = max(12, x2 - x1)
        box_h = max(12, y2 - y1)

        max_size = min(42, max(10, int(box_h * 0.45)))

        font, lines = fit_font(
            draw=draw,
            text=text,
            font_path=font_path,
            box_w=box_w,
            box_h=box_h,
            max_size=max_size,
            min_size=8,
        )

        bboxes = [draw.textbbox((0, 0), line, font=font) for line in lines]
        heights = [b[3] - b[1] for b in bboxes]
        widths = [b[2] - b[0] for b in bboxes]

        font_size = getattr(font, "size", 12)
        line_gap = int(font_size * 0.22)
        total_h = sum(heights) + max(0, len(lines) - 1) * line_gap

        cur_y = y1 + max(0, (box_h - total_h) // 2)

        for line, bw, bh in zip(lines, widths, heights):
            tx = x1 + max(0, (box_w - bw) // 2)
            ty = cur_y

            stroke_width = max(1, font_size // 18)

            draw.text(
                (tx, ty),
                line,
                font=font,
                fill=(0, 0, 0),
                stroke_width=stroke_width,
                stroke_fill=(255, 255, 255),
            )

            cur_y += bh + line_gap

    out_bgr = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
    return out_bgr


def save_debug_json(out_json_path, image_path, groups, translations):
    data = {
        "image": image_path,
        "items": []
    }

    for g in groups:
        trans = translations.get(g["id"], {})
        data["items"].append({
            "id": g["id"],
            "rect": g["rect"],
            "raw_text": g["raw_text"],
            "translated_text": trans.get("translated_text", ""),
            "ocr_parts": [
                {
                    "text": x["text"],
                    "conf": x["conf"],
                    "rect": x["rect"],
                }
                for x in g["items"]
            ]
        })

    with open(out_json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)




def file_nonempty(path):
    return os.path.isfile(path) and os.path.getsize(path) > 0

def process_image(ocr, image_path, output_dir, font_path=None, min_conf=0.35):
    print(f"\nProcessing page image: {image_path}")

    image_bgr = cv2.imread(image_path, cv2.IMREAD_COLOR)
    if image_bgr is None:
        print(f"  Failed to read image: {image_path}")
        return image_path

    base = os.path.splitext(os.path.basename(image_path))[0]
    out_img = os.path.join(output_dir, "translated_images", base + "_translated.png")
    out_json = os.path.join(output_dir, "debug_json", base + "_raw_and_translation.json")

    os.makedirs(os.path.dirname(out_img), exist_ok=True)
    os.makedirs(os.path.dirname(out_json), exist_ok=True)

    if file_nonempty(out_img):
        print(f"  Already translated. Skipping OCR/Groq/render step: {out_img}")
        return out_img

    items = ocr_image(ocr, image_path, min_conf=min_conf)

    if not items:
        print("  No text detected. Copying original page to translated output.")
        cv2.imwrite(out_img, image_bgr)
        save_debug_json(out_json, image_path, [], {})
        return out_img

    groups = group_ocr_items(items)

    if not groups:
        print("  OCR found only noise. Copying original page to translated output.")
        cv2.imwrite(out_img, image_bgr)
        save_debug_json(out_json, image_path, [], {})
        return out_img

    print("  Separate text regions:")
    for g in groups:
        print(f"    [{g['id']}] {g['raw_text'].replace(chr(10), ' / ')}")

    translations = translate_groups_with_groq_separately(groups)

    rendered = draw_translations(
        image_bgr=image_bgr,
        groups=groups,
        translations=translations,
        font_path=font_path,
    )

    cv2.imwrite(out_img, rendered)
    save_debug_json(out_json, image_path, groups, translations)

    print(f"  Saved translated page: {out_img}")
    return out_img


def images_to_pdf(image_paths, output_pdf_path):
    if not image_paths:
        raise SystemExit("No images to put into PDF.")

    os.makedirs(os.path.dirname(output_pdf_path), exist_ok=True)

    if fitz is None:
        raise SystemExit(
            "PyMuPDF is not installed. Run this first:\n\n"
            "pip install pymupdf\n"
        )

    doc = fitz.open()

    for p in image_paths:
        img = Image.open(p)
        img = ImageOps.exif_transpose(img).convert("RGB")
        width, height = img.size
        img.close()

        page = doc.new_page(width=width, height=height)
        rect = fitz.Rect(0, 0, width, height)
        page.insert_image(rect, filename=p)

    doc.save(output_pdf_path)
    doc.close()

    print(f"\nSaved final translated PDF: {output_pdf_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("url", help="Authorized comic/webtoon page URL")
    parser.add_argument("--out", default=None, help="Output folder")
    parser.add_argument("--selector", default="img", help="CSS selector for reader images")
    parser.add_argument("--lang", default="korean", help="PaddleOCR language: korean, japan, ch, en")
    parser.add_argument("--font", default=None, help="Path to .ttf font")
    parser.add_argument("--min-conf", type=float, default=0.35)
    parser.add_argument("--min-width", type=int, default=300)
    parser.add_argument("--min-height", type=int, default=250)
    parser.add_argument("--scroll-step", type=int, default=900)
    parser.add_argument("--max-scroll-rounds", type=int, default=350)
    parser.add_argument("--fallback-chunk-height", type=int, default=1800)
    parser.add_argument("--force-redownload", action="store_true", help="Ignore existing downloaded_pages and download again")
    parser.add_argument("--headless", action="store_true", help="Run browser hidden/headless")
    parser.add_argument("--no-manual-confirm", action="store_true", help="Do not wait for Enter before scrolling/capturing")
    args = parser.parse_args()

    output_dir = args.out or os.path.join(BASE_DIR, "translated_url_output", safe_name(args.url))
    os.makedirs(output_dir, exist_ok=True)

    page_images = lazy_load_page(
        url=args.url,
        output_dir=output_dir,
        selector=args.selector,
        min_width=args.min_width,
        min_height=args.min_height,
        scroll_step=args.scroll_step,
        max_scroll_rounds=args.max_scroll_rounds,
        force_redownload=args.force_redownload,
        fallback_chunk_height=args.fallback_chunk_height,
        headless=args.headless,
        manual_confirm=not args.no_manual_confirm,
    )

    if not page_images:
        raise SystemExit("No page images were captured.")

    print(f"\nUsing {len(page_images)} page image(s) in reading order.")

    print("\nLoading OCR model...")
    ocr = make_ocr(args.lang)

    translated_images = []

    for image_path in page_images:
        out_img = process_image(
            ocr=ocr,
            image_path=image_path,
            output_dir=output_dir,
            font_path=args.font,
            min_conf=args.min_conf,
        )
        translated_images.append(out_img)
        time.sleep(0.4)

    final_pdf = os.path.join(output_dir, "final_translated.pdf")
    images_to_pdf(translated_images, final_pdf)

    print("\nDone.")
    print(f"Final PDF: {final_pdf}")


if __name__ == "__main__":
    main()
