import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse, parse_qsl, urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from playwright.sync_api import sync_playwright

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def log(msg: str):
    try:
        print(f"[+] {msg}")
    except UnicodeEncodeError:
        safe = str(msg).encode("utf-8", errors="replace").decode("utf-8", errors="replace")
        print(f"[+] {safe}")


def warn(msg: str):
    try:
        print(f"[!] {msg}")
    except UnicodeEncodeError:
        safe = str(msg).encode("utf-8", errors="replace").decode("utf-8", errors="replace")
        print(f"[!] {safe}")


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


def detect_quality_from_url(url: str) -> str:
    m = re.search(r"(360|480|540|720|1080|1440|2160)p", url.lower())
    return m.group(1) + "P" if m else "unknown"


def detect_quality_from_playlist_text(text: str) -> str:
    best = 0
    for line in text.splitlines():
        m = re.search(r"RESOLUTION=(\d+)x(\d+)", line, re.I)
        if m:
            h = int(m.group(2))
            best = max(best, h)
    return f"{best}P" if best else "unknown"


def quality_score_from_hint(hint: str) -> int:
    m = re.search(r"(\d+)P", hint, re.I)
    if m:
        return int(m.group(1))
    return 0


def pick_highest_bandwidth_variant(master_url: str, text: str) -> str:
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    variants = []

    for i, line in enumerate(lines):
        if line.startswith("#EXT-X-STREAM-INF"):
            bw = 0
            res = None

            m = re.search(r"BANDWIDTH=(\d+)", line)
            if m:
                bw = int(m.group(1))

            r = re.search(r"RESOLUTION=(\d+)x(\d+)", line)
            if r:
                res = f"{r.group(2)}P"

            if i + 1 < len(lines) and not lines[i + 1].startswith("#"):
                url = urljoin(master_url, lines[i + 1])
                variants.append((bw, res, url))

    if not variants:
        warn("[QUALITY] No variant streams found, using master playlist directly")
        return master_url

    variants.sort(key=lambda x: x[0], reverse=True)

    log("[QUALITY] Available variants:")
    for bw, res, url in variants:
        log(f"  - {res or 'unknown'} | bandwidth={bw} | {url}")

    best = variants[0]
    log(f"[QUALITY] Selected variant: {best[1] or 'unknown'} (bandwidth={best[0]})")
    return best[2]


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
        if q.get("hyvid") == target_hyvid:
            return True
        if target_hyvid in url:
            return True
        return False
    except Exception:
        return False


def is_master_playlist(text: str) -> bool:
    return "#EXT-X-STREAM-INF" in text


def is_media_playlist(text: str) -> bool:
    return "#EXTM3U" in text and "#EXTINF" in text


def build_full_segment_urls(media_playlist_url: str, media_playlist_text: str, ts_template_url: str) -> list[str]:
    seg_paths = extract_segment_paths(media_playlist_url, media_playlist_text)
    if not seg_paths:
        raise RuntimeError("No segment paths found in playlist")

    template_q = parse_query(ts_template_url)
    full_urls = []
    for seg in seg_paths:
        full_urls.append(replace_query(seg, template_q))
    return full_urls


def choose_best_playlist(candidate_urls: list[str], headers: dict, target_hyvid: str) -> tuple[str, str]:
    checked = []
    seen = set()

    for url in candidate_urls:
        if not url or url in seen:
            continue
        seen.add(url)

        try:
            text = http_get_text(url, headers, timeout=30)
        except Exception as e:
            warn(f"[PLAYLIST] Failed to fetch candidate: {url} | {e}")
            continue

        master = is_master_playlist(text)
        media = is_media_playlist(text)
        matches_target = url_matches_target(url, target_hyvid)
        url_q = detect_quality_from_url(url)
        text_q = detect_quality_from_playlist_text(text)

        checked.append({
            "url": url,
            "text": text,
            "master": master,
            "media": media,
            "matches_target": matches_target,
            "url_quality": url_q,
            "text_quality": text_q,
        })

    if not checked:
        raise RuntimeError("Could not fetch any captured playlist candidates")

    log("[PLAYLIST] Candidate playlists checked:")
    for c in checked:
        kind = "master" if c["master"] else ("media" if c["media"] else "unknown")
        log(
            f"  - kind={kind} target_match={c['matches_target']} "
            f"url_hint={c['url_quality']} text_hint={c['text_quality']} {c['url']}"
        )

    def playlist_score(c):
        return max(
            quality_score_from_hint(c["url_quality"]),
            quality_score_from_hint(c["text_quality"]),
        )

    masters = [c for c in checked if c["master"] and c["matches_target"]]
    if not masters:
        masters = [c for c in checked if c["master"]]
    if masters:
        masters.sort(key=playlist_score, reverse=True)
        chosen = masters[0]
        log(f"[PLAYLIST] Using master playlist: {chosen['url']}")
        return chosen["url"], chosen["text"]

    media = [c for c in checked if c["media"] and c["matches_target"]]
    if not media:
        media = [c for c in checked if c["media"]]
    if media:
        media.sort(key=playlist_score, reverse=True)
        chosen = media[0]
        log(f"[PLAYLIST] Using best media playlist fallback: {chosen['url']}")
        return chosen["url"], chosen["text"]

    chosen = checked[0]
    warn(f"[PLAYLIST] Falling back to unknown playlist type: {chosen['url']}")
    return chosen["url"], chosen["text"]


