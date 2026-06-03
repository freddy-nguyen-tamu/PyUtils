#!/usr/bin/env python3
"""
Download transcripts/subtitles for every YouTube livestream VOD from a channel's
/streams page, then convert .vtt captions into readable .txt and structured .json.

This patched version is safer for long channel scans:
- Defaults to Vietnamese captions only to reduce YouTube 429 rate limits.
- Adds socket timeout so one video cannot hang forever.
- Skips already-downloaded transcripts on reruns.
- Adds retry/backoff around each video instead of crashing the whole run.

Install:
    pip install -U yt-dlp

Recommended:
    python ytb_transcript_safe.py "https://www.youtube.com/@Xuanpac781/streams" ^
        --search "Bến Yên Lãng" "Độ Mixi" "Mixi" "Yen Lang Bund" "Sena" ^
        --sleep 8 --cookies-from-browser chrome
"""

from __future__ import annotations

import argparse
import html
import json
import re
import socket
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

try:
    import yt_dlp
except ImportError:
    print("Missing dependency: yt-dlp")
    print("Install it with: pip install -U yt-dlp")
    sys.exit(1)


TIMESTAMP_RE = re.compile(
    r"(?P<start>\d{2}:\d{2}:\d{2}\.\d{3}|\d{2}:\d{2}\.\d{3})\s+-->\s+"
    r"(?P<end>\d{2}:\d{2}:\d{2}\.\d{3}|\d{2}:\d{2}\.\d{3})"
)
TAG_RE = re.compile(r"<[^>]+>")
MULTISPACE_RE = re.compile(r"\s+")


def safe_print(*args: object) -> None:
    try:
        print(*args, flush=True)
    except UnicodeEncodeError:
        text = " ".join(str(a) for a in args)
        print(text.encode("utf-8", errors="replace").decode("utf-8"), flush=True)


def clean_caption_line(line: str) -> str:
    line = html.unescape(line)
    line = TAG_RE.sub("", line)
    line = line.replace("\ufeff", "")
    line = MULTISPACE_RE.sub(" ", line).strip()
    return line


def parse_vtt(vtt_path: Path) -> List[Dict[str, str]]:
    raw = vtt_path.read_text(encoding="utf-8", errors="replace").splitlines()

    cues: List[Dict[str, str]] = []
    current_start: Optional[str] = None
    current_end: Optional[str] = None
    current_lines: List[str] = []

    def flush() -> None:
        nonlocal current_start, current_end, current_lines
        if current_start and current_end and current_lines:
            text = " ".join(
                cleaned for cleaned in (clean_caption_line(x) for x in current_lines) if cleaned
            )
            text = MULTISPACE_RE.sub(" ", text).strip()
            if text:
                cues.append({"start": current_start, "end": current_end, "text": text})
        current_start = None
        current_end = None
        current_lines = []

    for line in raw:
        stripped = line.strip()

        if not stripped:
            flush()
            continue

        if stripped.startswith(("WEBVTT", "Kind:", "Language:", "NOTE", "STYLE", "REGION")):
            continue

        match = TIMESTAMP_RE.search(stripped)
        if match:
            flush()
            current_start = match.group("start")
            current_end = match.group("end")
            continue

        if stripped.isdigit() and current_start is None:
            continue

        if current_start is not None:
            current_lines.append(stripped)

    flush()

    deduped: List[Dict[str, str]] = []
    last_text = ""
    for cue in cues:
        if cue["text"] and cue["text"] != last_text:
            deduped.append(cue)
            last_text = cue["text"]

    return deduped


def cues_to_readable_text(cues: List[Dict[str, str]]) -> str:
    return "\n".join(f'[{cue["start"]}] {cue["text"]}' for cue in cues)


def find_matches(cues: List[Dict[str, str]], terms: Iterable[str]) -> List[Dict[str, str]]:
    lowered_terms = [t.lower() for t in terms if t.strip()]
    matches: List[Dict[str, str]] = []

    for cue in cues:
        haystack = cue["text"].lower()
        hit_terms = [term for term in lowered_terms if term in haystack]
        if hit_terms:
            matches.append(
                {
                    "start": cue["start"],
                    "end": cue["end"],
                    "text": cue["text"],
                    "matched_terms": ", ".join(hit_terms),
                }
            )

    return matches


def normalize_url(url: str) -> str:
    url = url.strip()
    if not url:
        raise ValueError("Empty URL")
    return url


