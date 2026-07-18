#!/usr/bin/env python3
"""
Create 50/50 YouTube playlist thumbnail/title cards.

The left half contains the video's thumbnail. The right half contains the
centered video title. Only the final compressed JPEG cards are kept.

Install:
    python -m pip install -U yt-dlp pillow

Usage:
    python youtube_playlist_thumbnail_cards.py "PLAYLIST_URL"

Windows Command Prompt example (keep it on one line):
    python youtube_playlist_thumbnail_cards.py "https://www.youtube.com/watch?v=tNZegj60iLI&list=PLHHwmFh3owXSaW4vh6HfocmADUY8xh5m0"

Smaller files:
    python youtube_playlist_thumbnail_cards.py "PLAYLIST_URL" --half-width 400 --quality 65 --max-kb 150

Larger / higher quality:
    python youtube_playlist_thumbnail_cards.py "PLAYLIST_URL" --half-width 640 --quality 82 --max-kb 350
"""

from __future__ import annotations

import argparse
import html
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Iterable, Optional

import yt_dlp
from PIL import Image, ImageDraw, ImageFont, ImageOps


DEFAULT_PLAYLIST_URL = (
    "https://www.youtube.com/watch?v=tNZegj60iLI"
    "&list=PLHHwmFh3owXSaW4vh6HfocmADUY8xh5m0"
)
DEFAULT_OUTPUT_DIR = "youtube_thumbnail_cards"
DEFAULT_HALF_WIDTH = 480          # Final width is twice this: 960 px
DEFAULT_JPEG_QUALITY = 72
DEFAULT_MAX_KB = 220              # Adaptive compression target; 0 disables
DEFAULT_MIN_QUALITY = 35
DEFAULT_DELAY_SECONDS = 0.15
MAX_FILENAME_CHARS = 180


@dataclass
class VideoItem:
    index: int
    video_id: str
    url: str
    title_hint: str


@dataclass
class SaveResult:
    path: Path
    byte_count: int
    quality: int
    size: tuple[int, int]


def log(message: str = "") -> None:
    stamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = html.unescape(str(value))
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def sanitize_filename(value: str) -> str:
    value = clean_text(value)
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    return value or "Untitled"


def output_path_for(output_dir: Path, index: int, title: str, overwrite: bool) -> Path:
    safe_title = sanitize_filename(title)
    prefix = f"[{index:03d}] "
    max_title_chars = max(20, MAX_FILENAME_CHARS - len(prefix))
    safe_title = safe_title[:max_title_chars].rstrip(" .")
    base = f"{prefix}{safe_title}"
    path = output_dir / f"{base}.jpg"

    if overwrite or not path.exists():
        return path

    counter = 2
    while True:
        suffix = f" ({counter})"
        trimmed = base[: MAX_FILENAME_CHARS - len(suffix)].rstrip(" .")
        candidate = output_dir / f"{trimmed}{suffix}.jpg"
        if not candidate.exists():
            return candidate
        counter += 1


def find_font(size: int) -> ImageFont.ImageFont:
    candidates = [
        os.environ.get("FONT_PATH", ""),
        r"C:\Windows\Fonts\arialbd.ttf",
        r"C:\Windows\Fonts\segoeuib.ttf",
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\segoeui.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
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
        except (OSError, ValueError):
            continue

    return ImageFont.load_default()


def text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0]


def split_long_word(
    draw: ImageDraw.ImageDraw,
    word: str,
    font: ImageFont.ImageFont,
    max_width: int,
) -> list[str]:
    pieces: list[str] = []
    current = ""

    for char in word:
        test = current + char
        if not current or text_width(draw, test, font) <= max_width:
            current = test
        else:
            pieces.append(current)
            current = char

    if current:
        pieces.append(current)
    return pieces


def wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
) -> list[str]:
    words = clean_text(text).split()
    lines: list[str] = []
    current = ""

    for word in words:
        candidate = word if not current else f"{current} {word}"
        if text_width(draw, candidate, font) <= max_width:
            current = candidate
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

    return lines or [""]


def block_bbox(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    font: ImageFont.ImageFont,
    spacing: int,
) -> tuple[int, int, int, int]:
    return draw.multiline_textbbox(
        (0, 0),
        "\n".join(lines),
        font=font,
        spacing=spacing,
        align="center",
    )


def fits_box(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    font: ImageFont.ImageFont,
    spacing: int,
    width: int,
    height: int,
) -> bool:
    bbox = block_bbox(draw, lines, font, spacing)
    return (bbox[2] - bbox[0]) <= width and (bbox[3] - bbox[1]) <= height


