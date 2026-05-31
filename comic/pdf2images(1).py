from pathlib import Path
import json
import sys
import time
import traceback

try:
    import fitz  # PyMuPDF
except ImportError:
    print("ERROR: PyMuPDF is not installed.")
    print("Install it with:")
    print("    pip install pymupdf google-api-python-client google-auth-httplib2 google-auth-oauthlib")
    sys.exit(1)

try:
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
except ImportError:
    print("ERROR: Google Drive API packages are not installed.")
    print("Install them with:")
    print("    pip install pymupdf google-api-python-client google-auth-httplib2 google-auth-oauthlib")
    sys.exit(1)


# =============================================================================
# SETTINGS
# =============================================================================

LOCAL_FOLDER = Path(r"C:\Users\qacer\Downloads\conan_chapters_pdfs")

TARGET_DRIVE_FOLDER_ID = "1vNtP2TglieCVgLPAqLW5R8utOvQo78u9"

# Only used if TARGET_DRIVE_FOLDER_ID is empty.
TARGET_DRIVE_FOLDER_NAME = "conan_last"

CREDENTIALS_JSON = Path(__file__).with_name("credentials.json")
TOKEN_JSON = Path(__file__).with_name("token.json")

STATE_FILE = LOCAL_FOLDER / "_pdf_to_drive_state.json"

DPI = 200
JPG_QUALITY = 95

# Full Drive scope lets the script see folders you made manually in Drive.
SCOPES = ["https://www.googleapis.com/auth/drive"]

UPLOAD_CHUNK_SIZE = 8 * 1024 * 1024

# After this many local subfolders in a row are NOT found on Drive,
# the script assumes the remaining local subfolders are not uploaded either.
# It will stop checking Drive subfolder names for the rest of that run.
ASSUME_NOT_UPLOADED_AFTER_MISSING_FOLDER_STREAK = 10

# Debug Drive API searches. Leave True until the subfolder detection is fixed.
DEBUG_DRIVE_SEARCH = True
DEBUG_DRIVE_MAX_PRINT = 80


# =============================================================================
# LOGGING AND STATE
# =============================================================================

def log(message=""):
    print(message, flush=True)


def load_state():
    default_state = {
        "converted_pdfs": {},
        "uploaded_folders": {},
        "drive_folder_cache": {},
        "drive_file_cache": {},
        "run_history": []
    }

    if not STATE_FILE.exists():
        return default_state

    try:
        with STATE_FILE.open("r", encoding="utf-8") as f:
            state = json.load(f)

        for key, value in default_state.items():
            state.setdefault(key, value)

        return state

    except Exception:
        log("WARNING: Could not read state file.")
        log(f"State file path: {STATE_FILE}")
        log("A new state object will be used for this run.")
        return default_state


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")

    with tmp.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

    tmp.replace(STATE_FILE)


def add_history(state, event_type, message, extra=None):
    entry = {
        "time_unix": int(time.time()),
        "event_type": event_type,
        "message": message,
        "extra": extra or {}
    }

    state["run_history"].append(entry)
    save_state(state)


def is_real_file(path: Path):
    return path.exists() and path.is_file() and path.stat().st_size > 0


def expected_image_path(output_folder: Path, pdf_stem: str, page_number: int):
    return output_folder / f"{pdf_stem}_{page_number:03d}.jpg"


def should_stop_for_quota(error: HttpError):
    try:
        content = error.content.decode("utf-8", errors="replace")
    except Exception:
        content = str(error)

    quota_markers = [
        "storageQuotaExceeded",
        "quotaExceeded",
        "userRateLimitExceeded",
        "dailyLimitExceeded",
    ]

    return any(marker in content for marker in quota_markers)


# =============================================================================
# GOOGLE DRIVE AUTH
# =============================================================================

def get_drive_service():
    log("=" * 80)
    log("Preparing Google Drive connection.")

    if not CREDENTIALS_JSON.exists():
        log("")
        log("ERROR: credentials.json was not found.")
        log(f"Expected location: {CREDENTIALS_JSON}")
        log("")
        log("Create OAuth credentials like this:")
        log("1. Go to Google Cloud Console.")
        log("2. Enable Google Drive API.")
        log("3. Create OAuth Client ID.")
        log("4. Choose Application type: Desktop app.")
        log("5. Download the JSON.")
        log("6. Rename it to credentials.json.")
        log("7. Put it in the same folder as this script.")
        sys.exit(1)

    creds = None

    if TOKEN_JSON.exists():
        log(f"Found existing token file: {TOKEN_JSON}")
        creds = Credentials.from_authorized_user_file(str(TOKEN_JSON), SCOPES)
    else:
        log("No token.json found yet. Browser login will be required once.")

    if creds and creds.expired and creds.refresh_token:
        log("Google token expired. Refreshing token...")
        creds.refresh(Request())

    if not creds or not creds.valid:
        log("Starting Google login flow.")
        log("A browser window will open.")
        log("Choose the exact Google account that contains your Drive folder.")
        flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_JSON), SCOPES)
        creds = flow.run_local_server(port=0)

    with TOKEN_JSON.open("w", encoding="utf-8") as token:
        token.write(creds.to_json())

    log(f"Saved Google login token: {TOKEN_JSON}")
    log("Google Drive connection is ready.")

    return build("drive", "v3", credentials=creds)


# =============================================================================
# GOOGLE DRIVE HELPERS
# =============================================================================

