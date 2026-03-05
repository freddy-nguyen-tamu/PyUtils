#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow

from selenium import webdriver
from selenium.webdriver.chrome.options import Options


REPLAY_PAGE = "https://www.nimo.tv/user/[]"

HISTORY_FILE = "history.json"
CLIENT_SECRET_FILE = "client_secret.json"
TOKEN_FILE = "youtube_token.json"
DOWNLOAD_DIR = "downloads"
NIMO_DOWNLOADER = "nimo.py"
AUTO_STATE_FILE = "auto_state.json"

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

YOUTUBE_PRIVACY_STATUS = "private"
DELETE_AFTER_SUCCESS = True


def log(msg):
    print(f"[+] {msg}")


def warn(msg):
    print(f"[!] {msg}")


def write_auto_state(can_clear, reason):
    data = {
        "can_clear_downloads": bool(can_clear),
        "reason": reason,
        "time": int(time.time()),
    }
    with open(AUTO_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    log(f"[AUTO_STATE] {data}")


def load_history():
    if not os.path.exists(HISTORY_FILE):
        return {"uploaded": {}, "seen": {}}
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("uploaded", {})
        data.setdefault("seen", {})
        return data
    except Exception:
        return {"uploaded": {}, "seen": {}}


def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)


def sanitize_filename(name: str) -> str:
    name = unicodedata.normalize("NFKD", name)
    name = name.encode("ascii", "ignore").decode("ascii")
    name = re.sub(r'[<>:"/\\|?*]+', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:180] or "video"


def extract_video_id(url: str):
    m = re.search(r"/v/v-(\d+)", url)
    return m.group(1) if m else None


def extract_id_from_filename(path: Path):
    m = re.search(r"(\d{10,})", path.stem)
    return m.group(1) if m else None


def normalize_nimo_href(href: str) -> Optional[str]:
    if not href:
        return None

    href = href.strip()

    if href.startswith("http://") or href.startswith("https://"):
        return href

    if href.startswith("//"):
        return "https:" + href

    if href.startswith("/www.nimo.tv/"):
        return "https://" + href.lstrip("/")

    if href.startswith("www.nimo.tv/"):
        return "https://" + href

    if href.startswith("/"):
        return "https://www.nimo.tv" + href

    return None


def get_youtube():
    creds = None

    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(creds.to_json())

    return build("youtube", "v3", credentials=creds)