def auto_capture_urls(page_url: str, profile_dir: str, timeout_seconds: float):
    target_hyvid = extract_target_hyvid(page_url)

    found = {
        "m3u8_urls": [],
        "ts": None,
    }

    def clear_found():
        found["m3u8_urls"] = []
        found["ts"] = None

    def add_unique(lst, value):
        if value not in lst:
            lst.append(value)

    def parse_quality_label(text: str) -> int:
        if not text:
            return 0
        m = re.search(r"(\d+)\s*P", text.upper())
        return int(m.group(1)) if m else 0

    def ensure_max_quality(page, timeout_ms: int = 15000) -> str | None:
        end = time.time() + (timeout_ms / 1000.0)
        best_label = None

        while time.time() < end:
            try:
                try:
                    page.locator("#nimo-player, .nimo-player, .video-player, video").first.hover(timeout=1000)
                except Exception:
                    pass

                for sel in [".play-btn", ".nimo-icon-web-play", ".nimo-icon-play-btn", "video"]:
                    try:
                        page.locator(sel).first.click(timeout=500)
                        break
                    except Exception:
                        pass

                rate_current = page.locator(".rate-current").first
                rate_control = page.locator(".rate-control").first
                rate_items = page.locator(".rate-control_item")

                if rate_current.count() == 0 or rate_control.count() == 0:
                    time.sleep(0.5)
                    continue

                current_text = (rate_current.text_content() or "").strip()
                current_q = parse_quality_label(current_text)
                log(f"[QUALITY] Current UI quality: {current_text or 'unknown'}")

                try:
                    rate_control.hover(timeout=1000)
                except Exception:
                    pass
                try:
                    rate_current.click(timeout=1000)
                except Exception:
                    try:
                        rate_control.click(timeout=1000)
                    except Exception:
                        pass

                if rate_items.count() == 0:
                    time.sleep(0.5)
                    continue

                best_q = -1
                best_idx = None
                best_label = None

                for i in range(rate_items.count()):
                    text = (rate_items.nth(i).text_content() or "").strip()
                    q = parse_quality_label(text)
                    log(f"[QUALITY] Menu option found: {text or 'unknown'}")
                    if q > best_q:
                        best_q = q
                        best_idx = i
                        best_label = text

                if best_idx is None:
                    log(f"[QUALITY] Player UI set to: {current_text or 'unknown'}")
                    return current_text or None

                if current_q < best_q:
                    rate_items.nth(best_idx).click(timeout=2000)

                    try:
                        page.wait_for_function(
                            """target => {
                                const el = document.querySelector('.rate-current');
                                return el && el.textContent && el.textContent.trim().toUpperCase() === target.toUpperCase();
                            }""",
                            arg=best_label,
                            timeout=5000,
                        )
                    except Exception:
                        pass

                    log(f"Switched quality from {current_text or 'unknown'} to {best_label}")
                    log(f"[QUALITY] Player UI set to: {best_label or current_text}")
                    return best_label

                log(f"Quality already at max: {current_text}")
                log(f"[QUALITY] Player UI set to: {current_text or best_label or 'unknown'}")
                return current_text or best_label

            except Exception:
                time.sleep(0.5)

        warn("Could not confirm quality switch before capture")
        if best_label:
            log(f"[QUALITY] Last best UI quality seen: {best_label}")
        return best_label

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

            if ".m3u8" in lu and url_matches_target(url, target_hyvid):
                add_unique(found["m3u8_urls"], url)
                log(f"Captured target m3u8: {url}")

            if ".ts" in lu and url_matches_target(url, target_hyvid):
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

        for _ in range(3):
            for sel in selectors:
                try:
                    page.locator(sel).first.click(timeout=500)
                except Exception:
                    pass
            try:
                page.keyboard.press("Space")
            except Exception:
                pass
            time.sleep(0.5)

        selected_quality = ensure_max_quality(page)
        if selected_quality:
            log(f"[QUALITY] Confirmed player quality target: {selected_quality}")

        log("[QUALITY] Waiting after quality change so the player can request new playlists...")
        time.sleep(4)

        clear_found()
        log("[QUALITY] Cleared previously captured URLs after quality switch")

        try:
            page.reload(wait_until="domcontentloaded", timeout=90000)
            log("[QUALITY] Reloaded page to force fresh playlist requests")
        except Exception as e:
            warn(f"[QUALITY] Reload failed: {e}")

        for _ in range(3):
            for sel in selectors:
                try:
                    page.locator(sel).first.click(timeout=500)
                except Exception:
                    pass
            try:
                page.locator("#nimo-player, .nimo-player, .video-player, video").first.hover(timeout=1000)
            except Exception:
                pass
            try:
                page.keyboard.press("Space")
            except Exception:
                pass
            time.sleep(0.8)

        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            if found["m3u8_urls"] and found["ts"]:
                break

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

            try:
                page.locator("#nimo-player, .nimo-player, .video-player, video").first.hover(timeout=500)
            except Exception:
                pass

            time.sleep(1)

        context.close()

    if not found["m3u8_urls"]:
        raise RuntimeError(f"No target .m3u8 captured for hyvid={target_hyvid}")
    if not found["ts"]:
        raise RuntimeError(f"No target .ts captured for hyvid={target_hyvid}")

    log(f"[QUALITY] Captured {len(found['m3u8_urls'])} playlist candidate(s)")
    for u in found["m3u8_urls"]:
        log(f"[QUALITY] Candidate URL hint: {detect_quality_from_url(u)} | {u}")

    log(f"[QUALITY] Final captured ts URL hint: {detect_quality_from_url(found['ts'])}")

    return found["m3u8_urls"], found["ts"], ua, target_hyvid


