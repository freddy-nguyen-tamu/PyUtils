!pip -q install google-api-python-client google-auth-httplib2 google-auth-oauthlib tqdm
!apt-get -qq update
!apt-get -qq install -y ffmpeg

import os
import re
import json
import time
import pickle
import subprocess
from tqdm import tqdm
from google.colab import files
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload
from googleapiclient.errors import HttpError

ROOT_FOLDER_ID = "1FOKganXnOHidiFLfypQh7aFTuaI5zNB2"
CLIENT_SECRETS = "client_secrets.json"
TOKEN_FILE = "youtube_drive_token.pickle"
PROGRESS_FILE = "merge_upload_progress.json"

MASTER_PLAYLIST_TITLE = "Drive Uploads"
VIDEO_PRIVACY = "unlisted"
PLAYLIST_PRIVACY = "unlisted"

MAX_MERGED_DURATION_SECONDS = 12 * 60 * 60
MAX_MERGED_SIZE_BYTES = 256 * 1024 * 1024 * 1024
WAIT_SECONDS_BETWEEN_UPLOADS = 60

TMP_ROOT = "/content/tmp_merge_upload"
CLIPS_DIR = os.path.join(TMP_ROOT, "clips")
MERGED_DIR = os.path.join(TMP_ROOT, "merged")
os.makedirs(CLIPS_DIR, exist_ok=True)
os.makedirs(MERGED_DIR, exist_ok=True)

SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube"
]

def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"processed_drive_ids": {}, "batch_index": 1}

def save_progress(p):
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(p, f, ensure_ascii=False, indent=2)

progress = load_progress()

if not os.path.exists(CLIENT_SECRETS):
    uploaded = files.upload()
    name = next(iter(uploaded.keys()))
    if name != CLIENT_SECRETS:
        os.rename(name, CLIENT_SECRETS)

def get_services():
    creds = None
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "rb") as f:
            creds = pickle.load(f)

    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS, SCOPES)
        flow.redirect_uri = "urn:ietf:wg:oauth:2.0:oob"
        url, _ = flow.authorization_url(prompt="consent", access_type="offline")
        print(url)
        code = input().strip()
        flow.fetch_token(code=code)
        creds = flow.credentials
        with open(TOKEN_FILE, "wb") as f:
            pickle.dump(creds, f)

    return (
        build("drive", "v3", credentials=creds, cache_discovery=False),
        build("youtube", "v3", credentials=creds, cache_discovery=False),
    )

drive_service, youtube_service = get_services()

def youtube_scope_check():
    res = youtube_service.channels().list(part="id", mine=True).execute()
    if "items" not in res or len(res["items"]) == 0:
        raise RuntimeError("YouTube scope check failed")
    print("YOUTUBE_OK")

def seconds_to_hhmmss(sec):
    s = int(sec)
    return f"{s//3600:02d}:{(s%3600)//60:02d}:{s%60:02d}"

def ffprobe_duration(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nk=1:nw=1", path],
        capture_output=True, text=True
    )
    try:
        return float(r.stdout.strip())
    except:
        return None

def list_subfolders(folder_id):
    q = f"'{folder_id}' in parents and trashed=false and mimeType='application/vnd.google-apps.folder'"
    items, token = [], None
    while True:
        res = drive_service.files().list(
            q=q,
            orderBy="name",
            fields="nextPageToken, files(id,name)",
            pageSize=1000,
            pageToken=token,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
        ).execute()
        items.extend(res.get("files", []))
        token = res.get("nextPageToken")
        if not token:
            break
    items.sort(key=lambda x: x["name"].lower())
    return items

def list_mp4s(folder_id):
    q = f"'{folder_id}' in parents and trashed=false"
    items, token = [], None
    while True:
        res = drive_service.files().list(
            q=q,
            orderBy="name",
            fields="nextPageToken, files(id,name,size)",
            pageSize=1000,
            pageToken=token,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
        ).execute()
        items.extend(res.get("files", []))
        token = res.get("nextPageToken")
        if not token:
            break
    mp4s = [f for f in items if f["name"].lower().endswith(".mp4")]
    mp4s.sort(key=lambda x: x["name"].lower())
    return mp4s

def download(file_id, dest):
    req = drive_service.files().get_media(fileId=file_id, supportsAllDrives=True)
    with open(dest, "wb") as f:
        d = MediaIoBaseDownload(f, req, chunksize=16 * 1024 * 1024)
        done = False
        while not done:
            _, done = d.next_chunk()

def write_concat(paths, out):
    with open(out, "w", encoding="utf-8") as f:
        for p in paths:
            f.write("file '" + p.replace("'", "'\\''") + "'\n")

