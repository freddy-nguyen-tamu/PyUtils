#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
RUNS_DIR = LOG_DIR / "runs"
MASTER_LOG = LOG_DIR / "nimo_master.log"

PYTHON_EXE = sys.executable
MAIN_SCRIPT = BASE_DIR / "nimo_ytb.py"

SLEEP_AFTER_RUN = True
SLEEP_ONLY_IF_SUCCESS = True
POST_RUN_WAIT_SECONDS = 10


def ensure_dirs() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)


def timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def run_log_path() -> Path:
    return RUNS_DIR / f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"


def write_line(path: Path, line: str) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def log(run_log: Path, level: str, msg: str) -> None:
    line = f"{timestamp()} | {level} | {msg}"
    print(line, flush=True)
    write_line(run_log, line)
    write_line(MASTER_LOG, line)


def sleep_windows(run_log: Path) -> None:
    if not SLEEP_AFTER_RUN:
        log(run_log, "INFO", "Sleep skipped because SLEEP_AFTER_RUN is False")
        return

    log(run_log, "INFO", "Putting computer to sleep")
    subprocess.run(
        'rundll32.exe powrprof.dll,SetSuspendState 0,1,0',
        shell=True,
        check=False,
    )


def main() -> int:
    ensure_dirs()
    run_log = run_log_path()

    log(run_log, "INFO", "========== RUN START ==========")
    log(run_log, "INFO", f"BASE_DIR={BASE_DIR}")
    log(run_log, "INFO", f"RUN_LOG={run_log}")
    log(run_log, "INFO", f"MASTER_LOG={MASTER_LOG}")

    if not MAIN_SCRIPT.exists():
        log(run_log, "ERROR", f"Missing script: {MAIN_SCRIPT}")
        log(run_log, "INFO", "========== RUN END ==========")
        return 1

    cmd = [PYTHON_EXE, "-u", str(MAIN_SCRIPT)]
    log(run_log, "INFO", f"Running: {' '.join(cmd)}")

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

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

        assert process.stdout is not None

        # Raw child transcript file: exactly what nimo_ytb.py / nimo.py print
        raw_log = RUNS_DIR / f"{run_log.stem}_raw.log"

        for raw_line in process.stdout:
            line = raw_line.rstrip("\r\n")

            # Show exactly what child printed
            print(line, flush=True)

            # Save raw transcript exactly
            write_line(raw_log, line)

            # Save timestamped copies too
            write_line(run_log, f"{timestamp()} | CHILD | {line}")
            write_line(MASTER_LOG, f"{timestamp()} | CHILD | {line}")

        returncode = process.wait()
        log(run_log, "INFO", f"nimo_ytb.py exit code: {returncode}")

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
        log(run_log, "ERROR", f"Fatal launcher error: {e}")
        log(run_log, "INFO", "========== RUN END ==========")
        return 1


if __name__ == "__main__":
    sys.exit(main())