def get_playlist_and_segments(page_url: str, profile_dir: str, detect_timeout: float):
    m3u8_urls, ts_template_url, ua, target_hyvid = auto_capture_urls(
        page_url,
        profile_dir,
        detect_timeout,
    )

    headers = {
        "User-Agent": ua,
        "Referer": "https://www.nimo.tv/",
    }

    log(f"Locked to target hyvid={target_hyvid}")
    log("Downloading playlist candidates...")

    chosen_playlist_url, playlist_text = choose_best_playlist(m3u8_urls, headers, target_hyvid)

    if is_master_playlist(playlist_text):
        log(f"[QUALITY] Master playlist detected from URL hint: {detect_quality_from_url(chosen_playlist_url)}")
        media_playlist_url = pick_highest_bandwidth_variant(chosen_playlist_url, playlist_text)
        log(f"Using variant playlist: {media_playlist_url}")
        log(f"[QUALITY] Variant URL hint: {detect_quality_from_url(media_playlist_url)}")
        media_playlist_text = http_get_text(media_playlist_url, headers)
    else:
        media_playlist_url = chosen_playlist_url
        media_playlist_text = playlist_text
        log(f"[QUALITY] Media playlist in use; URL hint: {detect_quality_from_url(media_playlist_url)}")

    segment_urls = build_full_segment_urls(media_playlist_url, media_playlist_text, ts_template_url)
    if not segment_urls:
        raise RuntimeError("No segment URLs to download")

    log(f"[QUALITY] First segment URL sample:")
    log(f"  {segment_urls[0][:160]}...")

    return headers, target_hyvid, segment_urls