def list_channel_streams(channel_streams_url: str, limit: Optional[int], cookies_from_browser: Optional[str]) -> List[Dict[str, Any]]:
    safe_print(f"Listing streams from: {channel_streams_url}")

    ydl_opts: Dict[str, Any] = {
        "extract_flat": "in_playlist",
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
        "playlistend": limit,
        "socket_timeout": 20,
        "extractor_args": {
            "youtube": {"tab": ["streams"]},
        },
    }

    if cookies_from_browser:
        ydl_opts["cookiesfrombrowser"] = (cookies_from_browser,)

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(channel_streams_url, download=False)

    entries = []
    for entry in (info or {}).get("entries") or []:
        if not entry:
            continue

        video_id = entry.get("id")
        title = entry.get("title") or "Untitled"
        url = entry.get("url") or entry.get("webpage_url")

        if video_id and not str(url).startswith("http"):
            url = f"https://www.youtube.com/watch?v={video_id}"

        if video_id and url:
            entries.append(
                {
                    "id": video_id,
                    "title": title,
                    "url": url,
                    "duration": entry.get("duration"),
                    "upload_date": entry.get("upload_date"),
                }
            )

    seen = set()
    unique = []
    for e in entries:
        if e["id"] not in seen:
            unique.append(e)
            seen.add(e["id"])

    safe_print(f"Found {len(unique)} stream/video entries.")
    return unique


def existing_vtt_files_for_video(video_id: str, subs_dir: Path) -> List[Path]:
    return sorted(subs_dir.glob(f"*{video_id}*.vtt"))


def download_vtt_for_video(
    video: Dict[str, Any],
    subs_dir: Path,
    langs: List[str],
    sleep_seconds: float,
    cookies_from_browser: Optional[str],
    max_retries: int,
) -> List[Path]:
    video_url = video["url"]
    video_id = video["id"]

    already = existing_vtt_files_for_video(video_id, subs_dir)
    if already:
        safe_print(f"  Already have {len(already)} VTT file(s), reusing.")
        return already

    before = set(subs_dir.glob("*.vtt"))

    ydl_opts: Dict[str, Any] = {
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": langs,
        "subtitlesformat": "vtt",
        "outtmpl": str(subs_dir / "%(upload_date>%Y%m%d|unknown)s_%(id)s_%(title).120B.%(ext)s"),
        "quiet": True,
        "no_warnings": False,
        "ignoreerrors": True,
        "retries": 1,
        "fragment_retries": 1,
        "socket_timeout": 20,
        "sleep_interval_requests": sleep_seconds,
        "extractor_args": {
            # Use web client only; this often avoids some JS/player format noise.
            "youtube": {"player_client": ["web"]},
        },
    }

    if cookies_from_browser:
        ydl_opts["cookiesfrombrowser"] = (cookies_from_browser,)

    for attempt in range(1, max_retries + 1):
        try:
            safe_print(f"  Download attempt {attempt}/{max_retries}")
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([video_url])
            break
        except KeyboardInterrupt:
            safe_print("  Interrupted by user.")
            raise
        except Exception as exc:
            safe_print(f"  ERROR: {type(exc).__name__}: {exc}")
            if attempt < max_retries:
                wait = sleep_seconds * attempt
                safe_print(f"  Waiting {wait:.1f}s before retry...")
                time.sleep(wait)

    if sleep_seconds > 0:
        time.sleep(sleep_seconds)

    after = set(subs_dir.glob("*.vtt"))
    new_files = sorted(after - before, key=lambda p: p.stat().st_mtime, reverse=True)

    same_id = existing_vtt_files_for_video(video_id, subs_dir)
    for p in same_id:
        if p not in new_files:
            new_files.append(p)

    return new_files