def drive_escape(value: str):
    return value.replace("\\", "\\\\").replace("'", "\\'")



def debug_drive_api_identity(service):
    if not DEBUG_DRIVE_SEARCH:
        return

    log("=" * 80)
    log("DRIVE DEBUG: API identity")

    try:
        about = service.about().get(fields="user(displayName,emailAddress,permissionId)").execute()
        user = about.get("user", {})
        log(f"Drive API user display name: {user.get('displayName')}")
        log(f"Drive API user email: {user.get('emailAddress')}")
        log(f"Drive API permission ID: {user.get('permissionId')}")
    except Exception as e:
        log("DRIVE DEBUG: Could not read Drive API user identity.")
        log(f"DRIVE DEBUG ERROR: {e}")


def debug_drive_parent_folder(service, parent_id):
    if not DEBUG_DRIVE_SEARCH:
        return

    log("=" * 80)
    log("DRIVE DEBUG: Big parent folder metadata")
    log(f"Big parent folder ID being used: {parent_id}")

    try:
        folder = service.files().get(
            fileId=parent_id,
            fields=(
                "id, name, mimeType, parents, trashed, driveId, ownedByMe, "
                "webViewLink, shortcutDetails, "
                "capabilities(canListChildren, canReadRevisions, canEdit)"
            ),
            supportsAllDrives=True
        ).execute()

        log(f"Parent name: {folder.get('name')!r}")
        log(f"Parent mimeType: {folder.get('mimeType')!r}")
        log(f"Parent trashed: {folder.get('trashed')!r}")
        log(f"Parent parents: {folder.get('parents')!r}")
        log(f"Parent driveId: {folder.get('driveId')!r}")
        log(f"Parent ownedByMe: {folder.get('ownedByMe')!r}")
        log(f"Parent webViewLink: {folder.get('webViewLink')!r}")
        log(f"Parent shortcutDetails: {folder.get('shortcutDetails')!r}")
        log(f"Parent capabilities: {folder.get('capabilities')!r}")

        if folder.get("mimeType") == "application/vnd.google-apps.shortcut":
            log("")
            log("WARNING: The configured big folder ID is a Drive shortcut, not a real folder.")
            log("Use the shortcutDetails.targetId value as TARGET_DRIVE_FOLDER_ID instead.")

        if folder.get("mimeType") != "application/vnd.google-apps.folder":
            log("")
            log("WARNING: The configured big folder ID is not a normal Drive folder.")
            log("Subfolder searches using '<ID> in parents' will not work correctly.")

    except HttpError as e:
        log("DRIVE DEBUG: Could not read big parent folder metadata.")
        log(f"DRIVE DEBUG HTTP ERROR: {e}")
    except Exception as e:
        log("DRIVE DEBUG: Could not read big parent folder metadata.")
        log(f"DRIVE DEBUG ERROR: {e}")


def debug_drive_list_children(service, parent_id):
    if not DEBUG_DRIVE_SEARCH:
        return

    safe_parent = drive_escape(parent_id)

    queries = [
        (
            "ALL direct children, no MIME filter",
            f"'{safe_parent}' in parents and trashed = false"
        ),
        (
            "FOLDER direct children only",
            "mimeType = 'application/vnd.google-apps.folder' "
            f"and '{safe_parent}' in parents "
            "and trashed = false"
        ),
    ]

    for label, query in queries:
        log("=" * 80)
        log(f"DRIVE DEBUG: {label}")
        log(f"DRIVE DEBUG query: {query}")

        try:
            result = service.files().list(
                q=query,
                spaces="drive",
                fields="nextPageToken, files(id, name, mimeType, parents, trashed, shortcutDetails)",
                pageSize=100,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True
            ).execute()

            items = result.get("files", [])
            log(f"DRIVE DEBUG returned count on first page: {len(items)}")
            log(f"DRIVE DEBUG nextPageToken exists: {bool(result.get('nextPageToken'))}")

            for index, item in enumerate(items[:DEBUG_DRIVE_MAX_PRINT], start=1):
                log(
                    f"  [{index}] name={item.get('name')!r} | "
                    f"id={item.get('id')} | "
                    f"mimeType={item.get('mimeType')!r} | "
                    f"parents={item.get('parents')!r} | "
                    f"shortcutDetails={item.get('shortcutDetails')!r}"
                )

            if len(items) > DEBUG_DRIVE_MAX_PRINT:
                log(f"  ... and {len(items) - DEBUG_DRIVE_MAX_PRINT} more on first page")

            if not items:
                log("DRIVE DEBUG: This query returned ZERO items.")

        except HttpError as e:
            log("DRIVE DEBUG: Child-list query failed.")
            log(f"DRIVE DEBUG HTTP ERROR: {e}")
        except Exception as e:
            log("DRIVE DEBUG: Child-list query failed.")
            log(f"DRIVE DEBUG ERROR: {e}")


