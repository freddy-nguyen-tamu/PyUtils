from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import queue
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    from PIL import Image, ImageTk
except ImportError as exc:
    raise SystemExit(
        "Missing Pillow. Install dependencies with:\n"
        "py -m pip install pillow send2trash"
    ) from exc

try:
    from send2trash import send2trash
except ImportError as exc:
    raise SystemExit(
        "Missing send2trash. Install dependencies with:\n"
        "py -m pip install pillow send2trash"
    ) from exc


# ---------------------------------------------------------------------------
# MP4 Paint-Style Cropper
#
# Requires:
#   py -m pip install pillow send2trash
#   ffmpeg + ffprobe available on PATH
#
# Common Windows FFmpeg install:
#   winget install Gyan.FFmpeg
#
# Behavior:
# - Choose one MP4 or a directory of MP4 files.
# - Folder scans can skip unchanged videos already finalized in this app.
# - "Ignore History This Run" temporarily bypasses history without deleting it.
# - Paint-style crop rectangle: drag inside to move; drag 8 handles to resize;
#   drag outside to draw a replacement crop.
# - Crop is spatial and applies to the whole video.
# - Audio is copied unchanged when possible. If stream-copying audio fails, the
#   export is retried with AAC audio.
# - Original video goes to the Windows Recycle Bin only AFTER a successful,
#   validated cropped export.
# ---------------------------------------------------------------------------

HISTORY_FILENAME = ".mp4_paint_cropper_history.json"
HISTORY_VERSION = 1
MIN_CROP = 16
HANDLE_RADIUS = 7
PREVIEW_MARGIN = 16
OUTPUT_STEM_RE = re.compile(r"_cropped(?:_\d+)?$", re.IGNORECASE)

FINAL_HISTORY_STATES = {
    "reviewed_skipped",
    "processed",
    "process_failed",
    "output_created_trash_failed",
}


@dataclass
class CropBox:
    x0: int
    y0: int
    x1: int
    y1: int

    def normalized(self) -> "CropBox":
        return CropBox(
            min(self.x0, self.x1),
            min(self.y0, self.y1),
            max(self.x0, self.x1),
            max(self.y0, self.y1),
        )

    @property
    def width(self) -> int:
        b = self.normalized()
        return b.x1 - b.x0

    @property
    def height(self) -> int:
        b = self.normalized()
        return b.y1 - b.y0

    def as_list(self) -> list[int]:
        b = self.normalized()
        return [b.x0, b.y0, b.x1, b.y1]


@dataclass
class VideoItem:
    path: Path
    duration: float
    width: int
    height: int
    preview_time: float
    crop: CropBox
    approved: bool = False
    original_signature: dict | None = None


@dataclass
class ScanRequest:
    mode: str  # "file" or "folder"
    target: Path
    recursive: bool
    ignore_history: bool