def concat(list_file, out):
    if subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", out]
    ).returncode == 0:
        return True
    return subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file,
         "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
         "-c:a", "aac", "-b:a", "192k", out]
    ).returncode == 0

def upload(path, title):
    req = youtube_service.videos().insert(
        part="snippet,status",
        body={"snippet": {"title": title, "categoryId": "22"}, "status": {"privacyStatus": VIDEO_PRIVACY}},
        media_body=MediaFileUpload(path, mimetype="video/mp4", resumable=True, chunksize=8 * 1024 * 1024)
    )
    resp = None
    bar = tqdm(total=100, desc=f"Uploading {os.path.basename(path)}", unit="%")
    last = 0
    while resp is None:
        status, resp = req.next_chunk()
        if status:
            pct = int(status.progress() * 100)
            if pct > last:
                bar.update(pct - last)
                last = pct
    if last < 100:
        bar.update(100 - last)
    bar.close()
    return resp["id"]

def get_or_create_playlist(title):
    token = None
    while True:
        res = youtube_service.playlists().list(part="snippet", mine=True, maxResults=50, pageToken=token).execute()
        for p in res.get("items", []):
            if p["snippet"]["title"].strip().lower() == title.strip().lower():
                return p["id"]
        token = res.get("nextPageToken")
        if not token:
            break
    return youtube_service.playlists().insert(
        part="snippet,status",
        body={"snippet": {"title": title}, "status": {"privacyStatus": PLAYLIST_PRIVACY}}
    ).execute()["id"]

def add_to_playlist(playlist_id, video_id):
    youtube_service.playlistItems().insert(
        part="snippet",
        body={"snippet": {"playlistId": playlist_id, "resourceId": {"kind": "youtube#video", "videoId": video_id}}}
    ).execute()

def run():
    youtube_scope_check()

    playlist_id = get_or_create_playlist(MASTER_PLAYLIST_TITLE)

    batch = int(progress.get("batch_index", 1))
    used = progress.get("processed_drive_ids", {})

    paths, ids, chapters = [], [], []
    total_dur, total_size, cursor = 0.0, 0, 0.0

    def flush():
        nonlocal batch, paths, ids, chapters, total_dur, total_size, cursor

        if not paths:
            return True

        print("BATCH", str(batch))
        for c in chapters:
            print("CHAPTER", c)

        lst = os.path.join(MERGED_DIR, f"b{batch:04d}.txt")
        out = os.path.join(MERGED_DIR, f"b{batch:04d}.mp4")

        write_concat(paths, lst)
        if not concat(lst, out):
            return False

        vid = upload(out, f"Drive Batch {batch:04d}")
        add_to_playlist(playlist_id, vid)

        for i in ids:
            used[i] = {"batch": batch, "videoId": vid}

        progress["processed_drive_ids"] = used
        progress["batch_index"] = batch + 1
        save_progress(progress)

        for p in paths:
            try:
                os.remove(p)
            except:
                pass
        for p in [lst, out]:
            try:
                os.remove(p)
            except:
                pass

        batch += 1
        paths, ids, chapters = [], [], []
        total_dur, total_size, cursor = 0.0, 0, 0.0

        time.sleep(WAIT_SECONDS_BETWEEN_UPLOADS)
        return True

    subfolders = list_subfolders(folder_id=ROOT_FOLDER_ID)
    print(str(len(subfolders)))

    for sf in subfolders:
        sf_name, sf_id = sf["name"], sf["id"]
        mp4s = list_mp4s(sf_id)

        for f in mp4s:
            if f["id"] in used:
                continue

            fname = f["name"]
            fsize = int(f.get("size", 0) or 0)

            local_name = re.sub(r"[^A-Za-z0-9._ -]+", "_", fname)
            local = os.path.join(CLIPS_DIR, f"{batch:04d}_{len(paths):06d}_{local_name}")

            print("INCLUDE", sf_name, "/", fname)

            download(f["id"], local)
            d = ffprobe_duration(local)
            if d is None:
                try:
                    os.remove(local)
                except:
                    pass
                continue

            if paths and (total_dur + d > MAX_MERGED_DURATION_SECONDS or total_size + fsize > MAX_MERGED_SIZE_BYTES):
                if not flush():
                    return

            ts = seconds_to_hhmmss(cursor)
            chap = f"{ts} {sf_name} / {fname}"
            print("START_AT", chap)

            chapters.append(chap)
            cursor += d

            paths.append(local)
            ids.append(f["id"])
            total_dur += d
            total_size += fsize

            if total_dur >= MAX_MERGED_DURATION_SECONDS or total_size >= MAX_MERGED_SIZE_BYTES:
                if not flush():
                    return

    flush()
    print("ALL UPLOADS COMPLETE")

run()