def debug_local_folder_names():
    if not DEBUG_DRIVE_SEARCH:
        return

    log("=" * 80)
    log("DRIVE DEBUG: Local candidate folder names")

    try:
        local_candidate_folders = [
            folder
            for folder in sorted(LOCAL_FOLDER.iterdir())
            if local_folder_is_upload_candidate(folder)
        ]
    except Exception as e:
        log(f"DRIVE DEBUG: Could not list local folders: {e}")
        return

    log(f"Local candidate folder count: {len(local_candidate_folders)}")

    for index, folder in enumerate(local_candidate_folders[:DEBUG_DRIVE_MAX_PRINT], start=1):
        jpg_count = len(list(folder.glob('*.jpg')))
        log(f"  [{index}] {folder.name!r} | JPG count: {jpg_count}")

    if len(local_candidate_folders) > DEBUG_DRIVE_MAX_PRINT:
        log(f"  ... and {len(local_candidate_folders) - DEBUG_DRIVE_MAX_PRINT} more")

def find_drive_folder(service, folder_name, parent_id="root"):
    """
    Search for a direct child Drive folder by exact folder name.
    """
    safe_name = drive_escape(folder_name)
    safe_parent = drive_escape(parent_id)

    query = (
        "mimeType = 'application/vnd.google-apps.folder' "
        f"and name = '{safe_name}' "
        f"and '{safe_parent}' in parents "
        "and trashed = false"
    )

    log(f"Drive folder search query: {query}")

    result = service.files().list(
        q=query,
        spaces="drive",
        fields="files(id, name, mimeType, parents)",
        pageSize=100,
        supportsAllDrives=True,
        includeItemsFromAllDrives=True
    ).execute()

    files = result.get("files", [])

    if not files:
        return None

    if len(files) > 1:
        log("")
        log("WARNING: Multiple folders matched by name.")
        log("Using the first one.")
        for item in files:
            log(f"  Folder: {item.get('name')} | ID: {item.get('id')}")

    return files[0]["id"]


def create_drive_folder(service, folder_name, parent_id):
    metadata = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id]
    }

    created = service.files().create(
        body=metadata,
        fields="id, name",
        supportsAllDrives=True
    ).execute()

    return created["id"]


def get_or_create_drive_folder_by_name(service, folder_name, parent_id, state):
    """
    Find/create a Drive folder by matching name under the given parent.

    This does NOT trust cached IDs first. It searches by name, then updates
    the cache only after a matching folder is found or a folder is created.
    """
    cache_key = f"{parent_id}/{folder_name}"

    log(f"Searching Drive for matching folder name: {folder_name}")
    log(f"Parent Drive folder ID: {parent_id}")

    folder_id = find_drive_folder(service, folder_name, parent_id)

    if folder_id:
        log(f"Found Drive folder by matching name: {folder_name}")
        log(f"Drive folder ID: {folder_id}")

        state["drive_folder_cache"][cache_key] = folder_id
        save_state(state)

        return folder_id

    log(f"No Drive folder found with matching name: {folder_name}")
    log(f"Creating Drive folder: {folder_name}")

    folder_id = create_drive_folder(service, folder_name, parent_id)

    log(f"Created Drive folder: {folder_name}")
    log(f"Drive folder ID: {folder_id}")

    state["drive_folder_cache"][cache_key] = folder_id
    save_state(state)

    return folder_id


def list_drive_subfolders_in_folder(service, parent_id):
    """
    Return all direct child folders inside the big Drive folder.

    Key:
        folder name

    Value:
        list of folder records with that name
    """
    folders_by_name = {}
    page_token = None
    safe_parent = drive_escape(parent_id)

    query = (
        "mimeType = 'application/vnd.google-apps.folder' "
        f"and '{safe_parent}' in parents "
        "and trashed = false"
    )

    log("=" * 80)
    log("Listing existing Drive subfolders inside the big folder.")
    log(f"Big Drive folder ID: {parent_id}")
    log(f"Drive subfolder list query: {query}")

    while True:
        result = service.files().list(
            q=query,
            spaces="drive",
            fields="nextPageToken, files(id, name, mimeType, parents)",
            pageSize=100,
            pageToken=page_token,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
        ).execute()

        for item in result.get("files", []):
            name = item.get("name")
            if not name:
                continue

            folders_by_name.setdefault(name, []).append(item)

        page_token = result.get("nextPageToken")

        if not page_token:
            break

    log(f"Drive subfolder names found: {len(folders_by_name)}")

    if DEBUG_DRIVE_SEARCH:
        shown = 0
        for name, items in folders_by_name.items():
            for item in items:
                shown += 1
                log(f"  Drive subfolder [{shown}]: name={name!r} | id={item.get('id')}")
                if shown >= DEBUG_DRIVE_MAX_PRINT:
                    break
            if shown >= DEBUG_DRIVE_MAX_PRINT:
                break
        if not folders_by_name:
            log("DRIVE DEBUG: No Drive subfolders were loaded into the name map.")

    return folders_by_name


def find_drive_subfolder_by_name_from_map(subfolders_by_name, folder_name):
    """
    Find a Drive subfolder by matching the folder name.
    """
    matches = subfolders_by_name.get(folder_name, [])

    if not matches:
        return None

    if len(matches) > 1:
        log("")
        log(f"WARNING: Multiple Drive subfolders have this same name: {folder_name}")
        log("Using the first one.")
        for item in matches:
            log(f"  Folder name: {item.get('name')} | ID: {item.get('id')}")

    return matches[0]