def upload_video(youtube, file_path, title):
    media = MediaFileUpload(file_path, resumable=True)

    request = youtube.videos().insert(
        part="snippet,status",
        body={
            "snippet": {"title": title},
            "status": {"privacyStatus": YOUTUBE_PRIVACY_STATUS},
        },
        media_body=media,
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            log(f"Upload progress: {int(status.progress() * 100)}%")

    return response["id"]


def ensure_ffmpeg():
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH")


def get_media_duration_seconds(file_path: Path) -> float:
    ensure_ffmpeg()
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(file_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    value = result.stdout.strip()
    if not value:
        raise RuntimeError(f"Could not read duration for {file_path}")
    return float(value)


def split_mp4_into_two_parts(input_mp4: Path):
    ensure_ffmpeg()

    total = get_media_duration_seconds(input_mp4)
    if total <= 2:
        raise RuntimeError(f"Video too short to split meaningfully: {input_mp4}")

    half = total / 2.0
    split_at = max(1.0, half)

    part1 = input_mp4.with_name(input_mp4.stem + " - Part 1.mp4")
    part2 = input_mp4.with_name(input_mp4.stem + " - Part 2.mp4")

    log(f"Splitting video into 2 parts at {split_at:.2f} seconds")

    cmd1 = [
        "ffmpeg",
        "-y",
        "-i", str(input_mp4),
        "-t", str(split_at),
        "-c", "copy",
        str(part1),
    ]
    subprocess.run(cmd1, check=True)

    cmd2 = [
        "ffmpeg",
        "-y",
        "-ss", str(split_at),
        "-i", str(input_mp4),
        "-c", "copy",
        str(part2),
    ]
    subprocess.run(cmd2, check=True)

    if not part1.exists() or not part2.exists():
        raise RuntimeError("Split failed: one or both part files were not created")

    return part1, part2


def is_video_too_long_error(exc: Exception) -> bool:
    text = str(exc).lower()
    markers = [
        "too long",
        "video too long",
        "video was too long",
        "upload was abandoned",
        "upload aborted",
        "uploadaborted",
        "toolong",
        "videotoolong",
    ]
    return any(marker in text for marker in markers)


def upload_with_auto_split(youtube, mp4_path: Path, title: str):
    try:
        vid_id = upload_video(youtube, str(mp4_path), title)
        return {"mode": "single", "youtube_ids": [vid_id]}
    except Exception as e:
        if not is_video_too_long_error(e):
            raise

        warn("Upload failed because video appears too long; splitting into 2 parts and retrying...")

        part1, part2 = split_mp4_into_two_parts(mp4_path)

        yt1 = upload_video(youtube, str(part1), f"{title} - Part 1")
        yt2 = upload_video(youtube, str(part2), f"{title} - Part 2")

        if DELETE_AFTER_SUCCESS:
            try:
                part1.unlink(missing_ok=True)
                part2.unlink(missing_ok=True)
            except Exception as delete_err:
                warn(f"Could not delete split parts: {delete_err}")

        return {"mode": "split", "youtube_ids": [yt1, yt2]}


def run_downloader(url, output):
    subprocess.run([sys.executable, NIMO_DOWNLOADER, url, "-o", output], check=True)


def process_leftover_mp4(history, youtube, download_dir: Path):
    mp4s = list(download_dir.glob("*.mp4"))

    if not mp4s:
        return False, False

    had_pending = False
    had_fail = False

    for mp4 in mp4s:
        vid = extract_id_from_filename(mp4)
        if not vid:
            continue

        if vid in history["uploaded"]:
            log(f"Deleting already uploaded file: {mp4}")
            mp4.unlink(missing_ok=True)
            continue

        had_pending = True

        try:
            log(f"Uploading leftover: {mp4}")
            result = upload_with_auto_split(youtube, mp4, mp4.stem)

            history["uploaded"][vid] = {
                "youtube_ids": result["youtube_ids"],
                "upload_mode": result["mode"],
            }
            save_history(history)

            if DELETE_AFTER_SUCCESS:
                mp4.unlink(missing_ok=True)

        except Exception as e:
            warn(f"Upload failed for leftover {mp4}: {e}")
            had_fail = True

    return had_pending, had_fail


def detect_resume(download_dir: Path):
    return list(download_dir.glob("*.progress.json"))


def fetch_replay_entries(url):
    options = Options()
    driver = webdriver.Chrome(options=options)
    driver.get(url)
    time.sleep(5)

    soup = BeautifulSoup(driver.page_source, "html.parser")
    driver.quit()

    results = []
    seen = set()

    for a in soup.select('a[href*="/v/v-"]'):
        href = normalize_nimo_href(a.get("href"))
        if not href:
            continue

        vid = extract_video_id(href)
        if not vid or vid in seen:
            continue

        seen.add(vid)

        results.append({
            "video_id": vid,
            "url": href,
            "title": a.text.strip() or "Replay",
            "date": "",
        })

    return results


def main():
    download_dir = Path(DOWNLOAD_DIR)
    download_dir.mkdir(exist_ok=True)

    history = load_history()

    write_auto_state(False, "startup")

    youtube = get_youtube()

    had_pending, had_fail = process_leftover_mp4(history, youtube, download_dir)

    if had_pending:
        write_auto_state(False, "leftover_mp4_present")

    resume = detect_resume(download_dir)
    if resume:
        write_auto_state(False, "resume_present")

    log("Fetching replay page...")
    entries = fetch_replay_entries(REPLAY_PAGE)

    unseen = [e for e in entries if e["video_id"] not in history["uploaded"]]

    if not unseen:
        if had_fail or resume:
            write_auto_state(False, "recoverable_work_still_present")
        else:
            write_auto_state(True, "nothing_new")
        return 0

    for e in unseen:
        try:
            base = sanitize_filename(f"{e['date']} {e['video_id']} {e['title']}".strip())
            out = download_dir / f"{base}.mp4"

            if not out.exists():
                run_downloader(e["url"], str(out))

            result = upload_with_auto_split(youtube, out, e["title"])

            history["uploaded"][e["video_id"]] = {
                "youtube_ids": result["youtube_ids"],
                "upload_mode": result["mode"],
            }
            save_history(history)

            if DELETE_AFTER_SUCCESS:
                out.unlink(missing_ok=True)

        except Exception as err:
            warn(f"Failed: {err}")
            write_auto_state(False, "failure")
            return 1

    write_auto_state(True, "all_done")
    return 0


if __name__ == "__main__":
    sys.exit(main())