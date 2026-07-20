from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path


def find_program(name: str, override: str | None = None) -> Path:
    """Find ffmpeg/ffprobe from an override, environment, PATH, or common folders."""
    executable_name = name + (".exe" if os.name == "nt" else "")
    candidates: list[Path] = []

    if override:
        candidate = Path(override).expanduser()
        candidates.append(
            candidate / executable_name
            if candidate.is_dir()
            else candidate
        )

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
        user_profile = Path(
            os.environ.get("USERPROFILE", r"C:\Users\Default")
        )
        local_app_data = Path(
            os.environ.get(
                "LOCALAPPDATA",
                user_profile / "AppData" / "Local",
            )
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
    Return (use_last_frame, zero_based_seconds).

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


def run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def run_text(command: list[str]) -> str:
    completed = run_command(command)
    if completed.returncode != 0:
        message = completed.stderr.strip() or "The command failed."
        raise RuntimeError(message)
    return completed.stdout


def parse_timestamps(output: str) -> list[float]:
    timestamps: list[float] = []

    for line in output.splitlines():
        value = line.strip().split(",", 1)[0]
        if not value or value.casefold() == "n/a":
            continue

        try:
            timestamp = float(value)
        except ValueError:
            continue

        timestamps.append(timestamp)

    return timestamps


def probe_first_frame_timestamp(
    ffprobe: Path,
    input_path: Path,
) -> float:
    """
    Get the first decoded frame PTS.

    Some MP4 files and exported clips start at a nonzero stream timestamp.
    Playback applications still present their first frame as time zero.
    """
    output = run_text(
        [
            os.fspath(ffprobe),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-read_intervals",
            "%+#16",
            "-show_entries",
            "frame=best_effort_timestamp_time",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            os.fspath(input_path),
        ]
    )
    timestamps = parse_timestamps(output)
    if timestamps:
        return timestamps[0]

    # Full-scan fallback for unusual containers.
    output = run_text(
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
    timestamps = parse_timestamps(output)
    if timestamps:
        return timestamps[0]

    raise RuntimeError("No decoded video frame was found.")


def probe_duration(
    ffprobe: Path,
    input_path: Path,
) -> float | None:
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


def probe_last_frame_timestamp(
    ffprobe: Path,
    input_path: Path,
) -> float:
    """
    Get the absolute PTS of the final decoded frame.

    The interval optimization may start at an earlier keyframe, but it reads
    through EOF, so the final reported decoded frame remains the real last one.
    """
    duration = probe_duration(ffprobe, input_path)
    commands: list[list[str]] = []

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
        timestamps = parse_timestamps(run_text(command))
        if timestamps:
            return timestamps[-1]

    raise RuntimeError("No decoded video frame timestamp was found.")


def probe_zero_based_frame_bounds(
    ffprobe: Path,
    input_path: Path,
) -> tuple[float, float]:
    """
    Return (first_absolute_pts, last_zero_based_pts).

    ffprobe frame timestamps are absolute stream timestamps. User-facing
    playback time starts at the first decoded frame, so normalize by subtracting
    that first PTS before giving a timestamp to FFmpeg's seek options.
    """
    first_absolute = probe_first_frame_timestamp(
        ffprobe,
        input_path,
    )
    last_absolute = probe_last_frame_timestamp(
        ffprobe,
        input_path,
    )
    last_zero_based = max(0.0, last_absolute - first_absolute)
    return first_absolute, last_zero_based


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


def make_temporary_output(output_path: Path) -> Path:
    return output_path.with_name(
        f".{output_path.stem}.{uuid.uuid4().hex}.tmp"
        f"{output_path.suffix}"
    )


def extract_one_frame(
    ffmpeg: Path,
    input_path: Path,
    output_path: Path,
    zero_based_seconds: float,
) -> tuple[bool, str]:
    """
    Extract the first decoded frame at or after a zero-based playback time.

    Input seeking jumps close to the target. Output seeking decodes/discards
    the small preroll for frame accuracy.
    """
    zero_based_seconds = max(0.0, zero_based_seconds)
    preroll = min(zero_based_seconds, 5.0)
    input_seek = max(0.0, zero_based_seconds - preroll)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = make_temporary_output(output_path)

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
        "-c:v",
        "png",
        "-f",
        "image2",
        "-update",
        "1",
        os.fspath(temporary_path),
    ]

    completed = run_command(command)

    try:
        valid = (
            completed.returncode == 0
            and temporary_path.is_file()
            and temporary_path.stat().st_size > 0
        )

        if not valid:
            detail = completed.stderr.strip()
            if not detail:
                detail = (
                    "FFmpeg reached no decoded frame at the requested "
                    "zero-based timestamp."
                )
            return False, detail

        os.replace(temporary_path, output_path)
        return True, ""
    finally:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass


def extract_physical_last_frame(
    ffmpeg: Path,
    input_path: Path,
    output_path: Path,
) -> None:
    """
    Decode a tail window to a numbered PNG sequence and keep its final image.

    This fallback does not depend on timestamp arithmetic. It selects the last
    file FFmpeg physically decoded before EOF.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    attempts: list[list[str]] = []

    with tempfile.TemporaryDirectory(
        prefix="video_last_frame_"
    ) as temporary_directory:
        temp_dir = Path(temporary_directory)

        # A five-second tail is normally enough and keeps temporary work small.
        # If that yields no frames, decode the whole input as a final fallback.
        attempts.append(
            [
                os.fspath(ffmpeg),
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-y",
                "-sseof",
                "-5",
                "-i",
                os.fspath(input_path),
                "-map",
                "0:v:0",
                "-an",
                "-sn",
                "-dn",
                "-c:v",
                "png",
                "-fps_mode",
                "passthrough",
                "-f",
                "image2",
                os.fspath(temp_dir / "tail_%09d.png"),
            ]
        )

        attempts.append(
            [
                os.fspath(ffmpeg),
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-y",
                "-i",
                os.fspath(input_path),
                "-map",
                "0:v:0",
                "-an",
                "-sn",
                "-dn",
                "-c:v",
                "png",
                "-fps_mode",
                "passthrough",
                "-f",
                "image2",
                os.fspath(temp_dir / "full_%09d.png"),
            ]
        )

        errors: list[str] = []

        for command in attempts:
            # Clear any files from a previous attempt.
            for old_image in temp_dir.glob("*.png"):
                try:
                    old_image.unlink()
                except OSError:
                    pass

            completed = run_command(command)
            images = sorted(temp_dir.glob("*.png"))

            if completed.returncode == 0 and images:
                last_image = images[-1]
                if last_image.stat().st_size <= 0:
                    errors.append(
                        "The final decoded image was empty."
                    )
                    continue

                temporary_output = make_temporary_output(
                    output_path
                )
                shutil.copyfile(last_image, temporary_output)
                os.replace(temporary_output, output_path)
                return

            detail = completed.stderr.strip()
            if detail:
                errors.append(detail)

        raise RuntimeError(
            "\n\n".join(errors)
            or "FFmpeg decoded no video frame before EOF."
        )


def save_requested_frame(
    ffmpeg: Path,
    ffprobe: Path,
    input_path: Path,
    output_path: Path,
    *,
    use_last: bool,
    requested_seconds: float,
) -> tuple[float, str]:
    """
    Save the requested frame and return (actual_zero_based_time, method).
    """
    first_absolute, last_zero_based = (
        probe_zero_based_frame_bounds(
            ffprobe,
            input_path,
        )
    )

    if use_last:
        # First use the efficient exact normalized timestamp.
        target = max(0.0, last_zero_based - 0.001)
        success, detail = extract_one_frame(
            ffmpeg,
            input_path,
            output_path,
            target,
        )
        if success:
            return last_zero_based, "normalized-last-timestamp"

        # If container seeking remains unusual, select the last physically
        # decoded tail frame instead of guessing another timestamp.
        extract_physical_last_frame(
            ffmpeg,
            input_path,
            output_path,
        )
        return last_zero_based, "decoded-tail-final-frame"

    target = max(
        0.0,
        min(requested_seconds, last_zero_based),
    )

    success, detail = extract_one_frame(
        ffmpeg,
        input_path,
        output_path,
        target,
    )
    if success:
        return target, "normalized-timestamp"

    # A viewport timestamp can be slightly beyond the last frame because media
    # duration is an end boundary. Clamp just before the real final frame.
    fallback_target = max(0.0, last_zero_based - 0.001)
    if abs(fallback_target - target) > 0.0001:
        success, second_detail = extract_one_frame(
            ffmpeg,
            input_path,
            output_path,
            fallback_target,
        )
        if success:
            return last_zero_based, "clamped-final-timestamp"
        detail = f"{detail}\n\nFallback:\n{second_detail}"

    raise RuntimeError(
        "Could not extract a frame.\n\n"
        f"First frame absolute PTS: {first_absolute:.6f}\n"
        f"Last frame zero-based PTS: {last_zero_based:.6f}\n"
        f"Requested zero-based time: {requested_seconds:.6f}\n\n"
        f"{detail}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Save one video frame without interacting with the video player."
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
        use_last, requested_seconds = parse_timestamp(
            args.timestamp
        )

        if args.output:
            output_path = Path(args.output).expanduser().resolve()
        else:
            # The final exact timestamp is not known until probing completes.
            provisional_label = (
                "last"
                if use_last
                else format_filename_timestamp(requested_seconds)
            )
            output_path = input_path.with_name(
                f"{input_path.stem}_{provisional_label}.png"
            )
            output_path = unique_path(output_path)

        actual_seconds, method = save_requested_frame(
            ffmpeg,
            ffprobe,
            input_path,
            output_path,
            use_last=use_last,
            requested_seconds=requested_seconds,
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
                    "method": method,
                },
                ensure_ascii=False,
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
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
