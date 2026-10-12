#!/usr/bin/env python3
"""Root-only Google Photos uploader using gotohp-cli (unofficial mobile backend).

Requires gotohp-cli v0.11.0 or newer and its independently configured account.
See https://github.com/xob0t/gotohp for Windows binaries and sign-in instructions.

No OAuth client, Google Photos Library API, Playwright, or Chrome automation is used.
Google account storage limits and the provider's service rules still apply.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

ROOT = Path(r"C:\Users\qacer\Downloads\new\byoru")
IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".jpe", ".png", ".gif", ".webp", ".bmp",
    ".heic", ".heif", ".avif", ".tif", ".tiff", ".ico",
    ".dng", ".cr2", ".cr3", ".nef", ".arw", ".orf", ".rw2",
}
BACKEND = "gotohp-cli v0.11+"


def timestamp() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def setup_logger(path: Path) -> logging.Logger:
    logger = logging.getLogger("google_photos_upload")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def signature(path: Path) -> dict:
    stat = path.stat()
    return {"name": path.name, "size": stat.st_size, "sha256": sha256(path)}


def append_event(path: Path, logger: logging.Logger, event: str, sig: dict,
                 account: str, **extra: object) -> None:
    record = {"when": timestamp(), "event": event, "account": account,
              "name": sig["name"], "size": sig["size"], "sha256": sig["sha256"], **extra}
    with path.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(record, ensure_ascii=False) + "\n")
        fp.flush()
        os.fsync(fp.fileno())
    logger.info("JOURNAL %-18s %-50s account=%s", event, sig["name"], account)


def load_journal(path: Path, logger: logging.Logger) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    if not path.exists():
        return latest
    with path.open("r", encoding="utf-8") as fp:
        for line_number, line in enumerate(fp, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                if record.get("name") and record.get("event"):
                    latest[record["name"]] = record
            except (ValueError, TypeError) as exc:
                # A crash could interrupt writing a final JSONL line; fail closed.
                raise RuntimeError(
                    f"Upload journal has an unreadable line {line_number}: {exc}. "
                    "Repair it before proceeding."
                ) from exc
    logger.info("Loaded %d file records from recovery journal", len(latest))
    return latest


def matching_record(record: dict | None, file: Path) -> bool:
    if not record or record.get("event") not in {"confirmed", "uncertain", "attempting", "archived"}:
        return False
    stat = file.stat()
    if stat.st_size != record.get("size"):
        return False
    return sha256(file) == record.get("sha256")


def unique_destination(folder: Path, name: str) -> Path:
    original = Path(name)
    candidate = folder / name
    count = 1
    while candidate.exists():
        candidate = folder / f"{original.stem} ({count}){original.suffix}"
        count += 1
    return candidate


def move_safely(source: Path, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    dest = unique_destination(directory, source.name)
    # Both folders normally reside on the same filesystem. Never overwrite.
    source.rename(dest)
    return dest


def cli_executable(specified: str | None) -> str:
    if specified:
        candidates = [specified]
    else:
        base = Path(__file__).resolve().parent
        names = (
            "gotohp-cli-windows-amd64.exe", "gotohp-cli.exe",
            "gotohp-windows-amd64.exe", "gotohp.exe", "gotohp-cli", "gotohp",
        )
        candidates = [str(base / name) for name in names] + list(names)
    for candidate in candidates:
        if not candidate:
            continue
        found = shutil.which(candidate)
        if found:
            return found
        p = Path(candidate)
        if p.is_file():
            return str(p.resolve())
    raise RuntimeError(
        "gotohp-cli not found. Download the Windows gotohp-cli binary (v0.11.0+):\n"
        "https://github.com/xob0t/gotohp/releases/latest\n"
        "Put gotohp-cli-windows-amd64.exe beside this script, or use --cli FULL_PATH.\n"
        "First add your Google account in the separate gotohp GUI application."
    )


def is_storage_full(text: str) -> bool:
    text = text.lower()
    patterns = [
        r"storage\s+(?:quota\s+)?(?:is\s+)?(?:full|exceeded|limit reached)",
        r"quota\s+(?:has\s+been\s+)?exceeded",
        r"out of (?:google\s+)?storage",
        r"insufficient[_ -]storage", r"not enough storage",
        r"storagequotaexceeded", r"storage_quota_exceeded",
        r"storage limit", r"account storage is full",
    ]
    return any(re.search(pattern, text) for pattern in patterns)


def upload_one(cli: str, path: Path, account: str, timeout: int,
               logger: logging.Logger) -> tuple[bool, str]:
    # Always account for the user's normal Google storage. --use-quota is
    # unrelated to the old Google Cloud PROJECT write-requests/minute limit.
    command = [cli, "upload", str(path), "--account", account,
               "--use-quota", "--threads", "1"]
    logger.info("UPLOAD_ATTEMPT file=%s account=%s size=%s backend=%s",
                path, account, path.stat().st_size, BACKEND)
    started = time.monotonic()
    try:
        result = subprocess.run(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        logger.error("UPLOAD_TIMED_OUT file=%s timeout=%ds details=%s", path, timeout, exc)
        return False, f"Uploader timed out after {timeout}s; remote result is uncertain"
    except OSError as exc:
        logger.error("UPLOAD_START_FAILED file=%s error=%s", path, exc)
        return False, f"Could not start uploader: {exc}"
    elapsed = time.monotonic() - started
    output = result.stdout or ""
    logger.info("BACKEND_RESULT file=%s exit=%d duration=%.1fs\n%s",
                path, result.returncode, elapsed, output.strip()[:16000])
    if result.returncode != 0:
        reason = output.strip()[-2000:] or "no details provided"
        return False, f"Uploader exited {result.returncode}: {reason}"
    if is_storage_full(output):
        return False, "Uploader output mentions full storage, even though it exited 0"
    return True, output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Upload only ROOT images via gotohp-cli, archiving only confirmed uploads."
    )
    parser.add_argument("--folder", type=Path, default=ROOT,
                        help="Source folder; only its direct files are processed")
    parser.add_argument("--account", help="Google email already added to gotohp")
    parser.add_argument("--switch-account", action="store_true",
                        help="Prompt for which stored gotohp account to use")
    parser.add_argument("--cli", help="Path to gotohp-cli.exe")
    parser.add_argument("--limit", type=int, default=0,
                        help="Only process the first N root files, useful for testing")
    parser.add_argument("--timeout", type=int, default=1800,
                        help="Max seconds per file; uncertain uploads stop the run")
    parser.add_argument("--retry-uncertain", action="store_true",
                        help="Allow retry of a file with an ambiguous previous result (may duplicate)")
    parser.add_argument("--dry-run", action="store_true",
                        help="List work without uploading or moving files")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.folder.resolve()
    if not root.is_dir():
        print(f"ERROR: Folder does not exist: {root}", file=sys.stderr)
        return 2
    if args.limit < 0 or args.timeout <= 0:
        print("ERROR: --limit must be >= 0 and --timeout must be > 0", file=sys.stderr)
        return 2

    state_dir = root / "_google_photos_uploader"
    state_dir.mkdir(parents=True, exist_ok=True)
    log_path = state_dir / "upload_log.txt"
    journal_path = state_dir / "gotohp_upload_journal.jsonl"
    logger = setup_logger(log_path)
    logger.info("START root=%s root-only=True dry-run=%s", root, args.dry_run)

    # No rglob, walk or recursive enumeration: root files ONLY.
    files = sorted((entry for entry in root.iterdir() if entry.is_file() and not entry.is_symlink()),
                   key=lambda p: p.name.casefold())
    if args.limit:
        files = files[:args.limit]
    print(f"Found {len(files)} ROOT files (nested folders ignored).")
    print(f"Activity log: {log_path}")
    logger.info("SCAN root_files=%d nested_dirs_ignored=True", len(files))
    if not files:
        print("Nothing to upload.")
        return 0

    account = (args.account or "").strip()
    if args.switch_account or not account:
        print("\nEnter an email that you have already added in gotohp.")
        print("To add/switch accounts, use the gotohp GUI first (no automated browser sign-in).")
        account = input("Google Photos account email: ").strip()
    if "@" not in account or any(c.isspace() for c in account):
        print("ERROR: Please enter a valid account email.", file=sys.stderr)
        return 2

    if args.dry_run:
        images = sum(p.suffix.lower() in IMAGE_EXTENSIONS for p in files)
        print(f"DRY RUN account={account}: {images} image candidates, "
              f"{len(files) - images} not-image candidates. No files moved.")
        for p in files[:30]:
            logger.info("DRY_RUN %s action=%s", p,
                        "upload" if p.suffix.lower() in IMAGE_EXTENSIONS else "not image")
        return 0

    try:
        backend = cli_executable(args.cli)
        latest = load_journal(journal_path, logger)
    except Exception as exc:
        logger.error("SETUP_ERROR %s", exc)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    uploaded_dir, not_image_dir = root / "uploaded", root / "not image"
    uploaded = not_image = recovered = 0
    failures = 0
    stopped_reason = None
    print(f"Account: {account} | Backend: {backend}")
    print("Uploader requires gotohp v0.11.0+; its stored credentials are used automatically.")
    logger.info("ACCOUNT_SELECTED account=%s binary=%s", account, backend)
    try:
        from tqdm import tqdm
    except ImportError:
        print("Missing tqdm; run: python -m pip install tqdm", file=sys.stderr)
        return 2

    try:
        with tqdm(total=len(files), unit="file", dynamic_ncols=True, desc="Files") as bar:
            for path in files:
                bar.set_postfix(uploaded=uploaded, not_image=not_image, recovered=recovered,
                                failed=failures, refresh=False)
                if path.suffix.lower() not in IMAGE_EXTENSIONS:
                    try:
                        destination = move_safely(path, not_image_dir)
                        not_image += 1
                        logger.info("NOT_IMAGE_MOVED from=%s to=%s", path, destination)
                    except OSError as exc:
                        failures += 1
                        stopped_reason = f"Could not move non-image file {path}: {exc}"
                        logger.error("MOVE_FAILED %s", stopped_reason)
                        break
                    bar.update(1)
                    continue

                previous = latest.get(path.name)
                if previous and previous.get("event") == "confirmed" and matching_record(previous, path):
                    try:
                        dest = move_safely(path, uploaded_dir)
                        append_event(journal_path, logger, "archived", previous,
                                     previous.get("account", account), destination=str(dest))
                        recovered += 1
                        uploaded += 1
                        logger.info("RECOVERED_CONFIRMED from=%s to=%s", path, dest)
                        bar.update(1)
                        continue
                    except Exception as exc:
                        failures += 1
                        stopped_reason = f"Could not archive previously confirmed upload {path}: {exc}"
                        logger.error("RECOVERY_FAILED %s", stopped_reason)
                        break
                if (previous and previous.get("event") in {"uncertain", "attempting"} and
                        matching_record(previous, path) and not args.retry_uncertain):
                    failures += 1
                    stopped_reason = (f"Previous result is uncertain for {path.name}. Verify in Google Photos; "
                                      "if you accept possible duplicates, use --retry-uncertain.")
                    logger.error("UNCERTAIN_BLOCKED %s", stopped_reason)
                    break

                try:
                    sig = signature(path)
                    # Record intent before any network call. After a power outage,
                    # an incomplete attempt is treated as uncertain, not retried blind.
                    append_event(journal_path, logger, "attempting", sig, account)
                    ok, response = upload_one(backend, path, account, args.timeout, logger)
                    if not ok:
                        append_event(journal_path, logger, "uncertain", sig, account,
                                     error=response[-2000:])
                        failures += 1
                        if is_storage_full(response):
                            stopped_reason = ("Account storage appears full; stopped safely. "
                                              f"File remains in root: {path.name}. {response[-600:]}")
                        else:
                            stopped_reason = ("Upload could not be confirmed; stopped safely. "
                                              f"File remains in root: {path.name}. {response[-600:]}")
                        logger.error("UPLOAD_STOP %s", stopped_reason)
                        break
                    # If a file was modified while being uploaded, don't archive the new bytes.
                    if signature(path) != sig:
                        append_event(journal_path, logger, "uncertain", sig, account,
                                     error="Source changed during upload")
                        failures += 1
                        stopped_reason = f"Source changed during upload; leaving in root: {path.name}"
                        logger.error("SOURCE_CHANGED %s", stopped_reason)
                        break
                    # Durable success record BEFORE moving the local source.
                    append_event(journal_path, logger, "confirmed", sig, account)
                    latest[path.name] = {"event": "confirmed", "account": account, **sig}
                    dest = move_safely(path, uploaded_dir)
                    append_event(journal_path, logger, "archived", sig, account,
                                 destination=str(dest))
                    latest[path.name] = {"event": "archived", "account": account, **sig}
                    uploaded += 1
                    logger.info("UPLOADED_AND_MOVED from=%s to=%s", path, dest)
                    bar.update(1)
                except KeyboardInterrupt:
                    # The attempt record remains if writing a new record fails.
                    stopped_reason = "Interrupted by user; current file outcome may need verification"
                    logger.warning("USER_INTERRUPTED file=%s", path)
                    break
                except Exception as exc:
                    failures += 1
                    stopped_reason = f"Stopped on unexpected error for {path}: {exc}"
                    logger.exception("UNEXPECTED_ERROR file=%s", path)
                    break
    except KeyboardInterrupt:
        stopped_reason = "Interrupted by user"
        logger.warning("USER_INTERRUPTED")

    print(f"Uploaded/archived: {uploaded} | Recovered confirmed: {recovered} | "
          f"Not-image moved: {not_image} | Failed/uncertain: {failures}")
    if stopped_reason:
        print("STOP:", stopped_reason)
        logger.warning("STOP reason=%s", stopped_reason)
    else:
        print("Completed this run.")
        logger.info("COMPLETE uploaded=%s recovered=%s not_image=%s failed=%s",
                    uploaded, recovered, not_image, failures)
    print("Activity log:", log_path)
    print("Recovery journal:", journal_path)
    return 1 if stopped_reason else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nStopped by user; original files remain in place unless confirmed and archived.")
        sys.exit(130)
