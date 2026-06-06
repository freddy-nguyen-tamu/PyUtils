from pathlib import Path
import subprocess
import shutil
import ctypes
from ctypes import wintypes
import re

# ============================================================
# CONFIG
# ============================================================

SOURCE_DIR = Path(r"C:\Users\qacer\Downloads\new\new")
REVIEW_DIR = Path(r"C:\Users\qacer\Downloads\fixed_faulty_mp4_review")

# Faulty files definitely look like:
# 516875849502_2.mp4
CANDIDATE_NAME_RE = re.compile(r"^\d{12}_\d$")

ABSURD_CONTAINER_DURATION_SECONDS = 3600

# Repaired clips should be short, but leave room for a few longer ones.
MAX_REASONABLE_FIXED_DURATION_SECONDS = 300
MIN_REASONABLE_FIXED_DURATION_SECONDS = 0.75

# Force sane MP4 playback.
OUTPUT_FPS = 30

# ============================================================
# WINDOWS RECYCLE BIN SUPPORT
# ============================================================

FO_DELETE = 3
FOF_ALLOWUNDO = 0x40
FOF_NOCONFIRMATION = 0x10
FOF_SILENT = 0x04


class SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", wintypes.LPCWSTR),
        ("pTo", wintypes.LPCWSTR),
        ("fFlags", wintypes.UINT),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", wintypes.LPVOID),
        ("lpszProgressTitle", wintypes.LPCWSTR),
    ]


def move_to_recycle_bin(path: Path) -> bool:
    path = path.resolve()

    if not path.exists():
        return False

    from_buffer = str(path) + "\0\0"

    operation = SHFILEOPSTRUCTW()
    operation.hwnd = None
    operation.wFunc = FO_DELETE
    operation.pFrom = from_buffer
    operation.pTo = None
    operation.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT

    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))

    return result == 0 and not operation.fAnyOperationsAborted


# ============================================================
# COMMAND HELPERS
# ============================================================

def require_command(command: str) -> None:
    if shutil.which(command) is None:
        raise RuntimeError(
            f"{command} was not found in PATH. Install ffmpeg first, "
            f"then reopen Windows Terminal and try again."
        )


