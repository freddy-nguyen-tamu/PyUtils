#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import re
import subprocess
import sys
import time
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from bs4 import BeautifulSoup

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

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


# ---------------- AUTO STATE ----------------

def write_auto_state(can_clear, reason):
    data = {
        "can_clear_downloads": bool(can_clear),
        "reason": reason,
        "time": int(time.time())
    }
    with open(AUTO_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    log(f"[AUTO_STATE] {data}")


# ---------------- HISTORY ----------------

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


# ---------------- UTIL ----------------

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


# ---------------- YOUTUBE ----------------

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

        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

    return build("youtube", "v3", credentials=creds)


def upload_video(youtube, file_path, title):
    media = MediaFileUpload(file_path, resumable=True)

    request = youtube.videos().insert(
        part="snippet,status",
        body={
            "snippet": {"title": title},
            "status": {"privacyStatus": YOUTUBE_PRIVACY_STATUS}
        },
        media_body=media
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            log(f"Upload progress: {int(status.progress() * 100)}%")

    return response["id"]


# ---------------- DOWNLOAD ----------------

def run_downloader(url, output):
    subprocess.run([sys.executable, NIMO_DOWNLOADER, url, "-o", output], check=True)


# ---------------- LEFTOVER HANDLING ----------------

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
            vid_id = upload_video(youtube, str(mp4), mp4.stem)

            history["uploaded"][vid] = {"youtube_id": vid_id}
            save_history(history)

            if DELETE_AFTER_SUCCESS:
                mp4.unlink(missing_ok=True)

        except Exception as e:
            warn(f"Upload failed for leftover {mp4}: {e}")
            had_fail = True

    return had_pending, had_fail


def detect_resume(download_dir: Path):
    return list(download_dir.glob("*.progress.json"))


# ---------------- MAIN ----------------

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
        write_auto_state(True, "nothing_new")
        return 0

    for e in unseen:
        try:
            out = download_dir / sanitize_filename(f"{e['date']} {e['video_id']} {e['title']}") + ".mp4"

            if not out.exists():
                run_downloader(e["url"], str(out))

            vid_id = upload_video(youtube, str(out), e["title"])

            history["uploaded"][e["video_id"]] = {"youtube_id": vid_id}
            save_history(history)

            if DELETE_AFTER_SUCCESS:
                out.unlink(missing_ok=True)

        except Exception as err:
            warn(f"Failed: {err}")
            write_auto_state(False, "failure")
            return 1

    write_auto_state(True, "all_done")
    return 0


# ---------------- SCRAPER ----------------

def fetch_replay_entries(url):
    driver = webdriver.Chrome(options=Options())
    driver.get(url)
    time.sleep(5)

    soup = BeautifulSoup(driver.page_source, "html.parser")
    driver.quit()

    results = []
    seen = set()

    for a in soup.select('a[href*="/v/v-"]'):
        href = a.get("href")
        if not href:
            continue

        if href.startswith("/"):
            href = "https://www.nimo.tv" + href

        vid = extract_video_id(href)
        if not vid or vid in seen:
            continue

        seen.add(vid)

        results.append({
            "video_id": vid,
            "url": href,
            "title": a.text.strip() or "Replay",
            "date": ""
        })

    return results


if __name__ == "__main__":
    sys.exit(main())