class FolderHistory:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.path = self.root / HISTORY_FILENAME
        self.data = {
            "version": HISTORY_VERSION,
            "root": str(self.root),
            "entries": {},
        }
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            obj = json.loads(self.path.read_text(encoding="utf-8"))
            if (
                isinstance(obj, dict)
                and obj.get("version") == HISTORY_VERSION
                and isinstance(obj.get("entries"), dict)
            ):
                self.data = obj
                self.data.setdefault("root", str(self.root))
                self.data.setdefault("entries", {})
        except Exception:
            # Corrupt history should never stop the user's videos from opening.
            pass

    def _key(self, path: Path) -> str:
        try:
            rel = path.relative_to(self.root).as_posix()
        except Exception:
            rel = path.name
        return rel.lower() if os.name == "nt" else rel

    @staticmethod
    def signature(path: Path) -> dict:
        stat = path.stat()
        return {
            "size": int(stat.st_size),
            "mtime_ns": int(stat.st_mtime_ns),
        }

    def matching_entry(self, path: Path) -> dict | None:
        entry = self.data["entries"].get(self._key(path))
        if not isinstance(entry, dict):
            return None
        try:
            current_sig = self.signature(path)
        except OSError:
            return None
        if entry.get("signature") != current_sig:
            return None
        return entry

    def should_skip(self, path: Path) -> dict | None:
        entry = self.matching_entry(path)
        if entry and entry.get("status") in FINAL_HISTORY_STATES:
            return entry
        return None

    def mark(
        self,
        path: Path,
        status: str,
        *,
        signature: dict | None = None,
        **extra,
    ) -> None:
        key = self._key(path)
        previous = self.data["entries"].get(key, {})
        if signature is None:
            try:
                signature = self.signature(path)
            except OSError:
                signature = previous.get("signature")
        entry = {
            **previous,
            "name": path.name,
            "signature": signature,
            "status": status,
            "updated_at": time.time(),
            **extra,
        }
        self.data["entries"][key] = entry

    def save(self) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(
            json.dumps(self.data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(tmp, self.path)

    def forget(self) -> None:
        if self.path.exists():
            self.path.unlink()
        self.data = {
            "version": HISTORY_VERSION,
            "root": str(self.root),
            "entries": {},
        }


def ensure_console() -> None:
    if os.name != "nt":
        return
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        import ctypes
        ctypes.windll.kernel32.AllocConsole()
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        sys.stderr = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        sys.stdin = open("CONIN$", "r", encoding="utf-8")
    except Exception:
        pass


def find_ffmpeg() -> tuple[str | None, str | None]:
    return shutil.which("ffmpeg"), shutil.which("ffprobe")


def require_ffmpeg(parent=None) -> bool:
    ffmpeg, ffprobe = find_ffmpeg()
    if ffmpeg and ffprobe:
        return True
    messagebox.showerror(
        "FFmpeg required",
        "This program needs ffmpeg and ffprobe on PATH.\n\n"
        "One common Windows install command is:\n"
        "winget install Gyan.FFmpeg\n\n"
        "Restart the terminal/app after installation.",
        parent=parent,
    )
    return False


def is_generated_output(path: Path) -> bool:
    return bool(OUTPUT_STEM_RE.search(path.stem))


def collect_mp4s(folder: Path, recursive: bool) -> list[Path]:
    iterator = folder.rglob("*") if recursive else folder.glob("*")
    paths = [
        p for p in iterator
        if p.is_file()
        and p.suffix.lower() == ".mp4"
        and not is_generated_output(p)
    ]
    paths.sort(key=lambda p: (str(p.parent).lower(), p.name.lower()))
    return paths


def ffprobe_basic(path: Path) -> dict:
    _, ffprobe = find_ffmpeg()
    if not ffprobe:
        raise RuntimeError("ffprobe not found on PATH.")

    cmd = [
        ffprobe,
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json",
        str(path),
    ]
    cp = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if cp.returncode != 0:
        raise RuntimeError(cp.stderr.strip() or "ffprobe failed.")
    data = json.loads(cp.stdout or "{}")
    try:
        duration = float((data.get("format") or {}).get("duration") or 0.0)
    except Exception:
        duration = 0.0
    return {"duration": max(0.0, duration)}


def extract_preview_frame(path: Path, seconds: float) -> Image.Image:
    ffmpeg, _ = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found on PATH.")

    # ffmpeg autorotation is intentionally left enabled. The export path also
    # uses normal autorotation before its crop filter, so these coordinates
    # correspond to the displayed video orientation.
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel", "error",
        "-ss", f"{max(0.0, seconds):.6f}",
        "-i", str(path),
        "-frames:v", "1",
        "-f", "image2pipe",
        "-vcodec", "png",
        "pipe:1",
    ]
    cp = subprocess.run(cmd, capture_output=True)
    if cp.returncode != 0 or not cp.stdout:
        err = cp.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(err or "Could not extract a preview frame.")
    with Image.open(io.BytesIO(cp.stdout)) as im:
        return im.convert("RGB").copy()


def initial_preview_time(duration: float) -> float:
    if duration <= 0:
        return 0.0
    # Avoid a black first frame/intro where practical.
    return min(max(0.25, duration * 0.08), max(0.0, duration - 0.05))


def scan_request_terminal(request: ScanRequest) -> tuple[list[VideoItem], Path, str]:
    ensure_console()

    if request.mode == "file":
        root = request.target.parent
        paths = [request.target]
    else:
        root = request.target
        paths = collect_mp4s(root, request.recursive)

    history = FolderHistory(root)
    total = len(paths)
    items: list[VideoItem] = []
    skipped_history = 0
    skipped_generated = 0
    failed = 0

    print("\n" + "=" * 84, flush=True)
    print("MP4 PAINT-STYLE CROPPER - PREPARING VIDEOS", flush=True)
    print(f"Target: {request.target}", flush=True)
    print(f"History: {history.path}", flush=True)
    print(f"Ignore history this run: {'YES' if request.ignore_history else 'no'}", flush=True)
    if request.mode == "folder":
        print(f"Include subfolders: {'yes' if request.recursive else 'no'}", flush=True)
    print(f"MP4 files found: {total}", flush=True)
    print("=" * 84, flush=True)

    width = len(str(max(1, total)))

    for i, path in enumerate(paths, 1):
        try:
            rel = path.relative_to(root)
        except Exception:
            rel = path.name

        print(f"[{i:>{width}}/{total}] {rel}", end=" ... ", flush=True)

        if is_generated_output(path):
            skipped_generated += 1
            print("SKIP generated *_cropped output", flush=True)
            continue

        if not request.ignore_history:
            cached = history.should_skip(path)
            if cached is not None:
                skipped_history += 1
                print(
                    f"SKIP history: {cached.get('status', 'finalized')}",
                    flush=True,
                )
                continue

        try:
            signature = FolderHistory.signature(path)
            meta = ffprobe_basic(path)
            duration = meta["duration"]
            t = initial_preview_time(duration)
            frame = extract_preview_frame(path, t)

            # Do not finalize the file in history merely because it was probed.
            # If the user closes the review window before processing, it must
            # appear again next run.
            history.mark(
                path,
                "pending_review",
                signature=signature,
                duration=duration,
                display_width=frame.width,
                display_height=frame.height,
            )
            items.append(
                VideoItem(
                    path=path,
                    duration=duration,
                    width=frame.width,
                    height=frame.height,
                    preview_time=t,
                    crop=CropBox(0, 0, frame.width, frame.height),
                    approved=False,
                    original_signature=signature,
                )
            )
            print(
                f"READY | {frame.width}x{frame.height} | {duration:.1f}s",
                flush=True,
            )
        except Exception as exc:
            failed += 1
            # Probe failures are finalized for normal history-skipping. The
            # Ignore History button lets the user explicitly retry later.
            try:
                history.mark(
                    path,
                    "process_failed",
                    error=f"prepare: {type(exc).__name__}: {exc}",
                )
            except Exception:
                pass
            print(f"ERROR: {type(exc).__name__}: {exc}", flush=True)

    try:
        history.save()
    except Exception as exc:
        print(f"[WARN] Could not save history JSON: {exc}", flush=True)

    msg = (
        f"Prepared {len(items)} video(s); skipped {skipped_history} from history"
        f"; {failed} failed."
    )
    if skipped_generated:
        msg += f" {skipped_generated} generated output(s) ignored."
    print("-" * 84, flush=True)
    print(msg, flush=True)
    print("Opening crop review UI..." if items else "Nothing needs review.", flush=True)
    print("=" * 84 + "\n", flush=True)
    return items, root, msg


def unique_output_path(src: Path) -> Path:
    candidate = src.with_name(f"{src.stem}_cropped.mp4")
    if not candidate.exists():
        return candidate
    i = 2
    while True:
        candidate = src.with_name(f"{src.stem}_cropped_{i}.mp4")
        if not candidate.exists():
            return candidate
        i += 1


def normalize_even_crop(box: CropBox, width: int, height: int) -> CropBox:
    b = box.normalized()

    x0 = max(0, min(width - 2, int(round(b.x0))))
    y0 = max(0, min(height - 2, int(round(b.y0))))
    x1 = max(x0 + 2, min(width, int(round(b.x1))))
    y1 = max(y0 + 2, min(height, int(round(b.y1))))

    # yuv420p/H.264 is most portable with even crop origin and dimensions.
    x0 -= x0 % 2
    y0 -= y0 % 2

    w = x1 - x0
    h = y1 - y0
    w -= w % 2
    h -= h % 2
    w = max(2, w)
    h = max(2, h)

    if x0 + w > width:
        w = width - x0
        w -= w % 2
    if y0 + h > height:
        h = height - y0
        h -= h % 2

    return CropBox(x0, y0, x0 + max(2, w), y0 + max(2, h))


def parse_progress_seconds(value: str) -> float:
    # ffmpeg -progress can report out_time=HH:MM:SS.microseconds
    try:
        hh, mm, ss = value.strip().split(":")
        return int(hh) * 3600 + int(mm) * 60 + float(ss)
    except Exception:
        return 0.0


def run_ffmpeg_crop(
    item: VideoItem,
    output: Path,
    *,
    copy_audio: bool,
) -> tuple[bool, str]:
    """
    Run FFmpeg without allowing its output pipes to deadlock.

    Important implementation detail:
    FFmpeg's diagnostic stream is redirected into the same continuously-drained
    pipe as -progress.  An earlier version left stderr unread until the end,
    which can block FFmpeg once the Windows pipe buffer fills.

    A reader thread drains the pipe while this function:
      - prints progress,
      - prints a heartbeat after ~99%,
      - watches the temporary file for activity, and
      - terminates FFmpeg after an extended period with *no* observable
        progress/output/file changes.

    The original source is NOT touched here.  Recycling happens only later,
    after this function succeeds and the output validates.
    """
    ffmpeg, _ = find_ffmpeg()
    if not ffmpeg:
        return False, "ffmpeg not found on PATH."

    box = normalize_even_crop(item.crop, item.width, item.height)
    tmp = output.with_name(f".{output.stem}.working{output.suffix}")
    try:
        tmp.unlink(missing_ok=True)
    except Exception:
        pass

    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel", "warning",
        "-nostdin",
        "-y",
        "-i", str(item.path),
        "-map", "0:v:0",
        "-map", "0:a?",
        "-map_metadata", "0",
        "-map_chapters", "0",
        "-vf", f"crop={box.width}:{box.height}:{box.x0}:{box.y0}",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
    ]

    if copy_audio:
        cmd += ["-c:a", "copy"]
    else:
        cmd += ["-c:a", "aac", "-b:a", "192k"]

    cmd += [
        "-movflags", "+faststart",
        "-metadata:s:v:0", "rotate=0",
        "-progress", "pipe:1",
        "-nostats",
        str(tmp),
    ]

    # Only declare a stall after five minutes with no FFmpeg output/progress
    # AND no change to the temporary file.  This is intentionally conservative
    # so large files are not killed merely for taking time to finalize.
    STALL_TIMEOUT = 300.0
    HEARTBEAT_INTERVAL = 15.0

    proc = None
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            # Critical fix: continuously drain diagnostics through stdout.
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )

        line_queue: queue.Queue[str | None] = queue.Queue()
        diagnostics = deque(maxlen=80)

        def _reader() -> None:
            try:
                assert proc is not None
                assert proc.stdout is not None
                for raw in proc.stdout:
                    line_queue.put(raw)
            finally:
                line_queue.put(None)

        reader = threading.Thread(
            target=_reader,
            name="ffmpeg-output-drain",
            daemon=True,
        )
        reader.start()

        last_bucket = -1
        highest_pct = 0
        reached_end = False

        now = time.monotonic()
        last_observed_activity = now
        last_heartbeat = now
        finalizing_since: float | None = None

        last_tmp_size = -1
        last_tmp_mtime_ns = -1
        reader_eof = False

        while True:
            got_line = False
            try:
                raw = line_queue.get(timeout=1.0)
                if raw is None:
                    reader_eof = True
                else:
                    got_line = True
                    now = time.monotonic()
                    last_observed_activity = now

                    line = raw.strip()
                    if line:
                        if line.startswith("out_time=") and item.duration > 0:
                            elapsed = parse_progress_seconds(
                                line.split("=", 1)[1]
                            )
                            pct = max(
                                0,
                                min(100, int(elapsed / item.duration * 100)),
                            )
                            highest_pct = max(highest_pct, pct)
                            bucket = pct // 5
                            if bucket != last_bucket:
                                last_bucket = bucket
                                print(
                                    f"    progress {pct:3d}%",
                                    flush=True,
                                )

                            if pct >= 99 and finalizing_since is None:
                                finalizing_since = now
                                print(
                                    "    99% reached — FFmpeg is finishing "
                                    "the stream / MP4 metadata...",
                                    flush=True,
                                )

                        elif line == "progress=end":
                            reached_end = True
                            if finalizing_since is None:
                                finalizing_since = now
                            print(
                                "    encoding complete — finalizing MP4...",
                                flush=True,
                            )

                        elif not (
                            line.startswith("frame=")
                            or line.startswith("fps=")
                            or line.startswith("stream_")
                            or line.startswith("bitrate=")
                            or line.startswith("total_size=")
                            or line.startswith("out_time_us=")
                            or line.startswith("out_time_ms=")
                            or line.startswith("dup_frames=")
                            or line.startswith("drop_frames=")
                            or line.startswith("speed=")
                            or line.startswith("progress=")
                        ):
                            # Keep a bounded diagnostic tail for useful errors.
                            diagnostics.append(line)

            except queue.Empty:
                pass

            # File changes count as real activity even when FFmpeg has no new
            # progress line while it is flushing/finalizing.
            try:
                stat = tmp.stat()
                size = int(stat.st_size)
                mtime_ns = int(stat.st_mtime_ns)
                if size != last_tmp_size or mtime_ns != last_tmp_mtime_ns:
                    last_tmp_size = size
                    last_tmp_mtime_ns = mtime_ns
                    last_observed_activity = time.monotonic()
            except OSError:
                pass

            now = time.monotonic()

            # Once the visible progress is at the end, say something periodically
            # so a healthy finalization never *looks* frozen.
            if (
                (highest_pct >= 99 or reached_end)
                and now - last_heartbeat >= HEARTBEAT_INTERVAL
            ):
                last_heartbeat = now
                wait_for = (
                    now - finalizing_since
                    if finalizing_since is not None
                    else 0.0
                )
                print(
                    f"    still finalizing... {wait_for:.0f}s "
                    f"(FFmpeg process is alive)",
                    flush=True,
                )

            rc = proc.poll()
            if rc is not None and reader_eof:
                break

            # This catches genuine stalls while avoiding a false positive during
            # ordinary finalization.  Because both the pipe and file metadata
            # are monitored, healthy work resets the timer.
            idle_for = now - last_observed_activity
            if rc is None and idle_for >= STALL_TIMEOUT:
                print(
                    f"    STALL: no FFmpeg output/progress/file changes for "
                    f"{int(idle_for)} seconds. Stopping FFmpeg safely...",
                    flush=True,
                )
                try:
                    proc.terminate()
                    proc.wait(timeout=10)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass

                try:
                    tmp.unlink(missing_ok=True)
                except Exception:
                    pass

                diag = "\n".join(diagnostics).strip()
                detail = (
                    f"FFmpeg appeared stalled for {int(idle_for)} seconds "
                    "with no output or temporary-file changes."
                )
                if diag:
                    detail += "\n\nLast FFmpeg messages:\n" + diag
                return False, detail

        reader.join(timeout=2.0)
        rc = proc.wait()

        if rc != 0:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
            diag = "\n".join(diagnostics).strip()
            return False, diag or f"ffmpeg exited with code {rc}"

        if not tmp.exists() or tmp.stat().st_size <= 0:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
            return False, "ffmpeg produced no usable output file."

        print("    validating finished MP4...", flush=True)

        # Validate that ffprobe can read the result before replacing anything.
        check = ffprobe_basic(tmp)
        if check["duration"] <= 0 and item.duration > 0:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
            return False, "Output validation failed: duration could not be read."

        os.replace(tmp, output)
        print("    MP4 finalized and validated.", flush=True)
        return True, "ok"

    except Exception as exc:
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        return False, str(exc)


