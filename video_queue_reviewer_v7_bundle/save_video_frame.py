from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path


# The helper communicates with the UI through stdout/stderr pipes. On some
# Windows installations Python inherits a legacy code page such as cp1252,
# which cannot print many Vietnamese or other Unicode filename characters.
# Force UTF-8, while keeping ASCII-safe JSON as an additional safeguard.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(
        encoding="utf-8",
        errors="backslashreplace",
    )
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(
        encoding="utf-8",
        errors="backslashreplace",
    )


def find_program(name: str, override: str | None = None) -> Path:
    """Find ffmpeg/ffprobe from an override, environment, PATH, or common folders."""
    executable_name = name + (".exe" if os.name == "nt" else "")
    candidates: list[Path] = []

    if override:
        candidate = Path(override).expanduser()
        if candidate.is_dir():
            candidate = candidate / executable_name
        candidates.append(candidate)

    for variable_name in ("FFMPEG_DIR", "FFMPEG_HOME"):
        value = os.environ.get(variable_name)
        if value:
            base = Path(value)
            candidates.extend(
                (
                    base / executable_name,
                    base / "bin" / executable_name,
                )
            )

    discovered = shutil.which(executable_name) or shutil.which(name)
    if discovered:
        candidates.append(Path(discovered))

    if os.name == "nt":
        user_profile = Path(os.environ.get("USERPROFILE", r"C:\Users\Default"))
        local_app_data = Path(
            os.environ.get("LOCALAPPDATA", user_profile / "AppData" / "Local")
        )
        program_files = Path(
            os.environ.get("ProgramFiles", r"C:\Program Files")
        )
        program_data = Path(
            os.environ.get("ProgramData", r"C:\ProgramData")
        )

        candidates.extend(
            (
                Path(r"C:\ffmpeg\bin") / executable_name,
                program_files / "ffmpeg" / "bin" / executable_name,
                program_files / "FFmpeg" / "bin" / executable_name,
                program_data / "chocolatey" / "bin" / executable_name,
                user_profile / "scoop" / "shims" / executable_name,
                local_app_data / "Microsoft" / "WinGet"
                / "Links" / executable_name,
            )
        )

    seen: set[str] = set()
    for candidate in candidates:
        try:
            normalized = candidate.resolve()
        except OSError:
            normalized = candidate

        key = os.fspath(normalized).casefold()
        if key in seen:
            continue
        seen.add(key)

        if normalized.is_file():
            return normalized

    raise FileNotFoundError(
        f"{executable_name} was not found. Add FFmpeg's bin folder to PATH, "
        "set FFMPEG_DIR, or pass an explicit executable path."
    )


def parse_timestamp(value: str) -> tuple[bool, float]:
    """
    Return (use_last_frame, seconds).

    Accepted:
      last
      12.345
      01:12.345
      00:01:12.345
      12345ms
    """
    value = value.strip()

    if value.casefold() in {"last", "end", "eof"}:
        return True, 0.0

    if value.casefold().endswith("ms"):
        milliseconds = float(value[:-2].strip())
        if milliseconds < 0:
            raise ValueError("Timestamp cannot be negative.")
        return False, milliseconds / 1000.0

    parts = value.split(":")
    try:
        numbers = [float(part.strip()) for part in parts]
    except ValueError as exc:
        raise ValueError(f"Invalid timestamp: {value}") from exc

    if len(numbers) == 1:
        seconds = numbers[0]
    elif len(numbers) == 2:
        minutes, seconds_part = numbers
        if not 0 <= seconds_part < 60:
            raise ValueError("Seconds must be below 60.")
        seconds = minutes * 60 + seconds_part
    elif len(numbers) == 3:
        hours, minutes, seconds_part = numbers
        if not 0 <= minutes < 60 or not 0 <= seconds_part < 60:
            raise ValueError("Minutes and seconds must be below 60.")
        seconds = hours * 3600 + minutes * 60 + seconds_part
    else:
        raise ValueError(
            "Use seconds, MM:SS, HH:MM:SS.mmm, milliseconds with 'ms', "
            "or 'last'."
        )

    if seconds < 0:
        raise ValueError("Timestamp cannot be negative.")

    return False, seconds


def run_text(command: list[str]) -> str:
    completed = subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    if completed.returncode != 0:
        message = completed.stderr.strip() or "The command failed."
        raise RuntimeError(message)

    return completed.stdout


def probe_duration(ffprobe: Path, input_path: Path) -> float | None:
    output = run_text(
        [
            os.fspath(ffprobe),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            os.fspath(input_path),
        ]
    ).strip()

    try:
        duration = float(output)
    except (TypeError, ValueError):
        return None

    return duration if duration >= 0 else None


def parse_last_float(output: str) -> float | None:
    last: float | None = None

    for line in output.splitlines():
        value = line.strip().split(",", 1)[0]
        try:
            timestamp = float(value)
        except ValueError:
            continue

        if timestamp >= 0:
            last = timestamp

    return last