def fit_title(
    draw: ImageDraw.ImageDraw,
    title: str,
    box_width: int,
    box_height: int,
) -> tuple[ImageFont.ImageFont, list[str], int]:
    padding_x = max(22, int(box_width * 0.075))
    padding_y = max(18, int(box_height * 0.075))
    usable_width = max(20, box_width - 2 * padding_x)
    usable_height = max(20, box_height - 2 * padding_y)

    minimum = 12
    maximum = max(28, min(box_width, box_height) // 3)
    best: Optional[tuple[ImageFont.ImageFont, list[str], int]] = None

    low, high = minimum, maximum
    while low <= high:
        size = (low + high) // 2
        font = find_font(size)
        spacing = max(3, int(size * 0.20))
        lines = wrap_text(draw, title, font, usable_width)

        if fits_box(draw, lines, font, spacing, usable_width, usable_height):
            best = (font, lines, spacing)
            low = size + 1
        else:
            high = size - 1

    if best is not None:
        return best

    # Extremely long titles: use minimum font and shorten until it fits.
    font = find_font(minimum)
    spacing = 3
    words = clean_text(title).split()
    shortened = clean_text(title)

    while words:
        lines = wrap_text(draw, shortened, font, usable_width)
        if fits_box(draw, lines, font, spacing, usable_width, usable_height):
            return font, lines, spacing
        words.pop()
        shortened = " ".join(words).rstrip(" .") + "…"

    return font, ["…"], spacing


def create_card(thumbnail: Image.Image, title: str, half_width: int) -> Image.Image:
    # YouTube thumbnails are normally 16:9. The final card is 32:9 because
    # the left and right halves have the same dimensions.
    half_height = max(1, round(half_width * 9 / 16))

    left = ImageOps.fit(
        thumbnail.convert("RGB"),
        (half_width, half_height),
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.5),
    )

    right = Image.new("RGB", (half_width, half_height), "black")
    draw = ImageDraw.Draw(right)
    font, lines, spacing = fit_title(draw, title, half_width, half_height)
    text = "\n".join(lines)

    bbox = draw.multiline_textbbox(
        (0, 0), text, font=font, spacing=spacing, align="center"
    )
    block_width = bbox[2] - bbox[0]
    block_height = bbox[3] - bbox[1]
    x = (half_width - block_width) / 2 - bbox[0]
    y = (half_height - block_height) / 2 - bbox[1]

    draw.multiline_text(
        (x, y),
        text,
        fill="white",
        font=font,
        spacing=spacing,
        align="center",
    )

    final = Image.new("RGB", (half_width * 2, half_height), "black")
    final.paste(left, (0, 0))
    final.paste(right, (half_width, 0))
    return final


def jpeg_bytes(image: Image.Image, quality: int) -> bytes:
    buffer = BytesIO()
    image.save(
        buffer,
        format="JPEG",
        quality=quality,
        optimize=True,
        progressive=True,
        subsampling="4:2:0",
    )
    return buffer.getvalue()


def save_compressed_jpeg(
    image: Image.Image,
    path: Path,
    start_quality: int,
    min_quality: int,
    max_kb: int,
) -> SaveResult:
    start_quality = max(1, min(95, start_quality))
    min_quality = max(1, min(start_quality, min_quality))
    max_bytes = max_kb * 1024 if max_kb > 0 else 0

    current = image
    last_data = b""
    last_quality = start_quality

    while True:
        for quality in range(start_quality, min_quality - 1, -4):
            data = jpeg_bytes(current, quality)
            last_data = data
            last_quality = quality
            if not max_bytes or len(data) <= max_bytes:
                path.write_bytes(data)
                return SaveResult(path, len(data), quality, current.size)

        # Quality is already low; make the whole 50/50 card smaller while
        # preserving its aspect ratio and try again.
        if current.width <= 640 or current.height <= 180:
            path.write_bytes(last_data)
            return SaveResult(path, len(last_data), last_quality, current.size)

        new_width = max(640, int(current.width * 0.90))
        new_height = max(180, round(new_width * current.height / current.width))
        current = current.resize(
            (new_width, new_height),
            Image.Resampling.LANCZOS,
        )


