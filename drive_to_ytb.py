!pip -q install google-api-python-client google-auth-httplib2 google-auth-oauthlib tqdm

import os
import io
import re
import json
import pickle
from tqdm import tqdm
from google.colab import files
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload
from googleapiclient.errors import HttpError

ROOT_FOLDER_ID = "1FOKganXnOHidiFLfypQh7aFTuaI5zNB2"
TMP_DIR = "/content/tmp_uploads"
CLIENT_SECRETS = "client_secrets.json"
TOKEN_FILE = "youtube_drive_token.pickle"

MASTER_PLAYLIST_TITLE = "Drive Uploads"
VIDEO_PRIVACY = "unlisted"
PLAYLIST_PRIVACY = "unlisted"

PROGRESS_FILE = "progress.json"

SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube"
]

os.makedirs(TMP_DIR, exist_ok=True)

if not os.path.exists(CLIENT_SECRETS):
    uploaded = files.upload()
    uploaded_name = next(iter(uploaded.keys()))
    if uploaded_name != CLIENT_SECRETS:
        os.rename(uploaded_name, CLIENT_SECRETS)

def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_progress(progress):
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)

progress = load_progress()

def get_services():
    creds = None
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "rb") as f:
            creds = pickle.load(f)

    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS, SCOPES)
        flow.redirect_uri = "urn:ietf:wg:oauth:2.0:oob"
        auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline", include_granted_scopes="true")
        print(auth_url)
        code = input().strip()
        flow.fetch_token(code=code)
        creds = flow.credentials
        with open(TOKEN_FILE, "wb") as f:
            pickle.dump(creds, f)

    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
    return drive, youtube

drive_service, youtube_service = get_services()

def youtube_scope_check():
    res = youtube_service.channels().list(part="id", mine=True).execute()
    if "items" not in res or len(res["items"]) == 0:
        raise RuntimeError("YouTube scope check failed")
    print("YOUTUBE_OK")

def list_subfolders_sorted(folder_id):
    q = (
        f"'{folder_id}' in parents and trashed=false and "
        "mimeType='application/vnd.google-apps.folder'"
    )
    items, token = [], None
    while True:
        res = drive_service.files().list(
            q=q,
            fields="nextPageToken, files(id,name,mimeType)",
            pageSize=1000,
            pageToken=token,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
        ).execute()
        items.extend(res.get("files", []))
        token = res.get("nextPageToken")
        if not token:
            break
    return sorted(items, key=lambda x: x["name"].lower())

def list_mp4s_sorted(folder_id):
    q = f"'{folder_id}' in parents and trashed=false"
    items, token = [], None
    while True:
        res = drive_service.files().list(
            q=q,
            fields="nextPageToken, files(id,name,mimeType,size)",
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
    return sorted(mp4s, key=lambda x: x["name"].lower())

def download_file(file_id, dest):
    req = drive_service.files().get_media(fileId=file_id, supportsAllDrives=True)
    with io.FileIO(dest, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, req, chunksize=16 * 1024 * 1024)
        done = False
        while not done:
            _, done = downloader.next_chunk()

def sanitize_title(name):
    return re.sub(r"\.mp4$", "", name, flags=re.I).strip()[:95]

def upload_video(path, title, description):
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "categoryId": "22"
        },
        "status": {
            "privacyStatus": VIDEO_PRIVACY
        }
    }

    media = MediaFileUpload(path, mimetype="video/mp4", resumable=True, chunksize=8 * 1024 * 1024)
    request = youtube_service.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    bar = tqdm(total=100, desc=f"Uploading {os.path.basename(path)}", unit="%")
    last = 0

    while response is None:
        status, response = request.next_chunk()
        if status:
            pct = int(status.progress() * 100)
            if pct > last:
                bar.update(pct - last)
                last = pct

    if last < 100:
        bar.update(100 - last)
    bar.close()
    return response["id"]

def find_playlist(title):
    token = None
    while True:
        res = youtube_service.playlists().list(
            part="snippet",
            mine=True,
            maxResults=50,
            pageToken=token
        ).execute()
        for pl in res.get("items", []):
            if pl["snippet"]["title"].strip().lower() == title.strip().lower():
                return pl["id"]
        token = res.get("nextPageToken")
        if not token:
            break
    return None

def create_playlist(title):
    res = youtube_service.playlists().insert(
        part="snippet,status",
        body={"snippet": {"title": title}, "status": {"privacyStatus": PLAYLIST_PRIVACY}}
    ).execute()
    return res["id"]

def add_to_playlist(playlist_id, video_id):
    youtube_service.playlistItems().insert(
        part="snippet",
        body={"snippet": {"playlistId": playlist_id, "resourceId": {"kind": "youtube#video", "videoId": video_id}}}
    ).execute()

def is_upload_limit_error(e):
    try:
        data = json.loads(e.content.decode("utf-8"))
        for err in data.get("error", {}).get("errors", []):
            if err.get("reason") == "uploadLimitExceeded":
                return True
    except Exception:
        pass
    return False

def run():
    youtube_scope_check()

    playlist_id = find_playlist(MASTER_PLAYLIST_TITLE)
    if not playlist_id:
        playlist_id = create_playlist(MASTER_PLAYLIST_TITLE)

    subfolders = list_subfolders_sorted(ROOT_FOLDER_ID)
    print(len(subfolders))

    for sf in subfolders:
        sub_name, sub_id = sf["name"], sf["id"]
        mp4s = list_mp4s_sorted(sub_id)
        if not mp4s:
            continue

        print(sub_name)

        for f in mp4s:
            drive_id = f["id"]
            fname = f["name"]

            if drive_id in progress:
                continue

            local = os.path.join(TMP_DIR, fname)
            download_file(drive_id, local)

            title = sanitize_title(fname)
            desc = f"Uploaded from Drive folder: {sub_name}"

            try:
                vid = upload_video(local, title, desc)
                add_to_playlist(playlist_id, vid)
                progress[drive_id] = {"videoId": vid, "name": fname, "folder": sub_name}
                save_progress(progress)
            except HttpError as e:
                save_progress(progress)
                if is_upload_limit_error(e):
                    print("UPLOAD_LIMIT_EXCEEDED")
                    return
                raise
            finally:
                try:
                    os.remove(local)
                except:
                    pass

    print("ALL UPLOADS COMPLETE")

run()