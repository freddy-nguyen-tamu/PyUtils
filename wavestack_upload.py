from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from mutagen import File as MutagenFile


# =====================================================================
# CONFIGURATION
# =====================================================================

LOCAL_ROOT = Path(r"C:\Users\qacer\Downloads\WaveStack")

# Change this when the current Google Drive folder/account fills up.
# The script will skip files already uploaded to ANY previous destination.
DRIVE_ROOT_FOLDER_ID = "1qbVH_yaNn1aagSrMGvZIggCjSvRzZRSs"

# Change this when switching Google accounts.
DRIVE_ACCOUNT_HINT = "qacer6963.amz.1@gmail.com"

CREDENTIALS_FILE = Path("credentials.json")
TOKEN_FILE = Path(f"token_{DRIVE_ACCOUNT_HINT}.json")

UPLOAD_EVENTS_FILE = Path("wavestack_drive_upload_events.jsonl")
UPLOAD_STATE_FILE = Path("wavestack_drive_upload_state.json")
LEGACY_UPLOAD_LOG_FILE = Path("wavestack_drive_upload_log.json")
WAVESTACK_CATALOG_FILE = Path("wavestack_catalog.json")

SCOPES = ["https://www.googleapis.com/auth/drive"]

AUDIO_EXTENSIONS = {
    ".mp3",
    ".m4a",
    ".aac",
    ".wav",
    ".flac",
    ".ogg",
    ".opus",
    ".wma",
}

STOP_REQUESTED = False


# =====================================================================
# SAFE CTRL+C HANDLING
# =====================================================================

def handle_stop_signal(signum, frame):
    global STOP_REQUESTED
    STOP_REQUESTED = True
    print()
    print("Stop requested. Finishing current file, saving state, then exiting...")


signal.signal(signal.SIGINT, handle_stop_signal)
signal.signal(signal.SIGTERM, handle_stop_signal)


# =====================================================================
# AUTH
# =====================================================================

def get_drive_service():
    creds = None

    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDENTIALS_FILE.exists():
                raise FileNotFoundError(
                    f"Missing {CREDENTIALS_FILE.resolve()}\n\n"
                    "Download OAuth Desktop credentials from Google Cloud Console, "
                    "rename it to credentials.json, and place it beside this script."
                )

            flow = InstalledAppFlow.from_client_secrets_file(
                str(CREDENTIALS_FILE),
                SCOPES,
            )
            creds = flow.run_local_server(port=0)

        TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")

    return build("drive", "v3", credentials=creds)


# =====================================================================
# BASIC HELPERS
# =====================================================================

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_slashes(value: str) -> str:
    return value.replace("\\", "/")


def safe_text(value: Any) -> str | None:
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    return text


def slugify(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^\w\s-]", "", value)
    value = re.sub(r"[\s_-]+", "-", value)
    value = re.sub(r"^-+|-+$", "", value)
    return value or "unknown"


def normalize_file_identity(filename: str) -> str:
    """
    Stable upload/resume key based on the actual audio file name only.
    """

    return filename.strip().casefold()