def extract_playlist_items(
    playlist_url: str,
    cookiefile: Optional[str],
) -> tuple[str, list[VideoItem]]:
    """Read playlist metadata with yt-dlp without downloading media."""
    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": False,
        "ignoreerrors": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "noplaylist": False,
    }
    if cookiefile:
        options["cookiefile"] = cookiefile

    log("Reading playlist metadata with yt-dlp...")
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(playlist_url, download=False)

    if not info:
        raise RuntimeError("yt-dlp returned no playlist information")

    playlist_title = (
        clean_text(info.get("title"))
        or clean_text(info.get("playlist_title"))
        or "YouTube Playlist"
    )

    raw_entries = info.get("entries")
    if raw_entries is None:
        # A normal video URL without a playlist is still accepted and becomes
        # a one-item job.
        raw_entries = [info]

    items: list[VideoItem] = []
    for fallback_index, entry in enumerate(raw_entries, start=1):
        if not entry:
            log(f"[WARN] Skipping unavailable playlist item {fallback_index}")
            continue

        video_id = clean_text(entry.get("id"))
        title_hint = clean_text(entry.get("title")) or f"Video {fallback_index}"
        raw_url = clean_text(entry.get("webpage_url") or entry.get("url"))

        if raw_url.startswith(("http://", "https://")):
            video_url = raw_url
        elif video_id:
            video_url = f"https://www.youtube.com/watch?v={video_id}"
        else:
            log(f"[WARN] Skipping playlist item {fallback_index}; no video URL or id")
            continue

        raw_index = entry.get("playlist_index")
        try:
            playlist_index = int(raw_index) if raw_index is not None else fallback_index
        except (TypeError, ValueError):
            playlist_index = fallback_index

        items.append(
            VideoItem(
                index=playlist_index,
                video_id=video_id,
                url=video_url,
                title_hint=title_hint,
            )
        )

    if not items:
        raise RuntimeError(
            "No downloadable videos were found in the playlist. "
            "The playlist may be private, empty, or require cookies."
        )

    return playlist_title, items


def download_thumbnail_with_ytdlp(
    url: str,
    cookiefile: Optional[str],
) -> tuple[dict[str, Any], Image.Image, str]:
    """Use yt-dlp to write the best thumbnail, without downloading the video."""
    with TemporaryDirectory(prefix="yt_thumb_") as temp_name:
        temp_dir = Path(temp_name)
        output_template = str(temp_dir / "%(id)s.%(ext)s")

        options: dict[str, Any] = {
            "quiet": True,
            "no_warnings": False,
            "ignoreerrors": False,
            "ignore_no_formats_error": True,
            "skip_download": True,
            "writethumbnail": True,
            "write_all_thumbnails": False,
            "noplaylist": True,
            "outtmpl": output_template,
        }
        if cookiefile:
            options["cookiefile"] = cookiefile

        with yt_dlp.YoutubeDL(options) as ydl:
            # download=True is intentional: skip_download prevents the video,
            # while writethumbnail still writes the related thumbnail file.
            info = ydl.extract_info(url, download=True)

        if not info:
            raise RuntimeError("yt-dlp returned no video information")

        image_extensions = {
            ".jpg", ".jpeg", ".png", ".webp", ".avif", ".bmp", ".gif"
        }
        candidates = [
            path for path in temp_dir.iterdir()
            if path.is_file() and path.suffix.lower() in image_extensions
        ]
        if not candidates:
            raise RuntimeError("yt-dlp did not write a thumbnail file")

        # Normally there is one file. Choosing the largest is a safe fallback
        # if an extractor writes more than one candidate.
        thumbnail_path = max(candidates, key=lambda path: path.stat().st_size)
        with Image.open(thumbnail_path) as opened:
            opened.load()
            thumbnail = opened.convert("RGB")

        return info, thumbnail, thumbnail_path.suffix.lower()


def human_size(byte_count: int) -> str:
    if byte_count < 1024:
        return f"{byte_count} B"
    if byte_count < 1024 * 1024:
        return f"{byte_count / 1024:.1f} KB"
    return f"{byte_count / (1024 * 1024):.2f} MB"


