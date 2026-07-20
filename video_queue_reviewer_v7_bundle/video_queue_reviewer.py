from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import uuid
from collections import deque
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk


# ---------------------------------------------------------------------------
# VLC runtime discovery
# ---------------------------------------------------------------------------
#
# `pip install python-vlc` installs only Python's bindings. The native VLC
# application/runtime must also be installed because it supplies libvlc.dll,
# libvlccore.dll, and the plugins directory.
#
# For a portable or nonstandard VLC installation, either:
#   1. Set VLC_DIRECTORY_OVERRIDE below, or
#   2. Set the Windows environment variable VLC_DIR.
#
VLC_DIRECTORY_OVERRIDE: Path | None = None

# Keep the handle alive for the lifetime of the process. Closing the handle
# removes the directory from Windows' DLL search path.
_VLC_DLL_DIRECTORY_HANDLE = None


def _registry_vlc_directories() -> list[Path]:
    """Read common VLC installation keys from the Windows registry."""
    if sys.platform != "win32":
        return []

    try:
        import winreg
    except ImportError:
        return []

    directories: list[Path] = []
    roots = (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER)
    key_names = (
        r"SOFTWARE\VideoLAN\VLC",
        r"SOFTWARE\WOW6432Node\VideoLAN\VLC",
    )
    registry_views = (
        getattr(winreg, "KEY_WOW64_64KEY", 0),
        getattr(winreg, "KEY_WOW64_32KEY", 0),
    )

    for root in roots:
        for key_name in key_names:
            for registry_view in registry_views:
                try:
                    access = winreg.KEY_READ | registry_view
                    with winreg.OpenKey(root, key_name, 0, access) as key:
                        install_dir, _ = winreg.QueryValueEx(key, "InstallDir")
                    if install_dir:
                        directories.append(Path(install_dir))
                except OSError:
                    continue

    return directories


def _candidate_vlc_directories() -> list[Path]:
    """Return likely VLC directories, preserving order and removing duplicates."""
    candidates: list[Path] = []

    if VLC_DIRECTORY_OVERRIDE is not None:
        candidates.append(Path(VLC_DIRECTORY_OVERRIDE))

    for variable_name in (
        "VLC_DIR",
        "PYTHON_VLC_MODULE_PATH",
    ):
        value = os.environ.get(variable_name)
        if value:
            candidates.append(Path(value))

    explicit_library = os.environ.get("PYTHON_VLC_LIB_PATH")
    if explicit_library:
        candidates.append(Path(explicit_library).parent)

    candidates.extend(_registry_vlc_directories())

    for environment_name in (
        "ProgramFiles",
        "ProgramFiles(x86)",
        "LOCALAPPDATA",
    ):
        base = os.environ.get(environment_name)
        if not base:
            continue
        base_path = Path(base)
        candidates.extend(
            (
                base_path / "VideoLAN" / "VLC",
                base_path / "Programs" / "VideoLAN" / "VLC",
                base_path / "VLC",
            )
        )

    vlc_executable = shutil.which("vlc.exe") or shutil.which("vlc")
    if vlc_executable:
        candidates.append(Path(vlc_executable).resolve().parent)

    # Common absolute fallbacks are useful when environment variables have
    # been customized or removed.
    candidates.extend(
        (
            Path(r"C:\Program Files\VideoLAN\VLC"),
            Path(r"C:\Program Files (x86)\VideoLAN\VLC"),
        )
    )

    unique_candidates: list[Path] = []
    seen: set[str] = set()

    for candidate in candidates:
        try:
            normalized = candidate.expanduser().resolve()
        except OSError:
            normalized = candidate.expanduser()

        key = os.fspath(normalized).casefold()
        if key not in seen:
            seen.add(key)
            unique_candidates.append(normalized)

    return unique_candidates


def _find_vlc_directory() -> Path | None:
    """Find a VLC directory containing the required native files."""
    for directory in _candidate_vlc_directories():
        if (
            directory.is_dir()
            and (directory / "libvlc.dll").is_file()
            and (directory / "libvlccore.dll").is_file()
            and (directory / "plugins").is_dir()
        ):
            return directory
    return None


def _read_pe_architecture(dll_path: Path) -> str | None:
    """Read the DLL's Windows PE machine type without third-party packages."""
    machine_names = {
        0x014C: "32-bit x86",
        0x8664: "64-bit x64",
        0xAA64: "64-bit ARM",
    }

    try:
        with dll_path.open("rb") as dll:
            if dll.read(2) != b"MZ":
                return None

            dll.seek(0x3C)
            pe_offset_bytes = dll.read(4)
            if len(pe_offset_bytes) != 4:
                return None

            pe_offset = struct.unpack("<I", pe_offset_bytes)[0]
            dll.seek(pe_offset)

            if dll.read(4) != b"PE\0\0":
                return None

            machine_bytes = dll.read(2)
            if len(machine_bytes) != 2:
                return None

            machine = struct.unpack("<H", machine_bytes)[0]
            return machine_names.get(machine, f"unknown PE machine 0x{machine:04X}")
    except OSError:
        return None


def _configure_vlc_runtime() -> Path:
    """Configure Windows so python-vlc can load VLC's native DLLs."""
    global _VLC_DLL_DIRECTORY_HANDLE

    if sys.platform != "win32":
        raise SystemExit("This application is configured specifically for Windows.")

    vlc_directory = _find_vlc_directory()
    if vlc_directory is None:
        searched = "\n".join(
            f"  - {candidate}" for candidate in _candidate_vlc_directories()
        )
        raise SystemExit(
            "python-vlc is installed, but the native VLC desktop runtime was "
            "not found.\n\n"
            "Install the official desktop version of VLC Media Player, or set "
            "VLC_DIRECTORY_OVERRIDE near the top of this script to the folder "
            "that contains libvlc.dll.\n\n"
            "Typical folder:\n"
            r"  C:\Program Files\VideoLAN\VLC"
            "\n\nDirectories checked:\n"
            f"{searched or '  (none)'}"
        )

    libvlc_path = vlc_directory / "libvlc.dll"

    python_architecture = f"{struct.calcsize('P') * 8}-bit"
    vlc_architecture = _read_pe_architecture(libvlc_path)

    if (
        ("32-bit" in python_architecture and vlc_architecture == "64-bit x64")
        or ("64-bit" in python_architecture and vlc_architecture == "32-bit x86")
    ):
        raise SystemExit(
            "Python and VLC use different architectures.\n\n"
            f"Python: {python_architecture}\n"
            f"VLC: {vlc_architecture}\n\n"
            "Install a VLC build whose architecture matches this Python."
        )

    # python-vlc supports these environment variables before `import vlc`.
    os.environ["PYTHON_VLC_LIB_PATH"] = os.fspath(libvlc_path)
    os.environ["PYTHON_VLC_MODULE_PATH"] = os.fspath(vlc_directory)
    os.environ["VLC_PLUGIN_PATH"] = os.fspath(vlc_directory / "plugins")
    os.environ["PATH"] = (
        os.fspath(vlc_directory)
        + os.pathsep
        + os.environ.get("PATH", "")
    )

    # Python 3.8+ uses this explicit DLL search API for ctypes dependencies.
    if hasattr(os, "add_dll_directory"):
        _VLC_DLL_DIRECTORY_HANDLE = os.add_dll_directory(
            os.fspath(vlc_directory)
        )

    return vlc_directory


VLC_DIRECTORY = _configure_vlc_runtime()

try:
    import vlc
except ImportError as exc:
    raise SystemExit(
        "The 'python-vlc' package is not installed.\n"
        "Run: python -m pip install python-vlc"
    ) from exc
except (OSError, FileNotFoundError) as exc:
    raise SystemExit(
        "VLC was found, but Windows could not load libvlc.dll or one of its "
        "dependencies.\n\n"
        f"VLC directory: {VLC_DIRECTORY}\n"
        f"Python architecture: {struct.calcsize('P') * 8}-bit\n"
        f"VLC DLL architecture: "
        f"{_read_pe_architecture(VLC_DIRECTORY / 'libvlc.dll')}\n\n"
        "Make sure VLC and Python have matching architectures and that this is "
        "the full desktop VLC installation, not only the Microsoft Store app.\n\n"
        f"Original error: {exc}"
    ) from exc
except SystemExit as exc:
    raise SystemExit(
        "python-vlc could not initialize the native VLC runtime.\n\n"
        f"VLC directory: {VLC_DIRECTORY}\n"
        f"Python architecture: {struct.calcsize('P') * 8}-bit\n"
        f"VLC DLL architecture: "
        f"{_read_pe_architecture(VLC_DIRECTORY / 'libvlc.dll')}\n\n"
        "Make sure VLC and Python have matching architectures."
    ) from exc


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

VIDEO_FOLDER = Path(r"C:\Users\qacer\Downloads\new\yoonsul vids")

VIDEO_EXTENSIONS = {
    ".3gp", ".asf", ".avi", ".divx", ".f4v", ".flv", ".m2ts", ".m4v",
    ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".mts", ".ogv", ".rm",
    ".rmvb", ".ts", ".vob", ".webm", ".wmv",
}

KEEP_PREFIX = "keep_"
DELETE_SAVED_FRAME_PREFIX = "to_be_deleted_already_save_one_frame_"
DELETE_NO_FRAME_PREFIX = "to_be_deleted_"
SNAPSHOT_EXTENSION = ".png"

# Prevent already-processed videos from returning to the queue after restart.
SKIP_ALREADY_PROCESSED = True