def run_quiet(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def run_visible(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args)


def get_format_duration_seconds(path: Path) -> float | None:
    result = run_quiet([
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ])

    if result.returncode != 0:
        return None

    text = result.stdout.strip()

    if not text or text.upper() == "N/A":
        return None

    try:
        value = float(text)
    except ValueError:
        return None

    if value <= 0:
        return None

    return value


def get_video_fps_text(path: Path) -> str | None:
    result = run_quiet([
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=avg_frame_rate,r_frame_rate",
        "-of", "default=noprint_wrappers=1",
        str(path),
    ])

    if result.returncode != 0:
        return None

    text = result.stdout.strip()

    if not text:
        return None

    return text


def has_stream(path: Path, stream_selector: str) -> bool:
    result = run_quiet([
        "ffprobe",
        "-v", "error",
        "-select_streams", stream_selector,
        "-show_entries", "stream=index",
        "-of", "csv=p=0",
        str(path),
    ])

    return result.returncode == 0 and bool(result.stdout.strip())


def has_video_stream(path: Path) -> bool:
    return has_stream(path, "v:0")


def has_audio_stream(path: Path) -> bool:
    return has_stream(path, "a:0")


def is_candidate_name(path: Path) -> bool:
    return CANDIDATE_NAME_RE.match(path.stem) is not None


def output_is_good(path: Path) -> tuple[bool, str]:
    if not path.exists():
        return False, "output file does not exist"

    if path.stat().st_size <= 0:
        return False, "output file is empty"

    duration = get_format_duration_seconds(path)

    if duration is None:
        return False, "output duration is unreadable"

    if duration < MIN_REASONABLE_FIXED_DURATION_SECONDS:
        return False, f"output is suspiciously short: {duration:.2f} sec"

    if duration > MAX_REASONABLE_FIXED_DURATION_SECONDS:
        return False, f"output is suspiciously long: {duration:.2f} sec"

    if not has_video_stream(path):
        return False, "output has no readable video stream"

    fps_text = get_video_fps_text(path) or ""

    if "1000/1" in fps_text or "1k" in fps_text.lower():
        return False, "output still appears to be 1000 FPS"

    return True, f"output looks valid: {duration:.2f} sec"


# ============================================================
# FIXING
# ============================================================

def repair_with_video_and_audio(input_path: Path, output_path: Path) -> bool:
    """
    Main repair.

    These files are mislabeled .mp4 files. FFmpeg reads them as
    Matroska/WebM with VP9 video and Opus audio.

    Important:
    - Do NOT use setpts=N/(30*TB). That compresses the video.
    - Do NOT preserve 1k fps. That creates laggy level 6.1 MP4 files.
    - Use fps=30 filter instead. It keeps timeline duration but duplicates
      or drops frames to create a normal constant-frame-rate MP4.
    """
    temp_output_path = output_path.with_name(output_path.stem + ".repairing.mp4")

    if temp_output_path.exists():
        temp_output_path.unlink()

    if output_path.exists():
        output_path.unlink()

    args = [
        "ffmpeg",
        "-hide_banner",
        "-y",

        # Tolerate bad WebM/Matroska packets.
        "-fflags", "+genpts+discardcorrupt",
        "-err_detect", "ignore_err",

        "-i", str(input_path),

        "-map", "0:v:0",
        "-map", "0:a:0?",

        # Preserve timeline, reset start to zero, then make sane 30 FPS.
        "-vf", f"setpts=PTS-STARTPTS,fps={OUTPUT_FPS},format=yuv420p",

        # Rebuild audio timestamps.
        "-af", "aresample=async=1000:first_pts=0,asetpts=PTS-STARTPTS",

        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "18",

        # Force sane H.264 compatibility.
        "-r", str(OUTPUT_FPS),
        "-fps_mode", "cfr",
        "-profile:v", "high",
        "-level:v", "4.0",

        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", "48000",
        "-ac", "2",

        "-movflags", "+faststart",
        "-avoid_negative_ts", "make_zero",

        str(temp_output_path),
    ]

    print(f"  Attempt 1: rebuilding as normal {OUTPUT_FPS} FPS MP4 with audio...")
    result = run_visible(args)

    if result.returncode != 0:
        if temp_output_path.exists():
            temp_output_path.unlink()
        return False

    good, reason = output_is_good(temp_output_path)
    print(f"  Validation: {reason}")

    if not good:
        if temp_output_path.exists():
            temp_output_path.unlink()
        return False

    shutil.move(str(temp_output_path), str(output_path))
    return True


def repair_video_only(input_path: Path, output_path: Path) -> bool:
    """
    Fallback if audio is too corrupt.

    Creates a clean playable MP4 without audio instead of a laggy or broken
    file.
    """
    temp_output_path = output_path.with_name(output_path.stem + ".repairing.video_only.mp4")

    if temp_output_path.exists():
        temp_output_path.unlink()

    if output_path.exists():
        output_path.unlink()

    args = [
        "ffmpeg",
        "-hide_banner",
        "-y",

        "-fflags", "+genpts+discardcorrupt",
        "-err_detect", "ignore_err",

        "-i", str(input_path),

        "-map", "0:v:0",
        "-an",

        "-vf", f"setpts=PTS-STARTPTS,fps={OUTPUT_FPS},format=yuv420p",

        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "18",

        "-r", str(OUTPUT_FPS),
        "-fps_mode", "cfr",
        "-profile:v", "high",
        "-level:v", "4.0",

        "-movflags", "+faststart",
        "-avoid_negative_ts", "make_zero",

        str(temp_output_path),
    ]

    print(f"  Attempt 2: rebuilding as video-only {OUTPUT_FPS} FPS MP4...")
    result = run_visible(args)

    if result.returncode != 0:
        if temp_output_path.exists():
            temp_output_path.unlink()
        return False

    good, reason = output_is_good(temp_output_path)
    print(f"  Validation: {reason}")

    if not good:
        if temp_output_path.exists():
            temp_output_path.unlink()
        return False

    shutil.move(str(temp_output_path), str(output_path))
    return True


def repair_mp4(input_path: Path, output_path: Path) -> bool:
    if not has_video_stream(input_path):
        print("  No readable video stream found")
        return False

    audio_exists = has_audio_stream(input_path)

    if audio_exists:
        if repair_with_video_and_audio(input_path, output_path):
            print("  Video + audio repair succeeded")
            return True

        print("  Video + audio repair failed or produced bad output")

    if repair_video_only(input_path, output_path):
        print("  Video-only repair succeeded")
        return True

    print("  All repair attempts failed")
    return False


def should_fix_candidate(path: Path) -> tuple[bool, str]:
    duration = get_format_duration_seconds(path)

    if duration is None:
        return True, "container duration unreadable"

    if duration > ABSURD_CONTAINER_DURATION_SECONDS:
        return True, f"container duration is absurdly long: {duration:.2f} sec"

    return False, f"normal duration: {duration:.2f} sec"


# ============================================================
# MAIN
# ============================================================

def main() -> None:
    require_command("ffmpeg")
    require_command("ffprobe")

    if not SOURCE_DIR.exists():
        raise RuntimeError(f"Source folder does not exist: {SOURCE_DIR}")

    REVIEW_DIR.mkdir(parents=True, exist_ok=True)

    all_mp4_files = sorted(SOURCE_DIR.glob("*.mp4"))
    candidate_files = [p for p in all_mp4_files if is_candidate_name(p)]

    fixed_files: list[tuple[Path, Path]] = []

    print("Scanning folder:")
    print(SOURCE_DIR)
    print()
    print(f"Total MP4 files found: {len(all_mp4_files)}")
    print(f"Candidate files matching 12digits_1digit.mp4: {len(candidate_files)}")
    print(f"Skipped immediately by filename: {len(all_mp4_files) - len(candidate_files)}")
    print()

    if not candidate_files:
        print("No candidate MP4 files found.")
        return

    for input_path in candidate_files:
        output_path = REVIEW_DIR / input_path.name

        print(f"Checking candidate: {input_path.name}")

        should_fix, reason = should_fix_candidate(input_path)

        if not should_fix:
            print(f"  Ignored: {reason}")
            print()
            continue

        print(f"  Faulty/suspicious detected: {reason}")
        print("  Fixing...")

        fixed = repair_mp4(input_path, output_path)

        if fixed:
            print(f"  Fixed copy written to: {output_path}")
            fixed_files.append((input_path, output_path))
        else:
            print(f"  Failed to fix: {input_path.name}")

        print()

    if not fixed_files:
        print("No faulty candidate MP4 files were fixed.")
        return

    print()
    print("Done. Fixed versions were written to:")
    print(REVIEW_DIR)
    print()
    print("Opening review folder...")
    print("Check the fixed videos first.")
    print()

    subprocess.run(["explorer", str(REVIEW_DIR)])

    answer = input('After checking the folder, type yes to replace the originals: ').strip()

    if answer != "yes":
        print("Replacement cancelled. Originals were not changed.")
        return

    print()

    for original_path, fixed_path in fixed_files:
        print(f"Replacing: {original_path.name}")

        if not fixed_path.exists():
            print("  Fixed file missing, skipping")
            continue

        moved_to_trash = move_to_recycle_bin(original_path)

        if not moved_to_trash:
            print("  Could not move original to Recycle Bin, skipping replacement")
            continue

        shutil.move(str(fixed_path), str(original_path))

        print("  Replaced successfully")

    print()
    print("Finished.")
    print("Faulty originals were moved to the Windows Recycle Bin.")
    print("Fixed files were moved into:")
    print(SOURCE_DIR)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print()
        print(f"ERROR: {error}")
        input("Press Enter to exit...")