def process_playlist(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    playlist_title, items = extract_playlist_items(args.playlist_url, args.cookies)
    log(f"Playlist: {playlist_title}")
    log(f"Videos found: {len(items)}")
    log(f"Output: {output_dir}")
    log(
        "Card settings: "
        f"{args.half_width * 2}x{round(args.half_width * 9 / 16)}, "
        f"JPEG quality {args.quality}, "
        f"max {args.max_kb} KB" if args.max_kb else
        f"Card settings: {args.half_width * 2}x{round(args.half_width * 9 / 16)}, "
        f"JPEG quality {args.quality}, no file-size cap"
    )

    successful = 0
    failed = 0

    for position, item in enumerate(items, start=1):
        log("")
        log("=" * 78)
        log(f"[{position}/{len(items)}] {item.title_hint or item.video_id or item.url}")

        try:
            log("Downloading best thumbnail with yt-dlp; video download is disabled...")
            info, thumbnail, source_extension = download_thumbnail_with_ytdlp(
                item.url, args.cookies
            )
            title = clean_text(info.get("title")) or item.title_hint or item.video_id or "Untitled"

            log(f"Title: {title}")
            log(
                f"Source thumbnail: {thumbnail.width}x{thumbnail.height} "
                f"({source_extension or 'unknown format'})"
            )

            card = create_card(thumbnail, title, args.half_width)
            output_path = output_path_for(
                output_dir,
                item.index,
                title,
                overwrite=args.overwrite,
            )
            result = save_compressed_jpeg(
                card,
                output_path,
                start_quality=args.quality,
                min_quality=args.min_quality,
                max_kb=args.max_kb,
            )

            log(
                f"[OK] Saved: {result.path.name} | "
                f"{result.size[0]}x{result.size[1]} | "
                f"quality={result.quality} | {human_size(result.byte_count)}"
            )
            successful += 1

        except KeyboardInterrupt:
            raise
        except Exception as exc:
            log(f"[FAILED] {item.url}")
            log(f"Reason: {exc}")
            failed += 1

        if position < len(items) and args.delay > 0:
            time.sleep(args.delay)

    log("")
    log("Done.")
    log(f"Successful: {successful}")
    log(f"Failed: {failed}")
    log(f"Output folder: {output_dir}")
    return 0 if failed == 0 else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Download every playlist thumbnail with yt-dlp and create a "
            "compressed 50/50 thumbnail-and-title JPEG card."
        )
    )
    parser.add_argument(
        "playlist_url",
        nargs="?",
        default=DEFAULT_PLAYLIST_URL,
        help="YouTube playlist URL; defaults to the playlist in this script.",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output folder. Default: {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument(
        "--half-width",
        type=int,
        default=DEFAULT_HALF_WIDTH,
        help=(
            "Width of each 50%% half. Final width is twice this value. "
            f"Default: {DEFAULT_HALF_WIDTH}"
        ),
    )
    parser.add_argument(
        "--quality",
        type=int,
        default=DEFAULT_JPEG_QUALITY,
        help=f"Starting JPEG quality, 1-95. Default: {DEFAULT_JPEG_QUALITY}",
    )
    parser.add_argument(
        "--min-quality",
        type=int,
        default=DEFAULT_MIN_QUALITY,
        help=(
            "Lowest JPEG quality used while meeting --max-kb. "
            f"Default: {DEFAULT_MIN_QUALITY}"
        ),
    )
    parser.add_argument(
        "--max-kb",
        type=int,
        default=DEFAULT_MAX_KB,
        help=(
            "Approximate maximum file size per final card. The script lowers "
            "quality and, only if needed, dimensions. Use 0 to disable. "
            f"Default: {DEFAULT_MAX_KB}"
        ),
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY_SECONDS,
        help=f"Delay between videos in seconds. Default: {DEFAULT_DELAY_SECONDS}",
    )
    parser.add_argument(
        "--cookies",
        help=(
            "Optional Netscape-format cookies.txt file for private, age-restricted, "
            "or login-required videos."
        ),
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace matching output files instead of adding a numeric suffix.",
    )
    return parser


def validate_args(args: argparse.Namespace) -> None:
    if args.half_width < 160:
        raise ValueError("--half-width must be at least 160")
    if not 1 <= args.quality <= 95:
        raise ValueError("--quality must be between 1 and 95")
    if not 1 <= args.min_quality <= args.quality:
        raise ValueError("--min-quality must be between 1 and --quality")
    if args.max_kb < 0:
        raise ValueError("--max-kb cannot be negative")
    if args.delay < 0:
        raise ValueError("--delay cannot be negative")
    if args.cookies and not Path(args.cookies).expanduser().is_file():
        raise ValueError(f"Cookies file not found: {args.cookies}")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        validate_args(args)
        return process_playlist(args)
    except KeyboardInterrupt:
        log("Stopped by user.")
        return 130
    except Exception as exc:
        log(f"[FATAL] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
