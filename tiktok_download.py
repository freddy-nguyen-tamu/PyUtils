import re
import sys
import shutil
import subprocess
from pathlib import Path
from datetime import datetime


# ================== CONFIGURATION ==================
TXT_FILE = "ndhang.txt"

DOWNLOAD_DIR = Path.cwd() / "downloads"
TMP_DIR = Path.cwd() / "_tmp_tiktok_downloads"

FAILED_FILE = Path.cwd() / "failed_urls.txt"
SKIPPED_FILE = Path.cwd() / "skipped_urls.txt"
SUCCESS_FILE = Path.cwd() / "successful_urls.txt"

COOKIES_BROWSER = "firefox"

VIDEO_TOOL = "yt-dlp"
PHOTO_TOOL = "gallery-dl"

# If True, the script skips a URL when any existing file in downloads
# contains the TikTok video/photo ID in its filename.
SKIP_IF_ID_EXISTS_IN_DESTINATION = True

DOWNLOAD_DIR.mkdir(exist_ok=True)
TMP_DIR.mkdir(exist_ok=True)


# ================== LOGGING ==================
def log(message=""):
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def write_line(path, text):
    with open(path, "a", encoding="utf-8") as f:
        f.write(text + "\n")


# ================== URL EXTRACTION ==================
def extract_tiktok_urls(file_path):
    urls = []

    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            cleaned = re.sub(r"^webmssdk\.js:1\s+", "", line.strip())

            found_urls = re.findall(r"https://[^\s\"'<>]+", cleaned)

            for url in found_urls:
                url = url.strip().rstrip("),.;]'\"")

                if "tiktok.com" not in url:
                    continue

                if "/video/" not in url and "/photo/" not in url:
                    continue

                if url not in urls:
                    urls.append(url)

    return urls


def get_tiktok_kind_and_id(url):
    match = re.search(r"/(video|photo)/(\d+)", url)

    if not match:
        return None, None

    kind = match.group(1)
    tiktok_id = match.group(2)

    return kind, tiktok_id


# ================== DESTINATION SKIP CHECK ==================
def find_existing_files_for_id(tiktok_id):
    matches = []

    if not DOWNLOAD_DIR.exists():
        return matches

    for path in DOWNLOAD_DIR.rglob("*"):
        if not path.is_file():
            continue

        if path.name.endswith(".part"):
            continue

        if path.name.endswith(".crdownload"):
            continue

        if tiktok_id in path.name:
            matches.append(path)

    return matches


def should_skip_url(tiktok_id):
    if not SKIP_IF_ID_EXISTS_IN_DESTINATION:
        return False, []

    existing = find_existing_files_for_id(tiktok_id)

    return len(existing) > 0, existing


# ================== COMMAND RUNNER ==================
def run_command(command):
    log("Running command:")
    log(" ".join(f'"{x}"' if " " in str(x) else str(x) for x in command))

    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )

        for line in process.stdout:
            print(line, end="", flush=True)

        process.wait()

        return process.returncode

    except FileNotFoundError:
        log(f"ERROR: Command not found: {command[0]}")
        return 127

    except Exception as e:
        log(f"ERROR while running command: {type(e).__name__}: {e}")
        return 1


# ================== FILE MOVING ==================
def make_unique_destination_path(dest_path):
    if not dest_path.exists():
        return dest_path

    stem = dest_path.stem
    suffix = dest_path.suffix
    parent = dest_path.parent

    counter = 1

    while True:
        candidate = parent / f"{stem} ({counter}){suffix}"

        if not candidate.exists():
            return candidate

        counter += 1


def move_completed_files_from_tmp(tmp_item_dir):
    moved_files = []

    for path in tmp_item_dir.rglob("*"):
        if not path.is_file():
            continue

        lower_name = path.name.lower()

        if lower_name.endswith(".part"):
            log(f"Leaving unfinished temp file: {path}")
            continue

        if lower_name.endswith(".crdownload"):
            log(f"Leaving unfinished Chrome temp file: {path}")
            continue

        if lower_name.endswith(".ytdl"):
            log(f"Leaving yt-dlp temp file: {path}")
            continue

        dest_path = DOWNLOAD_DIR / path.name

        if dest_path.exists():
            log(f"Destination already has file with same name, skipping move: {dest_path.name}")
            continue

        final_dest = make_unique_destination_path(dest_path)

        shutil.move(str(path), str(final_dest))
        moved_files.append(final_dest)

        log(f"Moved to downloads: {final_dest.name}")

    return moved_files


def clear_tmp_item_dir(tmp_item_dir):
    if tmp_item_dir.exists():
        shutil.rmtree(tmp_item_dir, ignore_errors=True)

    tmp_item_dir.mkdir(parents=True, exist_ok=True)