def list_drive_files_in_folder(service, parent_id):
    """
    Return all non-trashed direct children inside a Drive folder.

    Key:
        file name

    Value:
        list of Drive records with that name
    """
    files_by_name = {}
    page_token = None
    safe_parent = drive_escape(parent_id)

    query = (
        f"'{safe_parent}' in parents "
        "and trashed = false"
    )

    if DEBUG_DRIVE_SEARCH:
        log(f"Drive file list query: {query}")

    while True:
        result = service.files().list(
            q=query,
            spaces="drive",
            fields="nextPageToken, files(id, name, size, mimeType, parents)",
            pageSize=100,
            pageToken=page_token,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
        ).execute()

        for item in result.get("files", []):
            name = item.get("name")
            if not name:
                continue

            files_by_name.setdefault(name, []).append(item)

        page_token = result.get("nextPageToken")

        if not page_token:
            break

    return files_by_name


def find_drive_file_by_name_from_map(files_by_name, file_name):
    """
    Find a Drive file by matching only the file name.
    """
    matches = files_by_name.get(file_name, [])

    if not matches:
        return None

    if len(matches) > 1:
        log("")
        log(f"WARNING: Multiple Drive files have this same name: {file_name}")
        log("Using the first one.")
        for item in matches:
            log(f"  File name: {item.get('name')} | ID: {item.get('id')}")

    return matches[0]


def find_drive_file(service, file_name, parent_id):
    """
    Search for a direct child Drive file by exact file name.
    """
    safe_name = drive_escape(file_name)
    safe_parent = drive_escape(parent_id)

    query = (
        f"name = '{safe_name}' "
        f"and '{safe_parent}' in parents "
        "and trashed = false"
    )

    result = service.files().list(
        q=query,
        spaces="drive",
        fields="files(id, name, size, mimeType, parents)",
        pageSize=100,
        supportsAllDrives=True,
        includeItemsFromAllDrives=True
    ).execute()

    files = result.get("files", [])
    return files[0] if files else None


def get_target_drive_folder_id(service, state):
    """
    Get the big Drive folder.

    This intentionally uses TARGET_DRIVE_FOLDER_ID when it is provided.
    Only subfolders and files are matched by name.
    """
    if TARGET_DRIVE_FOLDER_ID.strip():
        folder_id = TARGET_DRIVE_FOLDER_ID.strip()
        log(f"Using manually configured BIG target Drive folder ID: {folder_id}")
        return folder_id

    log("=" * 80)
    log("TARGET_DRIVE_FOLDER_ID is empty.")
    log(f"Searching My Drive root for folder named: {TARGET_DRIVE_FOLDER_NAME}")

    folder_id = find_drive_folder(service, TARGET_DRIVE_FOLDER_NAME, "root")

    if not folder_id:
        log("")
        log(f"ERROR: Could not find a Drive folder named '{TARGET_DRIVE_FOLDER_NAME}' in My Drive root.")
        log("")
        log("Fix options:")
        log("")
        log("Option 1:")
        log("Open the Drive folder in your browser and copy the folder ID from the URL.")
        log("Paste it into TARGET_DRIVE_FOLDER_ID near the top of this script.")
        log("")
        log("Option 2:")
        log("Make sure you logged into the same Google account that has the folder.")
        log("Delete token.json and run again:")
        log("")
        log("    del token.json")
        log("    python pdf2images.py")
        log("")
        sys.exit(1)

    log(f"Found target Drive folder: {TARGET_DRIVE_FOLDER_NAME}")
    log(f"Target Drive folder ID: {folder_id}")

    return folder_id


# =============================================================================
# UPLOAD LOGIC
# =============================================================================

def upload_file_to_drive(service, local_file: Path, drive_parent_id: str, existing_files_by_name=None):
    file_name = local_file.name

    existing = None

    if existing_files_by_name is not None:
        existing = find_drive_file_by_name_from_map(existing_files_by_name, file_name)
    else:
        existing = find_drive_file(service, file_name, drive_parent_id)

    if existing:
        log(f"File already exists on Drive by matching name, skipping: {file_name}")
        return existing["id"]

    file_metadata = {
        "name": file_name,
        "parents": [drive_parent_id]
    }

    media = MediaFileUpload(
        str(local_file),
        mimetype="image/jpeg",
        chunksize=UPLOAD_CHUNK_SIZE,
        resumable=True
    )

    log(f"Uploading file to Drive: {local_file}")

    request = service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id, name, size",
        supportsAllDrives=True
    )

    response = None

    while response is None:
        status, response = request.next_chunk()

        if status:
            percent = int(status.progress() * 100)
            log(f"Upload progress for {file_name}: {percent}%")

    file_id = response["id"]

    log(f"Uploaded file: {file_name}")
    log(f"Drive file ID: {file_id}")

    if existing_files_by_name is not None:
        existing_files_by_name.setdefault(file_name, []).append(response)

    return file_id


def local_folder_is_upload_candidate(folder: Path):
    if not folder.is_dir():
        return False

    if folder.name.startswith("_") or folder.name.startswith("."):
        return False

    jpg_files = sorted(folder.glob("*.jpg"))

    if not jpg_files:
        return False

    return True


def drive_folder_already_has_all_local_jpgs(service, local_folder: Path, drive_folder_id: str):
    """
    Check if every local JPG already exists in the matching Drive folder.

    This compares by file name only.
    """
    log(f"Checking existing Drive files by name inside folder ID: {drive_folder_id}")

    files_by_name = list_drive_files_in_folder(service, drive_folder_id)

    local_jpg_files = sorted(local_folder.glob("*.jpg"))
    missing_names = []

    log(f"Local JPG files: {len(local_jpg_files)}")
    log(f"Drive file names found in folder: {len(files_by_name)}")

    for image_path in local_jpg_files:
        existing = find_drive_file_by_name_from_map(files_by_name, image_path.name)

        if not existing:
            missing_names.append(image_path.name)

    complete = len(missing_names) == 0

    return complete, files_by_name, missing_names


