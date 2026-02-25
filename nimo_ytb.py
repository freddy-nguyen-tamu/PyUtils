#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

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

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

# change if you want
YOUTUBE_PRIVACY_STATUS = "private"

# keep downloaded files after successful upload?
DELETE_AFTER_SUCCESS = False


def log(msg: str) -> None:
    print(f"[+] {msg}")


def warn(msg: str) -> None:
    print(f"[!] {msg}")


def load_history(path: str) -> Dict:
    if not os.path.exists(path):
        return {"uploaded": {}, "seen": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "uploaded" not in data:
            data["uploaded"] = {}
        if "seen" not in data:
            data["seen"] = {}
        return data
    except Exception:
        return {"uploaded": {}, "seen": {}}


def save_history(path: str, history: Dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*]+', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:180]


def normalize_nimo_href(href: str) -> Optional[str]:
    if not href:
        return None
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return "https://www.nimo.tv" + href
    if href.startswith("http://") or href.startswith("https://"):
        return href
    return None


def extract_video_id(url: str) -> Optional[str]:
    m = re.search(r"/v/v-(\d+)", url)
    return m.group(1) if m else None


def setup_chrome_driver(headless: bool = False) -> webdriver.Chrome:
    options = Options()
    if headless:
        options.add_argument("--headless=new")

    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--start-maximized")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--log-level=3")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(90)

    driver.execute_script(
        """
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        });
        """
    )
    return driver


def scroll_to_load_all(driver: webdriver.Chrome, max_rounds: int = 12, pause: float = 2.0) -> None:
    last_count = 0
    stable_rounds = 0

    for i in range(max_rounds):
        links = driver.find_elements(By.CSS_SELECTOR, 'a[href*="/v/v-"]')
        current_count = len(links)
        log(f"Scroll round {i + 1}: currently found {current_count} replay links")

        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(pause)

        links_after = driver.find_elements(By.CSS_SELECTOR, 'a[href*="/v/v-"]')
        new_count = len(links_after)

        if new_count == last_count:
            stable_rounds += 1
        else:
            stable_rounds = 0

        last_count = new_count

        if stable_rounds >= 2:
            break


def parse_title_date_from_text(text: str) -> Dict[str, str]:
    raw = [x.strip() for x in text.splitlines() if x.strip()]
    joined = " | ".join(raw)

    date_match = re.search(r"\b\d{1,2}/\d{1,2}/\d{4}\b", joined)
    date_text = date_match.group(0) if date_match else ""

    filtered = []
    for item in raw:
        if re.fullmatch(r"\d+(?:\.\d+)?k?", item.lower()):
            continue
        if re.fullmatch(r"\d{1,3}:\d{2}(?::\d{2})?", item):
            continue
        if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", item):
            continue
        filtered.append(item)

    title = ""
    for item in filtered:
        if len(item) >= 2:
            title = item
            break

    if not title:
        title = "Nimo Replay"

    return {"title": title, "date": date_text}


def fetch_replay_entries(page_url: str) -> List[Dict]:
    driver = None
    try:
        driver = setup_chrome_driver(headless=False)

        log(f"Opening replay page in browser: {page_url}")
        driver.get(page_url)

        WebDriverWait(driver, 45).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'a[href*="/v/v-"]'))
        )

        time.sleep(3)
        scroll_to_load_all(driver)

        html = driver.page_source
        soup = BeautifulSoup(html, "html.parser")

        entries: List[Dict] = []
        seen_ids = set()

        for a in soup.select('a[href*="/v/v-"]'):
            href = normalize_nimo_href(a.get("href"))
            if not href:
                continue

            video_id = extract_video_id(href)
            if not video_id or video_id in seen_ids:
                continue

            seen_ids.add(video_id)

            card_text = a.get_text("\n", strip=True)
            parsed = parse_title_date_from_text(card_text)

            entries.append({
                "video_id": video_id,
                "url": href,
                "title": parsed["title"] or f"Nimo Replay {video_id}",
                "date": parsed["date"] or "",
            })

        return entries

    except TimeoutException:
        warn("Timed out waiting for replay links to load")
        return []
    except WebDriverException as e:
        warn(f"Selenium/Chrome error: {e}")
        return []
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


def get_youtube_service():
    creds = None

    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CLIENT_SECRET_FILE):
                raise FileNotFoundError(
                    f"Missing {CLIENT_SECRET_FILE}. Download OAuth client JSON from Google Cloud."
                )
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, "w", encoding="utf-8") as token:
            token.write(creds.to_json())

    return build("youtube", "v3", credentials=creds)


def upload_video_to_youtube(
    youtube,
    file_path: str,
    title: str,
    description: str = "",
    privacy_status: str = "private",
    category_id: str = "20",
) -> str:
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(file_path, chunksize=8 * 1024 * 1024, resumable=True)

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            log(f"Upload progress: {int(status.progress() * 100)}%")

    return response["id"]


def build_upload_title(entry: Dict) -> str:
    if entry["date"]:
        return f"{entry['date']} - {entry['title']}"
    return entry["title"]


