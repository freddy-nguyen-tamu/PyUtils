import os
import shutil
import subprocess
from pathlib import Path
from fractions import Fraction

ROOT_DIR = Path(r"C:\Users\qacer\Downloads\new\new")
OUTPUT_DIR = ROOT_DIR / "fixed_mp4s"

# Safer default: keep originals and put repaired copies in fixed_mp4s.
REPLACE_ORIGINALS = False

# Detection rule:
# If MP4 metadata says it is much longer than the actual packet timeline,
# it is considered broken.
MIN_ADVERTISED_SECONDS = 60
RATIO_THRESHOLD = 5.0
EXTRA_SECONDS_THRESHOLD = 30

# Used only when remuxing is not enough.
DEFAULT_FORCE_FPS = 30


def run_cmd(cmd, check=True):
    print("\n> " + " ".join(str(x) for x in cmd))
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    if check and result.returncode != 0:
        print(result.stdout)
        print(result.stderr)
        raise RuntimeError(f"Command failed with exit code {result.returncode}")

    return result


def require_tool(name):
    try:
        subprocess.run(
            [name, "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
    except Exception:
        raise RuntimeError(
            f"{name} was not found. Install FFmpeg and make sure {name}.exe "
            f"is available in PATH."
        )


def ffprobe_format_duration(path: Path):
    result = run_cmd(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        check=False,
    )

    text = result.stdout.strip()
    try:
        value = float(text)
        if value > 0:
            return value
    except ValueError:
        pass

    return None


def ffprobe_packet_timeline_duration(path: Path):
    """
    Estimate real playable duration from packet timestamps instead of container
    duration metadata. This catches files where the MP4 header lies.
    """
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "packet=pts_time,dts_time,duration_time",
        "-of",
        "csv=p=0",
        str(path),
    ]

    print(f"Checking packet timeline: {path.name}")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    min_t = None
    max_t = None

    assert proc.stdout is not None

    for line in proc.stdout:
        parts = [p.strip() for p in line.strip().split(",")]

        timestamp = None
        duration = 0.0

        for p in parts:
            if not p or p == "N/A":
                continue

            try:
                value = float(p)
            except ValueError:
                continue

            if timestamp is None:
                timestamp = value
            else:
                duration = max(duration, value)

        if timestamp is None:
            continue

        end_t = timestamp + max(duration, 0.0)

        if min_t is None or timestamp < min_t:
            min_t = timestamp
        if max_t is None or end_t > max_t:
            max_t = end_t

    _, stderr = proc.communicate()

    if proc.returncode != 0:
        print(stderr)
        return None

    if min_t is None or max_t is None:
        return None

    duration = max_t - min_t

    if duration <= 0:
        return None

    return duration


def get_video_fps(path: Path):
    result = run_cmd(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=avg_frame_rate,r_frame_rate",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        check=False,
    )

    candidates = result.stdout.strip().splitlines()

    for item in candidates:
        item = item.strip()
        if not item or item == "0/0":
            continue

        try:
            fps = float(Fraction(item))
            if 1 <= fps <= 240:
                return fps
        except Exception:
            pass

    return DEFAULT_FORCE_FPS


def is_bad_duration(path: Path):
    advertised = ffprobe_format_duration(path)

    if advertised is None:
        print(f"[BAD] Cannot read advertised duration: {path.name}")
        return True, None, None

    if advertised < MIN_ADVERTISED_SECONDS:
        return False, advertised, None

    actual = ffprobe_packet_timeline_duration(path)

    if actual is None:
        print(f"[BAD] Cannot read packet timeline: {path.name}")
        return True, advertised, None

    too_large_by_ratio = advertised > actual * RATIO_THRESHOLD
    too_large_by_seconds = advertised > actual + EXTRA_SECONDS_THRESHOLD

    bad = too_large_by_ratio and too_large_by_seconds

    return bad, advertised, actual


def remux_fix(src: Path, dst: Path):
    """
    First attempt: rebuild MP4 container without re-encoding.
    Fast and lossless.
    """
    temp = dst.with_suffix(".remux.tmp.mp4")

    if temp.exists():
        temp.unlink()

    run_cmd(
        [
            "ffmpeg",
            "-y",
            "-fflags",
            "+genpts",
            "-i",
            str(src),
            "-map",
            "0",
            "-c",
            "copy",
            "-avoid_negative_ts",
            "make_zero",
            "-movflags",
            "+faststart",
            str(temp),
        ]
    )

    temp.replace(dst)


def force_reencode_fix(src: Path, dst: Path):
    """
    Second attempt: decode frames and rebuild timestamps.

    This is slower and re-encodes the video, but it can fix files where
    the packet timestamps themselves are broken.
    """
    temp = dst.with_suffix(".reencode.tmp.mp4")
    fps = get_video_fps(src)

    if temp.exists():
        temp.unlink()

    # Force a clean constant frame timeline.
    video_filter = f"fps={fps},setpts=N/({fps}*TB)"

    run_cmd(
        [
            "ffmpeg",
            "-y",
            "-fflags",
            "+genpts",
            "-i",
            str(src),
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-vf",
            video_filter,
            "-af",
            "aresample=async=1:first_pts=0",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-movflags",
            "+faststart",
            str(temp),
        ]
    )

    temp.replace(dst)


def seconds_to_text(value):
    if value is None:
        return "unknown"

    value = int(round(value))
    h = value // 3600
    m = (value % 3600) // 60
    s = value % 60

    if h:
        return f"{h}h {m}m {s}s"

    return f"{m}m {s}s"


def fix_one_mp4(src: Path):
    print("\n" + "=" * 80)
    print(f"Scanning: {src}")

    bad, advertised, actual = is_bad_duration(src)

    print(f"Advertised duration: {seconds_to_text(advertised)}")
    print(f"Packet timeline:      {seconds_to_text(actual)}")

    if not bad:
        print("[OK] Duration looks normal. Skipping.")
        return

    print("[BAD] Suspicious fake/broken duration detected.")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    fixed_path = OUTPUT_DIR / src.name

    # Avoid name collision.
    if fixed_path.exists():
        fixed_path = OUTPUT_DIR / f"{src.stem}_fixed{src.suffix}"

    print("[FIX] Trying lossless remux first...")
    remux_fix(src, fixed_path)

    still_bad, new_advertised, new_actual = is_bad_duration(fixed_path)

    print(f"After remux advertised: {seconds_to_text(new_advertised)}")
    print(f"After remux timeline:   {seconds_to_text(new_actual)}")

    if still_bad:
        print("[FIX] Remux was not enough. Trying full timestamp rebuild...")
        force_reencode_fix(src, fixed_path)

        still_bad, new_advertised, new_actual = is_bad_duration(fixed_path)

        print(f"After reencode advertised: {seconds_to_text(new_advertised)}")
        print(f"After reencode timeline:   {seconds_to_text(new_actual)}")

    if still_bad:
        print("[FAILED] Could not fully fix this file.")
        return

    print(f"[FIXED] Saved repaired file to: {fixed_path}")

    if REPLACE_ORIGINALS:
        backup_path = src.with_suffix(src.suffix + ".bak")

        print(f"[BACKUP] Original moved to: {backup_path}")
        shutil.move(str(src), str(backup_path))

        print(f"[REPLACE] Fixed file moved to original path: {src}")
        shutil.move(str(fixed_path), str(src))


def main():
    require_tool("ffmpeg")
    require_tool("ffprobe")

    if not ROOT_DIR.exists():
        raise RuntimeError(f"Folder does not exist: {ROOT_DIR}")

    mp4_files = sorted(
        p for p in ROOT_DIR.rglob("*.mp4")
        if OUTPUT_DIR not in p.parents
    )

    if not mp4_files:
        print(f"No MP4 files found in: {ROOT_DIR}")
        return

    print(f"Found {len(mp4_files)} MP4 file(s).")

    fixed_count = 0
    failed_count = 0

    for mp4 in mp4_files:
        try:
            before_files = set(OUTPUT_DIR.glob("*")) if OUTPUT_DIR.exists() else set()
            fix_one_mp4(mp4)
            after_files = set(OUTPUT_DIR.glob("*")) if OUTPUT_DIR.exists() else set()

            if after_files - before_files:
                fixed_count += 1

        except Exception as e:
            failed_count += 1
            print(f"[ERROR] {mp4}")
            print(e)

    print("\n" + "=" * 80)
    print("Done.")
    print(f"Output folder: {OUTPUT_DIR}")
    print(f"Fixed or attempted: {fixed_count}")
    print(f"Errors: {failed_count}")


if __name__ == "__main__":
    main()