def mark_folder_uploaded_from_drive(state, folder_name, local_folder, drive_folder_id, file_count):
    state["uploaded_folders"][folder_name] = {
        "complete": True,
        "local_folder": str(local_folder),
        "drive_folder_id": drive_folder_id,
        "file_count": file_count,
        "uploaded_at_unix": int(time.time()),
        "marked_complete_because": "already_found_on_drive_by_name"
    }

    save_state(state)

    add_history(
        state,
        "folder_already_on_drive",
        f"Folder {folder_name} was already complete on Drive by name matching",
        {
            "folder_name": folder_name,
            "local_folder": str(local_folder),
            "drive_folder_id": drive_folder_id,
            "file_count": file_count
        }
    )


def mark_folder_uploaded_after_upload(state, folder_name, local_folder, drive_folder_id, file_count):
    state["uploaded_folders"][folder_name] = {
        "complete": True,
        "local_folder": str(local_folder),
        "drive_folder_id": drive_folder_id,
        "file_count": file_count,
        "uploaded_at_unix": int(time.time())
    }

    save_state(state)

    add_history(
        state,
        "folder_uploaded",
        f"Uploaded folder {folder_name}",
        {
            "folder_name": folder_name,
            "local_folder": str(local_folder),
            "drive_folder_id": drive_folder_id,
            "file_count": file_count
        }
    )


def get_local_folders_needing_upload(service, target_parent_id, state):
    """
    Find local image folders that need upload.

    Exact behavior:
    1. The big folder is selected by TARGET_DRIVE_FOLDER_ID.
    2. List the big folder's existing Drive subfolders once.
    3. Match each local subfolder by name.
    4. If matching Drive subfolder exists, check JPG files inside by name.
    5. If all JPG names exist, mark complete and skip.
    6. If the Drive subfolder is missing, add it to upload queue.
    7. After 10 consecutive missing subfolders, stop checking Drive and
       assume the rest are not uploaded yet.

    Returns a list of upload jobs:
        {
            "folder": Path,
            "drive_subfolder_id": str or None,
            "assume_missing": bool
        }
    """
    upload_jobs = []

    local_candidate_folders = [
        folder
        for folder in sorted(LOCAL_FOLDER.iterdir())
        if local_folder_is_upload_candidate(folder)
    ]

    log("=" * 80)
    log(f"Local image folders found: {len(local_candidate_folders)}")

    subfolders_by_name = list_drive_subfolders_in_folder(service, target_parent_id)

    missing_folder_streak = 0
    assume_rest_not_uploaded = False

    for folder in local_candidate_folders:
        folder_name = folder.name
        upload_record = state["uploaded_folders"].get(folder_name, {})

        if upload_record.get("complete") is True:
            log(f"History says already uploaded, skipping: {folder_name}")
            continue

        log("-" * 80)
        log(f"Checking local folder: {folder_name}")

        if assume_rest_not_uploaded:
            log("Already hit missing-folder streak limit.")
            log(f"Assuming this folder is not uploaded yet: {folder_name}")
            upload_jobs.append({
                "folder": folder,
                "drive_subfolder_id": None,
                "assume_missing": True
            })
            continue

        drive_subfolder = find_drive_subfolder_by_name_from_map(
            subfolders_by_name=subfolders_by_name,
            folder_name=folder_name
        )

        if not drive_subfolder:
            missing_folder_streak += 1

            log(f"No matching Drive subfolder name found: {folder_name}")
            log(
                "Consecutive missing Drive subfolders: "
                f"{missing_folder_streak}/"
                f"{ASSUME_NOT_UPLOADED_AFTER_MISSING_FOLDER_STREAK}"
            )

            upload_jobs.append({
                "folder": folder,
                "drive_subfolder_id": None,
                "assume_missing": True
            })

            if missing_folder_streak >= ASSUME_NOT_UPLOADED_AFTER_MISSING_FOLDER_STREAK:
                assume_rest_not_uploaded = True
                log("")
                log("=" * 80)
                log(
                    f"Reached {ASSUME_NOT_UPLOADED_AFTER_MISSING_FOLDER_STREAK} "
                    "consecutive missing Drive subfolders."
                )
                log("From now on, the script will stop checking Drive subfolder names.")
                log("It will assume the remaining local folders are not uploaded.")
                log("=" * 80)

            continue

        missing_folder_streak = 0
        drive_subfolder_id = drive_subfolder["id"]

        log(f"Found matching Drive subfolder by name: {folder_name}")
        log(f"Drive subfolder ID: {drive_subfolder_id}")

        complete_on_drive, files_by_name, missing_names = drive_folder_already_has_all_local_jpgs(
            service=service,
            local_folder=folder,
            drive_folder_id=drive_subfolder_id
        )

        if complete_on_drive:
            jpg_count = len(list(folder.glob("*.jpg")))

            log(f"Drive already has every JPG by matching name. Skipping: {folder_name}")

            mark_folder_uploaded_from_drive(
                state=state,
                folder_name=folder_name,
                local_folder=folder,
                drive_folder_id=drive_subfolder_id,
                file_count=jpg_count
            )

            continue

        log(f"Drive subfolder exists, but some JPG names are missing: {folder_name}")
        log(f"Missing JPG names: {len(missing_names)}")

        for name in missing_names[:20]:
            log(f"  Missing: {name}")

        if len(missing_names) > 20:
            log(f"  ... and {len(missing_names) - 20} more")

        upload_jobs.append({
            "folder": folder,
            "drive_subfolder_id": drive_subfolder_id,
            "assume_missing": False
        })

    return upload_jobs