def process_batch_terminal(
    items: list[VideoItem],
    root: Path,
) -> tuple[int, int, list[str]]:
    ensure_console()
    history = FolderHistory(root)
    approved = [x for x in items if x.approved]
    skipped = [x for x in items if not x.approved]

    print("\n" + "=" * 84, flush=True)
    print("MP4 PAINT-STYLE CROPPER - EXPORT", flush=True)
    print(f"Approved for crop: {len(approved)}", flush=True)
    print(f"Reviewed but skipped: {len(skipped)}", flush=True)
    print("=" * 84, flush=True)

    # An unapproved video becomes a finalized "reviewed_skipped" history entry.
    # It stays on disk, but normal future scans skip it unless history is ignored.
    for item in skipped:
        history.mark(
            item.path,
            "reviewed_skipped",
            signature=item.original_signature,
            crop=item.crop.as_list(),
        )

    successes = 0
    failures = 0
    messages: list[str] = []

    for idx, item in enumerate(approved, 1):
        output = unique_output_path(item.path)
        box = normalize_even_crop(item.crop, item.width, item.height)

        print(
            f"\n[{idx}/{len(approved)}] {item.path.name}\n"
            f"    crop x={box.x0}, y={box.y0}, "
            f"w={box.width}, h={box.height}\n"
            f"    output {output.name}",
            flush=True,
        )

        ok, detail = run_ffmpeg_crop(item, output, copy_audio=True)
        if not ok:
            print("    Audio stream copy failed or export failed; retrying with AAC audio...", flush=True)
            ok, detail = run_ffmpeg_crop(item, output, copy_audio=False)

        if not ok:
            failures += 1
            history.mark(
                item.path,
                "process_failed",
                signature=item.original_signature,
                crop=box.as_list(),
                error=detail,
            )
            messages.append(f"{item.path.name}: export failed: {detail}")
            print(f"    FAILED: {detail}", flush=True)
            continue

        # New file is complete and validated. Only now touch the original.
        try:
            send2trash(str(item.path))
        except Exception as exc:
            failures += 1
            history.mark(
                item.path,
                "output_created_trash_failed",
                signature=item.original_signature,
                crop=box.as_list(),
                output=output.name,
                error=str(exc),
            )
            messages.append(
                f"{item.path.name}: cropped output created as {output.name}, "
                f"but original could not be moved to Recycle Bin: {exc}"
            )
            print(
                f"    OUTPUT OK, BUT COULD NOT RECYCLE ORIGINAL: {exc}",
                flush=True,
            )
            continue

        successes += 1
        history.mark(
            item.path,
            "processed",
            signature=item.original_signature,
            crop=box.as_list(),
            output=output.name,
        )
        print("    DONE: cropped MP4 saved; original moved to Recycle Bin.", flush=True)

        # Persist after every source, so a crash later in a large batch does not
        # lose the already-completed history.
        try:
            history.save()
        except Exception as exc:
            print(f"    [WARN] Could not update history JSON: {exc}", flush=True)

    try:
        history.save()
    except Exception as exc:
        messages.append(f"Could not save history JSON: {exc}")

    print("\n" + "-" * 84, flush=True)
    print(f"Successfully cropped/recycled: {successes}", flush=True)
    print(f"Failures or recycle problems: {failures}", flush=True)
    print(f"Reviewed but intentionally skipped: {len(skipped)}", flush=True)
    print("=" * 84 + "\n", flush=True)

    return successes, failures, messages


