#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
RUNS_DIR = LOG_DIR / "runs"
MASTER_LOG = LOG_DIR / "nimo_master.log"
DOWNLOAD_DIR = BASE_DIR / "downloads"
AUTO_STATE_FILE = BASE_DIR / "auto_state.json"

MAIN_SCRIPT = BASE_DIR / "nimo_ytb.py"

SLEEP_AFTER_RUN = True
SLEEP_ONLY_IF_SUCCESS = True
POST_RUN_WAIT_SECONDS = 10


def ensure_dirs() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)


def timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def run_log_path() -> Path:
    return RUNS_DIR / f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"


def safe_write_line(path: Path, line: str) -> None:
    last_error = None

    for _ in range(5):
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            return
        except Exception as e:
            last_error = e
            time.sleep(0.2)

    try:
        fallback = LOG_DIR / "log_write_errors.log"
        with open(fallback, "a", encoding="utf-8") as f:
            f.write(f"{timestamp()} | WARN | Failed writing {path}: {last_error}\n")
            f.write(f"{timestamp()} | WARN | Lost log line: {line}\n")
    except Exception:
        pass


def log(run_log: Path, level: str, msg: str) -> None:
    line = f"{timestamp()} | {level} | {msg}"

    try:
        print(line, flush=True)
    except Exception:
        pass

    safe_write_line(run_log, line)
    safe_write_line(MASTER_LOG, line)


def list_download_items() -> list[Path]:
    if not DOWNLOAD_DIR.exists():
        return []
    return sorted([p for p in DOWNLOAD_DIR.iterdir()], key=lambda p: p.name.lower())


def log_download_folder(run_log: Path, title: str) -> None:
    items = list_download_items()
    log(run_log, "INFO", f"{title}: {len(items)} item(s) in downloads")
    for p in items:
        log(run_log, "INFO", f"downloads item: {p.name}")


def clear_downloads_folder(run_log: Path) -> None:
    log(run_log, "INFO", f"Clearing downloads folder: {DOWNLOAD_DIR}")

    if not DOWNLOAD_DIR.exists():
        DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        log(run_log, "INFO", "Downloads folder did not exist; created it")
        return

    for item in DOWNLOAD_DIR.iterdir():
        try:
            if item.is_dir():
                shutil.rmtree(item)
                log(run_log, "INFO", f"Deleted folder: {item}")
            else:
                item.unlink()
                log(run_log, "INFO", f"Deleted file: {item}")
        except Exception as e:
            log(run_log, "ERROR", f"Failed to delete {item}: {e}")


def read_auto_state() -> dict:
    if not AUTO_STATE_FILE.exists():
        return {
            "can_clear_downloads": False,
            "reason": "missing_auto_state_file",
            "details": {},
        }
    try:
        with open(AUTO_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "can_clear_downloads" not in data:
            data["can_clear_downloads"] = False
        if "reason" not in data:
            data["reason"] = "missing_reason"
        if "details" not in data:
            data["details"] = {}
        return data
    except Exception as e:
        return {
            "can_clear_downloads": False,
            "reason": f"invalid_auto_state_file: {e}",
            "details": {},
        }


def sleep_windows(run_log: Path) -> None:
    if not SLEEP_AFTER_RUN:
        log(run_log, "INFO", "Sleep skipped because SLEEP_AFTER_RUN is False")
        return

    log(run_log, "INFO", "Putting computer to sleep")
    try:
        subprocess.run(
            'rundll32.exe powrprof.dll,SetSuspendState 0,1,0',
            shell=True,
            check=False,
        )
    except Exception as e:
        log(run_log, "ERROR", f"Sleep command failed: {e}")


def main() -> int:
    ensure_dirs()
    run_log = run_log_path()
    raw_log = RUNS_DIR / f"{run_log.stem}_raw.log"

    log(run_log, "INFO", "========== RUN START ==========")
    log(run_log, "INFO", f"BASE_DIR={BASE_DIR}")
    log(run_log, "INFO", f"RUN_LOG={run_log}")
    log(run_log, "INFO", f"RAW_LOG={raw_log}")
    log(run_log, "INFO", f"MASTER_LOG={MASTER_LOG}")

    # IMPORTANT:
    # Do NOT clear downloads at startup anymore.
    # Let nimo_ytb.py inspect leftovers first.
    log_download_folder(run_log, "Startup snapshot")

    if not MAIN_SCRIPT.exists():
        log(run_log, "ERROR", f"Missing script: {MAIN_SCRIPT}")
        log(run_log, "INFO", "========== RUN END ==========")
        return 1

    cmd = [sys.executable, "-u", str(MAIN_SCRIPT)]
    log(run_log, "INFO", f"Running: {' '.join(cmd)}")

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    try:
        process = subprocess.Popen(
            cmd,
            cwd=str(BASE_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
        )
    except Exception as e:
        log(run_log, "ERROR", f"Fatal launcher error starting child: {e}")
        log(run_log, "INFO", "========== RUN END ==========")
        return 1

    try:
        assert process.stdout is not None

        for raw_line in process.stdout:
            line = raw_line.rstrip("\r\n")

            try:
                print(line, flush=True)
            except Exception:
                pass

            safe_write_line(raw_log, line)
            safe_write_line(run_log, f"{timestamp()} | CHILD | {line}")
            safe_write_line(MASTER_LOG, f"{timestamp()} | CHILD | {line}")

        returncode = process.wait()
        log(run_log, "INFO", f"nimo_ytb.py exit code: {returncode}")

        auto_state = read_auto_state()
        log(
            run_log,
            "INFO",
            f"auto_state: can_clear_downloads={auto_state['can_clear_downloads']} | reason={auto_state['reason']}",
        )

        # Clear only AFTER nimo_ytb.py has checked and explicitly allowed it.
        if auto_state["can_clear_downloads"]:
            clear_downloads_folder(run_log)
        else:
            log(run_log, "INFO", "Preserving downloads folder because recoverable work may still exist")

        log_download_folder(run_log, "Post-run snapshot")

        if returncode == 0:
            log(run_log, "INFO", "Run completed successfully")
            time.sleep(POST_RUN_WAIT_SECONDS)
            if SLEEP_AFTER_RUN:
                sleep_windows(run_log)
            log(run_log, "INFO", "========== RUN END ==========")
            return 0

        log(run_log, "ERROR", "Run finished with errors")
        if not SLEEP_ONLY_IF_SUCCESS:
            time.sleep(POST_RUN_WAIT_SECONDS)
            if SLEEP_AFTER_RUN:
                sleep_windows(run_log)

        log(run_log, "INFO", "========== RUN END ==========")
        return returncode or 1

    except Exception as e:
        try:
            process.terminate()
        except Exception:
            pass

        log(run_log, "ERROR", f"Fatal launcher error while streaming child output: {e}")
        log(run_log, "INFO", "========== RUN END ==========")
        return 1


if __name__ == "__main__":
    sys.exit(main())