def upload_local_folder_to_drive(
    service,
    local_folder: Path,
    target_parent_id: str,
    state,
    known_drive_subfolder_id=None,
    assume_missing=False
):
    folder_name = local_folder.name

    log("=" * 80)
    log(f"Uploading local folder before any new PDF conversion: {local_folder}")
    log(f"Folder name on Drive: {folder_name}")

    upload_record = state["uploaded_folders"].get(folder_name, {})

    if upload_record.get("complete") is True:
        log(f"History says folder already uploaded. Skipping: {folder_name}")
        return True

    jpg_files = sorted(local_folder.glob("*.jpg"))

    if not jpg_files:
        log(f"No JPG files found, skipping: {local_folder}")
        return False

    log(f"JPG files found: {len(jpg_files)}")

    existing_files_by_name = {}

    if known_drive_subfolder_id:
        drive_subfolder_id = known_drive_subfolder_id
        log(f"Using already matched Drive subfolder ID: {drive_subfolder_id}")

        complete_on_drive, existing_files_by_name, missing_names = drive_folder_already_has_all_local_jpgs(
            service=service,
            local_folder=local_folder,
            drive_folder_id=drive_subfolder_id
        )

        if complete_on_drive:
            log(f"Drive already has complete folder by file names. Skipping upload: {folder_name}")

            mark_folder_uploaded_from_drive(
                state=state,
                folder_name=folder_name,
                local_folder=local_folder,
                drive_folder_id=drive_subfolder_id,
                file_count=len(jpg_files)
            )

            return True

    elif assume_missing:
        log("This folder was treated as missing during the Drive subfolder scan.")
        log("Creating a new Drive subfolder without another search.")
        drive_subfolder_id = create_drive_folder(service, folder_name, target_parent_id)
        log(f"Created Drive folder: {folder_name}")
        log(f"Drive folder ID: {drive_subfolder_id}")

        cache_key = f"{target_parent_id}/{folder_name}"
        state["drive_folder_cache"][cache_key] = drive_subfolder_id
        save_state(state)

    else:
        # Normal fallback: search by name, then create only if the name is not found.
        drive_subfolder_id = get_or_create_drive_folder_by_name(
            service=service,
            folder_name=folder_name,
            parent_id=target_parent_id,
            state=state
        )

        complete_on_drive, existing_files_by_name, missing_names = drive_folder_already_has_all_local_jpgs(
            service=service,
            local_folder=local_folder,
            drive_folder_id=drive_subfolder_id
        )

        if complete_on_drive:
            log(f"Drive already has complete folder by file names. Skipping upload: {folder_name}")

            mark_folder_uploaded_from_drive(
                state=state,
                folder_name=folder_name,
                local_folder=local_folder,
                drive_folder_id=drive_subfolder_id,
                file_count=len(jpg_files)
            )

            return True

    uploaded_count = 0
    skipped_count = 0

    for index, image_path in enumerate(jpg_files, start=1):
        log("-" * 80)
        log(f"Upload item {index}/{len(jpg_files)}")
        log(f"Image: {image_path}")

        if not is_real_file(image_path):
            log(f"Skipping invalid or empty image: {image_path}")
            continue

        try:
            existing = find_drive_file_by_name_from_map(existing_files_by_name, image_path.name)

            if existing:
                log(f"Already on Drive by matching name. Skipping: {image_path.name}")
                skipped_count += 1
                continue

            upload_file_to_drive(
                service=service,
                local_file=image_path,
                drive_parent_id=drive_subfolder_id,
                existing_files_by_name=existing_files_by_name
            )

            uploaded_count += 1

        except KeyboardInterrupt:
            log("")
            log("Cancelled during upload.")
            log("Re-run the script and it will continue uploading missing images first.")
            raise

        except HttpError as e:
            log("")
            log("Google Drive upload error.")
            log(f"File that failed: {image_path}")
            log(f"Error: {e}")

            if should_stop_for_quota(e):
                log("")
                log("STOPPING: Google Drive reports storage/quota/limit was reached.")
                log("No PDF conversion will start after this.")
                raise RuntimeError("GOOGLE_DRIVE_QUOTA_OR_LIMIT_REACHED") from e

            raise

    log("-" * 80)
    log(f"Upload pass finished for folder: {folder_name}")
    log(f"Uploaded new files this run: {uploaded_count}")
    log(f"Skipped existing files by name: {skipped_count}")
    log("Verifying complete folder upload against Drive by file name...")

    complete_after_upload, final_files_by_name, final_missing_names = drive_folder_already_has_all_local_jpgs(
        service=service,
        local_folder=local_folder,
        drive_folder_id=drive_subfolder_id
    )

    if not complete_after_upload:
        log("Upload verification failed.")
        log("File names missing on Drive:")

        for name in final_missing_names:
            log(f"  {name}")

        log("Folder will NOT be marked uploaded in history.")
        return False

    mark_folder_uploaded_after_upload(
        state=state,
        folder_name=folder_name,
        local_folder=local_folder,
        drive_folder_id=drive_subfolder_id,
        file_count=len(jpg_files)
    )

    log(f"Folder marked uploaded in history: {folder_name}")

    return True