class Launcher(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("MP4 Paint-Style Cropper")
        self.geometry("650x360")
        self.resizable(False, False)

        self.request: ScanRequest | None = None
        self.ignore_history = False
        self.recursive = tk.BooleanVar(value=False)
        self.ignore_text = tk.StringVar(value="Ignore History This Run: OFF")

        outer = ttk.Frame(self, padding=22)
        outer.pack(fill="both", expand=True)

        ttk.Label(
            outer,
            text="MP4 Paint-Style Cropper",
            font=("Segoe UI", 18, "bold"),
        ).pack(anchor="w")

        ttk.Label(
            outer,
            text=(
                "Choose one MP4 or a folder. Draw a Paint-style crop frame, "
                "review it on different video frames, approve, then export. "
                "Audio stays with the cropped video."
            ),
            wraplength=600,
        ).pack(anchor="w", pady=(6, 18))

        row = ttk.Frame(outer)
        row.pack(fill="x")
        ttk.Button(
            row, text="Choose MP4 File…", command=self.choose_file
        ).pack(side="left", fill="x", expand=True, padx=(0, 6), ipady=8)
        ttk.Button(
            row, text="Choose Folder…", command=self.choose_folder
        ).pack(side="left", fill="x", expand=True, padx=(6, 0), ipady=8)

        ttk.Checkbutton(
            outer,
            text="Include subfolders when a directory is chosen",
            variable=self.recursive,
        ).pack(anchor="w", pady=(16, 8))

        history_row = ttk.Frame(outer)
        history_row.pack(fill="x", pady=(4, 0))

        self.ignore_button = ttk.Button(
            history_row,
            textvariable=self.ignore_text,
            command=self.toggle_ignore_history,
        )
        self.ignore_button.pack(side="left", fill="x", expand=True, padx=(0, 6))

        ttk.Button(
            history_row,
            text="Forget Folder History…",
            command=self.forget_history,
        ).pack(side="left", fill="x", expand=True, padx=(6, 0))

        ttk.Label(
            outer,
            text=(
                "History skips unchanged videos you already finalized. "
                "Ignore History bypasses it for one run without deleting anything."
            ),
            foreground="#555",
            wraplength=600,
        ).pack(anchor="w", pady=(10, 0))

        ffmpeg, ffprobe = find_ffmpeg()
        tool_status = (
            "FFmpeg and ffprobe detected."
            if ffmpeg and ffprobe
            else "FFmpeg/ffprobe not found. Install FFmpeg before processing."
        )
        ttk.Label(outer, text=tool_status, foreground="#555").pack(anchor="w", pady=(17, 0))

    def toggle_ignore_history(self):
        self.ignore_history = not self.ignore_history
        self.ignore_text.set(
            f"Ignore History This Run: {'ON' if self.ignore_history else 'OFF'}"
        )

    def choose_file(self):
        if not require_ffmpeg(self):
            return
        p = filedialog.askopenfilename(
            parent=self,
            title="Choose MP4",
            filetypes=[("MP4 video", "*.mp4"), ("All files", "*.*")],
        )
        if not p:
            return
        path = Path(p)
        if path.suffix.lower() != ".mp4":
            messagebox.showerror("MP4 required", "Choose an .mp4 file.", parent=self)
            return
        if is_generated_output(path):
            if not messagebox.askyesno(
                "Generated-looking file",
                "This filename looks like an output previously created by this app.\n\n"
                "Open it anyway?",
                parent=self,
            ):
                return
        self.request = ScanRequest(
            mode="file",
            target=path,
            recursive=False,
            ignore_history=self.ignore_history,
        )
        self.destroy()

    def choose_folder(self):
        if not require_ffmpeg(self):
            return
        p = filedialog.askdirectory(parent=self, title="Choose folder containing MP4 files")
        if not p:
            return
        self.request = ScanRequest(
            mode="folder",
            target=Path(p),
            recursive=bool(self.recursive.get()),
            ignore_history=self.ignore_history,
        )
        self.destroy()

    def forget_history(self):
        p = filedialog.askdirectory(
            parent=self,
            title="Choose folder whose MP4 crop history should be forgotten",
        )
        if not p:
            return
        root = Path(p)
        history = FolderHistory(root)
        count = len(history.data.get("entries", {}))
        if not messagebox.askyesno(
            "Forget folder history",
            f"Delete saved MP4 crop history for:\n{root}\n\n"
            f"Remembered entries: {count}\n\n"
            "This does not delete or modify any videos.",
            parent=self,
        ):
            return
        try:
            history.forget()
            messagebox.showinfo(
                "History forgotten",
                "The next normal run will treat the videos in this folder as unseen.",
                parent=self,
            )
        except Exception as exc:
            messagebox.showerror("Could not forget history", str(exc), parent=self)


class CropReviewApp(tk.Tk):
    def __init__(self, items: list[VideoItem]):
        super().__init__()
        self.title("Review MP4 Crops")
        self.geometry("1280x850")
        self.minsize(950, 650)

        self.items = items
        self.index = 0
        self.current_frame: Image.Image | None = None
        self.current_tk: ImageTk.PhotoImage | None = None

        self.img_scale = 1.0
        self.img_ox = 0.0
        self.img_oy = 0.0

        self.drag_mode: str | None = None
        self.drag_handle: str | None = None
        self.drag_start_img = (0.0, 0.0)
        self.drag_start_box: CropBox | None = None
        self.new_start = (0.0, 0.0)

        self.seek_var = tk.DoubleVar(value=0.0)
        self.time_var = tk.StringVar(value="")
        self.header_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="")

        self.process_requested = False

        self._build()
        self._bind_shortcuts()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(60, lambda: self.load_item(0))

    def _build(self):
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")

        ttk.Button(top, text="‹ Previous  [P / ←]", command=self.previous).pack(side="left")

        # Requested convenience: approve and next are immediately adjacent.
        ttk.Button(
            top,
            text="✓ Approve + Next  [A]",
            command=self.approve_and_next,
        ).pack(side="left", padx=(12, 2))
        ttk.Button(
            top,
            text="Next  [N / →] ›",
            command=self.next_item,
        ).pack(side="left", padx=(2, 10))

        ttk.Button(
            top,
            text="Reset Full Frame  [R]",
            command=self.reset_full,
        ).pack(side="left")

        ttk.Button(
            top,
            text="Play Original",
            command=self.play_original,
        ).pack(side="left", padx=(8, 0))

        ttk.Button(
            top,
            text="Finish Review / Process Approved…",
            command=self.finish_review,
        ).pack(side="right")

        ttk.Label(top, textvariable=self.header_var).pack(side="right", padx=12)

        seek = ttk.Frame(self, padding=(8, 0, 8, 8))
        seek.pack(fill="x")
        ttk.Label(seek, text="Inspect another moment:").pack(side="left")
        self.seek_scale = ttk.Scale(seek, from_=0.0, to=1.0, variable=self.seek_var)
        self.seek_scale.pack(side="left", fill="x", expand=True, padx=8)
        self.seek_scale.bind("<ButtonRelease-1>", lambda e: self.load_slider_frame())
        ttk.Button(seek, text="Load Frame", command=self.load_slider_frame).pack(side="left")
        ttk.Label(seek, textvariable=self.time_var, width=20).pack(side="left", padx=(8, 0))

        self.canvas = tk.Canvas(
            self,
            background="#202020",
            highlightthickness=0,
            cursor="crosshair",
        )
        self.canvas.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        self.canvas.bind("<Configure>", lambda e: self.redraw())
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Motion>", self.on_motion)

        bottom = ttk.Frame(self, padding=8)
        bottom.pack(fill="x")
        ttk.Label(
            bottom,
            textvariable=self.status_var,
        ).pack(side="left", fill="x", expand=True)

        ttk.Label(
            bottom,
            text=(
                "A = approve + next   N/→ = next   P/← = previous   "
                "R = full frame   Ctrl+Arrows = nudge"
            ),
            foreground="#555",
        ).pack(side="right")

    def _bind_shortcuts(self):
        for key in ("<KeyPress-a>", "<KeyPress-A>"):
            self.bind(key, lambda e: self.approve_and_next())
        for key in ("<KeyPress-n>", "<KeyPress-N>", "<Right>"):
            self.bind(key, lambda e: self.next_item())
        for key in ("<KeyPress-p>", "<KeyPress-P>", "<Left>"):
            self.bind(key, lambda e: self.previous())
        for key in ("<KeyPress-r>", "<KeyPress-R>"):
            self.bind(key, lambda e: self.reset_full())

        self.bind("<Control-Left>", lambda e: self.nudge(-1, 0))
        self.bind("<Control-Right>", lambda e: self.nudge(1, 0))
        self.bind("<Control-Up>", lambda e: self.nudge(0, -1))
        self.bind("<Control-Down>", lambda e: self.nudge(0, 1))
        self.bind("<Control-Shift-Left>", lambda e: self.nudge(-10, 0))
        self.bind("<Control-Shift-Right>", lambda e: self.nudge(10, 0))
        self.bind("<Control-Shift-Up>", lambda e: self.nudge(0, -10))
        self.bind("<Control-Shift-Down>", lambda e: self.nudge(0, 10))

    @staticmethod
    def fmt_time(seconds: float) -> str:
        seconds = max(0, int(round(seconds)))
        hh = seconds // 3600
        mm = (seconds % 3600) // 60
        ss = seconds % 60
        return f"{hh}:{mm:02d}:{ss:02d}" if hh else f"{mm}:{ss:02d}"

    def current(self) -> VideoItem:
        return self.items[self.index]

    def load_item(self, index: int):
        self.index = max(0, min(index, len(self.items) - 1))
        item = self.current()

        try:
            self.configure(cursor="watch")
            self.update_idletasks()
            frame = extract_preview_frame(item.path, item.preview_time)
        except Exception as exc:
            messagebox.showerror(
                "Could not load preview",
                f"{item.path.name}\n\n{exc}",
                parent=self,
            )
            return
        finally:
            self.configure(cursor="")

        self.current_frame = frame

        # Usually identical to the prepared dimensions. If not, use the actual
        # displayed frame dimensions and clamp the crop.
        if (frame.width, frame.height) != (item.width, item.height):
            old_w, old_h = item.width, item.height
            if old_w > 0 and old_h > 0:
                sx, sy = frame.width / old_w, frame.height / old_h
                item.crop = CropBox(
                    round(item.crop.x0 * sx),
                    round(item.crop.y0 * sy),
                    round(item.crop.x1 * sx),
                    round(item.crop.y1 * sy),
                )
            item.width, item.height = frame.size
            item.crop = self.clamp_box(item.crop)

        self.seek_scale.configure(to=max(0.001, item.duration))
        self.seek_var.set(item.preview_time)
        self._update_labels()
        self.redraw()

    def _update_labels(self):
        item = self.current()
        state = "APPROVED" if item.approved else "not approved"
        self.header_var.set(
            f"{self.index + 1}/{len(self.items)}  •  {item.path.name}  •  {state}"
        )
        self.time_var.set(
            f"{self.fmt_time(item.preview_time)} / {self.fmt_time(item.duration)}"
        )
        self.status_var.set(
            f"Crop: {item.crop.width}×{item.crop.height} from "
            f"{item.width}×{item.height}. "
            "Drag inside to move; drag a white handle to resize; "
            "drag outside to draw a replacement crop."
        )

    def load_slider_frame(self):
        item = self.current()
        t = max(0.0, min(item.duration, float(self.seek_var.get())))
        try:
            self.configure(cursor="watch")
            self.update_idletasks()
            frame = extract_preview_frame(item.path, t)
            item.preview_time = t
            self.current_frame = frame
            self._update_labels()
            self.redraw()
        except Exception as exc:
            messagebox.showerror("Preview failed", str(exc), parent=self)
        finally:
            self.configure(cursor="")

    def previous(self):
        if self.index > 0:
            self.load_item(self.index - 1)

    def next_item(self):
        if self.index < len(self.items) - 1:
            self.load_item(self.index + 1)
        else:
            self.status_var.set(
                "This is the last video. Use Finish Review / Process Approved when ready."
            )

    def approve_and_next(self):
        self.current().approved = True
        self._update_labels()
        if self.index < len(self.items) - 1:
            self.load_item(self.index + 1)
        else:
            self.status_var.set(
                "Last video approved. Use Finish Review / Process Approved to export."
            )

    def reset_full(self):
        item = self.current()
        item.crop = CropBox(0, 0, item.width, item.height)
        item.approved = False
        self._update_labels()
        self.redraw()

    def play_original(self):
        path = self.current().path
        try:
            if os.name == "nt":
                os.startfile(str(path))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:
            messagebox.showerror("Could not open video", str(exc), parent=self)

    def finish_review(self):
        approved = sum(1 for x in self.items if x.approved)
        skipped = len(self.items) - approved

        if not messagebox.askyesno(
            "Finish review",
            f"Approved for cropping: {approved}\n"
            f"Reviewed but not approved: {skipped}\n\n"
            "Approved videos will be exported in terminal mode. "
            "Each original will move to the Windows Recycle Bin only after "
            "its cropped MP4 succeeds.\n\n"
            "Unapproved videos will remain untouched but be remembered as "
            "reviewed/skipped for future runs.\n\n"
            "Continue?",
            parent=self,
        ):
            return

        self.process_requested = True
        self.destroy()

    def on_close(self):
        # Do not finalize history on a simple window close. pending_review
        # entries intentionally reappear next time.
        if messagebox.askyesno(
            "Close without processing?",
            "Close the review window?\n\n"
            "No originals will be changed. These pending videos will appear "
            "again on a future normal run.",
            parent=self,
        ):
            self.process_requested = False
            self.destroy()

    def clamp_box(self, box: CropBox) -> CropBox:
        item = self.current()
        b = box.normalized()
        if item.width < MIN_CROP or item.height < MIN_CROP:
            return CropBox(0, 0, item.width, item.height)

        x0 = max(0, min(item.width - MIN_CROP, int(round(b.x0))))
        y0 = max(0, min(item.height - MIN_CROP, int(round(b.y0))))
        x1 = max(x0 + MIN_CROP, min(item.width, int(round(b.x1))))
        y1 = max(y0 + MIN_CROP, min(item.height, int(round(b.y1))))
        return CropBox(x0, y0, x1, y1)

    def to_canvas(self, x: float, y: float) -> tuple[float, float]:
        return (
            self.img_ox + x * self.img_scale,
            self.img_oy + y * self.img_scale,
        )

    def to_image(self, cx: float, cy: float) -> tuple[float, float]:
        return (
            (cx - self.img_ox) / self.img_scale,
            (cy - self.img_oy) / self.img_scale,
        )

    def clamp_point(self, x: float, y: float) -> tuple[float, float]:
        item = self.current()
        return (
            max(0.0, min(float(item.width), x)),
            max(0.0, min(float(item.height), y)),
        )

    def redraw(self):
        if self.current_frame is None:
            return

        self.canvas.delete("all")
        cw = max(100, self.canvas.winfo_width())
        ch = max(100, self.canvas.winfo_height())
        iw, ih = self.current_frame.size

        scale = min(
            max(0.01, (cw - 2 * PREVIEW_MARGIN) / max(1, iw)),
            max(0.01, (ch - 2 * PREVIEW_MARGIN) / max(1, ih)),
        )
        pw = max(1, round(iw * scale))
        ph = max(1, round(ih * scale))

        display = self.current_frame.resize((pw, ph), Image.Resampling.LANCZOS)
        self.current_tk = ImageTk.PhotoImage(display)

        self.img_scale = scale
        self.img_ox = (cw - pw) / 2
        self.img_oy = (ch - ph) / 2

        self.canvas.create_image(
            self.img_ox,
            self.img_oy,
            anchor="nw",
            image=self.current_tk,
        )

        b = self.current().crop.normalized()
        x0, y0 = self.to_canvas(b.x0, b.y0)
        x1, y1 = self.to_canvas(b.x1, b.y1)
        right = self.img_ox + pw
        bottom = self.img_oy + ph

        # Darken the discarded region.
        for rect in (
            (self.img_ox, self.img_oy, right, y0),
            (self.img_ox, y1, right, bottom),
            (self.img_ox, y0, x0, y1),
            (x1, y0, right, y1),
        ):
            self.canvas.create_rectangle(
                *rect,
                fill="black",
                stipple="gray50",
                outline="",
            )

        # Paint-like marching selection border.
        self.canvas.create_rectangle(
            x0, y0, x1, y1,
            outline="white",
            width=3,
            dash=(6, 4),
        )
        self.canvas.create_rectangle(
            x0 + 1, y0 + 1, x1 - 1, y1 - 1,
            outline="black",
            width=1,
            dash=(6, 4),
        )

        for _, hx, hy in self.handle_positions_canvas(b):
            self.canvas.create_rectangle(
                hx - HANDLE_RADIUS,
                hy - HANDLE_RADIUS,
                hx + HANDLE_RADIUS,
                hy + HANDLE_RADIUS,
                fill="white",
                outline="black",
                width=1,
            )

        self.canvas.create_text(
            x0 + 8,
            y0 + 8,
            anchor="nw",
            text=f"{b.width} × {b.height}",
            fill="white",
            font=("Segoe UI", 10, "bold"),
        )

    def handle_positions_canvas(self, b: CropBox):
        x0, y0 = self.to_canvas(b.x0, b.y0)
        x1, y1 = self.to_canvas(b.x1, b.y1)
        xm = (x0 + x1) / 2
        ym = (y0 + y1) / 2
        return [
            ("nw", x0, y0),
            ("n", xm, y0),
            ("ne", x1, y0),
            ("e", x1, ym),
            ("se", x1, y1),
            ("s", xm, y1),
            ("sw", x0, y1),
            ("w", x0, ym),
        ]

    def hit_handle(self, cx: float, cy: float) -> str | None:
        b = self.current().crop.normalized()
        for name, hx, hy in self.handle_positions_canvas(b):
            if (
                abs(cx - hx) <= HANDLE_RADIUS + 4
                and abs(cy - hy) <= HANDLE_RADIUS + 4
            ):
                return name
        return None

    def inside_crop(self, ix: float, iy: float) -> bool:
        b = self.current().crop.normalized()
        return b.x0 <= ix <= b.x1 and b.y0 <= iy <= b.y1

    def on_press(self, event):
        item = self.current()
        ix, iy = self.to_image(event.x, event.y)
        ix, iy = self.clamp_point(ix, iy)

        handle = self.hit_handle(event.x, event.y)
        self.drag_start_img = (ix, iy)
        self.drag_start_box = item.crop.normalized()

        if handle is not None:
            self.drag_mode = "resize"
            self.drag_handle = handle
        elif self.inside_crop(ix, iy):
            self.drag_mode = "move"
            self.drag_handle = None
        else:
            self.drag_mode = "new"
            self.drag_handle = None
            self.new_start = (ix, iy)
            item.crop = CropBox(round(ix), round(iy), round(ix), round(iy))

        # Any crop edit requires explicit re-approval.
        item.approved = False
        self._update_labels()

    def on_drag(self, event):
        if self.drag_mode is None:
            return

        item = self.current()
        ix, iy = self.to_image(event.x, event.y)
        ix, iy = self.clamp_point(ix, iy)

        if self.drag_mode == "new":
            sx, sy = self.new_start
            item.crop = CropBox(
                int(round(sx)),
                int(round(sy)),
                int(round(ix)),
                int(round(iy)),
            ).normalized()

        elif self.drag_mode == "move" and self.drag_start_box is not None:
            sx, sy = self.drag_start_img
            dx = ix - sx
            dy = iy - sy
            b = self.drag_start_box
            w, h = b.width, b.height

            nx0 = max(0.0, min(item.width - w, b.x0 + dx))
            ny0 = max(0.0, min(item.height - h, b.y0 + dy))
            item.crop = CropBox(
                int(round(nx0)),
                int(round(ny0)),
                int(round(nx0 + w)),
                int(round(ny0 + h)),
            )

        elif self.drag_mode == "resize" and self.drag_start_box is not None:
            b = self.drag_start_box
            x0, y0, x1, y1 = b.x0, b.y0, b.x1, b.y1
            h = self.drag_handle or ""

            if "w" in h:
                x0 = int(round(ix))
            if "e" in h:
                x1 = int(round(ix))
            if "n" in h:
                y0 = int(round(iy))
            if "s" in h:
                y1 = int(round(iy))

            # Edge-only handles do not contain compass letters on both axes.
            if h == "n":
                y0 = int(round(iy))
            elif h == "s":
                y1 = int(round(iy))
            elif h == "w":
                x0 = int(round(ix))
            elif h == "e":
                x1 = int(round(ix))

            item.crop = CropBox(x0, y0, x1, y1).normalized()

        self.redraw()

    def on_release(self, event):
        if self.drag_mode is not None:
            self.current().crop = self.clamp_box(self.current().crop)
            self.drag_mode = None
            self.drag_handle = None
            self.drag_start_box = None
            self._update_labels()
            self.redraw()

    def on_motion(self, event):
        handle = self.hit_handle(event.x, event.y)
        cursors = {
            "nw": "size_nw_se",
            "se": "size_nw_se",
            "ne": "size_ne_sw",
            "sw": "size_ne_sw",
            "n": "sb_v_double_arrow",
            "s": "sb_v_double_arrow",
            "e": "sb_h_double_arrow",
            "w": "sb_h_double_arrow",
        }
        if handle:
            # Not every Tk/Windows combination exposes modern diagonal cursor
            # names, so fall back harmlessly.
            try:
                self.canvas.configure(cursor=cursors.get(handle, "crosshair"))
            except tk.TclError:
                self.canvas.configure(cursor="crosshair")
            return

        ix, iy = self.to_image(event.x, event.y)
        if self.inside_crop(ix, iy):
            self.canvas.configure(cursor="fleur")
        else:
            self.canvas.configure(cursor="crosshair")

    def nudge(self, dx: int, dy: int):
        item = self.current()
        b = item.crop.normalized()
        w, h = b.width, b.height

        nx0 = max(0, min(item.width - w, b.x0 + dx))
        ny0 = max(0, min(item.height - h, b.y0 + dy))
        item.crop = CropBox(nx0, ny0, nx0 + w, ny0 + h)
        item.approved = False
        self._update_labels()
        self.redraw()