def probe_last_frame_timestamp(ffprobe: Path, input_path: Path) -> float:
    """
    Ask ffprobe for the timestamp of the final decoded video frame.

    This avoids guessing from duration or frame rate. Duration is an end
    boundary and frequently does not correspond to an actual frame.
    """
    duration = probe_duration(ffprobe, input_path)
    commands: list[list[str]] = []

    # Try only the final 30 seconds first. ffprobe may seek to an earlier
    # keyframe, which is fine; the final reported decoded frame is still used.
    if duration is not None:
        start = max(0.0, duration - 30.0)
        commands.append(
            [
                os.fspath(ffprobe),
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-read_intervals",
                f"{start:.6f}%",
                "-show_entries",
                "frame=best_effort_timestamp_time",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                os.fspath(input_path),
            ]
        )

    # Full-scan fallback for files whose seeking/index metadata is unusual.
    commands.append(
        [
            os.fspath(ffprobe),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "frame=best_effort_timestamp_time",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            os.fspath(input_path),
        ]
    )

    for command in commands:
        timestamp = parse_last_float(run_text(command))
        if timestamp is not None:
            return timestamp

    raise RuntimeError("No decoded video frame timestamp was found.")


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path

    counter = 2
    while True:
        candidate = path.with_name(
            f"{path.stem}_{counter}{path.suffix}"
        )
        if not candidate.exists():
            return candidate
        counter += 1


def format_filename_timestamp(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1_000)
    return f"{hours:02d}-{minutes:02d}-{secs:02d}-{millis:03d}"


def extract_frame(
    ffmpeg: Path,
    input_path: Path,
    output_path: Path,
    timestamp_seconds: float,
) -> None:
    """
    Extract the first decoded frame at or after timestamp_seconds.

    A five-second preroll keeps arbitrary seeks fast while the post-input seek
    remains frame-accurate over the small final interval.
    """
    preroll = min(max(0.0, timestamp_seconds), 5.0)
    input_seek = max(0.0, timestamp_seconds - preroll)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_name(
        f".{output_path.stem}.{uuid.uuid4().hex}.tmp{output_path.suffix}"
    )

    command = [
        os.fspath(ffmpeg),
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-ss",
        f"{input_seek:.6f}",
        "-i",
        os.fspath(input_path),
        "-ss",
        f"{preroll:.6f}",
        "-map",
        "0:v:0",
        "-an",
        "-sn",
        "-dn",
        "-frames:v",
        "1",
        "-fps_mode",
        "vfr",
        os.fspath(temporary_path),
    ]

    try:
        run_text(command)

        if (
            not temporary_path.is_file()
            or temporary_path.stat().st_size <= 0
        ):
            raise RuntimeError(
                "FFmpeg completed without writing a valid image."
            )

        os.replace(temporary_path, output_path)
    finally:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Save one exact video frame without opening or modifying "
            "the video player."
        )
    )
    parser.add_argument("input", help="Input video file")
    parser.add_argument(
        "timestamp",
        help="Seconds, MM:SS, HH:MM:SS.mmm, 1234ms, or 'last'",
    )
    parser.add_argument(
        "output",
        nargs="?",
        help=(
            "Output image path. When omitted, a timestamped PNG is "
            "created beside the video."
        ),
    )
    parser.add_argument(
        "--ffmpeg",
        help="Path to ffmpeg.exe or its folder",
    )
    parser.add_argument(
        "--ffprobe",
        help="Path to ffprobe.exe or its folder",
    )
    args = parser.parse_args()

    try:
        input_path = Path(args.input).expanduser().resolve()
        if not input_path.is_file():
            raise FileNotFoundError(
                f"Input video does not exist: {input_path}"
            )

        ffmpeg = find_program("ffmpeg", args.ffmpeg)

        ffprobe_override = args.ffprobe
        if not ffprobe_override:
            sibling_name = (
                "ffprobe.exe" if os.name == "nt" else "ffprobe"
            )
            sibling = ffmpeg.with_name(sibling_name)
            if sibling.is_file():
                ffprobe_override = os.fspath(sibling)

        ffprobe = find_program("ffprobe", ffprobe_override)

        use_last, requested_seconds = parse_timestamp(args.timestamp)

        if use_last:
            actual_seconds = probe_last_frame_timestamp(
                ffprobe,
                input_path,
            )

            # Seeking 1 ms before the exact PTS avoids floating-point rounding
            # past the final frame. FFmpeg then outputs that final frame.
            extraction_seconds = max(0.0, actual_seconds - 0.001)
        else:
            actual_seconds = requested_seconds
            extraction_seconds = requested_seconds

        if args.output:
            output_path = Path(args.output).expanduser().resolve()
        else:
            output_path = input_path.with_name(
                f"{input_path.stem}_"
                f"{format_filename_timestamp(actual_seconds)}.png"
            )
            output_path = unique_path(output_path)

        extract_frame(
            ffmpeg,
            input_path,
            output_path,
            extraction_seconds,
        )

        print(
            json.dumps(
                {
                    "ok": True,
                    "input": os.fspath(input_path),
                    "output": os.fspath(output_path),
                    "requested": args.timestamp,
                    "actual_timestamp_seconds": round(
                        actual_seconds,
                        6,
                    ),
                    "last_frame": use_last,
                },
                ensure_ascii=True,
            )
        )
        return 0

    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": str(exc),
                },
                ensure_ascii=True,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())