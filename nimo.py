import argparse
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse, parse_qsl, urlencode
from urllib.request import Request, urlopen

from playwright.sync_api import sync_playwright


def log(msg: str):
    print(f"[+] {msg}")


def ensure_ffmpeg():
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH")


def http_get_text(url: str, headers: dict, timeout: int = 60) -> str:
    req = Request(url, headers=headers)
    with urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def http_get_bytes(url: str, headers: dict, timeout: int = 60) -> bytes:
    req = Request(url, headers=headers)
    with urlopen(req, timeout=timeout) as r:
        return r.read()


def parse_query(url: str) -> dict:
    return dict(parse_qsl(urlparse(url).query, keep_blank_values=True))


def replace_query(url: str, query_dict: dict) -> str:
    p = urlparse(url)
    return urlunparse((
        p.scheme,
        p.netloc,
        p.path,
        p.params,
        urlencode(query_dict, doseq=True),
        p.fragment,
    ))


def pick_highest_bandwidth_variant(master_url: str, text: str) -> str:
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    variants = []

    for i, line in enumerate(lines):
        if line.startswith("#EXT-X-STREAM-INF"):
            bw = 0
            m = re.search(r"BANDWIDTH=(\d+)", line)
            if m:
                bw = int(m.group(1))
            if i + 1 < len(lines) and not lines[i + 1].startswith("#"):
                variants.append((bw, urljoin(master_url, lines[i + 1])))

    if not variants:
        return master_url

    variants.sort(key=lambda x: x[0], reverse=True)
    return variants[0][1]


def extract_segment_paths(media_playlist_url: str, playlist_text: str) -> list[str]:
    segs = []
    for raw in playlist_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        segs.append(urljoin(media_playlist_url, line))
    return segs


def remux_ts_to_mp4(input_ts: Path, output_mp4: Path):
    ensure_ffmpeg()
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_ts),
        "-c",
        "copy",
        "-bsf:a",
        "aac_adtstoasc",
        str(output_mp4),
    ]
    subprocess.run(cmd, check=True)


def extract_target_hyvid(page_url: str) -> str:
    m = re.search(r"/v/v-(\d+)", page_url)
    if not m:
        raise RuntimeError("Could not extract target video ID from URL")
    return m.group(1)


def url_matches_target(url: str, target_hyvid: str) -> bool:
    try:
        q = parse_query(url)
        return q.get("hyvid") == target_hyvid
    except Exception:
        return False


def build_full_segment_urls(media_playlist_url: str, media_playlist_text: str, ts_template_url: str) -> list[str]:
    seg_paths = extract_segment_paths(media_playlist_url, media_playlist_text)
    if not seg_paths:
        raise RuntimeError("No segment paths found in playlist")

    template_q = parse_query(ts_template_url)
    full_urls = []
    for seg in seg_paths:
        full_urls.append(replace_query(seg, template_q))
    return full_urls


def auto_capture_urls(page_url: str, profile_dir: str, timeout_seconds: float):
    target_hyvid = extract_target_hyvid(page_url)

    found = {
        "m3u8": None,
        "ts": None,
    }

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            channel="chrome",
            headless=False,
            no_viewport=True,
        )
        page = context.pages[0] if context.pages else context.new_page()
        ua = page.evaluate("() => navigator.userAgent")

        def remember(url: str):
            if not url or "nimo.tv" not in url:
                return

            lu = url.lower()
            if not url_matches_target(url, target_hyvid):
                return

            if ".m3u8" in lu:
                if found["m3u8"] != url:
                    found["m3u8"] = url
                    log(f"Captured target m3u8: {url}")

            if ".ts" in lu:
                if found["ts"] is None:
                    found["ts"] = url
                    log(f"Captured target ts template: {url}")

        page.on("request", lambda r: remember(r.url))
        page.on("response", lambda r: remember(r.url))

        cdp = context.new_cdp_session(page)
        cdp.send("Network.enable")

        def on_req(params):
            u = params.get("request", {}).get("url", "")
            remember(u)

        def on_resp(params):
            u = params.get("response", {}).get("url", "")
            remember(u)

        cdp.on("Network.requestWillBeSent", on_req)
        cdp.on("Network.responseReceived", on_resp)

        log(f"Opening: {page_url}")
        page.goto(page_url, wait_until="domcontentloaded", timeout=90000)

        selectors = [
            "video",
            ".play-btn",
            ".nimo-icon-web-play",
            ".nimo-icon-play-btn",
            "[class*=play]",
            "[title*=Play]",
            "[title*=Start]",
        ]

        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            if found["m3u8"] and found["ts"]:
                break

            for sel in selectors:
                try:
                    page.locator(sel).first.click(timeout=500)
                except Exception:
                    pass

            try:
                page.keyboard.press("Space")
                page.keyboard.press("Space")
            except Exception:
                pass

            try:
                perf_urls = page.evaluate("""
                    () => performance.getEntries()
                        .map(e => e.name)
                        .filter(Boolean)
                """)
                for u in perf_urls:
                    remember(u)
            except Exception:
                pass

            time.sleep(1)

        context.close()

    if not found["m3u8"]:
        raise RuntimeError(f"No target .m3u8 captured for hyvid={target_hyvid}")
    if not found["ts"]:
        raise RuntimeError(f"No target .ts captured for hyvid={target_hyvid}")

    return found["m3u8"], found["ts"], ua, target_hyvid


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url", help="Nimo page URL")
    ap.add_argument("-o", "--output", default="video.mp4")
    ap.add_argument("--profile-dir", default="nimo_profile")
    ap.add_argument("--detect-timeout", type=float, default=30.0)
    ap.add_argument("--start-index", type=int, default=1)
    ap.add_argument("--max-segments", type=int, default=0)
    args = ap.parse_args()

    out_mp4 = Path(args.output).resolve()
    joined_ts = out_mp4.with_suffix(".joined.ts")

    m3u8_url, ts_template_url, ua, target_hyvid = auto_capture_urls(
        args.url,
        args.profile_dir,
        args.detect_timeout,
    )

    headers = {
        "User-Agent": ua,
        "Referer": "https://www.nimo.tv/",
    }

    log(f"Locked to target hyvid={target_hyvid}")
    log("Downloading playlist...")
    playlist_text = http_get_text(m3u8_url, headers)

    if "#EXT-X-STREAM-INF" in playlist_text:
        media_playlist_url = pick_highest_bandwidth_variant(m3u8_url, playlist_text)
        log(f"Using variant playlist: {media_playlist_url}")
        media_playlist_text = http_get_text(media_playlist_url, headers)
    else:
        media_playlist_url = m3u8_url
        media_playlist_text = playlist_text

    segment_urls = build_full_segment_urls(media_playlist_url, media_playlist_text, ts_template_url)

    if args.start_index > 1:
        segment_urls = segment_urls[args.start_index - 1:]

    if args.max_segments > 0:
        segment_urls = segment_urls[:args.max_segments]

    if not segment_urls:
        raise RuntimeError("No segment URLs to download")

    log(f"Total segments to download: {len(segment_urls)}")

    with open(joined_ts, "wb") as out:
        for i, seg_url in enumerate(segment_urls, 1):
            log(f"Downloading segment {i}/{len(segment_urls)}")
            out.write(http_get_bytes(seg_url, headers))

    log("Remuxing to mp4...")
    remux_ts_to_mp4(joined_ts, out_mp4)
    log(f"Saved to {out_mp4}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[x] Cancelled")
        sys.exit(1)
    except Exception as e:
        print(f"[x] {e}", file=sys.stderr)
        sys.exit(1)