def load_progress(progress_file: Path):
    if not progress_file.exists():
        return None
    try:
        return json.loads(progress_file.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_progress(progress_file: Path, data: dict):
    progress_file.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def delete_if_exists(path: Path):
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def is_refreshable_error(exc: Exception) -> bool:
    text = str(exc).lower()
    if isinstance(exc, HTTPError):
        if exc.code in (401, 403):
            return True
    if "403" in text or "401" in text or "forbidden" in text or "unauthorized" in text:
        return True
    if "timed out" in text or "timeout" in text or "remote end closed connection" in text:
        return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url", help="Nimo page URL")
    ap.add_argument("-o", "--output", default="video.mp4")
    ap.add_argument("--profile-dir", default="nimo_profile")
    ap.add_argument("--detect-timeout", type=float, default=30.0)
    ap.add_argument("--start-index", type=int, default=1)
    ap.add_argument("--max-segments", type=int, default=0)
    ap.add_argument("--refresh-retries", type=int, default=20)
    ap.add_argument("--request-timeout", type=int, default=60)
    args = ap.parse_args()

    out_mp4 = Path(args.output).resolve()
    joined_ts = out_mp4.with_suffix(".joined.ts")
    progress_file = out_mp4.with_suffix(".progress.json")

    progress = load_progress(progress_file)
    if progress and progress.get("url") == args.url:
        current_index = int(progress.get("next_index", 1))
        log(f"Resuming from saved progress at segment {current_index}")
    else:
        current_index = max(1, args.start_index)

    headers, target_hyvid, segment_urls = get_playlist_and_segments(
        args.url,
        args.profile_dir,
        args.detect_timeout,
    )

    if args.max_segments > 0:
        segment_urls = segment_urls[:args.max_segments]

    total_segments = len(segment_urls)
    log(f"Total segments to download: {total_segments}")

    if current_index > total_segments:
        log("All segments already downloaded, going to remux")
    else:
        refresh_count = 0
        mode = "ab" if joined_ts.exists() and current_index > 1 else "wb"

        while current_index <= total_segments:
            try:
                with open(joined_ts, mode) as out:
                    for i in range(current_index, total_segments + 1):
                        seg_url = segment_urls[i - 1]

                        if i == current_index:
                            log(f"[QUALITY] Downloading from segment URL hint: {detect_quality_from_url(seg_url)}")

                        if i == current_index or i % 25 == 0 or i == total_segments:
                            log(f"Downloading segment {i}/{total_segments}")

                        data = http_get_bytes(seg_url, headers, timeout=args.request_timeout)
                        out.write(data)

                        save_progress(progress_file, {
                            "url": args.url,
                            "target_hyvid": target_hyvid,
                            "next_index": i + 1,
                            "total_segments": total_segments,
                            "output": str(out_mp4),
                            "updated_at": int(time.time()),
                        })

                        current_index = i + 1

                break

            except Exception as e:
                if not is_refreshable_error(e):
                    raise

                refresh_count += 1
                if refresh_count > args.refresh_retries:
                    raise RuntimeError(
                        f"Too many refresh attempts ({args.refresh_retries}) at segment {current_index}: {e}"
                    ) from e

                warn(f"Request failed at segment {current_index}: {e}")
                warn(f"Refreshing signed URLs (attempt {refresh_count}/{args.refresh_retries})...")

                headers, target_hyvid, fresh_segment_urls = get_playlist_and_segments(
                    args.url,
                    args.profile_dir,
                    args.detect_timeout,
                )

                if args.max_segments > 0:
                    fresh_segment_urls = fresh_segment_urls[:args.max_segments]

                if len(fresh_segment_urls) != total_segments:
                    warn(
                        f"Playlist length changed from {total_segments} to {len(fresh_segment_urls)}; "
                        f"continuing with fresh playlist"
                    )
                    total_segments = len(fresh_segment_urls)

                segment_urls = fresh_segment_urls
                mode = "ab"
                time.sleep(2)

    if not joined_ts.exists():
        raise RuntimeError("joined.ts was not created")

    log("Remuxing to mp4...")
    remux_ts_to_mp4(joined_ts, out_mp4)
    log(f"Saved to {out_mp4}")

    delete_if_exists(progress_file)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[x] Cancelled")
        sys.exit(1)
    except Exception as e:
        try:
            print(f"[x] {e}", file=sys.stderr)
        except UnicodeEncodeError:
            safe = str(e).encode("utf-8", errors="replace").decode("utf-8", errors="replace")
            print(f"[x] {safe}", file=sys.stderr)
        sys.exit(1)