def upload_pending_folders_first(service, target_drive_folder_id, state):
    log("=" * 80)
    log("FIRST PHASE: Checking local image folders against Drive.")
    log("PDF conversion will NOT start until this phase finishes.")

    upload_jobs = get_local_folders_needing_upload(
        service=service,
        target_parent_id=target_drive_folder_id,
        state=state
    )

    log(f"Folders still needing upload after Drive check: {len(upload_jobs)}")

    uploaded_success = 0
    uploaded_failed = 0
    already_uploaded = 0

    for job in upload_jobs:
        folder = job["folder"]
        folder_name = folder.name

        if state["uploaded_folders"].get(folder_name, {}).get("complete") is True:
            log(f"Already uploaded by history, skipping: {folder_name}")
            already_uploaded += 1
            continue

        try:
            success = upload_local_folder_to_drive(
                service=service,
                local_folder=folder,
                target_parent_id=target_drive_folder_id,
                state=state,
                known_drive_subfolder_id=job.get("drive_subfolder_id"),
                assume_missing=job.get("assume_missing", False)
            )

            if success:
                uploaded_success += 1
            else:
                uploaded_failed += 1

        except RuntimeError as e:
            if str(e) == "GOOGLE_DRIVE_QUOTA_OR_LIMIT_REACHED":
                log("=" * 80)
                log("Stopped because Google Drive storage/quota/limit was reached.")
                log("PDF conversion will NOT start.")
                return False, uploaded_success, uploaded_failed, already_uploaded

            raise

    log("=" * 80)
    log("First upload phase finished.")
    log(f"Uploaded folders now complete: {uploaded_success}")
    log(f"Upload failed or skipped folders: {uploaded_failed}")
    log(f"Already uploaded folders skipped: {already_uploaded}")

    if uploaded_failed > 0:
        log("Some folders failed upload. PDF conversion will still continue unless quota was reached.")

    return True, uploaded_success, uploaded_failed, already_uploaded


# =============================================================================
# PDF CONVERSION LOGIC
# =============================================================================

def convert_pdf_to_images(pdf_path: Path, state):
    pdf_stem = pdf_path.stem
    output_folder = pdf_path.parent / pdf_stem

    log("=" * 80)
    log(f"Processing PDF: {pdf_path}")
    log(f"PDF name without extension: {pdf_stem}")
    log(f"Output folder: {output_folder}")

    output_folder.mkdir(parents=True, exist_ok=True)

    log(f"Output folder exists or was created: {output_folder}")

    try:
        doc = fitz.open(pdf_path)
        total_pages = doc.page_count

        log("Opened PDF successfully.")
        log(f"Total pages found: {total_pages}")

        if total_pages == 0:
            log("PDF has 0 pages. Skipping delete for safety.")
            doc.close()
            return False

        zoom = DPI / 72
        matrix = fitz.Matrix(zoom, zoom)

        existing_count = 0
        rendered_count = 0

        for page_index in range(total_pages):
            page_number = page_index + 1
            image_path = expected_image_path(output_folder, pdf_stem, page_number)

            if is_real_file(image_path):
                log(f"Page {page_number}/{total_pages} already exists, skipping: {image_path.name}")
                existing_count += 1
                continue

            log(f"Rendering page {page_number}/{total_pages}...")

            page = doc.load_page(page_index)
            pix = page.get_pixmap(matrix=matrix, alpha=False)

            temp_image_path = image_path.with_name(image_path.name + ".tmp")

            if temp_image_path.exists():
                temp_image_path.unlink()

            log(f"Saving temporary image: {temp_image_path}")

            pix.save(temp_image_path, jpg_quality=JPG_QUALITY)

            if not is_real_file(temp_image_path):
                raise RuntimeError(f"Temporary image was not saved correctly: {temp_image_path}")

            temp_image_path.replace(image_path)

            if not is_real_file(image_path):
                raise RuntimeError(f"Final image was not saved correctly: {image_path}")

            rendered_count += 1

            log(f"Saved page {page_number} as: {image_path.name}")

        doc.close()

        missing_pages = []

        for page_number in range(1, total_pages + 1):
            image_path = expected_image_path(output_folder, pdf_stem, page_number)

            if not is_real_file(image_path):
                missing_pages.append(page_number)

        log("")
        log(f"Finished conversion check for PDF: {pdf_path.name}")
        log(f"Pages already existing before this run: {existing_count}")
        log(f"Pages newly rendered in this run: {rendered_count}")

        if missing_pages:
            log("ERROR: Some pages are still missing.")
            log(f"Missing page numbers: {missing_pages}")
            log("PDF will NOT be deleted.")
            return False

        state["converted_pdfs"][pdf_stem] = {
            "source_pdf": str(pdf_path),
            "output_folder": str(output_folder),
            "total_pages": total_pages,
            "converted_at_unix": int(time.time())
        }

        save_state(state)

        add_history(
            state,
            "pdf_converted",
            f"Converted PDF {pdf_path.name}",
            {
                "source_pdf": str(pdf_path),
                "output_folder": str(output_folder),
                "total_pages": total_pages
            }
        )

        log(f"All {total_pages} pages exist as JPG files.")
        log(f"Deleting fully converted PDF: {pdf_path}")

        pdf_path.unlink()

        log(f"Deleted PDF: {pdf_path.name}")

        return True

    except KeyboardInterrupt:
        log("")
        log("Cancelled during PDF conversion.")
        log("Progress is safe.")
        log("Re-run the script to continue from the next missing page.")
        raise

    except Exception as e:
        log("")
        log("ERROR while processing this PDF.")
        log(f"PDF was NOT deleted: {pdf_path}")
        log(f"Reason: {e}")
        log("")
        traceback.print_exc()
        return False


