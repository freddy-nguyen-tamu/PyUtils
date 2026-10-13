#!/usr/bin/env python3
"""Upload root-level images to Google Photos with the GPMC Python library.

No gotohp executable, browser automation, or Google Cloud OAuth project.
Uses the *unofficial* reverse-engineered Google Photos mobile interface.
Normal Google account storage accounting is explicitly enabled.

Install: py -m pip install --upgrade gpmc gpsoauth tqdm keyring pyyaml
Sign-in with normal Chrome (once):
  py google_photos_upload.py --add-account you@gmail.com
Or import your already-authorized gotohp accounts without running gotohp:
  py google_photos_upload.py --import-gotohp
Then: py google_photos_upload.py --limit 3
      py google_photos_upload.py

Transient network errors are retried (up to 2 extra times) with exponential
backoff and a remote hash check. Failed images remain untouched in root and are
deferred so the scan continues. Uncertain commits are NEVER replayed by default;
storage-full and account-authentication errors stop immediately.

Browser sign-in opens Google Embedded Setup and asks you to paste the one-use
short-lived oauth_token cookie (same method gotohp uses). gpsoauth exchanges it
for a Google master token, which is highly sensitive. Never share this token.
Only the resulting auth data is saved via Windows Credential Manager / keyring.
This is an unofficial Google mobile-app authentication method, not Google OAuth
for third-party apps; Google may reject or change it without warning.
"""

from __future__ import annotations

import argparse
import datetime as dt
import getpass
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import time
import webbrowser
from urllib.parse import parse_qs, urlencode