def convert_vtt_files(
    video: Dict[str, Any],
    vtt_files: List[Path],
    text_dir: Path,
    json_dir: Path,
    search_terms: List[str],
) -> Dict[str, Any]:
    report: Dict[str, Any] = {
        "id": video["id"],
        "title": video["title"],
        "url": video["url"],
        "vtt_files": [str(p) for p in vtt_files],
        "txt_files": [],
        "json_files": [],
        "matches": [],
    }

    for vtt in vtt_files:
        cues = parse_vtt(vtt)
        if not cues:
            continue

        base = vtt.stem
        txt_path = text_dir / f"{base}.txt"
        json_path = json_dir / f"{base}.json"

        txt_path.write_text(cues_to_readable_text(cues), encoding="utf-8")
        json_path.write_text(
            json.dumps(
                {
                    "video": video,
                    "source_vtt": str(vtt),
                    "cue_count": len(cues),
                    "cues": cues,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        report["txt_files"].append(str(txt_path))
        report["json_files"].append(str(json_path))

        if search_terms:
            matches = find_matches(cues, search_terms)
            for match in matches:
                match["file"] = str(txt_path)
                match["video_url"] = video["url"]
                match["video_title"] = video["title"]
            report["matches"].extend(matches)

    return report


def main() -> int:
    socket.setdefaulttimeout(20)

    parser = argparse.ArgumentParser(
        description="Download transcript/subtitle files for each YouTube livestream VOD."
    )
    parser.add_argument("url", help='Example: "https://www.youtube.com/@Xuanpac781/streams"')
    parser.add_argument("--out", default="youtube_stream_transcripts")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--start-index",
        type=int,
        default=1,
        help="Start from this 1-based item index. Useful after interruption.",
    )
    parser.add_argument(
        "--langs",
        nargs="+",
        default=["vi"],
        help='Subtitle languages to try. Default: vi. Use "vi en" only if you really need English too.',
    )
    parser.add_argument("--search", nargs="*", default=[])
    parser.add_argument(
        "--sleep",
        type=float,
        default=8.0,
        help="Seconds to wait between videos. Default: 8.0 to reduce HTTP 429.",
    )
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument(
        "--cookies-from-browser",
        choices=["chrome", "edge", "firefox", "brave", "chromium", "opera", "vivaldi"],
        default=None,
    )

    args = parser.parse_args()

    channel_url = normalize_url(args.url)
    out_dir = Path(args.out)
    subs_dir = out_dir / "vtt"
    text_dir = out_dir / "txt"
    json_dir = out_dir / "json"
    reports_dir = out_dir / "reports"

    for d in (subs_dir, text_dir, json_dir, reports_dir):
        d.mkdir(parents=True, exist_ok=True)

    videos = list_channel_streams(channel_url, args.limit, args.cookies_from_browser)
    if not videos:
        safe_print("No videos found. Try: pip install -U yt-dlp")
        return 2

    start_i = max(1, args.start_index)
    selected_videos = videos[start_i - 1 :]

    all_reports: List[Dict[str, Any]] = []
    all_matches: List[Dict[str, str]] = []

    for offset, video in enumerate(selected_videos, start=start_i):
        safe_print(f"\n[{offset}/{len(videos)}] {video['title']}")
        safe_print(f"  {video['url']}")

        try:
            vtt_files = download_vtt_for_video(
                video=video,
                subs_dir=subs_dir,
                langs=args.langs,
                sleep_seconds=args.sleep,
                cookies_from_browser=args.cookies_from_browser,
                max_retries=args.retries,
            )
        except KeyboardInterrupt:
            safe_print("\nStopped. Rerun with --start-index", offset)
            break

        if not vtt_files:
            safe_print("  No transcript/subtitle downloaded.")
            all_reports.append(
                {
                    "id": video["id"],
                    "title": video["title"],
                    "url": video["url"],
                    "vtt_files": [],
                    "txt_files": [],
                    "json_files": [],
                    "matches": [],
                    "status": "no transcript/subtitle downloaded",
                }
            )
            continue

        report = convert_vtt_files(video, vtt_files, text_dir, json_dir, args.search)
        all_reports.append(report)
        all_matches.extend(report["matches"])

        safe_print(f"  VTT files: {len(report['vtt_files'])}")
        safe_print(f"  TXT files: {len(report['txt_files'])}")
        if args.search:
            safe_print(f"  Matches: {len(report['matches'])}")

        # Save progress after every video.
        index_path = reports_dir / "index_partial.json"
        index_path.write_text(json.dumps(all_reports, ensure_ascii=False, indent=2), encoding="utf-8")

        if args.search:
            matches_path = reports_dir / "matches_partial.json"
            matches_path.write_text(json.dumps(all_matches, ensure_ascii=False, indent=2), encoding="utf-8")

    index_path = reports_dir / "index.json"
    index_path.write_text(json.dumps(all_reports, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.search:
        matches_path = reports_dir / "matches.json"
        matches_path.write_text(json.dumps(all_matches, ensure_ascii=False, indent=2), encoding="utf-8")

        matches_txt = reports_dir / "matches.txt"
        lines = []
        for m in all_matches:
            lines.append(
                f"{m['video_title']}\n"
                f"{m['video_url']}\n"
                f"[{m['start']}] {m['text']}\n"
                f"matched: {m['matched_terms']}\n"
            )
        matches_txt.write_text("\n---\n".join(lines), encoding="utf-8")
        safe_print(f"\nSearch report written to: {matches_txt}")

    safe_print("\nDone.")
    safe_print(f"Readable transcripts: {text_dir}")
    safe_print(f"Structured JSON:      {json_dir}")
    safe_print(f"Run report:           {index_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