# ================== DOWNLOADERS ==================
def download_video_with_ytdlp(url, tiktok_id, tmp_item_dir):
    output_template = str(tmp_item_dir / "%(uploader)s_%(id)s.%(ext)s")

    command = [
        VIDEO_TOOL,
        url,
        "--cookies-from-browser",
        COOKIES_BROWSER,
        "--windows-filenames",
        "--no-overwrites",
        "--restrict-filenames",
        "-o",
        output_template,
    ]

    return run_command(command)


def download_photo_with_gallerydl(url, tiktok_id, tmp_item_dir):
    command = [
        PHOTO_TOOL,
        "--cookies-from-browser",
        COOKIES_BROWSER,
        "--directory",
        str(tmp_item_dir),
        url,
    ]

    return run_command(command)


# ================== TOOL CHECK ==================
def check_required_tools():
    missing = []

    if shutil.which(VIDEO_TOOL) is None:
        missing.append(VIDEO_TOOL)

    if shutil.which(PHOTO_TOOL) is None:
        missing.append(PHOTO_TOOL)

    if missing:
        log("Missing required command-line tools:")
        for tool in missing:
            log(f"  - {tool}")

        log("")
        log("Install missing tools, then run again.")
        log("Example:")
        log("  pip install -U yt-dlp gallery-dl")
        sys.exit(1)


# ================== MAIN ==================
def main():
    check_required_tools()

    FAILED_FILE.write_text("", encoding="utf-8")
    SKIPPED_FILE.write_text("", encoding="utf-8")
    SUCCESS_FILE.write_text("", encoding="utf-8")

    urls = extract_tiktok_urls(TXT_FILE)

    if not urls:
        log("No TikTok /video/ or /photo/ URLs found.")
        return

    log(f"Found {len(urls)} TikTok URLs.")
    log(f"Final download folder: {DOWNLOAD_DIR.resolve()}")
    log(f"Temporary folder: {TMP_DIR.resolve()}")
    log("")

    success_count = 0
    skipped_count = 0
    failed_count = 0

    for index, url in enumerate(urls, start=1):
        log("=" * 80)
        log(f"[{index}/{len(urls)}] URL: {url}")

        kind, tiktok_id = get_tiktok_kind_and_id(url)

        if not kind or not tiktok_id:
            log("Could not detect TikTok type or ID. Marking failed.")
            write_line(FAILED_FILE, f"{url} | Could not detect type or ID")
            failed_count += 1
            continue

        log(f"Detected type: {kind}")
        log(f"Detected ID: {tiktok_id}")

        skip, existing_files = should_skip_url(tiktok_id)

        if skip:
            log("Skipping because destination already contains file(s) with this TikTok ID:")

            for existing in existing_files:
                log(f"  Existing: {existing}")

            write_line(SKIPPED_FILE, f"{url} | ID already exists in downloads: {tiktok_id}")
            skipped_count += 1
            continue

        tmp_item_dir = TMP_DIR / f"{kind}_{tiktok_id}"
        clear_tmp_item_dir(tmp_item_dir)

        if kind == "video":
            return_code = download_video_with_ytdlp(url, tiktok_id, tmp_item_dir)
        elif kind == "photo":
            return_code = download_photo_with_gallerydl(url, tiktok_id, tmp_item_dir)
        else:
            log(f"Unknown TikTok type: {kind}")
            write_line(FAILED_FILE, f"{url} | Unknown TikTok type: {kind}")
            failed_count += 1
            continue

        if return_code != 0:
            log(f"Download command failed with exit code: {return_code}")
            write_line(FAILED_FILE, f"{url} | Command failed with exit code {return_code}")
            failed_count += 1
            continue

        moved_files = move_completed_files_from_tmp(tmp_item_dir)

        if not moved_files:
            log("Command finished, but no completed files were found to move.")
            write_line(FAILED_FILE, f"{url} | No completed files found after download")
            failed_count += 1
            continue

        log("Download completed successfully.")
        write_line(SUCCESS_FILE, f"{url} | {tiktok_id} | {len(moved_files)} file(s)")
        success_count += 1

    log("=" * 80)
    log("SUMMARY")
    log(f"Successful downloads: {success_count}")
    log(f"Skipped existing:      {skipped_count}")
    log(f"Failed:                {failed_count}")
    log("")
    log(f"Success log: {SUCCESS_FILE.resolve()}")
    log(f"Skipped log: {SKIPPED_FILE.resolve()}")
    log(f"Failed log:  {FAILED_FILE.resolve()}")
    log(f"Downloads:   {DOWNLOAD_DIR.resolve()}")


if __name__ == "__main__":
    main()