ROOT = Path(r"C:\Users\qacer\Downloads\new\byoru")
IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".jpe", ".png", ".gif", ".webp", ".bmp",
    ".heic", ".heif", ".avif", ".tif", ".tiff", ".dng",
    ".cr2", ".cr3", ".nef", ".arw", ".orf", ".rw2", ".raf",
}
KEYRING_SERVICE = "byoru-google-photos-gpmc-v1"


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def setup_log(path: Path) -> logging.Logger:
    logger = logging.getLogger("byoru_upload")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    file_handler = logging.FileHandler(path, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(file_handler)
    logger.propagate = False
    # Do not forward arbitrary third-party messages to disk: an upstream
    # HTTP exception could contain credential-bearing request parameters.
    return logger


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def file_signature(path: Path) -> dict:
    stat = path.stat()
    return {"name": path.name, "size": stat.st_size, "sha256": sha256(path)}


def log_event(journal: Path, logger: logging.Logger, event: str,
              signature: dict, account: str, **details: object) -> None:
    # Copy only the immutable file signature: legacy journal records also
    # contain an "event" key and must not override this new event.
    data = {"when": now(), "event": event, "account": account,
            "name": signature["name"], "size": signature["size"],
            "sha256": signature["sha256"], **details}
    with journal.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(data, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    logger.info("EVENT event=%s account=%s file=%s size=%s", event, account,
                signature["name"], signature["size"])


def read_journal(path: Path) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    if not path.exists():
        return latest
    with path.open("r", encoding="utf-8") as stream:
        for line_num, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Journal line {line_num} is corrupt; stop to avoid duplicates: {exc}") from exc
            if record.get("name") and record.get("event"):
                latest[record["name"]] = record
    return latest


def same_file(record: dict | None, path: Path) -> bool:
    return bool(record and path.stat().st_size == record.get("size")
                and sha256(path) == record.get("sha256"))


def move_unique(source: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    destination = dest_dir / source.name
    i = 1
    while destination.exists():
        destination = dest_dir / f"{source.stem} ({i}){source.suffix}"
        i += 1
    source.rename(destination)  # same drive, never overwrite
    return destination


def storage_full(error: str) -> bool:
    return any(re.search(pattern, error, flags=re.I) for pattern in (
        r"storage\s+(?:quota\s+)?(?:is\s+)?(?:full|exceeded|limit)",
        r"out of (?:google\s+)?storage", r"insufficient[_ -]storage",
        r"not enough storage", r"storage_quota_exceeded",
        r"storagequotaexceeded", r"account storage",
    ))


def email_from_auth(auth_data: str) -> str:
    fields = parse_qs(auth_data.strip().lstrip("?"), keep_blank_values=True)
    result = (fields.get("Email") or fields.get("email") or [""])[0].strip().lower()
    if not result or "@" not in result or any(c.isspace() for c in result):
        raise ValueError("This doesn't look like GPMC mobile auth_data (Email= is missing). "
                         "An oauth_token cookie cannot be pasted here directly.")
    if "androidId=" not in auth_data:
        raise ValueError("Expected a mobile auth_data string beginning with androidId=...")
    return result


def get_keyring():
    try:
        import keyring
    except ImportError as exc:
        raise RuntimeError("Install keyring: py -m pip install keyring") from exc
    return keyring


def account_manifest(state: Path) -> Path:
    # The older gotohp/API wrappers also wrote "accounts.json", but used
    # incompatible layouts. NEVER overwrite or rename their configuration.
    return state / "gpmc_accounts.json"


def _account_names(data: object) -> list[str]:
    """Read only recognizable account indexes, never auth/token values."""
    if isinstance(data, dict):
        # Some historical versions stored {"accounts": [...]}, while others
        # stored an email -> metadata mapping. Avoid traversing arbitrary
        # dictionaries, as they could contain credentials.
        if "accounts" in data:
            data = data["accounts"]
        elif "emails" in data:
            data = data["emails"]
        elif all(isinstance(k, str) and "@" in k for k in data):
            data = list(data)
        else:
            return []
    if isinstance(data, dict):
        # For {"accounts": {"user@gmail.com": {...}}}.
        data = list(data)
    if not isinstance(data, list):
        return []
    result: set[str] = set()
    for item in data:
        if isinstance(item, str):
            email = item.strip().lower()
        elif isinstance(item, dict):
            email = str(item.get("email", "")).strip().lower()
        else:
            continue
        if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            result.add(email)
    return sorted(result)


def list_accounts(state: Path) -> list[str]:
    path = account_manifest(state)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            # Stop instead of silently destroying OUR own account index.
            raise RuntimeError(f"Cannot read GPMC account index: {path} ({type(exc).__name__})") from exc
        if not isinstance(data, list):
            raise RuntimeError(f"Invalid GPMC account index: {path} (expected list)")
        return _account_names(data)

    # Migrate only recognized account names from an older uploader index.
    # If the format is unfamiliar/corrupt, simply ignore it; --import-gotohp
    # will still register accounts and create a NEW independent index.
    legacy = state / "accounts.json"
    if legacy.exists():
        try:
            candidates = _account_names(json.loads(legacy.read_text(encoding="utf-8-sig")))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return []
        # The legacy index may list accounts authorized only with the old
        # Google OAuth script. Show an account as available to GPMC ONLY if
        # its matching mobile credential already exists in our keyring.
        if candidates:
            try:
                ring = get_keyring()
                usable = []
                for email in candidates:
                    credential = ring.get_password(KEYRING_SERVICE, email)
                    if credential:
                        try:
                            if email_from_auth(credential) == email:
                                usable.append(email)
                        except ValueError:
                            continue
                return usable
            except Exception:
                # Legacy index is optional; imports will build our own index.
                return []
    return []


def save_account_name(state: Path, email: str) -> None:
    names = sorted(set(list_accounts(state) + [email.strip().lower()]))
    target = account_manifest(state)
    temp = target.with_suffix(".tmp")
    temp.write_text(json.dumps(names, ensure_ascii=False, indent=2), encoding="utf-8")
    # Atomic replacement for our index only. The legacy accounts.json,
    # upload logs, and recovery journals are left completely untouched.
    temp.replace(target)


GOOGLE_EMBEDDED_SETUP = "https://accounts.google.com/EmbeddedSetup"
PHOTOS_APP = "com.google.android.apps.photos"
# Public SHA1 signature of the Google Photos APK; not an account secret.
PHOTOS_APP_SIGNATURE = "24bb24c05e47e0aefa68a58a766179d9b613a600"
PHOTOS_AUTH_SCOPE = (
    "oauth2:openid https://www.googleapis.com/auth/mobileapps.native "
    "https://www.googleapis.com/auth/photos.native"
)


def launch_normal_chrome(url: str) -> None:
    """Open ordinary installed Chrome; no Selenium, Playwright or debugging port."""
    paths = [
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
    ]
    found = shutil.which("chrome") or shutil.which("chrome.exe")
    if found:
        paths.insert(0, Path(found))
    for candidate in paths:
        if candidate.is_file():
            subprocess.Popen([str(candidate), "--new-tab", url],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
    if not webbrowser.open(url):
        print(f"Open this in normal Chrome manually: {url}")


def make_mobile_auth(email: str, master_token: str, android_id: str) -> str:
    """Build the request parameters consumed by GPMC's photos.native auth flow.

    This is experimental: auth token-binding, protocol versions and Google rules
    can make particular accounts fail. It does not use the official Photos API.
    """
    if not re.fullmatch(r"[0-9a-f]{16}", android_id):
        raise ValueError("Invalid Android ID")
    if not master_token.startswith("aas_et/"):
        raise ValueError("Google did not return a recognized master token")
    values = {
        "androidId": android_id,
        "app": PHOTOS_APP,
        "callerPkg": PHOTOS_APP,
        "client_sig": PHOTOS_APP_SIGNATURE,
        "callerSig": PHOTOS_APP_SIGNATURE,
        "Email": email,
        "Token": master_token,
        "service": PHOTOS_AUTH_SCOPE,
        "lang": "en-US",
        "device_country": "us",
        "google_play_services_version": "240913000",
        "sdk_version": "28",
        "source": "android",
        "oauth2_foreground": "1",
        "check_email": "1",
    }
    return urlencode(values)


def browser_register_account(state: Path, selected: str) -> None:
    """One-time Chrome sign-in and oauth_token -> master token exchange."""
    try:
        import gpsoauth
    except ImportError as exc:
        raise RuntimeError("Install gpsoauth: python -m pip install gpsoauth") from exc
    selected = selected.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", selected):
        raise ValueError("Expected a valid Google account email")
    print("\nThis uses Google's unofficial Android authentication path.")
    print("Google's master token can access more than Photos. Treat it as a password.")
    print("Google may reject the sign-in or require extra verification.")
    print("\n1. Your NORMAL installed Chrome will open (not an automated browser).")
    print(f"2. Sign in as {selected} at: {GOOGLE_EMBEDDED_SETUP}")
    print("3. Click 'I agree'. A page spinning forever here is normal.")
    print("4. Press F12 > Application > Cookies > https://accounts.google.com")
    print("5. Copy the VALUE of oauth_token (begins oauth2_4/). It expires quickly.")
    launch_normal_chrome(GOOGLE_EMBEDDED_SETUP)
    short_token = getpass.getpass("oauth_token value (hidden, one-time): ").strip()
    if not short_token.startswith("oauth2_4/"):
        raise ValueError("Expected a short-lived cookie beginning oauth2_4/")
    android_id = secrets.token_hex(8)
    print("Exchanging one-time token with Google...")
    try:
        try:
            answer = gpsoauth.exchange_token(selected, short_token, android_id)
        except Exception as exc:
            # Don't allow unknown authentication library errors to leak cookies
            # or master tokens via console output or our activity log.
            raise RuntimeError(f"Google token exchange failed ({type(exc).__name__}). "
                               "Try a fresh browser cookie.") from None
    finally:
        short_token = ""  # don't retain cookie any longer than needed
    # Never show/log Google master token, or dump full exchange response.
    returned_email = (str(answer.get("Email", "")).strip().lower()
                      if isinstance(answer, dict) else "")
    if returned_email and returned_email != selected:
        raise RuntimeError("Google authenticated a different account than requested")
    master_token = answer.get("Token", "") if isinstance(answer, dict) else ""
    if not master_token:
        code = str(answer.get("Error", "unknown"))[:60] if isinstance(answer, dict) else "unknown"
        raise RuntimeError(f"Google did not issue a master token (code={code}). Try a fresh cookie.")
    auth_data = make_mobile_auth(selected, master_token, android_id)
    get_keyring().set_password(KEYRING_SERVICE, selected, auth_data)
    save_account_name(state, selected)
    print(f"Saved {selected} in Windows Credential Manager. No gotohp required.")
    print("Try --limit 3 and verify the Photos account before uploading all images.")


def register_account(state: Path, selected: str) -> None:
    browser_register_account(state, selected)


def import_gotohp(state: Path, custom: str | None = None) -> list[str]:
    """Copy already-saved gotohp auth strings into Windows Credential Manager."""
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("Install PyYAML: python -m pip install pyyaml") from exc
    candidates: list[Path] = []
    if custom:
        candidates.append(Path(custom).expanduser())
    else:
        candidates += [Path.cwd() / "gotohp.config",
                       Path(__file__).resolve().parent / "gotohp.config"]
        for var in ("APPDATA", "LOCALAPPDATA"):
            if os.environ.get(var):
                candidates.append(Path(os.environ[var]) / "gotohp" / "gotohp.config")
    cfg = next((x for x in candidates if x.is_file()), None)
    if cfg is None:
        checked = "\n  ".join(map(str, candidates))
        raise FileNotFoundError(f"No gotohp.config found. Checked:\n  {checked}\n"
                                "Use --import-gotohp C:\\path\\to\\gotohp.config if needed")
    data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("gotohp config is not a mapping")
    account_section = data.get("account") or data
    if not isinstance(account_section, dict):
        raise ValueError("gotohp account section is not a mapping")
    creds = account_section.get("credentials", [])
    if not isinstance(creds, list):
        raise ValueError("gotohp credentials is not a list")
    added = []
    for credential in creds:
        if not isinstance(credential, str):
            continue
        try:
            email = email_from_auth(credential)
        except ValueError:
            continue
        # Don't print or log credentials, and never write to output folder.
        get_keyring().set_password(KEYRING_SERVICE, email, credential)
        save_account_name(state, email)
        added.append(email)
    if not added:
        raise RuntimeError("No usable Google Photos credentials in gotohp config")
    print(f"Imported {len(added)} account(s) from {cfg}:")
    for email in added:
        print(f"  {email}")
    print("Credentials were copied into the OS credential store; gotohp is no longer needed.")
    print("Keep the original gotohp.config private; it still contains credentials.")
    return added

def select_account(state: Path, choice: str | None, switch: bool) -> str:
    available = list_accounts(state)
    if choice and not switch:
        return choice.strip().lower()
    if available:
        print("\nSaved Google Photos accounts:")
        for i, email in enumerate(available, 1):
            print(f"  {i}. {email}")
        print("  N. Register a new account")
        selected = input("Account number [1]: ").strip()
        if not selected:
            return available[0]
        if selected.lower() == "n":
            email = input("New account email: ").strip().lower()
            if "@" not in email:
                raise ValueError("Invalid account email")
            register_account(state, email)
            return email
        if selected.isdecimal() and 1 <= int(selected) <= len(available):
            return available[int(selected) - 1]
        raise ValueError("Invalid account selection")
    print("No saved accounts. First run:")
    print("  py google_photos_upload.py --import-gotohp")
    print("  OR: py google_photos_upload.py --add-account you@gmail.com")
    raise RuntimeError("No saved accounts")


def read_credential(email: str) -> str:
    value = get_keyring().get_password(KEYRING_SERVICE, email)
    if not value:
        raise RuntimeError(f"No authentication data found for {email}. "
                           f"Run --add-account {email} or --import-gotohp first.")
    actual = email_from_auth(value)
    if actual != email:
        raise RuntimeError(f"Stored credentials belong to {actual}, not {email}; refusing upload")
    return value


def sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sanitized_error(error: object) -> str:
    """Avoid persisting any mobile auth string if GPMC exposes an HTTP URL."""
    result = str(error)
    result = re.sub(r"(?i)(?:androidId=)[^\s\"']+", "androidId=[REDACTED]", result)
    result = re.sub(r"(?i)(access_token|auth_data|authorization|oauth_token|token)=([^&\s\"']+)",
                    r"\1=[REDACTED]", result)
    result = re.sub(r"aas_et/[^\s\"']+", "[REDACTED]", result)
    result = re.sub(r"oauth2_4/[^\s\"']+", "[REDACTED]", result)
    return result[:1200]


def classify_upload_error(message: str) -> str:
    """Classify server errors conservatively (uncertain != failed)."""
    if storage_full(message):
        return "storage_full"
    if re.search(r"(?i)(?:invalid[_ -]grant|authentication failed|unauthori[sz]ed|invalid[_ -]token|"
                 r"expired token|re-?authenticate|login required|\b40[13]\b)", message):
        return "account_error"
    if re.search(r"(?i)(?:media key is empty|missing media key|failed to parse accepted|"
                 r"commit failed|error committing|finaliz(?:e|ing))", message):
        return "ambiguous_commit"
    if re.search(r"(?i)(?:unsupported (?:image|file|format|media)|corrupt(?:ed)? (?:image|file)|"
                 r"invalid (?:image|file|media|mime)|cannot identify image|"
                 r"unrecognized image|image decode|malformed image|file too large|"
                 r"payload too large|mime type does not match|no valid media files|"
                 r"\b413\b)", message):
        return "bad_file"
    if re.search(r"(?i)(?:\b429\b|rate.?limit|resource_exhausted|too many requests|"
                 r"timeout|timed out|temporar(?:y|ily)|connection (?:reset|aborted|refused)|"
                 r"remote disconnected|broken pipe|server error|gateway|"
                 r"\b(?:500|502|503|504)\b|service unavailable)", message):
        return "transient"
    return "uncertain"


def lookup_remote_hash(client, path: Path, logger: logging.Logger) -> tuple[str | None, bool]:
    """(media key, lookup succeeded). A negative lookup is not proof of absence."""
    try:
        media_key = client.get_media_key_by_hash(sha1(path))
        if isinstance(media_key, str) and media_key:
            return media_key, True
        return None, True
    except Exception as exc:
        logger.warning("REMOTE_HASH_LOOKUP_FAILED file=%s type=%s", path.name,
                       type(exc).__name__)
        return None, False


def confirmed_archive(journal: Path, logger: logging.Logger, path: Path,
                      signature: dict, account: str, key: str | None,
                      dest_dir: Path) -> Path:
    """Durably journal remote confirmation BEFORE moving the original."""
    if key:
        log_event(journal, logger, "confirmed", signature, account, media_key=key)
    target = move_unique(path, dest_dir)
    log_event(journal, logger, "archived", signature, account,
              media_key=key or "", destination=str(target))
    logger.info("UPLOADED_AND_MOVED file=%s destination=%s", path, target)
    return target


def args_parse() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upload root-only images to Google Photos using GPMC Python library")
    parser.add_argument("--folder", type=Path, default=ROOT, help="Source directory (non-recursive)")
    parser.add_argument("--account", help="Use this previously registered Google account")
    parser.add_argument("--switch-account", action="store_true", help="Choose a saved account")
    parser.add_argument("--add-account", metavar="EMAIL", help="Normal Chrome one-time sign-in and save credentials")
    parser.add_argument("--import-gotohp", nargs="?", const="", metavar="CONFIG_PATH",
                        help="Import gotohp's already-authorized accounts; optional gotohp.config path")
    parser.add_argument("--limit", type=int, default=0, help="Process only N root files (try 3 first)")
    parser.add_argument("--timeout", type=int, default=90, help="Network request timeout in seconds")
    parser.add_argument("--retry-uncertain", action="store_true",
                        help="After manually reviewing a prior uncertain file, retry it (possible duplicates)")
    parser.add_argument("--retry-failed", action="store_true",
                        help="Retry previously rejected per-file uploads")
    parser.add_argument("--max-retries", type=int, default=2,
                        help="Extra retries for identifiable transient errors (default 2)")
    parser.add_argument("--retry-backoff", type=float, default=8.0,
                        help="Seconds before first retry, doubled each time (default 8)")
    parser.add_argument("--dry-run", action="store_true", help="Only count/show file handling, without uploads or moves")
    return parser.parse_args()


class GpmcErrorCollector(logging.Handler):
    """Capture error text GPMC may swallow while returning an empty dict."""
    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage()[-1500:])


def main() -> int:
    args = args_parse()
    root = args.folder.resolve()
    if not root.is_dir():
        print(f"ERROR: Folder doesn't exist: {root}", file=sys.stderr)
        return 2
    if args.limit < 0 or args.timeout <= 0 or args.max_retries < 0 or args.retry_backoff < 0:
        print("ERROR: --limit, --max-retries, --retry-backoff must be >= 0; --timeout > 0", file=sys.stderr)
        return 2

    state = root / "_google_photos_uploader"
    state.mkdir(parents=True, exist_ok=True)
    logger = setup_log(state / "upload_log.txt")
    journal = state / "upload_journal.jsonl"
    legacy = state / "gotohp_upload_journal.jsonl"
    if not journal.exists() and legacy.exists():
        # Continue confirmed/uncertain state from the earlier gotohp wrapper.
        journal = legacy
    logger.info("START root=%s backend=gpmc root_only=TRUE", root)

    if args.add_account or args.import_gotohp is not None:
        try:
            if args.import_gotohp is not None:
                import_gotohp(state, args.import_gotohp)
            else:
                register_account(state, args.add_account.strip().lower())
            return 0
        except Exception as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            # Don't log third-party exception details from token exchange:
            # errors can contain fragments of sensitive authentication material.
            logger.error("ACCOUNT_SETUP_FAILED type=%s", type(exc).__name__)
            return 2

    files = sorted((p for p in root.iterdir() if p.is_file() and not p.is_symlink()),
                   key=lambda p: p.name.casefold())
    if args.limit:
        files = files[:args.limit]
    print(f"Found {len(files)} ROOT files. All subfolders ignored.")
    print(f"Activity log: {state / 'upload_log.txt'}")
    logger.info("SCAN root_files=%d", len(files))
    if not files:
        return 0
    if args.dry_run:
        count_images = sum(p.suffix.lower() in IMAGE_EXTENSIONS for p in files)
        print(f"DRY RUN: {count_images} image candidates; {len(files)-count_images} other files; nothing moved.")
        logger.info("DRY_RUN images=%d other=%d", count_images, len(files)-count_images)
        return 0

    try:
        from gpmc import Client
        from tqdm import tqdm
        account = select_account(state, args.account, args.switch_account)
        auth_data = read_credential(account)
        latest = read_journal(journal)
        # Initialize once per account, not once per file.
        client = Client(auth_data=auth_data, timeout=args.timeout, log_level="WARNING")
        print(f"Using Google Photos account: {account}")
        print("Backend: native Python GPMC (normal storage quota accounting)")
        logger.info("ACCOUNT_SELECTED account=%s journal=%s", account, journal)
    except Exception as exc:
        logger.exception("INITIALIZATION_FAILED: %s", exc)
        print(f"ERROR: {exc}", file=sys.stderr)
        print("Install dependencies: py -m pip install --upgrade gpmc gpsoauth tqdm keyring pyyaml")
        return 2

    uploaded = recovered = not_image = failed = deferred = skipped_failed = retries = 0
    stopped_reason: str | None = None
    uploaded_folder = root / "uploaded"
    not_image_folder = root / "not image"
    collector = GpmcErrorCollector()
    # GPMC catches upload errors internally, so inspect its Rich ERROR records.
    rich_logger = logging.getLogger("rich")
    rich_logger.addHandler(collector)
    original_rich_level = rich_logger.level
    rich_logger.setLevel(min(original_rich_level, logging.ERROR))
    try:
        with tqdm(total=len(files), unit="file", desc="Files", dynamic_ncols=True,
                  position=0) as main_bar:
            for path in files:
                main_bar.set_postfix(uploaded=uploaded, recovered=recovered,
                                     nonimage=not_image, skipped=deferred + skipped_failed,
                                     errors=failed, refresh=False)
                try:
                    if path.suffix.lower() not in IMAGE_EXTENSIONS:
                        target = move_unique(path, not_image_folder)
                        not_image += 1
                        logger.info("NOT_IMAGE moved=%s -> %s", path, target)
                        continue

                    previous = latest.get(path.name)
                    matching_previous = (previous is not None and same_file(previous, path))
                    event = str(previous.get("event", "")) if matching_previous else ""
                    if matching_previous and previous.get("account") != account \
                            and event in ("attempting", "uncertain", "failed_file"):
                        # Unconfirmed result for an entirely different Google
                        # account does not block this selected account.
                        logger.info("OTHER_ACCOUNT_PREVIOUS_STATUS file=%s previous_account=%s event=%s",
                                    path.name, previous.get("account"), event)
                        event = ""
                    sig = file_signature(path)

                    if event == "confirmed":
                        # A past run received a media key but crashed before moving.
                        target = move_unique(path, uploaded_folder)
                        log_event(journal, logger, "archived", sig,
                                  previous.get("account", account),
                                  media_key=previous.get("media_key", ""),
                                  destination=str(target))
                        recovered += 1
                        uploaded += 1
                        logger.info("RECOVERED_CONFIRMED file=%s", path)
                        continue
                    if event in ("attempting", "uncertain") and not args.retry_uncertain:
                        # Do not block 30,000 other uploads because one was
                        # interrupted / had an ambiguous server commit.
                        deferred += 1
                        logger.warning("DEFERRED_UNCERTAIN file=%s prior_event=%s", path, event)
                        continue
                    if event == "failed_file" and not args.retry_failed:
                        skipped_failed += 1
                        logger.warning("DEFERRED_REJECTED file=%s", path)
                        continue
                    if event == "archived" and matching_previous:
                        # Same-name file has returned after a previous archive.
                        # Check the remote hash; don't assume the new file has
                        # been copied from the archive successfully.
                        key, checked = lookup_remote_hash(client, path, logger)
                        if key:
                            confirmed_archive(journal, logger, path, sig,
                                              account, key, uploaded_folder)
                            uploaded += 1
                            recovered += 1
                            continue
                        if not checked:
                            log_event(journal, logger, "uncertain", sig, account,
                                      error="Remote check failed for reappearing archived file")
                            deferred += 1
                            continue

                    if sig["size"] == 0:
                        log_event(journal, logger, "failed_file", sig, account,
                                  error="Empty image file")
                        failed += 1
                        logger.warning("REJECTED_EMPTY file=%s", path)
                        continue

                    # If the prior attempt might already exist, first attempt
                    # positive confirmation via SHA-1. Absence is not definitive;
                    # --retry-uncertain means the user accepts retry risk.
                    if event in ("attempting", "uncertain"):
                        key, checked = lookup_remote_hash(client, path, logger)
                        if key:
                            confirmed_archive(journal, logger, path, sig,
                                              account, key, uploaded_folder)
                            recovered += 1
                            uploaded += 1
                            continue
                        if not checked:
                            log_event(journal, logger, "uncertain", sig, account,
                                      error="Could not verify remote status before manual retry")
                            deferred += 1
                            continue

                    for attempt in range(1, args.max_retries + 2):
                        collector.messages.clear()
                        log_event(journal, logger, "attempting", sig, account,
                                  attempt=attempt)
                        logger.info("UPLOAD_ATTEMPT file=%s attempt=%d/%d account=%s",
                                    path, attempt, args.max_retries + 1, account)
                        returned_key = None
                        upload_error = ""
                        try:
                            with tqdm(total=sig["size"], unit="B", unit_scale=True,
                                      unit_divisor=1024, desc=f"Uploading {path.name[:25]}",
                                      dynamic_ncols=True, leave=False, position=1) as bytes_bar:
                                last_phase = ""

                                def progress(update: dict) -> None:
                                    nonlocal last_phase
                                    phase = str(update.get("phase", ""))
                                    if phase != last_phase:
                                        bytes_bar.set_description(
                                            f"{phase.title()} {path.name[:22]}")
                                        if phase == "uploading":
                                            bytes_bar.n = 0
                                        last_phase = phase
                                    if phase == "uploading":
                                        completed = int(update.get("bytes_completed", 0))
                                        if completed >= bytes_bar.n:
                                            bytes_bar.n = min(completed, bytes_bar.total)
                                            bytes_bar.refresh()
                                    if phase == "complete":
                                        bytes_bar.n = bytes_bar.total
                                        bytes_bar.refresh()
                                    if phase == "error":
                                        logger.warning("GPMC_PROGRESS_ERROR file=%s", path.name)

                                result = client.upload(
                                    target=path, use_quota=True, saver=False,
                                    show_progress=False, recursive=False,
                                    threads=1, force_upload=False,
                                    delete_from_host=False,
                                    progress_callback=progress)
                                if isinstance(result, dict):
                                    returned_key = (result.get(path.absolute().as_posix())
                                                    or result.get(str(path.absolute())))
                            if not isinstance(returned_key, str) or not returned_key:
                                upload_error = (collector.messages[-1] if collector.messages
                                                else "No media key returned by GPMC")
                        except KeyboardInterrupt:
                            raise
                        except Exception as exc:
                            upload_error = f"{type(exc).__name__}: {sanitized_error(exc)}"

                        if returned_key and isinstance(returned_key, str):
                            if file_signature(path) != sig:
                                log_event(journal, logger, "uncertain", sig, account,
                                          error="Local image changed while uploading")
                                deferred += 1
                                logger.error("SOURCE_CHANGED file=%s", path)
                                break
                            confirmed_archive(journal, logger, path, sig,
                                              account, returned_key, uploaded_folder)
                            uploaded += 1
                            break

                        upload_error = sanitized_error(upload_error)
                        category = classify_upload_error(upload_error)
                        logger.warning("UPLOAD_NOT_CONFIRMED file=%s attempt=%d kind=%s reason=%s",
                                       path.name, attempt, category, upload_error)
                        # Resolve potentially successful server-side upload
                        # by checking the remote SHA-1 rather than guessing.
                        key, checked = lookup_remote_hash(client, path, logger)
                        if key:
                            if file_signature(path) != sig:
                                log_event(journal, logger, "uncertain", sig, account,
                                          error="Source changed during remote recovery")
                                deferred += 1
                                break
                            confirmed_archive(journal, logger, path, sig,
                                              account, key, uploaded_folder)
                            recovered += 1
                            uploaded += 1
                            logger.info("RECOVERED_BY_HASH file=%s", path.name)
                            break

                        if category in ("storage_full", "account_error"):
                            log_event(journal, logger, "uncertain", sig, account,
                                      error=upload_error, category=category)
                            failed += 1
                            stopped_reason = (f"Account-wide {category} error on {path.name}: "
                                              f"{upload_error}")
                            break

                        if category == "bad_file":
                            log_event(journal, logger, "failed_file", sig, account,
                                      error=upload_error, category=category)
                            failed += 1
                            break

                        # ONLY retry recognizable transient errors, and only
                        # when a remote hash lookup completed. Missing keys at
                        # commit time remain uncertain: repeat upload might
                        # create a duplicate if consistency is delayed.
                        if category == "transient" and checked and attempt <= args.max_retries:
                            delay = min(args.retry_backoff * (2 ** (attempt - 1)), 120.0)
                            retries += 1
                            logger.info("RETRY_WAIT file=%s attempt=%d seconds=%.2f",
                                        path.name, attempt, delay)
                            if delay:
                                tqdm.write(f"Temporary failure: {path.name}; "
                                           f"retrying in {delay:g}s ({attempt}/{args.max_retries})")
                                time.sleep(delay)
                            continue

                        log_event(journal, logger, "uncertain", sig, account,
                                  error=upload_error, category=category,
                                  attempts=attempt)
                        deferred += 1
                        logger.warning("DEFERRED_UNCONFIRMED file=%s category=%s",
                                       path.name, category)
                        if category == "transient" and re.search(
                                r"(?i)(?:\b429\b|rate.?limit|too many requests|resource_exhausted)",
                                upload_error):
                            # After bounded backoff, continuing with 30,000 more
                            # files would hammer an account-wide throttle.
                            stopped_reason = ("Rate limiting persisted after retries. "
                                              f"Wait before resuming. Last file: {path.name}")
                        break

                    if stopped_reason:
                        break
                except KeyboardInterrupt:
                    stopped_reason = (f"Interrupted while handling {path.name}. "
                                      "Check the remote account before retrying.")
                    logger.warning("INTERRUPTED file=%s", path.name)
                    break
                except OSError as exc:
                    # Local disk move/journal/write errors require stopping:
                    # further uploads could invalidate recovery state.
                    stopped_reason = f"Local filesystem failure on {path.name}: {exc}"
                    logger.error("LOCAL_FS_ERROR file=%s type=%s", path.name,
                                 type(exc).__name__)
                    break
                except Exception as exc:
                    # Unexpected bugs should not be silently classed as
                    # per-file upload rejections.
                    stopped_reason = (f"Unexpected program error on {path.name}: "
                                      f"{type(exc).__name__}: {sanitized_error(exc)}")
                    logger.exception("PROGRAM_ERROR file=%s type=%s",
                                     path.name, type(exc).__name__)
                    break
                finally:
                    main_bar.update(1)
    except KeyboardInterrupt:
        stopped_reason = "Stopped by user. Last upload may be uncertain."
    finally:
        rich_logger.removeHandler(collector)
        rich_logger.setLevel(original_rich_level)

    outstanding = failed + deferred + skipped_failed
    print(f"Uploaded/archived: {uploaded} | Recovered: {recovered} | "
          f"Not-image moved: {not_image} | Failed: {failed} | "
          f"Deferred/uncertain: {deferred} | Previous failures skipped: {skipped_failed} | "
          f"Retries: {retries}")
    if stopped_reason:
        print("STOP:", stopped_reason)
        logger.warning("STOP reason=%s", stopped_reason)
    elif outstanding:
        print("Finished scanning, but some files remain in the ROOT folder.")
        print("Review the activity log and journal. Reruns skip uncertain/failed files "
              "unless you explicitly use --retry-uncertain or --retry-failed.")
    else:
        print("Completed successfully.")
        logger.info("COMPLETE uploaded=%s recovered=%s not_image=%s",
                    uploaded, recovered, not_image)
    print("Activity log:", state / "upload_log.txt")
    print("Recovery journal:", journal)
    logger.info("END uploaded=%s recovered=%s nonimage=%s failed=%s deferred=%s "
                "skipped_failed=%s retries=%s stop=%s", uploaded, recovered,
                not_image, failed, deferred, skipped_failed, retries,
                bool(stopped_reason))
    return 1 if stopped_reason or outstanding else 0


if __name__ == "__main__":
    sys.exit(main())