def show_info(title: str, text: str) -> None:
    root = tk.Tk()
    root.withdraw()
    try:
        messagebox.showinfo(title, text, parent=root)
    finally:
        root.destroy()


def show_warning(title: str, text: str) -> None:
    root = tk.Tk()
    root.withdraw()
    try:
        messagebox.showwarning(title, text, parent=root)
    finally:
        root.destroy()


def main() -> None:
    ensure_console()

    while True:
        launcher = Launcher()
        launcher.mainloop()
        request = launcher.request
        if request is None:
            return

        # Launcher is already destroyed: preparation happens terminal-only.
        items, root, scan_summary = scan_request_terminal(request)

        if not items:
            show_info(
                "Nothing to review",
                scan_summary + "\n\n"
                "Use Ignore History This Run if you want previously finalized "
                "videos to appear again.",
            )
            continue

        review = CropReviewApp(items)
        review.mainloop()

        if not review.process_requested:
            # pending_review history remains intentionally non-final.
            continue

        # Review GUI has closed. Export runs terminal-only, so FFmpeg work does
        # not make the Tk window appear frozen.
        successes, failures, messages = process_batch_terminal(items, root)

        if failures or messages:
            details = "\n".join(messages[:10])
            if len(messages) > 10:
                details += f"\n...and {len(messages) - 10} more."
            show_warning(
                "Batch finished with issues",
                f"Successfully cropped/recycled: {successes}\n"
                f"Problems: {failures}\n\n{details}",
            )
        else:
            show_info(
                "Batch complete",
                f"Successfully cropped {successes} approved video(s).\n\n"
                "Each successful source video was moved to the Windows Recycle Bin. "
                "Unapproved videos were left untouched and remembered as reviewed/skipped.",
            )


if __name__ == "__main__":
    main()
