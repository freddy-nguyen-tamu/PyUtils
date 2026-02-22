#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import logging
import subprocess
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "nimo_diary.log"

PYTHON_EXE = "py -3.11"
MAIN_SCRIPT = BASE_DIR / "nimo_ytb.py"

SLEEP_AFTER_RUN = True
SLEEP_ONLY_IF_SUCCESS = True
POST_RUN_WAIT_SECONDS = 10


def setup_logging() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("nimo_auto")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=2 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger


logger = setup_logging()


def log(msg: str) -> None:
    logger.info(msg)


def err(msg: str) -> None:
    logger.error(msg)


def sleep_windows() -> None:
    if not SLEEP_AFTER_RUN:
        log("Sleep skipped because SLEEP_AFTER_RUN is False")
        return

    log("Putting computer to sleep")
    subprocess.run(
        'rundll32.exe powrprof.dll,SetSuspendState 0,1,0',
        shell=True,
        check=False,
    )


def main() -> int:
    log("========== RUN START ==========")

    if not MAIN_SCRIPT.exists():
        err(f"Missing script: {MAIN_SCRIPT}")
        log("========== RUN END ==========")
        return 1

    cmd = f'{PYTHON_EXE} "{MAIN_SCRIPT}"'
    log(f"Running: {cmd}")

    try:
        result = subprocess.run(
            cmd,
            cwd=str(BASE_DIR),
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        if result.stdout:
            for line in result.stdout.splitlines():
                log(f"[nimo_ytb] {line}")

        log(f"nimo_ytb.py exit code: {result.returncode}")

        if result.returncode == 0:
            log("Run completed successfully")
            time.sleep(POST_RUN_WAIT_SECONDS)
            sleep_windows()
            log("========== RUN END ==========")
            return 0

        err("Run finished with errors")
        if not SLEEP_ONLY_IF_SUCCESS:
            time.sleep(POST_RUN_WAIT_SECONDS)
            sleep_windows()

        log("========== RUN END ==========")
        return result.returncode or 1

    except Exception as e:
        err(f"Fatal launcher error: {e}")
        log("========== RUN END ==========")
        return 1


if __name__ == "__main__":
    sys.exit(main())