def run_nimo_downloader(video_url: str, output_path: str) -> None:
    if not os.path.exists(NIMO_DOWNLOADER):
        raise FileNotFoundError(
            f"Missing {NIMO_DOWNLOADER}. Put your working downloader script in this folder."
        )

    cmd = [
        sys.executable,
        NIMO_DOWNLOADER,
        video_url,
        "-o",
        output_path,
    ]

    log("Running downloader: " + " ".join(cmd))
    subprocess.run(cmd, check=True)


def output_paths_for_entry(entry: Dict, download_dir: Path) -> Dict[str, Path]:
    safe_base = sanitize_filename(f"{entry['date']} {entry['video_id']} {entry['title']}".strip())
    mp4 = download_dir / f"{safe_base}.mp4"
    joined_ts = download_dir / f"{safe_base}.joined.ts"
    progress_json = download_dir / f"{safe_base}.progress.json"
    return {
        "mp4": mp4,
        "joined_ts": joined_ts,
        "progress_json": progress_json,
    }


def mark_seen(history: Dict, entry: Dict, status: str, note: str = "") -> None:
    history["seen"][entry["video_id"]] = {
        "nimo_url": entry["url"],
        "date": entry["date"],
        "source_title": entry["title"],
        "status": status,
        "note": note,
        "updated_at_epoch": int(time.time()),
    }
    save_history(HISTORY_FILE, history)


def process_entry(entry: Dict, history: Dict, youtube, download_dir: Path) -> None:
    video_id = entry["video_id"]

    if video_id in history["uploaded"]:
        log(f"Skipping already uploaded: {video_id}")
        return

    paths = output_paths_for_entry(entry, download_dir)
    output_file = paths["mp4"]

    mark_seen(history, entry, "discovered")

    if output_file.exists():
        log(f"File already exists, skipping download: {output_file}")
        mark_seen(history, entry, "downloaded", "mp4 already existed before this run")
    else:
        log(f"Downloading {video_id} from {entry['url']}")
        mark_seen(history, entry, "download_started")
        run_nimo_downloader(entry["url"], str(output_file))
        mark_seen(history, entry, "downloaded")

    if not output_file.exists():
        raise FileNotFoundError(f"Expected MP4 not found: {output_file}")

    yt_title = build_upload_title(entry)
    yt_description = (
        f"Imported from my Nimo replay.\n"
        f"Original replay date: {entry['date']}\n"
        f"Source: {entry['url']}"
    )

    log(f"Uploading to YouTube: {yt_title}")
    mark_seen(history, entry, "upload_started")

    youtube_video_id = upload_video_to_youtube(
        youtube=youtube,
        file_path=str(output_file),
        title=yt_title,
        description=yt_description,
        privacy_status=YOUTUBE_PRIVACY_STATUS,
        category_id="20",
    )

    log(f"Uploaded YouTube video id: {youtube_video_id}")
    log(f"YouTube watch URL: https://www.youtube.com/watch?v={youtube_video_id}")

    history["uploaded"][video_id] = {
        "nimo_url": entry["url"],
        "date": entry["date"],
        "source_title": entry["title"],
        "youtube_video_id": youtube_video_id,
        "uploaded_at_epoch": int(time.time()),
        "local_mp4": str(output_file),
    }
    save_history(HISTORY_FILE, history)

    mark_seen(history, entry, "uploaded")

    if DELETE_AFTER_SUCCESS:
        log(f"Deleting local file: {output_file}")
        output_file.unlink(missing_ok=True)

        log(f"Deleting temp file: {paths['joined_ts']}")
        paths["joined_ts"].unlink(missing_ok=True)

        log(f"Deleting progress file: {paths['progress_json']}")
        paths["progress_json"].unlink(missing_ok=True)
    else:
        log("Keeping local files after successful upload")


def main():
    download_dir = Path(DOWNLOAD_DIR)
    download_dir.mkdir(parents=True, exist_ok=True)

    history = load_history(HISTORY_FILE)
    save_history(HISTORY_FILE, history)

    log(f"Fetching replay page: {REPLAY_PAGE}")
    entries = fetch_replay_entries(REPLAY_PAGE)
    log(f"Found {len(entries)} replay entries")

    if not entries:
        warn("No replay entries found")
        warn("Make sure Chrome is installed and chromedriver matches your Chrome version.")
        return

    unseen = [e for e in entries if e["video_id"] not in history["uploaded"]]
    log(f"Unseen entries: {len(unseen)}")

    if not unseen:
        log("Nothing new to do")
        return

    youtube = get_youtube_service()

    try:
        channel = youtube.channels().list(part="snippet", mine=True).execute()
        if channel.get("items"):
            log(f"Uploading to channel: {channel['items'][0]['snippet']['title']}")
    except Exception as e:
        warn(f"Could not confirm YouTube channel name: {e}")

    for entry in unseen:
        try:
            process_entry(entry, history, youtube, download_dir)
        except HttpError as e:
            mark_seen(history, entry, "upload_failed", str(e))
            warn(f"YouTube API error for {entry['video_id']}: {e}")
        except subprocess.CalledProcessError as e:
            mark_seen(history, entry, "download_failed", f"exit={e.returncode}")
            warn(f"Downloader failed for {entry['video_id']}: exit={e.returncode}")
        except Exception as e:
            mark_seen(history, entry, "failed", str(e))
            warn(f"Failed for {entry['video_id']}: {e}")


if __name__ == "__main__":
    main()