# Exact frame extraction and clip export use FFmpeg. This avoids disturbing
# VLC playback and fixes wrong-frame/keyframe behavior near the video end.
#
# Set this when ffmpeg.exe is not on PATH:
#     FFMPEG_DIRECTORY_OVERRIDE = Path(r"C:\ffmpeg\bin")
#
FFMPEG_DIRECTORY_OVERRIDE: Path | None = None


def _find_ffmpeg_executable() -> Path | None:
    candidates: list[Path] = []

    if FFMPEG_DIRECTORY_OVERRIDE is not None:
        override = Path(FFMPEG_DIRECTORY_OVERRIDE)
        candidates.append(
            override if override.name.casefold() == "ffmpeg.exe"
            else override / "ffmpeg.exe"
        )

    for variable_name in ("FFMPEG_DIR", "FFMPEG_HOME"):
        value = os.environ.get(variable_name)
        if value:
            directory = Path(value)
            candidates.extend(
                (
                    directory / "ffmpeg.exe",
                    directory / "bin" / "ffmpeg.exe",
                )
            )

    discovered = shutil.which("ffmpeg.exe") or shutil.which("ffmpeg")
    if discovered:
        candidates.append(Path(discovered))

    user_profile = os.environ.get("USERPROFILE")
    local_app_data = os.environ.get("LOCALAPPDATA")
    program_files = os.environ.get("ProgramFiles")
    program_data = os.environ.get("ProgramData")

    if user_profile:
        candidates.extend(
            (
                Path(user_profile) / "scoop" / "shims" / "ffmpeg.exe",
                Path(user_profile) / "AppData" / "Local"
                / "Microsoft" / "WinGet" / "Links" / "ffmpeg.exe",
            )
        )

    if local_app_data:
        candidates.extend(
            (
                Path(local_app_data) / "Microsoft" / "WinGet"
                / "Links" / "ffmpeg.exe",
                Path(local_app_data) / "Programs" / "ffmpeg"
                / "bin" / "ffmpeg.exe",
            )
        )

    if program_files:
        candidates.extend(
            (
                Path(program_files) / "ffmpeg" / "bin" / "ffmpeg.exe",
                Path(program_files) / "FFmpeg" / "bin" / "ffmpeg.exe",
            )
        )

    if program_data:
        candidates.append(
            Path(program_data) / "chocolatey" / "bin" / "ffmpeg.exe"
        )

    candidates.extend(
        (
            Path(r"C:\ffmpeg\bin\ffmpeg.exe"),
            Path(r"C:\Program Files\ffmpeg\bin\ffmpeg.exe"),
        )
    )

    seen: set[str] = set()
    for candidate in candidates:
        try:
            normalized = candidate.expanduser().resolve()
        except OSError:
            normalized = candidate.expanduser()

        key = os.fspath(normalized).casefold()
        if key in seen:
            continue
        seen.add(key)

        if normalized.is_file():
            return normalized

    return None


FFMPEG_EXECUTABLE = _find_ffmpeg_executable()

# Keep save_video_frame.py in the same folder as this UI script.
# Every frame-saving request is delegated to that independent helper.
FRAME_HELPER_PATH = Path(__file__).resolve().with_name(
    "save_video_frame.py"
)


def natural_sort_key(path: Path) -> tuple:
    """Sort filenames naturally: video2 before video10."""
    import re

    parts = re.split(r"(\d+)", path.name.casefold())
    return tuple(int(part) if part.isdigit() else part for part in parts)


def unique_path(path: Path) -> Path:
    """Return path, or path_2/path_3/etc. when a file already exists."""
    if not path.exists():
        return path

    counter = 2
    while True:
        candidate = path.with_name(f"{path.stem}_{counter}{path.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def format_playback_timestamp(milliseconds: int) -> str:
    """
    Convert a playback position to a Windows-safe timestamp.

    Example:
        3723456 ms -> 01-02-03-456
    """
    milliseconds = max(0, milliseconds)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02d}-{minutes:02d}-{seconds:02d}-{millis:03d}"