def convert_remaining_pdfs(state):
    log("=" * 80)
    log("SECOND PHASE: Starting PDF conversion after pending folder uploads.")

    pdf_files = sorted(LOCAL_FOLDER.glob("*.pdf"))

    log(f"PDF files found locally: {len(pdf_files)}")

    converted_success = 0
    converted_failed = 0

    for pdf_path in pdf_files:
        try:
            success = convert_pdf_to_images(pdf_path, state)

            if success:
                converted_success += 1
            else:
                converted_failed += 1

        except KeyboardInterrupt:
            log("")
            log("Script cancelled by user.")
            log("Re-run it later.")
            log("It will upload pending folders first, then resume PDF conversion.")
            sys.exit(130)

    log("=" * 80)
    log("PDF conversion phase finished.")
    log(f"PDFs converted successfully this run: {converted_success}")
    log(f"PDFs failed or skipped this run: {converted_failed}")

    return converted_success, converted_failed


# =============================================================================
# MAIN
# =============================================================================

def main():
    log("Starting resumable PDF-to-JPG-to-Google-Drive pipeline.")
    log(f"Local big folder: {LOCAL_FOLDER}")
    log(f"DPI: {DPI}")
    log(f"JPG quality: {JPG_QUALITY}")
    log(f"State/history file: {STATE_FILE}")
    log("")
    log("Order of operations:")
    log("1. Connect to Google Drive.")
    log("2. Use TARGET_DRIVE_FOLDER_ID as the big parent Drive folder.")
    log("3. List existing subfolders inside that big folder.")
    log("4. Match local subfolders to Drive subfolders by name.")
    log("5. Match JPG files inside subfolders by name.")
    log("6. Skip folders already complete on Drive.")
    log("7. Upload only missing folders/files.")
    log("8. After 10 consecutive missing subfolders, assume the rest are not uploaded.")
    log("9. Only after upload checking finishes, convert remaining PDFs.")

    if not LOCAL_FOLDER.exists():
        log("")
        log("ERROR: Local folder does not exist:")
        log(str(LOCAL_FOLDER))
        sys.exit(1)

    if not LOCAL_FOLDER.is_dir():
        log("")
        log("ERROR: This path is not a folder:")
        log(str(LOCAL_FOLDER))
        sys.exit(1)

    state = load_state()
    save_state(state)

    add_history(
        state,
        "run_started",
        "Script run started",
        {"local_folder": str(LOCAL_FOLDER)}
    )

    service = get_drive_service()
    target_drive_folder_id = get_target_drive_folder_id(service, state)

    debug_drive_api_identity(service)
    debug_drive_parent_folder(service, target_drive_folder_id)
    debug_drive_list_children(service, target_drive_folder_id)
    debug_local_folder_names()

    can_continue, uploaded_success, uploaded_failed, already_uploaded = upload_pending_folders_first(
        service=service,
        target_drive_folder_id=target_drive_folder_id,
        state=state
    )

    if not can_continue:
        add_history(
            state,
            "run_stopped",
            "Script stopped before PDF conversion because Drive quota/limit was reached",
            {}
        )

        log("=" * 80)
        log("Stopped before PDF conversion.")
        log("Reason: Google Drive storage/quota/limit reached.")
        return

    converted_success, converted_failed = convert_remaining_pdfs(state)

    add_history(
        state,
        "run_finished",
        "Script run finished",
        {
            "uploaded_success": uploaded_success,
            "uploaded_failed": uploaded_failed,
            "already_uploaded": already_uploaded,
            "converted_success": converted_success,
            "converted_failed": converted_failed
        }
    )

    log("=" * 80)
    log("All done.")
    log(f"Uploaded folders successfully before conversion: {uploaded_success}")
    log(f"Upload failed/skipped folders before conversion: {uploaded_failed}")
    log(f"Already uploaded folders skipped by history: {already_uploaded}")
    log(f"PDFs converted successfully after uploads: {converted_success}")
    log(f"PDFs failed/skipped after uploads: {converted_failed}")
    log("")
    log("Important behavior:")
    log("- The big Drive folder is selected by TARGET_DRIVE_FOLDER_ID.")
    log("- Subfolders inside the big Drive folder are matched by name.")
    log("- JPG files inside subfolders are matched by name.")
    log("- File size is not used for skip decisions.")
    log("- After 10 consecutive missing subfolders, Drive subfolder checks stop for the rest of the run.")
    log("- PDF conversion starts only after the upload-first phase is done.")
    log("- Folder upload history is stored in:")
    log(f"  {STATE_FILE}")
    log("- Converted PDFs are deleted only after all pages exist locally.")
    log("- If Drive storage/quota is reached, the script stops before converting more PDFs.")


if __name__ == "__main__":
    main()
