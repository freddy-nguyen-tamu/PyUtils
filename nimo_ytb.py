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

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def log(msg: str) -> None:
    try:
        print(f"[+] {msg}")
    except UnicodeEncodeError:
        safe = str(msg).encode("utf-8", errors="replace").decode("utf-8", errors="replace")
        print(f"[+] {safe}")


def warn(msg: str) -> None:
    try:
        print(f"[!] {msg}")
    except UnicodeEncodeError:
        safe = str(msg).encode("utf-8", errors="replace").decode("utf-8", errors="replace")
        print(f"[!] {safe}")


def load_json_file(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json_file(path: str, data) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_history(path: str) -> Dict:
    data = load_json_file(path, {"uploaded": {}, "seen": {}})
    if "uploaded" not in data:
        data["uploaded"] = {}
    if "seen" not in data:
        data["seen"] = {}
    return data


def save_history(path: str, history: Dict) -> None:
    save_json_file(path, history)


def write_auto_state(can_clear_downloads: bool, reason: str, details: Optional[dict] = None) -> None:
    payload = {
        "can_clear_downloads": bool(can_clear_downloads),
        "reason": reason,
        "updated_at_epoch": int(time.time()),
        "details": details or {},
    }
    save_json_file(AUTO_STATE_FILE, payload)
    log(f"[AUTO_STATE] can_clear_downloads={payload['can_clear_downloads']} | reason={reason}")


def sanitize_filename(name: str) -> str:
    name = unicodedata.normalize("NFKD", name)
    name = name.encode("ascii", "ignore").decode("ascii")
    name = re.sub(r'[<>:"/\\|?*]+', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    if not name:
        name = "video"
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


def extract_video_id_from_filename(path: Path) -> Optional[str]:
    # expects names like: 3_10_2026 1859303657273951077 Title.mp4
    m = re.search(r"(?:^|\s)(\d{10,})(?:\s|\.|$)", path.stem)
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
    if entry.get("date"):
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
    safe_base = sanitize_filename(f"{entry.get('date', '')} {entry['video_id']} {entry.get('title', '')}".strip())
    mp4 = download_dir / f"{safe_base}.mp4"
    joined_ts = download_dir / f"{safe_base}.joined.ts"
    progress_json = download_dir / f"{safe_base}.progress.json"
    return {
        "mp4": mp4,
        "joined_ts": joined_ts,
        "progress_json": progress_json,
    }


def guess_sidecar_paths_from_mp4(mp4_path: Path) -> Tuple[Path, Path]:
    joined_ts = mp4_path.with_suffix(".joined.ts")
    progress_json = mp4_path.with_suffix(".progress.json")
    return joined_ts, progress_json


def mark_seen(history: Dict, entry: Dict, status: str, note: str = "") -> None:
    history["seen"][entry["video_id"]] = {
        "nimo_url": entry.get("url", ""),
        "date": entry.get("date", ""),
        "source_title": entry.get("title", ""),
        "status": status,
        "note": note,
        "updated_at_epoch": int(time.time()),
    }
    save_history(HISTORY_FILE, history)


def safe_delete(path: Path, label: str, retries: int = 10, delay: float = 1.0) -> bool:
    for attempt in range(1, retries + 1):
        try:
            if path.exists():
                path.unlink()
                log(f"Deleted {label}: {path}")
            else:
                log(f"{label} already missing: {path}")
            return True
        except Exception as e:
            warn(f"Failed to delete {label} (attempt {attempt}/{retries}): {path} | {e}")
            time.sleep(delay)
    return False


def cleanup_uploaded_files(mp4_path: Path) -> None:
    joined_ts, progress_json = guess_sidecar_paths_from_mp4(mp4_path)
    log("Starting cleanup after successful upload...")
    time.sleep(2)
    ok_mp4 = safe_delete(mp4_path, "mp4")
    ok_ts = safe_delete(joined_ts, "joined.ts")
    ok_progress = safe_delete(progress_json, "progress.json")

    if ok_mp4 and ok_ts and ok_progress:
        log("Cleanup finished successfully")
    else:
        warn("Cleanup finished with some files not deleted")


def process_completed_leftover_mp4s(history: Dict, youtube, download_dir: Path) -> Tuple[bool, bool]:
    """
    Returns:
      (had_pending_uploadable_mp4_before_processing, had_failures)
    """
    mp4_files = sorted(download_dir.glob("*.mp4"))
    if not mp4_files:
        log("No completed leftover mp4 files found")
        return False, False

    log(f"Found {len(mp4_files)} completed leftover mp4 file(s)")
    had_pending_uploadable_mp4 = False
    had_failures = False

    for mp4_path in mp4_files:
        video_id = extract_video_id_from_filename(mp4_path)
        if not video_id:
            warn(f"Could not extract video id from leftover mp4 filename, preserving: {mp4_path.name}")
            had_failures = True
            continue

        if video_id in history["uploaded"]:
            log(f"Leftover mp4 already uploaded according to history, deleting local copy: {mp4_path.name}")
            cleanup_uploaded_files(mp4_path)
            continue

        had_pending_uploadable_mp4 = True

        # Build a minimal synthetic entry from filename
        synthetic_entry = {
            "video_id": video_id,
            "url": f"https://www.nimo.tv/v/v-{video_id}",
            "title": mp4_path.stem,
            "date": "",
        }

        # Clean title a bit: remove leading date/id if present
        m = re.match(r"^\s*\S+\s+(\d{10,})\s+(.*)$", mp4_path.stem)
        if m:
            synthetic_entry["title"] = m.group(2).strip() or mp4_path.stem

        mark_seen(history, synthetic_entry, "upload_started", "recovered from leftover completed mp4")

        try:
            yt_title = build_upload_title(synthetic_entry)
            yt_description = (
                f"Imported from my Nimo replay.\n"
                f"Recovered from completed local MP4.\n"
                f"Source: {synthetic_entry['url']}"
            )

            log(f"Uploading leftover mp4 to YouTube: {mp4_path.name}")
            youtube_video_id = upload_video_to_youtube(
                youtube=youtube,
                file_path=str(mp4_path),
                title=yt_title,
                description=yt_description,
                privacy_status=YOUTUBE_PRIVACY_STATUS,
                category_id="20",
            )

            log(f"Uploaded YouTube video id: {youtube_video_id}")
            log(f"YouTube watch URL: https://www.youtube.com/watch?v={youtube_video_id}")

            history["uploaded"][video_id] = {
                "nimo_url": synthetic_entry["url"],
                "date": synthetic_entry["date"],
                "source_title": synthetic_entry["title"],
                "youtube_video_id": youtube_video_id,
                "uploaded_at_epoch": int(time.time()),
                "local_mp4": str(mp4_path),
            }
            save_history(HISTORY_FILE, history)

            mark_seen(history, synthetic_entry, "uploaded", "recovered from leftover completed mp4")

            if DELETE_AFTER_SUCCESS:
                cleanup_uploaded_files(mp4_path)

        except Exception as e:
            had_failures = True
            mark_seen(history, synthetic_entry, "upload_failed", f"leftover mp4 upload failed: {e}")
            warn(f"Failed uploading leftover mp4 {mp4_path.name}: {e}")

    return had_pending_uploadable_mp4, had_failures


def detect_continuable_work(download_dir: Path) -> List[dict]:
    results = []

    for progress_file in sorted(download_dir.glob("*.progress.json")):
        try:
            data = json.loads(progress_file.read_text(encoding="utf-8"))
        except Exception as e:
            results.append({
                "progress_file": str(progress_file),
                "kind": "broken_progress",
                "note": str(e),
            })
            continue

        output = data.get("output", "")
        next_index = int(data.get("next_index", 1))
        total_segments = int(data.get("total_segments", 0))

        if output:
            out_mp4 = Path(output)
        else:
            out_mp4 = progress_file.with_suffix(".mp4")

        joined_ts = out_mp4.with_suffix(".joined.ts")
        video_url = data.get("url", "")

        if joined_ts.exists() and next_index > 1 and total_segments >= next_index and video_url:
            results.append({
                "progress_file": str(progress_file),
                "joined_ts": str(joined_ts),
                "output": str(out_mp4),
                "video_url": video_url,
                "next_index": next_index,
                "total_segments": total_segments,
                "kind": "continuable_download",
            })

    return results


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
        f"Original replay date: {entry.get('date', '')}\n"
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
        "date": entry.get("date", ""),
        "source_title": entry.get("title", ""),
        "youtube_video_id": youtube_video_id,
        "uploaded_at_epoch": int(time.time()),
        "local_mp4": str(output_file),
    }
    save_history(HISTORY_FILE, history)

    mark_seen(history, entry, "uploaded")

    if DELETE_AFTER_SUCCESS:
        cleanup_uploaded_files(output_file)
    else:
        log("Keeping local files after successful upload")


def main():
    download_dir = Path(DOWNLOAD_DIR)
    download_dir.mkdir(parents=True, exist_ok=True)

    history = load_history(HISTORY_FILE)
    save_history(HISTORY_FILE, history)

    # Default: do NOT clear unless we prove there is no useful work
    write_auto_state(False, "startup_default_preserve")

    youtube = get_youtube_service()

    try:
        channel = youtube.channels().list(part="snippet", mine=True).execute()
        if channel.get("items"):
            log(f"Uploading to channel: {channel['items'][0]['snippet']['title']}")
    except Exception as e:
        warn(f"Could not confirm YouTube channel name: {e}")

    had_failures = False

    # 1) First, handle completed leftover mp4 files.
    had_pending_leftover_mp4, leftover_mp4_failures = process_completed_leftover_mp4s(
        history, youtube, download_dir
    )
    had_failures = had_failures or leftover_mp4_failures

    if had_pending_leftover_mp4:
        # We found at least one completed local mp4 that needed upload.
        # nimo_auto should definitely not clear the folder before this pass.
        write_auto_state(False, "leftover_completed_mp4_was_present")

    # 2) Then detect resumable partial downloads.
    continuable = detect_continuable_work(download_dir)
    if continuable:
        log(f"Found {len(continuable)} continuable partial download(s); preserving downloads folder")
        for item in continuable:
            log(
                f"Continuable work: output={item['output']} | next={item['next_index']}/{item['total_segments']}"
            )
        write_auto_state(False, "continuable_work_present", {"count": len(continuable)})
    else:
        # No resumable work remains.
        pass

    # 3) Normal replay scraping.
    log(f"Fetching replay page: {REPLAY_PAGE}")
    entries = fetch_replay_entries(REPLAY_PAGE)
    log(f"Found {len(entries)} replay entries")

    if not entries:
        warn("No replay entries found")
        warn("Make sure Chrome is installed and chromedriver matches your Chrome version.")

        # If no replay entries and no continuable work and no leftover completed mp4,
        # then it is safe for nimo_auto to clear stale leftovers after this run.
        if not continuable:
            mp4_left = list(download_dir.glob("*.mp4"))
            if not mp4_left:
                write_auto_state(True, "no_replay_entries_and_no_recoverable_work")
        return 1

    unseen = [e for e in entries if e["video_id"] not in history["uploaded"]]
    log(f"Unseen entries: {len(unseen)}")

    if not unseen:
        log("Nothing new to do")

        # Safe to clear only if there is no completed mp4 awaiting upload and no continuable work.
        mp4_left = [
            p for p in download_dir.glob("*.mp4")
            if (extract_video_id_from_filename(p) or "") not in history["uploaded"]
        ]
        continuable_after = detect_continuable_work(download_dir)

        if not mp4_left and not continuable_after:
            write_auto_state(True, "nothing_new_and_no_recoverable_work")
        else:
            write_auto_state(False, "preserve_due_to_remaining_recoverable_work")
        return 0

    for entry in unseen:
        try:
            process_entry(entry, history, youtube, download_dir)
        except HttpError as e:
            had_failures = True
            mark_seen(history, entry, "upload_failed", str(e))
            warn(f"YouTube API error for {entry['video_id']}: {e}")
        except subprocess.CalledProcessError as e:
            had_failures = True
            mark_seen(history, entry, "download_failed", f"exit={e.returncode}")
            warn(f"Downloader failed for {entry['video_id']}: exit={e.returncode}")
        except Exception as e:
            had_failures = True
            mark_seen(history, entry, "failed", str(e))
            warn(f"Failed for {entry['video_id']}: {e}")

    # Final decision for nimo_auto.py:
    mp4_left = [
        p for p in download_dir.glob("*.mp4")
        if (extract_video_id_from_filename(p) or "") not in history["uploaded"]
    ]
    continuable_after = detect_continuable_work(download_dir)

    if had_failures or mp4_left or continuable_after:
        reason_bits = []
        if had_failures:
            reason_bits.append("had_failures")
        if mp4_left:
            reason_bits.append("mp4_left_to_upload")
        if continuable_after:
            reason_bits.append("continuable_work_present")
        write_auto_state(False, ",".join(reason_bits))
    else:
        write_auto_state(True, "all_work_finished_no_recoverable_leftovers")

    return 1 if had_failures else 0


if __name__ == "__main__":
    sys.exit(main())