def drive_query_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def is_audio_file(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_EXTENSIONS


def get_local_file_fingerprint(path: Path) -> dict[str, Any]:
    stat = path.stat()

    return {
        "size_bytes": stat.st_size,
        "modified_at": datetime.fromtimestamp(
            stat.st_mtime,
            tz=timezone.utc,
        ).isoformat(),
    }


def same_local_fingerprint(
    old_local: dict[str, Any],
    new_fingerprint: dict[str, Any],
) -> bool:
    return (
        old_local.get("size_bytes") == new_fingerprint.get("size_bytes")
        and old_local.get("modified_at") == new_fingerprint.get("modified_at")
    )


def get_destination_key() -> str:
    return f"{DRIVE_ACCOUNT_HINT}::{DRIVE_ROOT_FOLDER_ID}"


def get_folder_log_key(relative_folder: str) -> str:
    return f"{get_destination_key()}::{relative_folder}"


# =====================================================================
# SAFE FILE WRITES FOR WINDOWS
# =====================================================================

def safe_replace_json(
    target_file: Path,
    data: dict[str, Any],
    recovery_event_type: str,
) -> None:
    """
    Writes JSON safely on Windows.

    If Windows temporarily locks the target file, retry. If it is still
    locked, save a recovered copy instead of crashing.
    """

    text = json.dumps(data, indent=2, ensure_ascii=False)

    temp_file = target_file.with_name(
        f"{target_file.name}.{os.getpid()}.tmp"
    )

    temp_file.write_text(text, encoding="utf-8")

    last_error = None

    for _ in range(10):
        try:
            os.replace(temp_file, target_file)
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(0.5)

    fallback_file = target_file.with_name(
        f"{target_file.stem}.recovered-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    )

    shutil.copyfile(temp_file, fallback_file)

    append_event(
        event_type=recovery_event_type,
        payload={
            "reason": "permission_error_replacing_file",
            "target_file": str(target_file),
            "fallback_file": str(fallback_file),
            "error": str(last_error),
        },
    )

    print()
    print("WARNING: Windows locked this file, so the script saved a recovered copy:")
    print(f"  Target:    {target_file.resolve()}")
    print(f"  Recovered: {fallback_file.resolve()}")
    print("Close apps previewing/opening the JSON file, then copy the recovered file over the target if needed.")
    print()


# =====================================================================
# APPEND-ONLY EVENTS LOG
# =====================================================================

def append_event(event_type: str, payload: dict[str, Any]) -> None:
    event = {
        "event_type": event_type,
        "event_time": now_iso(),
        "drive_account_hint": DRIVE_ACCOUNT_HINT,
        "drive_root_folder_id": DRIVE_ROOT_FOLDER_ID,
        "destination_key": get_destination_key(),
        "payload": payload,
    }

    with UPLOAD_EVENTS_FILE.open("a", encoding="utf-8") as file:
        file.write(json.dumps(event, ensure_ascii=False) + "\n")
        file.flush()


# =====================================================================
# STATE / CATALOG
# =====================================================================

def new_empty_state() -> dict[str, Any]:
    return {
        "schema_version": 6,
        "identity_strategy": "normalized_file_name",
        "upload_policy": "upload_each_file_once_globally",
        "local_root_last_used": str(LOCAL_ROOT),
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "active_destination": {
            "drive_account_hint": DRIVE_ACCOUNT_HINT,
            "drive_root_folder_id": DRIVE_ROOT_FOLDER_ID,
            "token_file": str(TOKEN_FILE),
        },
        "destinations": {},
        "folders": {},
        "files": {},
    }


def load_state() -> dict[str, Any]:
    if not UPLOAD_STATE_FILE.exists():
        state = new_empty_state()
    else:
        state = json.loads(UPLOAD_STATE_FILE.read_text(encoding="utf-8"))

    state.setdefault("schema_version", 6)
    state.setdefault("identity_strategy", "normalized_file_name")
    state.setdefault("upload_policy", "upload_each_file_once_globally")
    state.setdefault("local_root_last_used", str(LOCAL_ROOT))
    state.setdefault("created_at", now_iso())
    state.setdefault("updated_at", now_iso())
    state.setdefault("active_destination", {})
    state.setdefault("destinations", {})
    state.setdefault("folders", {})
    state.setdefault("files", {})

    return state


def save_state(state: dict[str, Any]) -> None:
    state["schema_version"] = 6
    state["identity_strategy"] = "normalized_file_name"
    state["upload_policy"] = "upload_each_file_once_globally"
    state["updated_at"] = now_iso()
    state["local_root_last_used"] = str(LOCAL_ROOT)
    state["active_destination"] = {
        "drive_account_hint": DRIVE_ACCOUNT_HINT,
        "drive_root_folder_id": DRIVE_ROOT_FOLDER_ID,
        "token_file": str(TOKEN_FILE),
    }

    destination_key = get_destination_key()
    state["destinations"][destination_key] = {
        "drive_account_hint": DRIVE_ACCOUNT_HINT,
        "drive_root_folder_id": DRIVE_ROOT_FOLDER_ID,
        "token_file": str(TOKEN_FILE),
        "last_used_at": now_iso(),
    }

    safe_replace_json(
        target_file=UPLOAD_STATE_FILE,
        data=state,
        recovery_event_type="state_save_recovered_to_fallback",
    )


def get_assigned_destination(file_entry: dict[str, Any]) -> dict[str, Any] | None:
    assigned_key = file_entry.get("assigned_destination_key")
    destinations = file_entry.get("destinations", {})

    if assigned_key and assigned_key in destinations:
        return destinations[assigned_key]

    for destination in destinations.values():
        if destination.get("file_id"):
            return destination

    return None


def save_wavestack_catalog(state: dict[str, Any]) -> None:
    tracks = []

    for file_identity_key, file_entry in state.get("files", {}).items():
        metadata = file_entry.get("metadata", {})
        wavestack = file_entry.get("wavestack", {})
        local = file_entry.get("local", {})

        if not metadata.get("is_audio"):
            continue

        assigned_destination = get_assigned_destination(file_entry)
        if not assigned_destination:
            continue

        drive_file_id = assigned_destination.get("file_id")
        if not drive_file_id:
            continue

        tracks.append(
            {
                "id": drive_file_id,
                "identityKey": file_identity_key,
                "title": wavestack.get("display_title") or metadata.get("title"),
                "artist": wavestack.get("display_artist") or metadata.get("artist"),
                "album": wavestack.get("display_album") or metadata.get("album"),
                "genre": metadata.get("genre"),
                "year": metadata.get("year"),
                "trackNumber": metadata.get("track_number"),
                "durationSeconds": metadata.get("duration_seconds"),
                "mimeType": assigned_destination.get("mime_type"),
                "source": "google_drive",
                "driveAccountHint": assigned_destination.get("drive_account_hint"),
                "driveRootFolderId": assigned_destination.get("drive_root_folder_id"),
                "driveFileId": drive_file_id,
                "driveFileUrl": assigned_destination.get("file_url"),
                "streamUrl": assigned_destination.get("stream_url"),
                "lookupKey": wavestack.get("lookup_key"),
                "originalFileName": local.get("file_name"),
                "localPathLastSeen": local.get("absolute_path_last_seen"),
                "localRelativePathLastSeen": local.get("relative_path_last_seen"),
                "assignedDestinationKey": file_entry.get("assigned_destination_key"),
            }
        )

    catalog = {
        "schema_version": 5,
        "generated_at": now_iso(),
        "identity_strategy": "normalized_file_name",
        "upload_policy": "upload_each_file_once_globally",
        "active_drive_account_hint": DRIVE_ACCOUNT_HINT,
        "active_drive_root_folder_id": DRIVE_ROOT_FOLDER_ID,
        "source_state": str(UPLOAD_STATE_FILE),
        "source_events": str(UPLOAD_EVENTS_FILE),
        "track_count": len(tracks),
        "tracks": tracks,
    }

    safe_replace_json(
        target_file=WAVESTACK_CATALOG_FILE,
        data=catalog,
        recovery_event_type="catalog_save_recovered_to_fallback",
    )


# =====================================================================
# OPTIONAL LEGACY MIGRATION
# =====================================================================

def migrate_legacy_log_if_needed(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("files"):
        return state

    if not LEGACY_UPLOAD_LOG_FILE.exists():
        return state

    try:
        legacy = json.loads(LEGACY_UPLOAD_LOG_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        append_event(
            event_type="legacy_migration_failed",
            payload={
                "legacy_file": str(LEGACY_UPLOAD_LOG_FILE),
                "error": str(exc),
            },
        )
        return state

    legacy_files = legacy.get("files", {})
    if not isinstance(legacy_files, dict):
        return state

    migrated_count = 0

    for legacy_key, legacy_entry in legacy_files.items():
        local = legacy_entry.get("local", {})
        metadata = legacy_entry.get("metadata", {})
        wavestack = legacy_entry.get("wavestack", {})
        drive = legacy_entry.get("drive", {})

        file_name = (
            local.get("file_name")
            or drive.get("file_name")
            or Path(str(legacy_key)).name
        )

        if not file_name:
            continue

        file_identity_key = normalize_file_identity(file_name)

        drive_file_id = drive.get("file_id")
        if not drive_file_id:
            continue

        destination_key = (
            drive.get("destination_key")
            or f"{drive.get('drive_account_hint', 'legacy')}::{drive.get('drive_root_folder_id', legacy.get('drive_root_folder_id', 'legacy'))}"
        )

        destination_entry = {
            "destination_key": destination_key,
            "drive_account_hint": drive.get("drive_account_hint", "legacy"),
            "drive_root_folder_id": drive.get(
                "drive_root_folder_id",
                legacy.get("drive_root_folder_id", "legacy"),
            ),
            "action": drive.get("action", drive.get("drive_action", "uploaded")),
            "file_id": drive_file_id,
            "file_name": drive.get("file_name", file_name),
            "parent_folder_id": drive.get("parent_folder_id", drive.get("drive_parent_folder_id")),
            "file_url": drive.get("file_url", drive.get("drive_file_url")),
            "stream_url": drive.get(
                "stream_url",
                f"https://drive.google.com/uc?export=download&id={drive_file_id}",
            ),
            "mime_type": drive.get("mime_type"),
            "size_bytes": drive.get("size_bytes"),
            "md5_checksum": drive.get("md5_checksum"),
            "uploaded_at": legacy_entry.get("uploaded_at", now_iso()),
        }

        state["files"][file_identity_key] = {
            "identity": {
                "type": "normalized_file_name",
                "key": file_identity_key,
                "file_name": file_name,
            },
            "local": {
                "absolute_path_last_seen": local.get(
                    "absolute_path_last_seen",
                    local.get("absolute_path", ""),
                ),
                "relative_path_last_seen": local.get(
                    "relative_path_last_seen",
                    local.get("relative_path", file_name),
                ),
                "relative_folder_last_seen": local.get(
                    "relative_folder_last_seen",
                    local.get("relative_folder", "."),
                ),
                "file_name": file_name,
                "file_stem": Path(file_name).stem,
                "file_extension": Path(file_name).suffix.lower(),
                "size_bytes": local.get("size_bytes"),
                "modified_at": local.get("modified_at"),
            },
            "metadata": metadata,
            "wavestack": wavestack,
            "assigned_destination_key": destination_key,
            "destinations": {
                destination_key: destination_entry,
            },
            "last_seen_at": now_iso(),
        }

        migrated_count += 1

    if migrated_count:
        append_event(
            event_type="legacy_migration_completed",
            payload={
                "legacy_file": str(LEGACY_UPLOAD_LOG_FILE),
                "migrated_count": migrated_count,
            },
        )

    return state


# =====================================================================
# AUDIO METADATA
# =====================================================================

def read_audio_metadata(local_file: Path) -> dict[str, Any]:
    base_metadata = {
        "is_audio": is_audio_file(local_file),
        "title": local_file.stem,
        "artist": "Unknown Artist",
        "album": None,
        "album_artist": None,
        "genre": None,
        "duration_seconds": None,
        "track_number": None,
        "disc_number": None,
        "year": None,
        "date": None,
        "composer": None,
        "comment": None,
        "metadata_read_error": None,
    }

    if not is_audio_file(local_file):
        return base_metadata

    try:
        audio = MutagenFile(str(local_file), easy=True)
    except Exception as exc:
        base_metadata["metadata_read_error"] = str(exc)
        return base_metadata

    if audio is None:
        return base_metadata

    tags = audio.tags or {}

    def first(*tag_names: str) -> str | None:
        for tag_name in tag_names:
            values = tags.get(tag_name)
            if values:
                text = safe_text(values[0])
                if text:
                    return text
        return None

    duration = None
    if audio.info and getattr(audio.info, "length", None):
        duration = round(float(audio.info.length))

    title = first("title") or local_file.stem
    artist = first("artist", "albumartist", "composer") or "Unknown Artist"
    album = first("album")
    album_artist = first("albumartist")
    date = first("date", "originaldate")
    year = None

    if date:
        match = re.search(r"\d{4}", date)
        if match:
            year = match.group(0)

    base_metadata.update(
        {
            "title": title,
            "artist": artist,
            "album": album,
            "album_artist": album_artist,
            "genre": first("genre"),
            "duration_seconds": duration,
            "track_number": first("tracknumber"),
            "disc_number": first("discnumber"),
            "year": year,
            "date": date,
            "composer": first("composer"),
            "comment": first("comment", "description"),
        }
    )

    return base_metadata


def build_wavestack_fields(metadata: dict[str, Any], local_file: Path) -> dict[str, Any]:
    title = metadata.get("title") or local_file.stem
    artist = metadata.get("artist") or "Unknown Artist"
    album = metadata.get("album")

    artist_slug = slugify(artist)
    title_slug = slugify(title)

    return {
        "display_title": title,
        "display_artist": artist,
        "display_album": album,
        "lookup_key": f"{artist_slug}/{title_slug}",
        "search_text": " ".join(
            part
            for part in [
                title,
                artist,
                album,
                metadata.get("genre"),
                metadata.get("year"),
                local_file.name,
            ]
            if part
        ),
        "source": "google_drive",
    }


# =====================================================================
# GOOGLE DRIVE HELPERS
# =====================================================================

def find_drive_folder(service, name: str, parent_id: str) -> str | None:
    safe_name = drive_query_escape(name)

    query = (
        f"name = '{safe_name}' "
        f"and mimeType = 'application/vnd.google-apps.folder' "
        f"and '{parent_id}' in parents "
        f"and trashed = false"
    )

    result = service.files().list(
        q=query,
        spaces="drive",
        fields="files(id, name)",
        pageSize=10,
    ).execute()

    files = result.get("files", [])
    if not files:
        return None

    return files[0]["id"]


def create_drive_folder(service, name: str, parent_id: str) -> str:
    metadata = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }

    folder = service.files().create(
        body=metadata,
        fields="id, name",
    ).execute()

    return folder["id"]


def get_or_create_drive_folder(
    service,
    local_relative_folder: Path,
    state: dict[str, Any],
) -> str:
    if str(local_relative_folder) in ("", "."):
        return DRIVE_ROOT_FOLDER_ID

    current_parent_id = DRIVE_ROOT_FOLDER_ID
    current_parts: list[str] = []

    for part in local_relative_folder.parts:
        current_parts.append(part)
        relative_folder_key = normalize_slashes(str(Path(*current_parts)))
        folder_log_key = get_folder_log_key(relative_folder_key)

        existing_state_entry = state["folders"].get(folder_log_key)
        if existing_state_entry:
            current_parent_id = existing_state_entry["drive_folder_id"]
            continue

        existing_drive_id = find_drive_folder(service, part, current_parent_id)

        if existing_drive_id:
            drive_folder_id = existing_drive_id
            action = "found_existing"
        else:
            drive_folder_id = create_drive_folder(service, part, current_parent_id)
            action = "created"

        folder_entry = {
            "destination_key": get_destination_key(),
            "drive_account_hint": DRIVE_ACCOUNT_HINT,
            "drive_root_folder_id": DRIVE_ROOT_FOLDER_ID,
            "local_relative_folder": relative_folder_key,
            "drive_folder_name": part,
            "drive_folder_id": drive_folder_id,
            "drive_parent_folder_id": current_parent_id,
            "drive_folder_url": f"https://drive.google.com/drive/folders/{drive_folder_id}",
            "drive_action": action,
            "logged_at": now_iso(),
        }

        state["folders"][folder_log_key] = folder_entry

        append_event(
            event_type="folder_logged",
            payload=folder_entry,
        )

        save_state(state)

        current_parent_id = drive_folder_id

    return current_parent_id


def find_existing_drive_file(
    service,
    filename: str,
    parent_id: str,
) -> dict[str, Any] | None:
    safe_name = drive_query_escape(filename)

    query = (
        f"name = '{safe_name}' "
        f"and '{parent_id}' in parents "
        f"and trashed = false"
    )

    result = service.files().list(
        q=query,
        spaces="drive",
        fields="files(id, name, size, modifiedTime, md5Checksum, mimeType, webViewLink)",
        pageSize=10,
    ).execute()

    files = result.get("files", [])
    if not files:
        return None

    return files[0]


def upload_file(service, local_file: Path, parent_folder_id: str) -> dict[str, Any]:
    mime_type, _ = mimetypes.guess_type(str(local_file))
    if mime_type is None:
        mime_type = "application/octet-stream"

    media = MediaFileUpload(
        str(local_file),
        mimetype=mime_type,
        resumable=True,
    )

    metadata = {
        "name": local_file.name,
        "parents": [parent_folder_id],
    }

    uploaded = service.files().create(
        body=metadata,
        media_body=media,
        fields="id, name, size, mimeType, md5Checksum, webViewLink, parents",
    ).execute()

    return uploaded


def update_existing_file(
    service,
    local_file: Path,
    drive_file_id: str,
) -> dict[str, Any]:
    mime_type, _ = mimetypes.guess_type(str(local_file))
    if mime_type is None:
        mime_type = "application/octet-stream"

    media = MediaFileUpload(
        str(local_file),
        mimetype=mime_type,
        resumable=True,
    )

    updated = service.files().update(
        fileId=drive_file_id,
        media_body=media,
        fields="id, name, size, mimeType, md5Checksum, webViewLink, parents",
    ).execute()

    return updated


# =====================================================================
# RESUME / SKIP LOGIC
# =====================================================================

def should_skip_local_script_file(path: Path) -> bool:
    skip_names = {
        "credentials.json",
        "wavestack_drive_upload_events.jsonl",
        "wavestack_drive_upload_state.json",
        "wavestack_drive_upload_log.json",
        "wavestack_catalog.json",
        "wavestack_upload.py",
        "upload_wavestack_to_drive.py",
    }

    if path.name in skip_names:
        return True

    if path.name.startswith("token_") and path.suffix == ".json":
        return True

    if path.name.startswith("."):
        return True

    return False


def is_uploaded_anywhere(
    state: dict[str, Any],
    file_identity_key: str,
    local_file: Path,
) -> bool:
    file_entry = state.get("files", {}).get(file_identity_key)
    if not file_entry:
        return False

    destinations = file_entry.get("destinations", {})
    if not destinations:
        return False

    has_any_uploaded_destination = any(
        destination.get("file_id")
        for destination in destinations.values()
    )

    if not has_any_uploaded_destination:
        return False

    current_fingerprint = get_local_file_fingerprint(local_file)
    old_local = file_entry.get("local", {})

    # If an older migrated entry has no fingerprint, still skip by filename.
    if old_local.get("size_bytes") is None or old_local.get("modified_at") is None:
        return True

    if not same_local_fingerprint(old_local, current_fingerprint):
        return False

    return True


def build_base_file_entry(
    local_file: Path,
    relative_file: Path,
    relative_folder: Path,
    metadata: dict[str, Any],
    wavestack_fields: dict[str, Any],
) -> dict[str, Any]:
    fingerprint = get_local_file_fingerprint(local_file)

    relative_path = normalize_slashes(str(relative_file))
    relative_folder_path = normalize_slashes(str(relative_folder))
    file_identity_key = normalize_file_identity(local_file.name)

    return {
        "identity": {
            "type": "normalized_file_name",
            "key": file_identity_key,
            "file_name": local_file.name,
        },
        "local": {
            "absolute_path_last_seen": str(local_file),
            "relative_path_last_seen": relative_path,
            "relative_folder_last_seen": relative_folder_path,
            "file_name": local_file.name,
            "file_stem": local_file.stem,
            "file_extension": local_file.suffix.lower(),
            "size_bytes": fingerprint["size_bytes"],
            "modified_at": fingerprint["modified_at"],
        },
        "metadata": metadata,
        "wavestack": wavestack_fields,
        "assigned_destination_key": None,
        "destinations": {},
        "last_seen_at": now_iso(),
    }


def build_destination_entry(
    drive_file: dict[str, Any],
    parent_drive_folder_id: str,
    action: str,
) -> dict[str, Any]:
    drive_file_id = drive_file["id"]

    return {
        "destination_key": get_destination_key(),
        "drive_account_hint": DRIVE_ACCOUNT_HINT,
        "drive_root_folder_id": DRIVE_ROOT_FOLDER_ID,
        "action": action,
        "file_id": drive_file_id,
        "file_name": drive_file.get("name"),
        "parent_folder_id": parent_drive_folder_id,
        "file_url": drive_file.get(
            "webViewLink",
            f"https://drive.google.com/file/d/{drive_file_id}/view",
        ),
        "stream_url": f"https://drive.google.com/uc?export=download&id={drive_file_id}",
        "mime_type": drive_file.get("mimeType"),
        "size_bytes": drive_file.get("size"),
        "md5_checksum": drive_file.get("md5Checksum"),
        "uploaded_at": now_iso(),
    }


# =====================================================================
# MAIN UPLOAD LOGIC
# =====================================================================

def main() -> int:
    if not LOCAL_ROOT.exists():
        print(f"ERROR: Local folder does not exist: {LOCAL_ROOT}")
        return 1

    if not LOCAL_ROOT.is_dir():
        print(f"ERROR: Local path is not a folder: {LOCAL_ROOT}")
        return 1

    print("WaveStack upload destination:")
    print(f"  Local root:    {LOCAL_ROOT}")
    print(f"  Account hint:  {DRIVE_ACCOUNT_HINT}")
    print(f"  Root folder:   {DRIVE_ROOT_FOLDER_ID}")
    print(f"  Token file:    {TOKEN_FILE}")
    print(f"  Identity mode: actual file name")
    print(f"  Upload policy: upload each file once globally")
    print()

    append_event(
        event_type="run_started",
        payload={
            "local_root": str(LOCAL_ROOT),
            "drive_account_hint": DRIVE_ACCOUNT_HINT,
            "drive_root_folder_id": DRIVE_ROOT_FOLDER_ID,
            "token_file": str(TOKEN_FILE),
            "identity_strategy": "normalized_file_name",
            "upload_policy": "upload_each_file_once_globally",
        },
    )

    service = get_drive_service()

    state = load_state()
    state = migrate_legacy_log_if_needed(state)
    save_state(state)
    save_wavestack_catalog(state)

    all_files = [
        path
        for path in LOCAL_ROOT.rglob("*")
        if path.is_file() and not should_skip_local_script_file(path)
    ]

    total_files = len(all_files)
    uploaded_count = 0
    updated_count = 0
    skipped_count = 0
    failed_count = 0

    print(f"Found {total_files} files from:")
    print(LOCAL_ROOT)
    print()

    for index, local_file in enumerate(all_files, start=1):
        if STOP_REQUESTED:
            print("Stopped before starting next file.")
            break

        relative_file = local_file.relative_to(LOCAL_ROOT)
        relative_folder = relative_file.parent
        relative_file_key = normalize_slashes(str(relative_file))
        file_identity_key = normalize_file_identity(local_file.name)

        print(f"[{index}/{total_files}] {local_file.name}")
        print(f"  identity key: {file_identity_key}")
        print(f"  local path:   {relative_file_key}")

        if is_uploaded_anywhere(state, file_identity_key, local_file):
            skipped_count += 1

            existing_entry = state["files"][file_identity_key]
            existing_destinations = existing_entry.get("destinations", {})
            existing_destination_keys = list(existing_destinations.keys())
            assigned_destination_key = existing_entry.get("assigned_destination_key")

            print("  skipped: file was already uploaded to a Drive destination")
            print(f"  assigned destination: {assigned_destination_key}")
            print()

            append_event(
                event_type="file_skipped",
                payload={
                    "reason": "already_uploaded_anywhere",
                    "file_identity_key": file_identity_key,
                    "file_name": local_file.name,
                    "local_path_seen": str(local_file),
                    "local_relative_path_seen": relative_file_key,
                    "assigned_destination_key": assigned_destination_key,
                    "existing_destination_keys": existing_destination_keys,
                },
            )

            continue

        try:
            metadata = read_audio_metadata(local_file)
            wavestack_fields = build_wavestack_fields(metadata, local_file)

            if metadata["is_audio"]:
                print(
                    f"  metadata: "
                    f"{wavestack_fields['display_artist']} - "
                    f"{wavestack_fields['display_title']}"
                )
            else:
                print("  metadata: non-audio file")

            parent_drive_folder_id = get_or_create_drive_folder(
                service=service,
                local_relative_folder=relative_folder,
                state=state,
            )

            existing = find_existing_drive_file(
                service=service,
                filename=local_file.name,
                parent_id=parent_drive_folder_id,
            )

            if existing:
                drive_file = update_existing_file(
                    service=service,
                    local_file=local_file,
                    drive_file_id=existing["id"],
                )
                action = "updated"
                updated_count += 1
            else:
                drive_file = upload_file(
                    service=service,
                    local_file=local_file,
                    parent_folder_id=parent_drive_folder_id,
                )
                action = "uploaded"
                uploaded_count += 1

            old_entry = state["files"].get(file_identity_key, {})

            new_entry = build_base_file_entry(
                local_file=local_file,
                relative_file=relative_file,
                relative_folder=relative_folder,
                metadata=metadata,
                wavestack_fields=wavestack_fields,
            )

            existing_destinations = old_entry.get("destinations", {})
            new_entry["destinations"] = existing_destinations

            destination_entry = build_destination_entry(
                drive_file=drive_file,
                parent_drive_folder_id=parent_drive_folder_id,
                action=action,
            )

            destination_key = get_destination_key()
            new_entry["assigned_destination_key"] = destination_key
            new_entry["destinations"][destination_key] = destination_entry

            state["files"][file_identity_key] = new_entry

            append_event(
                event_type="file_uploaded",
                payload={
                    "action": action,
                    "file_identity_key": file_identity_key,
                    "file_name": local_file.name,
                    "local_path_seen": str(local_file),
                    "local_relative_path_seen": relative_file_key,
                    "display_title": wavestack_fields.get("display_title"),
                    "display_artist": wavestack_fields.get("display_artist"),
                    "display_album": wavestack_fields.get("display_album"),
                    "assigned_destination_key": destination_key,
                    "drive_file_id": drive_file.get("id"),
                    "drive_file_name": drive_file.get("name"),
                    "drive_file_url": drive_file.get("webViewLink"),
                    "drive_parent_folder_id": parent_drive_folder_id,
                    "destination": destination_entry,
                },
            )

            save_state(state)
            save_wavestack_catalog(state)

            print(f"  {action}: {drive_file.get('webViewLink', drive_file['id'])}")
            print()

        except KeyboardInterrupt:
            save_state(state)
            save_wavestack_catalog(state)

            append_event(
                event_type="run_interrupted",
                payload={
                    "reason": "keyboard_interrupt",
                    "last_file_identity_key": file_identity_key,
                    "last_file_name": local_file.name,
                    "last_local_path_seen": str(local_file),
                },
            )

            print("Stopped. Progress saved.")
            return 130

        except HttpError as exc:
            failed_count += 1
            print(f"  failed Google API error: {exc}")
            print()

            error_text = str(exc).lower()

            if (
                "storagequotaexceeded" in error_text
                or "quota" in error_text
                or "storage" in error_text
            ):
                print()
                print("Google Drive storage appears to be full or quota-limited.")
                print("Change DRIVE_ROOT_FOLDER_ID and/or DRIVE_ACCOUNT_HINT, then rerun.")
                print("Already uploaded files will be skipped globally.")
                print("This failed file will be retried in the new destination.")
                print()

            append_event(
                event_type="file_failed",
                payload={
                    "error_type": "google_api_error",
                    "error": str(exc),
                    "file_identity_key": file_identity_key,
                    "file_name": local_file.name,
                    "local_path_seen": str(local_file),
                    "local_relative_path_seen": relative_file_key,
                },
            )

            save_state(state)
            save_wavestack_catalog(state)

            status = getattr(exc.resp, "status", None)
            if status in {401, 403, 429}:
                print("Stopping because this looks like an auth, permission, or quota issue.")

                append_event(
                    event_type="run_stopped",
                    payload={
                        "reason": "auth_permission_or_quota_issue",
                        "http_status": status,
                    },
                )

                return 1

        except Exception as exc:
            failed_count += 1
            print(f"  failed: {exc}")
            print()

            append_event(
                event_type="file_failed",
                payload={
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "file_identity_key": file_identity_key,
                    "file_name": local_file.name,
                    "local_path_seen": str(local_file),
                    "local_relative_path_seen": relative_file_key,
                },
            )

            save_state(state)
            save_wavestack_catalog(state)

    save_state(state)
    save_wavestack_catalog(state)

    append_event(
        event_type="run_finished",
        payload={
            "uploaded": uploaded_count,
            "updated": updated_count,
            "skipped": skipped_count,
            "failed": failed_count,
            "total_files_seen": total_files,
            "upload_policy": "upload_each_file_once_globally",
        },
    )

    print("Done.")
    print(f"Uploaded: {uploaded_count}")
    print(f"Updated:  {updated_count}")
    print(f"Skipped:  {skipped_count}")
    print(f"Failed:   {failed_count}")
    print()
    print(f"Append-only event log: {UPLOAD_EVENTS_FILE.resolve()}")
    print(f"Resume state file:      {UPLOAD_STATE_FILE.resolve()}")
    print(f"WaveStack catalog:      {WAVESTACK_CATALOG_FILE.resolve()}")

    return 0 if failed_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())