def format_clock(milliseconds: int) -> str:
    """Human-readable playback time for the UI."""
    milliseconds = max(0, milliseconds)
    total_seconds = milliseconds // 1_000
    hours, remainder = divmod(total_seconds, 3_600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def format_entry_timestamp(milliseconds: int) -> str:
    """Format a timestamp for the clip input fields."""
    milliseconds = max(0, int(milliseconds))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def parse_entry_timestamp(value: str) -> int:
    """Parse SS, MM:SS, HH:MM:SS.mmm, or HH-MM-SS-mmm."""
    value = value.strip()
    if not value:
        raise ValueError("Timestamp is empty.")

    if ":" not in value and value.count("-") == 3:
        hours, minutes, seconds, millis = value.split("-")
        value = f"{hours}:{minutes}:{seconds}.{millis}"

    parts = value.split(":")
    if len(parts) > 3:
        raise ValueError("Use SS, MM:SS, or HH:MM:SS.mmm.")

    try:
        numeric = [float(part.strip()) for part in parts]
    except ValueError as exc:
        raise ValueError("Timestamp contains invalid characters.") from exc

    if len(numeric) == 1:
        total_seconds = numeric[0]
    elif len(numeric) == 2:
        minutes, seconds = numeric
        if not 0 <= seconds < 60:
            raise ValueError("Seconds must be below 60.")
        total_seconds = (minutes * 60) + seconds
    else:
        hours, minutes, seconds = numeric
        if not 0 <= minutes < 60 or not 0 <= seconds < 60:
            raise ValueError("Minutes and seconds must be below 60.")
        total_seconds = (hours * 3600) + (minutes * 60) + seconds

    if total_seconds < 0:
        raise ValueError("Timestamp cannot be negative.")

    return int(round(total_seconds * 1000))


def ffmpeg_seconds(milliseconds: int) -> str:
    return f"{max(0, int(milliseconds)) / 1000.0:.3f}"


class SmoothTimeline(tk.Canvas):
    """
    A lightweight YouTube-style timeline.

    - Click anywhere to seek.
    - Drag to scrub.
    - Hover to see the target timestamp.
    - The parent supplies an interpolated position, so the bar can move
      smoothly without querying VLC on every animation frame.
    """

    TRACK_COLOR = "#5f6368"
    PLAYED_COLOR = "#ff0033"
    THUMB_COLOR = "#ff0033"
    TOOLTIP_BG = "#202124"
    TOOLTIP_FG = "#ffffff"

    def __init__(self, parent, seek_callback) -> None:
        super().__init__(
            parent,
            height=38,
            background=parent.winfo_toplevel().cget("background"),
            highlightthickness=0,
            borderwidth=0,
            cursor="hand2",
            takefocus=True,
        )
        self.seek_callback = seek_callback
        self.duration_ms = 0
        self.position_ms = 0
        self.dragging = False
        self.hovering = False
        self.hover_ms = 0

        self.track_id = self.create_line(0, 0, 0, 0, fill=self.TRACK_COLOR, width=4)
        self.played_id = self.create_line(0, 0, 0, 0, fill=self.PLAYED_COLOR, width=4)
        self.hover_id = self.create_line(
            0, 0, 0, 0, fill="#ffffff", width=1, state="hidden"
        )
        self.thumb_id = self.create_oval(
            0, 0, 0, 0, fill=self.THUMB_COLOR, outline="", state="hidden"
        )
        self.tooltip_box_id = self.create_rectangle(
            0, 0, 0, 0, fill=self.TOOLTIP_BG, outline="", state="hidden"
        )
        self.tooltip_text_id = self.create_text(
            0,
            0,
            fill=self.TOOLTIP_FG,
            font=("Segoe UI", 9),
            text="00:00",
            state="hidden",
        )

        self.bind("<Configure>", self._on_resize)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Motion>", self._on_motion)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)

    def set_duration(self, duration_ms: int) -> None:
        duration_ms = max(0, int(duration_ms))
        if duration_ms != self.duration_ms:
            self.duration_ms = duration_ms
            self.position_ms = min(self.position_ms, duration_ms)
            self._redraw()

    def set_position(self, position_ms: int) -> None:
        if self.dragging:
            return
        position_ms = max(0, int(position_ms))
        if self.duration_ms > 0:
            position_ms = min(position_ms, self.duration_ms)
        if position_ms != self.position_ms:
            self.position_ms = position_ms
            self._redraw()

    def _geometry(self) -> tuple[float, float, float]:
        width = max(20, self.winfo_width())
        left = 8.0
        right = float(width - 8)
        y = 25.0
        return left, right, y

    def _ms_to_x(self, milliseconds: int) -> float:
        left, right, _ = self._geometry()
        if self.duration_ms <= 0:
            return left
        fraction = max(0.0, min(1.0, milliseconds / self.duration_ms))
        return left + ((right - left) * fraction)

    def _x_to_ms(self, x: float) -> int:
        left, right, _ = self._geometry()
        if self.duration_ms <= 0 or right <= left:
            return 0
        fraction = max(0.0, min(1.0, (x - left) / (right - left)))
        return int(round(self.duration_ms * fraction))

    def _redraw(self) -> None:
        left, right, y = self._geometry()
        current_x = self._ms_to_x(self.position_ms)
        expanded = self.hovering or self.dragging
        line_width = 6 if expanded else 4
        radius = 6

        self.coords(self.track_id, left, y, right, y)
        self.coords(self.played_id, left, y, current_x, y)
        self.itemconfigure(self.track_id, width=line_width)
        self.itemconfigure(self.played_id, width=line_width)

        self.coords(
            self.thumb_id,
            current_x - radius,
            y - radius,
            current_x + radius,
            y + radius,
        )
        self.itemconfigure(self.thumb_id, state="normal" if expanded else "hidden")

        if self.hovering or self.dragging:
            hover_x = self._ms_to_x(self.hover_ms)
            self.coords(self.hover_id, hover_x, y - 8, hover_x, y + 8)
            self.itemconfigure(self.hover_id, state="normal")

            tooltip_text = format_clock(self.hover_ms)
            self.itemconfigure(
                self.tooltip_text_id,
                text=tooltip_text,
                state="normal",
            )

            # Keep the tooltip within the canvas.
            tooltip_x = max(28, min(self.winfo_width() - 28, hover_x))
            tooltip_y = 8
            self.coords(self.tooltip_text_id, tooltip_x, tooltip_y)

            bbox = self.bbox(self.tooltip_text_id)
            if bbox:
                x1, y1, x2, y2 = bbox
                self.coords(
                    self.tooltip_box_id,
                    x1 - 5,
                    y1 - 3,
                    x2 + 5,
                    y2 + 3,
                )
                self.itemconfigure(self.tooltip_box_id, state="normal")
                self.tag_raise(self.tooltip_text_id)
        else:
            self.itemconfigure(self.hover_id, state="hidden")
            self.itemconfigure(self.tooltip_box_id, state="hidden")
            self.itemconfigure(self.tooltip_text_id, state="hidden")

        self.tag_raise(self.played_id)
        self.tag_raise(self.hover_id)
        self.tag_raise(self.thumb_id)
        self.tag_raise(self.tooltip_box_id)
        self.tag_raise(self.tooltip_text_id)

    def _set_hover_from_x(self, x: float) -> int:
        self.hover_ms = self._x_to_ms(x)
        if self.dragging:
            self.position_ms = self.hover_ms
        self._redraw()
        return self.hover_ms

    def _on_resize(self, _event) -> None:
        self._redraw()

    def _on_enter(self, event) -> None:
        self.hovering = True
        self._set_hover_from_x(event.x)

    def _on_leave(self, _event) -> None:
        if not self.dragging:
            self.hovering = False
            self._redraw()

    def _on_motion(self, event) -> None:
        self.hovering = True
        self._set_hover_from_x(event.x)

    def _on_press(self, event) -> str:
        self.focus_set()
        self.dragging = True
        self.hovering = True
        target_ms = self._set_hover_from_x(event.x)
        self.seek_callback(target_ms, False)
        return "break"

    def _on_drag(self, event) -> str:
        target_ms = self._set_hover_from_x(event.x)
        self.seek_callback(target_ms, False)
        return "break"

    def _on_release(self, event) -> str:
        target_ms = self._set_hover_from_x(event.x)
        self.dragging = False
        self.position_ms = target_ms
        self.seek_callback(target_ms, True)
        self._redraw()
        return "break"


class VideoQueueReviewer:
    # VLC is sampled only five times per second. The timeline itself animates
    # from cached data at 25 FPS, which is visually smooth but inexpensive.
    PLAYER_POLL_MS = 200
    TIMELINE_ANIMATION_MS = 40

    # Scrub seeks are throttled so dragging does not flood libVLC.
    DRAG_SEEK_THROTTLE_MS = 75

    # libVLC queues snapshot work in its video-output thread. A return value of
    # zero means that the request was accepted, not that the PNG is already
    # complete on disk. The first attempt leaves playback untouched. Only a
    # failed first attempt uses the slower exact-frame stabilization fallback.
    SNAPSHOT_POLL_MS = 50
    SNAPSHOT_PRIMARY_TIMEOUT_MS = 1800
    SNAPSHOT_FALLBACK_TIMEOUT_MS = 6000

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Video Queue Reviewer")
        self.root.geometry("1420x840")
        self.root.minsize(1050, 650)

        self.queue: list[Path] = []
        self.media = None
        self.closed = False
        self.busy = False
        self.capture_busy = False
        self.capture_id = 0
        self.keys_down: set[str] = set()

        self.at_end = False
        self.restarting = False
        self.transition_id = 0

        self.cached_time_ms = 0
        self.cached_length_ms = 0
        self.cached_playing = False
        self.sample_clock = time.perf_counter()

        # VLC samples can arrive slightly late. The visible playhead uses a
        # separate monotonic clock, so it never jitters backward during normal
        # forward playback.
        self.display_time_ms = 0.0
        self.timeline_target_ms = 0.0
        self.last_animation_clock = time.perf_counter()
        self.ignore_stale_poll_until = 0.0

        self.last_known_fps = 0.0
        self.last_displayed_second = -1
        self.last_displayed_duration_second = -1

        self.pending_drag_seek_ms: int | None = None
        self.drag_seek_after_id = None
        self.toast_after_id = None

        self.ffmpeg_path = FFMPEG_EXECUTABLE

        # One background worker processes jobs sequentially. Enqueuing is
        # instant, so the UI remains available while extraction/encoding runs.
        self.background_jobs: deque[dict] = deque()
        self.active_background_job: dict | None = None
        self.export_process: subprocess.Popen | None = None
        self.export_context: dict | None = None
        self.next_background_job_id = 1
        self.reserved_output_paths: set[str] = set()

        # Both optional side panels start collapsed so the video-choice list
        # receives most of the available vertical space.
        self.controls_collapsed = True
        self.background_collapsed = True
        self.queue_selection_in_progress = False

        self.last_clip_field = "start"

        self.status_var = tk.StringVar(value="Starting…")
        self.current_file_var = tk.StringVar(value="No video loaded")
        self.queue_count_var = tk.StringVar(value="Queue: 0")
        self.current_time_var = tk.StringVar(value="00:00")
        self.duration_var = tk.StringVar(value="00:00")
        self.clip_start_var = tk.StringVar(value="")
        self.clip_end_var = tk.StringVar(value="")
        self.background_count_var = tk.StringVar(value="Jobs: 0")

        self._build_ui()
        self.root.update_idletasks()

        try:
            # Keep VLC output simple. There is no snapshot preview widget or
            # Python-side video decoding; VLC renders directly into the HWND.
            self.vlc_instance = vlc.Instance("--no-video-title-show", "--quiet")
            self.player = self.vlc_instance.media_player_new()
        except Exception as exc:
            messagebox.showerror(
                "VLC could not start",
                "Install VLC Media Player, then make sure VLC and Python are "
                f"both 64-bit or both 32-bit.\n\nDetails:\n{exc}",
            )
            self.root.destroy()
            return

        self.player.set_hwnd(self.video_panel.winfo_id())

        event_manager = self.player.event_manager()
        event_manager.event_attach(
            vlc.EventType.MediaPlayerEndReached,
            self._on_media_ended_from_vlc_thread,
        )
        event_manager.event_attach(
            vlc.EventType.MediaPlayerEncounteredError,
            self._on_media_error_from_vlc_thread,
        )

        self._bind_keys()
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        if not VIDEO_FOLDER.is_dir():
            messagebox.showerror(
                "Folder not found",
                f"The configured folder does not exist:\n{VIDEO_FOLDER}",
            )
            self.root.destroy()
            return

        self._scan_folder()
        self._populate_queue_list()
        self._load_current_video()

        self.root.after(self.PLAYER_POLL_MS, self._poll_player)
        self.root.after(self.TIMELINE_ANIMATION_MS, self._animate_timeline)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        main = ttk.Frame(self.root, padding=8)
        main.grid(row=0, column=0, sticky="nsew")
        main.columnconfigure(0, weight=5)
        main.columnconfigure(1, weight=2, minsize=420)
        main.rowconfigure(1, weight=1)
        self.main_frame = main

        header = ttk.Frame(main)
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        header.columnconfigure(0, weight=1)

        ttk.Label(
            header,
            textvariable=self.current_file_var,
            font=("Segoe UI", 12, "bold"),
        ).grid(row=0, column=0, sticky="w")

        ttk.Label(
            header,
            textvariable=self.queue_count_var,
        ).grid(row=0, column=1, sticky="e")

        self.video_panel = tk.Frame(main, background="black")
        self.video_panel.grid(row=1, column=0, sticky="nsew", padx=(0, 8))

        self.side_panel = ttk.Frame(main)
        side = self.side_panel
        side.grid(row=1, column=1, sticky="nsew")
        side.columnconfigure(0, weight=1)
        side.rowconfigure(2, weight=8)
        side.rowconfigure(4, weight=0)

        self.controls_toggle_button = ttk.Button(
            side,
            text="▶ Keyboard shortcuts",
            command=self._toggle_controls_panel,
            takefocus=False,
        )
        self.controls_toggle_button.grid(
            row=0,
            column=0,
            sticky="ew",
        )

        self.controls_frame = ttk.Frame(side)
        self.controls_frame.grid(
            row=1,
            column=0,
            sticky="ew",
            pady=(5, 8),
        )

        controls_text = (
            "1  Keep + next\n"
            "2  Save frame + mark delete + next\n"
            "3  Save frame + stay\n"
            "4  Mark delete + next\n\n"
            "Space / K  Pause or play\n"
            "← / →  Seek 5 seconds\n"
            "J / L  Seek 10 seconds\n"
            "Home  Beginning\n"
            "Shift+Space  Save frame + stay\n\n"
            "Click any queue video to jump to it.\n"
            "Frame saves are queued in background.\n"
            "Clip: focus Start/End, then use current time."
        )
        ttk.Label(
            self.controls_frame,
            text=controls_text,
            justify="left",
            wraplength=390,
        ).grid(row=0, column=0, sticky="ew")
        self.controls_frame.columnconfigure(0, weight=1)

        self.queue_frame = ttk.LabelFrame(
            side,
            text="Video choices — click one to jump",
        )
        self.queue_frame.grid(
            row=2,
            column=0,
            sticky="nsew",
            pady=(7, 0),
        )
        self.queue_frame.columnconfigure(0, weight=1)
        self.queue_frame.rowconfigure(0, weight=1)

        self.queue_list = tk.Listbox(
            self.queue_frame,
            activestyle="dotbox",
            exportselection=False,
            selectmode=tk.SINGLE,
            borderwidth=1,
            highlightthickness=1,
            width=48,
        )
        self.queue_list.grid(
            row=0,
            column=0,
            sticky="nsew",
        )

        queue_scroll = ttk.Scrollbar(
            self.queue_frame,
            orient="vertical",
            command=self.queue_list.yview,
        )
        queue_scroll.grid(row=0, column=1, sticky="ns")

        queue_horizontal_scroll = ttk.Scrollbar(
            self.queue_frame,
            orient="horizontal",
            command=self.queue_list.xview,
        )
        queue_horizontal_scroll.grid(
            row=1,
            column=0,
            sticky="ew",
        )

        self.queue_list.configure(
            yscrollcommand=queue_scroll.set,
            xscrollcommand=queue_horizontal_scroll.set,
        )
        self.queue_list.bind(
            "<<ListboxSelect>>",
            self._on_queue_item_selected,
        )

        self.background_toggle_button = ttk.Button(
            side,
            text="▶ Background jobs: 0",
            command=self._toggle_background_panel,
            takefocus=False,
        )
        self.background_toggle_button.grid(
            row=3,
            column=0,
            sticky="ew",
            pady=(8, 0),
        )

        self.background_frame = ttk.LabelFrame(
            side,
            text="Background job details",
        )
        self.background_frame.grid(
            row=4,
            column=0,
            sticky="nsew",
            pady=(5, 0),
        )
        self.background_frame.columnconfigure(0, weight=1)
        self.background_frame.rowconfigure(0, weight=1)

        self.background_list = tk.Listbox(
            self.background_frame,
            height=7,
            activestyle="none",
            exportselection=False,
            borderwidth=1,
            highlightthickness=1,
        )
        self.background_list.grid(
            row=0,
            column=0,
            sticky="nsew",
        )

        background_scroll = ttk.Scrollbar(
            self.background_frame,
            orient="vertical",
            command=self.background_list.yview,
        )
        background_scroll.grid(
            row=0,
            column=1,
            sticky="ns",
        )
        self.background_list.configure(
            yscrollcommand=background_scroll.set
        )

        # Start with both optional sections collapsed, maximizing queue space.
        self.controls_frame.grid_remove()
        self.background_frame.grid_remove()

        buttons = ttk.Frame(main)
        buttons.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(7, 0))
        for column in range(5):
            buttons.columnconfigure(column, weight=1)

        button_specs = (
            ("1 — Keep + Next", self.keep_and_next),
            ("2 — Save + Delete + Next", self.capture_mark_delete_and_next),
            ("3 — Save + Stay", self.capture_and_stay),
            ("4 — Delete + Next", self.mark_delete_and_next),
            ("Space — Play / Pause", self.toggle_pause),
        )
        for column, (label, command) in enumerate(button_specs):
            ttk.Button(
                buttons,
                text=label,
                command=command,
                takefocus=False,
            ).grid(
                row=0,
                column=column,
                sticky="ew",
                padx=(0 if column == 0 else 3, 0 if column == 4 else 3),
            )

        clip_row = ttk.LabelFrame(main, text="Save video segment")
        clip_row.grid(
            row=3,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(6, 0),
        )
        clip_row.columnconfigure(1, weight=1)
        clip_row.columnconfigure(3, weight=1)

        ttk.Label(clip_row, text="Start").grid(
            row=0, column=0, padx=(8, 4), pady=6
        )
        self.clip_start_entry = ttk.Entry(
            clip_row,
            textvariable=self.clip_start_var,
            width=18,
        )
        self.clip_start_entry.grid(
            row=0, column=1, sticky="ew", padx=(0, 8), pady=6
        )
        self.clip_start_entry.bind(
            "<FocusIn>",
            lambda _event: self._remember_clip_field("start"),
        )

        ttk.Label(clip_row, text="End").grid(
            row=0, column=2, padx=(0, 4), pady=6
        )
        self.clip_end_entry = ttk.Entry(
            clip_row,
            textvariable=self.clip_end_var,
            width=18,
        )
        self.clip_end_entry.grid(
            row=0, column=3, sticky="ew", padx=(0, 8), pady=6
        )
        self.clip_end_entry.bind(
            "<FocusIn>",
            lambda _event: self._remember_clip_field("end"),
        )

        ttk.Button(
            clip_row,
            text="Use Current Time",
            command=self.set_current_time_in_focused_clip_field,
            takefocus=False,
        ).grid(row=0, column=4, padx=(0, 6), pady=6)

        ttk.Button(
            clip_row,
            text="Save Clip",
            command=self.save_video_clip,
            takefocus=False,
        ).grid(row=0, column=5, padx=(0, 8), pady=6)

        ttk.Label(
            clip_row,
            text="Format: HH:MM:SS.mmm",
        ).grid(
            row=1,
            column=0,
            columnspan=6,
            sticky="w",
            padx=8,
            pady=(0, 5),
        )

        timeline_row = ttk.Frame(main)
        timeline_row.grid(
            row=4,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(5, 0),
        )
        timeline_row.columnconfigure(1, weight=1)

        ttk.Label(
            timeline_row,
            textvariable=self.current_time_var,
            width=8,
            anchor="e",
        ).grid(row=0, column=0, padx=(0, 5))

        self.timeline = SmoothTimeline(timeline_row, self._timeline_seek)
        self.timeline.grid(row=0, column=1, sticky="ew")

        ttk.Label(
            timeline_row,
            textvariable=self.duration_var,
            width=8,
            anchor="w",
        ).grid(row=0, column=2, padx=(5, 0))

        ttk.Label(
            main,
            textvariable=self.status_var,
            anchor="w",
        ).grid(
            row=5,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(3, 0),
        )

        ttk.Label(
            main,
            text=f"Folder: {VIDEO_FOLDER}",
            anchor="w",
        ).grid(
            row=6,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(2, 0),
        )

        # Text-only toast. It never loads, scales, or displays the saved image.
        self.toast_label = tk.Label(
            main,
            text="",
            background="#202124",
            foreground="#ffffff",
            font=("Segoe UI", 10, "bold"),
            padx=12,
            pady=7,
            relief="flat",
        )

    def _set_status(self, text: str) -> None:
        if self.status_var.get() != text:
            self.status_var.set(text)

    def _show_saved_notification(
        self,
        filename: str,
        *,
        kind: str = "Frame",
    ) -> None:
        self._set_status(f"✓ Saved {kind.casefold()}: {filename}")
        self.toast_label.configure(text=f"✓ {kind} saved\n{filename}")
        self.toast_label.place(relx=0.985, rely=0.02, anchor="ne")
        self.toast_label.lift()

        if self.toast_after_id is not None:
            try:
                self.root.after_cancel(self.toast_after_id)
            except tk.TclError:
                pass

        self.toast_after_id = self.root.after(2200, self._hide_toast)

    def _hide_toast(self) -> None:
        self.toast_after_id = None
        if not self.closed:
            self.toast_label.place_forget()

    def _toggle_controls_panel(self) -> None:
        self.controls_collapsed = not self.controls_collapsed

        if self.controls_collapsed:
            self.controls_frame.grid_remove()
            self.controls_toggle_button.configure(
                text="▶ Keyboard shortcuts"
            )
        else:
            self.controls_frame.grid()
            self.controls_toggle_button.configure(
                text="▼ Keyboard shortcuts"
            )

    def _toggle_background_panel(self) -> None:
        self.background_collapsed = not self.background_collapsed

        if self.background_collapsed:
            self.background_frame.grid_remove()
            self.side_panel.rowconfigure(4, weight=0)
        else:
            self.background_frame.grid()
            self.side_panel.rowconfigure(4, weight=2)

        self._update_background_count()

    def _on_queue_item_selected(self, _event=None) -> None:
        """
        Make the clicked list item the active video.

        The remaining queue is rotated rather than truncated. Therefore, after
        the selected video is processed, playback continues with the video that
        originally followed it. Earlier unprocessed entries wrap to the end.
        """
        if self.queue_selection_in_progress or not self.queue:
            return

        selection = self.queue_list.curselection()
        if not selection:
            return

        selected_index = int(selection[0])
        if selected_index <= 0 or selected_index >= len(self.queue):
            return

        if self.busy or self.restarting:
            self.queue_selection_in_progress = True
            try:
                self.queue_list.selection_clear(0, tk.END)
                self.queue_list.selection_set(0)
                self.queue_list.activate(0)
            finally:
                self.queue_selection_in_progress = False
            self._set_status(
                "Please wait for the current video transition."
            )
            return

        selected_path = self.queue[selected_index]

        self.queue_selection_in_progress = True
        try:
            self._cancel_transition()
            self._release_current_media()

            # Rotate so the clicked item becomes current while preserving every
            # unprocessed video and the original forward order from that item.
            self.queue = (
                self.queue[selected_index:]
                + self.queue[:selected_index]
            )
            self._populate_queue_list()
            self._load_current_video()
            self._set_status(
                f"Jumped to {selected_path.name}. "
                "Processing will continue from this point."
            )
        finally:
            self.queue_selection_in_progress = False

    def _bind_keys(self) -> None:
        self.root.bind_all("<KeyPress>", self._on_key_press, add="+")
        self.root.bind_all("<KeyRelease>", self._on_key_release, add="+")

    def _on_key_press(self, event: tk.Event) -> str | None:
        keysym = str(event.keysym).lower()

        # Avoid keyboard auto-repeat processing multiple queue items.
        if keysym in self.keys_down:
            return "break"
        self.keys_down.add(keysym)

        if keysym in {"1", "kp_1"}:
            self.keep_and_next()
            return "break"
        if keysym in {"2", "kp_2"}:
            self.capture_mark_delete_and_next()
            return "break"
        if keysym in {"3", "kp_3"}:
            self.capture_and_stay()
            return "break"
        if keysym in {"4", "kp_4"}:
            self.mark_delete_and_next()
            return "break"

        if keysym == "space":
            if bool(int(event.state) & 0x0001):
                self.capture_and_stay()
            else:
                self.toggle_pause()
            return "break"

        if keysym == "k":
            self.toggle_pause()
            return "break"
        if keysym == "left":
            self.seek_relative(-5_000)
            return "break"
        if keysym == "right":
            self.seek_relative(5_000)
            return "break"
        if keysym == "j":
            self.seek_relative(-10_000)
            return "break"
        if keysym == "l":
            self.seek_relative(10_000)
            return "break"
        if keysym == "home":
            self.seek_to(0)
            return "break"

        return None

    def _on_key_release(self, event: tk.Event) -> None:
        self.keys_down.discard(str(event.keysym).lower())

    # ------------------------------------------------------------------
    # Queue
    # ------------------------------------------------------------------

    def _scan_folder(self) -> None:
        videos: list[Path] = []

        # os.scandir avoids creating Path/stat objects for files that will be
        # rejected, which matters in very large folders.
        with os.scandir(VIDEO_FOLDER) as entries:
            for entry in entries:
                try:
                    if not entry.is_file():
                        continue
                except OSError:
                    continue

                path = Path(entry.path)
                if path.suffix.casefold() not in VIDEO_EXTENSIONS:
                    continue

                if SKIP_ALREADY_PROCESSED:
                    lowered = path.name.casefold()
                    if lowered.startswith("keep"):
                        continue
                    if lowered.startswith("to_be_deleted"):
                        continue

                videos.append(path)

        self.queue = sorted(videos, key=natural_sort_key)

    def _populate_queue_list(self) -> None:
        previous_guard = self.queue_selection_in_progress
        self.queue_selection_in_progress = True

        try:
            self.queue_list.delete(0, tk.END)
            if self.queue:
                items = [
                    ("▶ " if index == 0 else "   ") + path.name
                    for index, path in enumerate(self.queue)
                ]
                # One Tcl call is much faster than inserting one item at a time.
                self.queue_list.insert(tk.END, *items)
                self.queue_list.selection_set(0)
                self.queue_list.activate(0)
                self.queue_list.see(0)
        finally:
            self.queue_selection_in_progress = previous_guard

        self.queue_count_var.set(f"Queue: {len(self.queue)}")

    def _remove_finished_queue_item(self, source: Path) -> None:
        if self.queue and self.queue[0] == source:
            self.queue.pop(0)
            if self.queue_list.size():
                self.queue_list.delete(0)
        else:
            try:
                index = self.queue.index(source)
            except ValueError:
                index = -1
            if index >= 0:
                self.queue.pop(index)
                if index < self.queue_list.size():
                    self.queue_list.delete(index)

        if self.queue:
            # Update only the new first row instead of rebuilding the list.
            if self.queue_list.size():
                self.queue_list.delete(0)
            self.queue_list.insert(0, "▶ " + self.queue[0].name)
            previous_guard = self.queue_selection_in_progress
            self.queue_selection_in_progress = True
            try:
                self.queue_list.selection_clear(0, tk.END)
                self.queue_list.selection_set(0)
                self.queue_list.activate(0)
                self.queue_list.see(0)
            finally:
                self.queue_selection_in_progress = previous_guard

        self.queue_count_var.set(f"Queue: {len(self.queue)}")

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------

    def _load_current_video(self) -> None:
        self.busy = False
        self.capture_busy = False
        self.capture_id += 1
        self._cancel_transition()

        self.at_end = False
        self.cached_time_ms = 0
        self.cached_length_ms = 0
        self.cached_playing = False
        self.sample_clock = time.perf_counter()
        self.display_time_ms = 0.0
        self.timeline_target_ms = 0.0
        self.last_animation_clock = time.perf_counter()
        self.ignore_stale_poll_until = 0.0
        self.last_known_fps = 0.0
        self.last_displayed_second = -1
        self.last_displayed_duration_second = -1

        self.timeline.set_duration(0)
        self.timeline.set_position(0)
        self.current_time_var.set("00:00")
        self.duration_var.set("00:00")

        if not self.queue:
            self._release_current_media()
            self.current_file_var.set("Queue complete")
            self._set_status("No unprocessed video files remain.")
            return

        path = self.queue[0]
        if not path.exists():
            self._set_status(f"Skipped missing file: {path.name}")
            self._remove_finished_queue_item(path)
            self.root.after_idle(self._load_current_video)
            return

        self._release_current_media()
        self.current_file_var.set(path.name)
        self._set_status("Playing. Press 1, 2, 3, or 4.")

        try:
            self.media = self.vlc_instance.media_new(path.resolve().as_uri())
            self.player.set_media(self.media)
            self.player.set_hwnd(self.video_panel.winfo_id())
            result = self.player.play()
            if result == -1:
                raise RuntimeError("VLC returned an error when starting playback.")
            self.cached_playing = True
            self.sample_clock = time.perf_counter()
        except Exception as exc:
            self.cached_playing = False
            self._set_status(f"Could not play {path.name}: {exc}")

    def _release_current_media(self) -> None:
        if not hasattr(self, "player"):
            return

        try:
            self.player.stop()
        except Exception:
            pass

        try:
            self.player.set_media(None)
        except Exception:
            pass

        if self.media is not None:
            try:
                self.media.release()
            except Exception:
                pass
            self.media = None

    def _cancel_transition(self) -> None:
        self.transition_id += 1
        self.restarting = False

    def _estimated_time_ms(self) -> int:
        if self.timeline.dragging:
            return self.timeline.position_ms

        position = int(round(self.display_time_ms))
        if self.cached_length_ms > 0:
            position = min(position, self.cached_length_ms)
        return max(0, position)

    def _set_playhead_position(
        self,
        position_ms: int,
        *,
        ignore_stale_polls: bool = False,
    ) -> None:
        position_ms = max(0, int(position_ms))
        if self.cached_length_ms > 0:
            position_ms = min(position_ms, self.cached_length_ms)

        self.cached_time_ms = position_ms
        self.timeline_target_ms = float(position_ms)
        self.display_time_ms = float(position_ms)
        self.sample_clock = time.perf_counter()
        self.last_animation_clock = self.sample_clock

        if ignore_stale_polls:
            self.ignore_stale_poll_until = self.sample_clock + 0.65

        self.timeline.set_position(position_ms)

    def _last_frame_position(self) -> int:
        length_ms = max(self.cached_length_ms, int(self.player.get_length()), 0)
        fps = self.last_known_fps

        if fps <= 0:
            try:
                fps = float(self.player.get_fps())
            except Exception:
                fps = 0.0

        frame_ms = max(1, round(1000.0 / fps)) if fps > 0 else 50
        return max(0, length_ms - frame_ms)

    def toggle_pause(self) -> None:
        if self.busy or not self.queue or self.restarting:
            return

        state = self.player.get_state()

        if self.at_end or state == vlc.State.Ended:
            self._restart_at(
                0,
                pause_after=False,
                mark_as_end=False,
                ready_message="Replaying from the beginning.",
            )
            return

        if self.player.is_playing():
            current = max(0, int(self.player.get_time()))
            self.player.set_pause(1)
            self._set_playhead_position(current)
            self.cached_playing = False
            self._set_status("Paused.")
        else:
            if state in {
                vlc.State.NothingSpecial,
                vlc.State.Stopped,
                vlc.State.Error,
            }:
                self.player.play()
            else:
                self.player.set_pause(0)

            current = max(0, int(self.player.get_time()))
            self._set_playhead_position(current)
            self.cached_playing = True
            self.at_end = False
            self._set_status("Playing.")

    def seek_relative(self, offset_ms: int) -> None:
        self.seek_to(self._estimated_time_ms() + int(offset_ms))

    def seek_to(self, target_ms: int) -> None:
        if self.busy or not self.queue or self.restarting:
            return

        length_ms = max(self.cached_length_ms, int(self.player.get_length()), 0)
        target_ms = max(0, int(target_ms))
        if length_ms > 0:
            target_ms = min(target_ms, max(0, length_ms - 1))

        state = self.player.get_state()
        if self.at_end or state == vlc.State.Ended:
            # Clicking the timeline after the video ends restarts playback at
            # the clicked point, matching common web-player behavior.
            self._restart_at(
                target_ms,
                pause_after=False,
                mark_as_end=False,
                ready_message=f"Playing from {format_clock(target_ms)}.",
            )
            return

        self.at_end = False
        self.player.set_time(target_ms)
        self._set_playhead_position(
            target_ms,
            ignore_stale_polls=True,
        )

    def _timeline_seek(self, target_ms: int, final: bool) -> None:
        if self.busy or not self.queue:
            return

        target_ms = max(0, int(target_ms))
        self._set_playhead_position(
            target_ms,
            ignore_stale_polls=True,
        )

        # At Ended, wait for release so dragging does not repeatedly restart VLC.
        state = self.player.get_state()
        if self.at_end or state == vlc.State.Ended:
            if final:
                self.seek_to(target_ms)
            return

        self.pending_drag_seek_ms = target_ms

        if final:
            if self.drag_seek_after_id is not None:
                try:
                    self.root.after_cancel(self.drag_seek_after_id)
                except tk.TclError:
                    pass
                self.drag_seek_after_id = None
            self._flush_drag_seek()
            return

        if self.drag_seek_after_id is None:
            self.drag_seek_after_id = self.root.after(
                self.DRAG_SEEK_THROTTLE_MS,
                self._flush_drag_seek,
            )

    def _flush_drag_seek(self) -> None:
        self.drag_seek_after_id = None
        if self.closed or self.busy or self.pending_drag_seek_ms is None:
            return

        target_ms = self.pending_drag_seek_ms
        self.pending_drag_seek_ms = None
        self.player.set_time(target_ms)
        self._set_playhead_position(
            target_ms,
            ignore_stale_polls=True,
        )

    def _restart_at(
        self,
        target_ms: int,
        *,
        pause_after: bool,
        mark_as_end: bool,
        ready_message: str,
        on_ready=None,
        on_error=None,
    ) -> None:
        """
        Leave VLC's terminal Ended state without blocking Tkinter.

        This operation runs as a small after()-driven state machine. It is used
        only when needed: replaying, seeking after end, or capturing the last
        frame. The old version restored the final frame automatically after
        every video, which caused avoidable decoder restarts.
        """
        if self.busy or not self.queue or self.media is None:
            return

        self.transition_id += 1
        transition_id = self.transition_id
        self.restarting = True
        self.cached_playing = False

        try:
            self.player.stop()
            self.player.set_media(self.media)
            self.player.set_hwnd(self.video_panel.winfo_id())
            if self.player.play() == -1:
                raise RuntimeError("VLC could not restart playback.")
        except Exception as exc:
            self.restarting = False
            if on_error is not None:
                on_error(str(exc))
            else:
                self._set_status(f"Could not restart this video: {exc}")
            return

        self.root.after(
            25,
            lambda: self._wait_for_restart(
                transition_id,
                max(0, int(target_ms)),
                pause_after,
                mark_as_end,
                ready_message,
                on_ready,
                on_error,
                attempts_remaining=60,
            ),
        )

    def _wait_for_restart(
        self,
        transition_id: int,
        target_ms: int,
        pause_after: bool,
        mark_as_end: bool,
        ready_message: str,
        on_ready,
        on_error,
        attempts_remaining: int,
    ) -> None:
        if self.closed or transition_id != self.transition_id:
            return

        state = self.player.get_state()
        if state in {vlc.State.Playing, vlc.State.Paused}:
            length_ms = max(
                self.cached_length_ms,
                int(self.player.get_length()),
                0,
            )
            if length_ms > 0:
                target_ms = min(target_ms, max(0, length_ms - 1))

            self.player.set_time(target_ms)
            self.cached_time_ms = target_ms
            self.sample_clock = time.perf_counter()

            # Let VLC decode the target frame. A capture callback needs a
            # slightly longer delay than ordinary seeking.
            delay = 150 if on_ready is not None else 70
            self.root.after(
                delay,
                lambda: self._finish_restart(
                    transition_id,
                    target_ms,
                    pause_after,
                    mark_as_end,
                    ready_message,
                    on_ready,
                ),
            )
            return

        if attempts_remaining > 0 and state != vlc.State.Error:
            self.root.after(
                40,
                lambda: self._wait_for_restart(
                    transition_id,
                    target_ms,
                    pause_after,
                    mark_as_end,
                    ready_message,
                    on_ready,
                    on_error,
                    attempts_remaining - 1,
                ),
            )
            return

        self.restarting = False
        message = "VLC could not restore playback after the video ended."
        if on_error is not None:
            on_error(message)
        else:
            self._set_status(message)

    def _finish_restart(
        self,
        transition_id: int,
        target_ms: int,
        pause_after: bool,
        mark_as_end: bool,
        ready_message: str,
        on_ready,
    ) -> None:
        if self.closed or transition_id != self.transition_id:
            return

        if pause_after:
            self.player.set_pause(1)

        actual_ms = max(0, int(self.player.get_time()))
        self._set_playhead_position(
            actual_ms if actual_ms >= 0 else target_ms,
            ignore_stale_polls=True,
        )
        self.cached_playing = not pause_after
        self.at_end = mark_as_end
        self.restarting = False
        self._set_status(ready_message)

        if on_ready is not None:
            self.root.after_idle(on_ready)

    # ------------------------------------------------------------------
    # Actions and background job queue
    # ------------------------------------------------------------------

    def _action_allowed(self) -> bool:
        if self.busy:
            self._set_status(
                "Please wait for the current video transition."
            )
            return False
        if not self.queue:
            self._set_status("The queue is empty.")
            return False
        return True

    def _frame_request_allowed(self) -> bool:
        if self.busy:
            self._set_status(
                "Please wait for the current video transition."
            )
            return False
        if not self.queue:
            self._set_status("The queue is empty.")
            return False
        if not FRAME_HELPER_PATH.is_file():
            messagebox.showerror(
                "Frame helper missing",
                "save_video_frame.py must be in the same folder as "
                "video_queue_reviewer.py.\n\n"
                f"Expected location:\n{FRAME_HELPER_PATH}",
            )
            self._set_status("Frame helper script was not found.")
            return False
        return True

    def keep_and_next(self) -> None:
        if not self._action_allowed():
            return

        source = self.queue[0]
        self._enqueue_background_job(
            {
                "kind": "rename",
                "source": source,
                "prefix": KEEP_PREFIX,
                "description": f"Keep: {source.name}",
            },
            start_immediately=False,
        )
        self._advance_current_immediately(source)
        self._start_next_background_job()

    def capture_mark_delete_and_next(self) -> None:
        if not self._frame_request_allowed():
            return

        source = self.queue[0]
        requested_ms = self._capture_timestamp_now()
        last_frame = self._capture_is_last_frame()

        queued = self._queue_frame_job(
            source,
            requested_ms,
            last_frame=last_frame,
            rename_prefix_after_success=(
                DELETE_SAVED_FRAME_PREFIX
            ),
            start_immediately=False,
        )
        if not queued:
            return

        # The source remains at its original path until this frame job reaches
        # the front of the background queue and succeeds. The UI can already
        # move on to the next video.
        self._advance_current_immediately(source)
        self._start_next_background_job()

    def capture_and_stay(self) -> None:
        if not self._frame_request_allowed():
            return

        source = self.queue[0]
        requested_ms = self._capture_timestamp_now()
        self._queue_frame_job(
            source,
            requested_ms,
            last_frame=self._capture_is_last_frame(),
        )

    def mark_delete_and_next(self) -> None:
        if not self._action_allowed():
            return

        source = self.queue[0]
        self._enqueue_background_job(
            {
                "kind": "rename",
                "source": source,
                "prefix": DELETE_NO_FRAME_PREFIX,
                "description": f"Mark delete: {source.name}",
            },
            start_immediately=False,
        )
        self._advance_current_immediately(source)
        self._start_next_background_job()

    def _capture_timestamp_now(self) -> int:
        """
        Capture the timestamp at the key/button event without pausing playback.
        """
        state = self.player.get_state()
        if self.at_end or state == vlc.State.Ended:
            return self._last_frame_position()

        current = int(self.player.get_time())
        if current >= 0:
            return current
        return self._estimated_time_ms()

    def _capture_is_last_frame(self) -> bool:
        state = self.player.get_state()
        return self.at_end or state == vlc.State.Ended

    def _require_ffmpeg(self) -> bool:
        if self.ffmpeg_path is not None and self.ffmpeg_path.is_file():
            return True

        messagebox.showerror(
            "FFmpeg is required",
            "Frame extraction and clip export require ffmpeg.exe.\n\n"
            "Install FFmpeg and add its bin folder to PATH, set FFMPEG_DIR, "
            "or set FFMPEG_DIRECTORY_OVERRIDE near the top of this script.",
        )
        self._set_status("FFmpeg was not found.")
        return False

    def _reserved_path_key(self, path: Path) -> str:
        try:
            normalized = path.resolve()
        except OSError:
            normalized = path
        return os.fspath(normalized).casefold()

    def _unique_reserved_path(self, path: Path) -> Path:
        counter = 1
        candidate = path

        while (
            candidate.exists()
            or self._reserved_path_key(candidate)
            in self.reserved_output_paths
        ):
            counter += 1
            candidate = path.with_name(
                f"{path.stem}_{counter}{path.suffix}"
            )

        self.reserved_output_paths.add(
            self._reserved_path_key(candidate)
        )
        return candidate

    def _release_reserved_path(self, path: Path | None) -> None:
        if isinstance(path, Path):
            self.reserved_output_paths.discard(
                self._reserved_path_key(path)
            )

    def _queue_frame_job(
        self,
        source: Path,
        requested_ms: int,
        *,
        last_frame: bool,
        rename_prefix_after_success: str | None = None,
        start_immediately: bool = True,
    ) -> bool:
        if not FRAME_HELPER_PATH.is_file():
            return False

        timestamp_ms = max(0, int(requested_ms))
        timestamp_argument = (
            "last"
            if last_frame
            else f"{timestamp_ms}ms"
        )

        timestamp_label_ms = (
            max(
                0,
                self.cached_length_ms,
                int(self.player.get_length()),
            )
            if last_frame
            else timestamp_ms
        )

        final_path = self._unique_reserved_path(
            source.with_name(
                f"{source.stem}_"
                f"{format_playback_timestamp(timestamp_label_ms)}"
                f"{SNAPSHOT_EXTENSION}"
            )
        )

        command = [
            sys.executable,
            os.fspath(FRAME_HELPER_PATH),
            os.fspath(source),
            timestamp_argument,
            os.fspath(final_path),
        ]

        if self.ffmpeg_path is not None and self.ffmpeg_path.is_file():
            command.extend(
                (
                    "--ffmpeg",
                    os.fspath(self.ffmpeg_path),
                )
            )

        timestamp_label = (
            "last frame"
            if last_frame
            else format_entry_timestamp(timestamp_ms)
        )

        self._enqueue_background_job(
            {
                "kind": "frame",
                "source": source,
                "command": command,
                "final_path": final_path,
                "timestamp_argument": timestamp_argument,
                "rename_prefix_after_success": (
                    rename_prefix_after_success
                ),
                "description": (
                    f"Frame: {source.name} @ {timestamp_label}"
                ),
            },
            start_immediately=start_immediately,
        )
        return True

    def _enqueue_background_job(
        self,
        job: dict,
        *,
        start_immediately: bool = True,
    ) -> None:
        job["id"] = self.next_background_job_id
        self.next_background_job_id += 1
        job.setdefault("status", "Waiting")

        self.background_jobs.append(job)
        self.background_list.insert(
            tk.END,
            f"… {job['description']}",
        )
        self._update_background_count()

        position = len(self.background_jobs)
        if self.active_background_job is not None:
            position += 1

        self._set_status(
            f"Queued background job #{job['id']} "
            f"(position {position}): {job['description']}"
        )

        if start_immediately:
            self.root.after_idle(
                self._start_next_background_job
            )

    def _update_background_count(self) -> None:
        count = len(self.background_jobs)
        if self.active_background_job is not None:
            count += 1

        label = f"Background jobs: {count}"
        self.background_count_var.set(label)

        arrow = "▶" if self.background_collapsed else "▼"
        if hasattr(self, "background_toggle_button"):
            self.background_toggle_button.configure(
                text=f"{arrow} {label}"
            )

    def _start_next_background_job(self) -> None:
        if (
            self.closed
            or self.active_background_job is not None
            or self.export_process is not None
            or not self.background_jobs
        ):
            return

        job = self.background_jobs.popleft()
        self.active_background_job = job
        self.export_context = job

        if self.background_list.size() > 0:
            self.background_list.delete(0)
            self.background_list.insert(
                0,
                f"▶ {job['description']}",
            )
            self.background_list.selection_clear(0, tk.END)
            self.background_list.selection_set(0)
            self.background_list.see(0)

        self._update_background_count()
        self._set_status(
            f"Processing job #{job['id']}: "
            f"{job['description']}"
        )

        kind = job.get("kind")
        if kind == "rename":
            self.root.after(
                20,
                lambda: self._run_active_rename_job(
                    attempts_remaining=20
                ),
            )
            return

        command = job.get("command")
        if not isinstance(command, list):
            self._fail_active_background_job(
                "The queued job has no valid command."
            )
            return

        creation_flags = 0
        if sys.platform == "win32":
            creation_flags |= getattr(
                subprocess,
                "CREATE_NO_WINDOW",
                0,
            )
            creation_flags |= getattr(
                subprocess,
                "BELOW_NORMAL_PRIORITY_CLASS",
                0,
            )

        try:
            self.export_process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creation_flags,
            )
        except OSError as exc:
            self.export_process = None
            self._fail_active_background_job(
                f"Could not start the background process.\n\n{exc}"
            )
            return

        self.root.after(
            100,
            self._poll_active_background_process,
        )

    def _poll_active_background_process(self) -> None:
        process = self.export_process
        job = self.active_background_job

        if process is None or job is None:
            return

        return_code = process.poll()
        if return_code is None:
            self.root.after(
                100,
                self._poll_active_background_process,
            )
            return

        try:
            stdout, stderr = process.communicate()
        except OSError:
            stdout, stderr = "", ""

        self.export_process = None

        kind = job.get("kind")
        if kind == "frame":
            self._finish_frame_process(
                job,
                return_code,
                stdout,
                stderr,
            )
            return

        if kind == "clip":
            self._finish_clip_process(
                job,
                return_code,
                stderr,
            )
            return

        self._fail_active_background_job(
            f"Unknown completed job type: {kind}"
        )

    def _read_helper_error(
        self,
        stdout: str,
        stderr: str,
    ) -> str:
        raw_details = stderr.strip() or stdout.strip()
        details = raw_details

        if raw_details:
            try:
                payload = json.loads(raw_details)
                if isinstance(payload, dict):
                    details = str(
                        payload.get("error")
                        or payload.get("message")
                        or raw_details
                    )
            except (
                json.JSONDecodeError,
                TypeError,
                ValueError,
            ):
                pass

        if len(details) > 4000:
            details = details[-4000:]

        return details or (
            "The frame helper exited without creating a valid PNG."
        )

    def _finish_frame_process(
        self,
        job: dict,
        return_code: int,
        stdout: str,
        stderr: str,
    ) -> None:
        final_path: Path = job["final_path"]

        try:
            valid_output = (
                final_path.is_file()
                and final_path.stat().st_size > 0
            )
        except OSError:
            valid_output = False

        if return_code != 0 or not valid_output:
            self._fail_active_background_job(
                self._read_helper_error(stdout, stderr),
                title="Frame save failed",
            )
            return

        self._show_saved_notification(
            final_path.name,
            kind="Frame",
        )

        rename_prefix = job.get(
            "rename_prefix_after_success"
        )
        if rename_prefix:
            job["prefix"] = rename_prefix
            job["rename_target"] = None
            self._set_status(
                f"Frame saved; marking {job['source'].name}…"
            )
            self.root.after(
                10,
                lambda: self._run_active_rename_job(
                    attempts_remaining=20
                ),
            )
            return

        self._complete_active_background_job()

    def _finish_clip_process(
        self,
        job: dict,
        return_code: int,
        stderr: str,
    ) -> None:
        temporary_path: Path = job["temporary_path"]
        final_path: Path = job["final_path"]

        if return_code != 0:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass

            detail = (
                stderr.strip()[-3000:]
                or "FFmpeg returned an unknown error."
            )
            self._fail_active_background_job(
                detail,
                title="Clip export failed",
            )
            return

        try:
            valid = (
                temporary_path.is_file()
                and temporary_path.stat().st_size > 0
            )
        except OSError:
            valid = False

        if not valid:
            self._fail_active_background_job(
                "FFmpeg finished without creating a valid clip.",
                title="Clip export failed",
            )
            return

        try:
            shutil.move(
                os.fspath(temporary_path),
                os.fspath(final_path),
            )
        except OSError as exc:
            self._fail_active_background_job(
                f"The clip was created, but could not be moved to:\n"
                f"{final_path}\n\n{exc}",
                title="Clip export failed",
            )
            return

        self._show_saved_notification(
            final_path.name,
            kind="Clip",
        )
        self._complete_active_background_job()

    def _run_active_rename_job(
        self,
        *,
        attempts_remaining: int,
    ) -> None:
        job = self.active_background_job
        if job is None:
            return

        source: Path = job["source"]
        prefix = str(job.get("prefix", ""))

        target = job.get("rename_target")
        if not isinstance(target, Path):
            target = unique_path(
                source.with_name(prefix + source.name)
            )
            job["rename_target"] = target

        try:
            source.rename(target)
        except PermissionError:
            if attempts_remaining > 0:
                self.root.after(
                    80,
                    lambda: self._run_active_rename_job(
                        attempts_remaining=(
                            attempts_remaining - 1
                        )
                    ),
                )
                return

            self._fail_active_background_job(
                "Windows still reports the source video as in use:\n"
                f"{source}",
                title="Rename failed",
            )
            return
        except FileNotFoundError:
            self._fail_active_background_job(
                "The source video no longer exists at:\n"
                f"{source}",
                title="Rename failed",
            )
            return
        except OSError as exc:
            self._fail_active_background_job(
                f"Could not rename:\n{source.name}\n\n{exc}",
                title="Rename failed",
            )
            return

        self._set_status(f"Renamed to {target.name}")
        self._complete_active_background_job()

    def _complete_active_background_job(self) -> None:
        job = self.active_background_job
        if job is not None:
            self._release_reserved_path(
                job.get("final_path")
            )

        if self.background_list.size() > 0:
            self.background_list.delete(0)

        self.active_background_job = None
        self.export_context = None
        self.export_process = None
        self._update_background_count()

        self.root.after(
            30,
            self._start_next_background_job,
        )

    def _fail_active_background_job(
        self,
        message: str,
        *,
        title: str = "Background job failed",
    ) -> None:
        job = self.active_background_job

        if job is not None:
            temporary_path = job.get("temporary_path")
            if isinstance(temporary_path, Path):
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

        self._set_status(
            f"{title}; continuing with the next queued job."
        )
        messagebox.showerror(title, message)
        self._complete_active_background_job()

    def _advance_current_immediately(
        self,
        source: Path,
    ) -> None:
        """
        Detach the current VLC media and display the next video immediately.

        Renaming is handled later by the ordered background queue, after any
        frame requests that still need the original source path.
        """
        self.busy = True
        self._cancel_transition()
        self._release_current_media()
        self._remove_finished_queue_item(source)
        self.busy = False
        self.root.after_idle(self._load_current_video)

    # ------------------------------------------------------------------
    # Clip controls
    # ------------------------------------------------------------------

    def _remember_clip_field(self, field: str) -> None:
        self.last_clip_field = field

    def set_current_time_in_focused_clip_field(self) -> None:
        if not self.queue:
            self._set_status("The queue is empty.")
            return

        focus_widget = self.root.focus_get()
        if focus_widget == self.clip_start_entry:
            field = "start"
        elif focus_widget == self.clip_end_entry:
            field = "end"
        else:
            field = self.last_clip_field
            if not self.clip_start_var.get().strip():
                field = "start"
            elif not self.clip_end_var.get().strip():
                field = "end"

        state = self.player.get_state()
        if self.at_end or state == vlc.State.Ended:
            timestamp_ms = max(
                self.cached_length_ms,
                int(self.player.get_length()),
                0,
            )
        else:
            current_ms = int(self.player.get_time())
            timestamp_ms = (
                current_ms
                if current_ms >= 0
                else self._estimated_time_ms()
            )

        value = format_entry_timestamp(timestamp_ms)

        if field == "end":
            self.clip_end_var.set(value)
            self.last_clip_field = "end"
            self.clip_end_entry.focus_set()
            self.clip_end_entry.selection_range(0, tk.END)
        else:
            self.clip_start_var.set(value)
            self.last_clip_field = "start"
            self.clip_start_entry.focus_set()
            self.clip_start_entry.selection_range(0, tk.END)

        self._set_status(f"Set clip {field} to {value}.")

    def save_video_clip(self) -> None:
        if not self._action_allowed() or not self._require_ffmpeg():
            return

        try:
            start_ms = parse_entry_timestamp(
                self.clip_start_var.get()
            )
            end_ms = parse_entry_timestamp(
                self.clip_end_var.get()
            )
        except ValueError as exc:
            messagebox.showerror(
                "Invalid clip timestamp",
                str(exc),
            )
            return

        if end_ms <= start_ms:
            messagebox.showerror(
                "Invalid clip range",
                "The ending timestamp must be later than "
                "the starting timestamp.",
            )
            return

        length_ms = max(
            self.cached_length_ms,
            int(self.player.get_length()),
            0,
        )
        if length_ms > 0 and start_ms >= length_ms:
            messagebox.showerror(
                "Invalid clip range",
                "The starting timestamp is beyond the end "
                "of the video.",
            )
            return

        if length_ms > 0:
            end_ms = min(end_ms, length_ms)
            self.clip_end_var.set(
                format_entry_timestamp(end_ms)
            )

        source = self.queue[0]
        final_path = self._unique_reserved_path(
            source.with_name(
                f"{source.stem}_clip_"
                f"{format_playback_timestamp(start_ms)}_to_"
                f"{format_playback_timestamp(end_ms)}.mp4"
            )
        )
        temporary_path = final_path.with_name(
            f".{final_path.stem}_{uuid.uuid4().hex}"
            ".temporary.mp4"
        )

        command = self._build_clip_command(
            source,
            temporary_path,
            start_ms,
            end_ms,
        )

        self._enqueue_background_job(
            {
                "kind": "clip",
                "source": source,
                "command": command,
                "temporary_path": temporary_path,
                "final_path": final_path,
                "description": (
                    f"Clip: {source.name} "
                    f"{format_entry_timestamp(start_ms)} → "
                    f"{format_entry_timestamp(end_ms)}"
                ),
            }
        )

    def _build_clip_command(
        self,
        video_path: Path,
        output_path: Path,
        start_ms: int,
        end_ms: int,
    ) -> list[str]:
        duration_ms = end_ms - start_ms
        preroll_ms = min(start_ms, 2_000)
        input_seek_ms = start_ms - preroll_ms

        return [
            os.fspath(self.ffmpeg_path),
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-ss",
            ffmpeg_seconds(input_seek_ms),
            "-i",
            os.fspath(video_path),
            "-ss",
            ffmpeg_seconds(preroll_ms),
            "-t",
            ffmpeg_seconds(duration_ms),
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-threads",
            "2",
            "-movflags",
            "+faststart",
            "-avoid_negative_ts",
            "make_zero",
            os.fspath(output_path),
        ]

    # ------------------------------------------------------------------
    # Low-overhead timeline updates and VLC events
    # ------------------------------------------------------------------

    def _poll_player(self) -> None:
        if self.closed:
            return

        if (
            hasattr(self, "player")
            and self.queue
            and not self.busy
            and not self.restarting
        ):
            try:
                state = self.player.get_state()
                current = int(self.player.get_time())
                length = int(self.player.get_length())
                now = time.perf_counter()

                if length > 0:
                    self.cached_length_ms = length
                    self.timeline.set_duration(length)

                if state == vlc.State.Playing:
                    if current >= 0:
                        if (
                            now >= self.ignore_stale_poll_until
                            or abs(current - self.timeline_target_ms) < 700
                        ):
                            self.cached_time_ms = current
                            self.timeline_target_ms = max(
                                self.timeline_target_ms,
                                float(current),
                            )

                    self.cached_playing = True
                    self.sample_clock = now
                    self.at_end = False

                elif state == vlc.State.Ended:
                    self.cached_playing = False
                    self.at_end = True
                    if self.cached_length_ms > 0:
                        self.cached_time_ms = self.cached_length_ms
                        self.timeline_target_ms = float(
                            self.cached_length_ms
                        )

                else:
                    if current >= 0:
                        self.cached_time_ms = current
                        self.timeline_target_ms = float(current)
                        if not self.timeline.dragging:
                            self.display_time_ms = float(current)

                    self.cached_playing = False
                    self.sample_clock = now

                if self.last_known_fps <= 0:
                    fps = float(self.player.get_fps())
                    if fps > 0:
                        self.last_known_fps = fps

            except Exception:
                pass

        self.root.after(self.PLAYER_POLL_MS, self._poll_player)

    def _animate_timeline(self) -> None:
        if self.closed:
            return

        now = time.perf_counter()
        elapsed_ms = max(
            0.0,
            min(250.0, (now - self.last_animation_clock) * 1000.0),
        )
        self.last_animation_clock = now

        if not self.timeline.dragging:
            if self.cached_playing and not self.at_end:
                lead_limit = self.timeline_target_ms + self.PLAYER_POLL_MS
                if self.cached_length_ms > 0:
                    lead_limit = min(
                        lead_limit,
                        float(self.cached_length_ms),
                    )

                self.display_time_ms = min(
                    lead_limit,
                    self.display_time_ms + elapsed_ms,
                )
            else:
                self.display_time_ms = self.timeline_target_ms

            if self.cached_length_ms > 0:
                self.display_time_ms = min(
                    self.display_time_ms,
                    float(self.cached_length_ms),
                )

            self.display_time_ms = max(0.0, self.display_time_ms)
            self.timeline.set_position(int(round(self.display_time_ms)))

        position_ms = self._estimated_time_ms()

        current_second = position_ms // 1000
        if current_second != self.last_displayed_second:
            self.last_displayed_second = current_second
            self.current_time_var.set(format_clock(position_ms))

        duration_second = self.cached_length_ms // 1000
        if duration_second != self.last_displayed_duration_second:
            self.last_displayed_duration_second = duration_second
            self.duration_var.set(format_clock(self.cached_length_ms))

        self.root.after(
            self.TIMELINE_ANIMATION_MS,
            self._animate_timeline,
        )

    def _on_media_ended_from_vlc_thread(self, _event) -> None:
        self.root.after(0, self._handle_media_ended)

    def _handle_media_ended(self) -> None:
        if not self.queue or self.busy or self.restarting:
            return

        # Do not immediately restart/seek/decode the video. Keep the terminal
        # state idle and recover lazily only if the user replays, seeks, or
        # requests the final frame.
        self.at_end = True
        self.cached_playing = False
        if self.cached_length_ms > 0:
            self.cached_time_ms = self.cached_length_ms
            self.timeline_target_ms = float(self.cached_length_ms)
            self.display_time_ms = float(self.cached_length_ms)
            self.timeline.set_position(self.cached_length_ms)
        self._set_status(
            "Video ended. Click the timeline to replay from a point, press "
            "Space to replay, or choose 1, 2, 3, or 4."
        )

    def _on_media_error_from_vlc_thread(self, _event) -> None:
        self.root.after(
            0,
            lambda: self._set_status(
                "VLC reported a playback error for this file."
            ),
        )

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def close(self) -> None:
        pending_count = len(self.background_jobs)
        if self.active_background_job is not None:
            pending_count += 1

        if pending_count > 0:
            should_close = messagebox.askyesno(
                "Background jobs are pending",
                f"{pending_count} background job(s) have not finished.\n\n"
                "Closing now will cancel the active job and discard the "
                "waiting jobs. Close anyway?",
            )
            if not should_close:
                return

        self.closed = True
        self.capture_id += 1

        if self.export_process is not None:
            try:
                self.export_process.terminate()
            except OSError:
                pass
            self.export_process = None
            self.export_context = None

        if self.drag_seek_after_id is not None:
            try:
                self.root.after_cancel(self.drag_seek_after_id)
            except tk.TclError:
                pass

        if self.toast_after_id is not None:
            try:
                self.root.after_cancel(self.toast_after_id)
            except tk.TclError:
                pass

        try:
            self._release_current_media()
            if hasattr(self, "player"):
                self.player.release()
            if hasattr(self, "vlc_instance"):
                self.vlc_instance.release()
        finally:
            self.root.destroy()


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("This version is configured specifically for Windows.")

    root = tk.Tk()
    VideoQueueReviewer(root)
    root.mainloop()


if __name__ == "__main__":
    main()