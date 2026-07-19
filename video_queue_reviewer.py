from __future__ import annotations

import os
import shutil
import struct
import sys
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


class VideoQueueReviewer:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Video Queue Reviewer")
        self.root.geometry("1280x820")
        self.root.minsize(900, 600)

        self.queue: list[Path] = []
        self.media = None
        self.busy = False
        self.dragging_slider = False
        self.keys_down: set[str] = set()

        # VLC's Ended state is terminal for seeking/snapshots until playback
        # is restarted. These fields coordinate restart-and-seek operations.
        self.at_end = False
        self.recovering_end = False
        self.playback_transition_id = 0
        self.pending_end_action = None
        self.last_known_time_ms = 0
        self.last_known_length_ms = 0
        self.last_known_fps = 0.0

        self.status_var = tk.StringVar(value="Starting…")
        self.current_file_var = tk.StringVar(value="No video loaded")
        self.queue_count_var = tk.StringVar(value="Queue: 0")
        self.time_var = tk.StringVar(value="00:00 / 00:00")
        self.progress_var = tk.DoubleVar(value=0.0)

        self._build_ui()
        self.root.update_idletasks()

        try:
            # The installed VLC application and Python must have matching
            # architecture (normally both 64-bit).
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

        # Embed VLC's video output inside the Tkinter panel on Windows.
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
        self._refresh_queue_list()
        self._load_current_video()
        self._update_ui_timer()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        main = ttk.Frame(self.root, padding=10)
        main.grid(row=0, column=0, sticky="nsew")
        main.columnconfigure(0, weight=4)
        main.columnconfigure(1, weight=1)
        main.rowconfigure(1, weight=1)

        header = ttk.Frame(main)
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        header.columnconfigure(0, weight=1)

        ttk.Label(
            header,
            textvariable=self.current_file_var,
            font=("Segoe UI", 13, "bold"),
        ).grid(row=0, column=0, sticky="w")

        ttk.Label(
            header,
            textvariable=self.queue_count_var,
            font=("Segoe UI", 10),
        ).grid(row=0, column=1, sticky="e")

        self.video_panel = tk.Frame(main, background="black")
        self.video_panel.grid(row=1, column=0, sticky="nsew", padx=(0, 10))

        side = ttk.Frame(main)
        side.grid(row=1, column=1, sticky="nsew")
        side.columnconfigure(0, weight=1)
        side.rowconfigure(2, weight=1)

        ttk.Label(
            side,
            text="Keyboard controls",
            font=("Segoe UI", 12, "bold"),
        ).grid(row=0, column=0, sticky="w")

        controls_text = (
            "1  Keep video, rename, next\n\n"
            "2  Save current frame, mark video\n"
            "   for deletion, next\n\n"
            "3  Save current frame, stay here\n\n"
            "4  Mark video for deletion, next\n"
            "   (does not save a frame)\n\n"
            "Space  Pause / replay from end\n\n"
            "Left / Right  Rewind / forward 5 sec\n"
            "Home  Rewind to beginning\n\n"
            "Shift+Space  Save frame and stay\n"
            "(extra shortcut resolving the Space conflict)"
        )
        ttk.Label(
            side,
            text=controls_text,
            justify="left",
            wraplength=280,
        ).grid(row=1, column=0, sticky="ew", pady=(8, 12))

        queue_frame = ttk.LabelFrame(side, text="Queue")
        queue_frame.grid(row=2, column=0, sticky="nsew")
        queue_frame.columnconfigure(0, weight=1)
        queue_frame.rowconfigure(0, weight=1)

        self.queue_list = tk.Listbox(
            queue_frame,
            activestyle="none",
            exportselection=False,
            selectmode=tk.SINGLE,
        )
        self.queue_list.grid(row=0, column=0, sticky="nsew")

        queue_scroll = ttk.Scrollbar(
            queue_frame,
            orient="vertical",
            command=self.queue_list.yview,
        )
        queue_scroll.grid(row=0, column=1, sticky="ns")
        self.queue_list.configure(yscrollcommand=queue_scroll.set)

        buttons = ttk.Frame(main)
        buttons.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        for column in range(5):
            buttons.columnconfigure(column, weight=1)

        ttk.Button(
            buttons,
            text="1 — Keep + Next",
            command=self.keep_and_next,
            takefocus=False,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 5))

        ttk.Button(
            buttons,
            text="2 — Save + Mark Delete + Next",
            command=self.capture_mark_delete_and_next,
            takefocus=False,
        ).grid(row=0, column=1, sticky="ew", padx=5)

        ttk.Button(
            buttons,
            text="3 — Save Frame + Stay",
            command=self.capture_and_stay,
            takefocus=False,
        ).grid(row=0, column=2, sticky="ew", padx=5)

        ttk.Button(
            buttons,
            text="4 — Mark Delete + Next",
            command=self.mark_delete_and_next,
            takefocus=False,
        ).grid(row=0, column=3, sticky="ew", padx=5)

        ttk.Button(
            buttons,
            text="Space — Pause / Resume",
            command=self.toggle_pause,
            takefocus=False,
        ).grid(row=0, column=4, sticky="ew", padx=(5, 0))

        progress_row = ttk.Frame(main)
        progress_row.grid(
            row=3,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(10, 0),
        )
        progress_row.columnconfigure(0, weight=1)

        self.progress_slider = ttk.Scale(
            progress_row,
            from_=0,
            to=1000,
            orient="horizontal",
            variable=self.progress_var,
        )
        self.progress_slider.grid(row=0, column=0, sticky="ew")
        self.progress_slider.bind("<ButtonPress-1>", self._slider_press)
        self.progress_slider.bind("<ButtonRelease-1>", self._slider_release)

        ttk.Label(
            progress_row,
            textvariable=self.time_var,
            width=17,
            anchor="e",
        ).grid(row=0, column=1, padx=(10, 0))

        ttk.Label(
            main,
            textvariable=self.status_var,
            anchor="w",
        ).grid(
            row=4,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(8, 0),
        )

        ttk.Label(
            main,
            text=f"Folder: {VIDEO_FOLDER}",
            anchor="w",
        ).grid(
            row=5,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(4, 0),
        )

    def _bind_keys(self) -> None:
        self.root.bind_all("<KeyPress>", self._on_key_press, add="+")
        self.root.bind_all("<KeyRelease>", self._on_key_release, add="+")

    def _on_key_press(self, event: tk.Event) -> str | None:
        keysym = str(event.keysym).lower()

        # Ignore keyboard auto-repeat. This prevents one long keypress from
        # processing several videos.
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
            shift_pressed = bool(int(event.state) & 0x0001)
            if shift_pressed:
                self.capture_and_stay()
            else:
                self.toggle_pause()
            return "break"

        if keysym == "left":
            self.seek_relative(-5_000)
            return "break"

        if keysym == "right":
            self.seek_relative(5_000)
            return "break"

        if keysym == "home":
            self.seek_to(0)
            return "break"

        return None

    def _on_key_release(self, event: tk.Event) -> None:
        self.keys_down.discard(str(event.keysym).lower())

    # ------------------------------------------------------------------
    # Queue and playback
    # ------------------------------------------------------------------

    def _scan_folder(self) -> None:
        videos: list[Path] = []

        for path in VIDEO_FOLDER.iterdir():
            if not path.is_file():
                continue
            if path.suffix.casefold() not in VIDEO_EXTENSIONS:
                continue

            if SKIP_ALREADY_PROCESSED:
                lowered = path.name.casefold()
                # Broad matching deliberately handles files processed by
                # this version, older versions, or manual renames.
                if lowered.startswith("keep"):
                    continue
                if lowered.startswith("to_be_deleted"):
                    continue

            videos.append(path)

        self.queue = sorted(videos, key=natural_sort_key)

    def _refresh_queue_list(self) -> None:
        self.queue_list.delete(0, tk.END)
        for index, path in enumerate(self.queue):
            marker = "▶ " if index == 0 else "   "
            self.queue_list.insert(tk.END, marker + path.name)

        if self.queue:
            self.queue_list.selection_set(0)
            self.queue_list.activate(0)
            self.queue_list.see(0)

        self.queue_count_var.set(f"Queue: {len(self.queue)}")

    def _load_current_video(self) -> None:
        self.busy = False
        self._cancel_playback_transition()
        self.at_end = False
        self.last_known_time_ms = 0
        self.last_known_length_ms = 0
        self.last_known_fps = 0.0

        if not self.queue:
            self._release_current_media()
            self.current_file_var.set("Queue complete")
            self.status_var.set("No unprocessed video files remain.")
            self.time_var.set("00:00 / 00:00")
            self.progress_var.set(0)
            self._refresh_queue_list()
            return

        path = self.queue[0]
        if not path.exists():
            self.status_var.set(f"Skipped missing file: {path.name}")
            self.queue.pop(0)
            self._refresh_queue_list()
            self.root.after(10, self._load_current_video)
            return

        self._release_current_media()
        self.current_file_var.set(path.name)
        self.status_var.set("Playing. Press 1, 2, 3, or 4.")
        self.progress_var.set(0)
        self.time_var.set("00:00 / 00:00")
        self._refresh_queue_list()

        try:
            self.media = self.vlc_instance.media_new(path.resolve().as_uri())
            self.player.set_media(self.media)
            self.player.set_hwnd(self.video_panel.winfo_id())
            result = self.player.play()
            if result == -1:
                raise RuntimeError("VLC returned an error when starting playback.")
        except Exception as exc:
            self.status_var.set(f"Could not play {path.name}: {exc}")

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

    def _cancel_playback_transition(self) -> None:
        """Cancel delayed callbacks belonging to an older playback operation."""
        self.playback_transition_id += 1
        self.recovering_end = False
        self.pending_end_action = None

    def _last_frame_position(self, length_ms: int) -> int:
        """
        Estimate the timestamp of the final decodable frame.

        VLC reports media length as the end boundary. Seeking exactly to that
        boundary puts the player back into Ended, so seek one frame earlier.
        """
        length_ms = max(0, int(length_ms))
        fps = self.last_known_fps

        if fps <= 0:
            try:
                fps = float(self.player.get_fps())
            except Exception:
                fps = 0.0

        frame_ms = max(1, round(1000.0 / fps)) if fps > 0 else 50
        return max(0, length_ms - frame_ms)

    def _restart_and_seek(
        self,
        target_ms: int,
        *,
        pause_after: bool,
        logical_end: bool,
        ready_message: str,
        on_ready=None,
    ) -> None:
        """
        Restart VLC, wait until its decoder is active, then seek.

        libVLC's Ended state cannot reliably seek or take snapshots. Calling
        set_time() before play() also does not reliably leave Ended. This
        sequence restarts first, waits for Playing, pauses when requested,
        seeks, then waits for the requested frame to be rendered.
        """
        if self.busy or not self.queue or self.media is None:
            return

        self.playback_transition_id += 1
        transition_id = self.playback_transition_id
        self.recovering_end = logical_end
        self.at_end = logical_end

        try:
            self.player.stop()
            self.player.set_media(self.media)
            self.player.set_hwnd(self.video_panel.winfo_id())
            result = self.player.play()
            if result == -1:
                raise RuntimeError("VLC could not restart playback.")
        except Exception as exc:
            self.recovering_end = False
            self.status_var.set(f"Could not restart this video: {exc}")
            return

        self.status_var.set("Preparing video frame…")
        self.root.after(
            30,
            lambda: self._wait_for_restart(
                transition_id,
                max(0, int(target_ms)),
                pause_after,
                logical_end,
                ready_message,
                on_ready,
                attempts_remaining=60,
            ),
        )

    def _wait_for_restart(
        self,
        transition_id: int,
        target_ms: int,
        pause_after: bool,
        logical_end: bool,
        ready_message: str,
        on_ready,
        attempts_remaining: int,
    ) -> None:
        if transition_id != self.playback_transition_id:
            return
        if self.busy or not self.queue:
            return

        state = self.player.get_state()
        length_ms = max(
            int(self.player.get_length()),
            self.last_known_length_ms,
            0,
        )

        if state in {vlc.State.Playing, vlc.State.Paused}:
            if target_ms <= 0 and not pause_after:
                self.recovering_end = False
                self.at_end = False
                self.status_var.set(ready_message)
                if on_ready is not None:
                    on_ready()
                return

            if pause_after:
                self.player.set_pause(1)

            self.root.after(
                70,
                lambda: self._apply_restart_seek(
                    transition_id,
                    target_ms,
                    length_ms,
                    pause_after,
                    logical_end,
                    ready_message,
                    on_ready,
                ),
            )
            return

        if attempts_remaining > 0 and state not in {vlc.State.Error}:
            self.root.after(
                50,
                lambda: self._wait_for_restart(
                    transition_id,
                    target_ms,
                    pause_after,
                    logical_end,
                    ready_message,
                    on_ready,
                    attempts_remaining - 1,
                ),
            )
            return

        self.recovering_end = False
        self.status_var.set(
            "VLC could not restore playback after the video ended."
        )

    def _apply_restart_seek(
        self,
        transition_id: int,
        target_ms: int,
        length_ms: int,
        pause_after: bool,
        logical_end: bool,
        ready_message: str,
        on_ready,
    ) -> None:
        if transition_id != self.playback_transition_id:
            return

        live_length = max(int(self.player.get_length()), length_ms, 0)
        if live_length > 0:
            target_ms = min(target_ms, max(0, live_length - 1))

        self.player.set_time(max(0, target_ms))

        # Give VLC time to decode and display the sought frame before allowing
        # a snapshot. This is especially important after MediaPlayerEndReached.
        self.root.after(
            220 if pause_after else 80,
            lambda: self._finish_restart_seek(
                transition_id,
                target_ms,
                pause_after,
                logical_end,
                ready_message,
                on_ready,
            ),
        )

    def _finish_restart_seek(
        self,
        transition_id: int,
        target_ms: int,
        pause_after: bool,
        logical_end: bool,
        ready_message: str,
        on_ready,
    ) -> None:
        if transition_id != self.playback_transition_id:
            return

        if pause_after:
            self.player.set_pause(1)

        self.last_known_time_ms = max(0, int(self.player.get_time()))
        self.recovering_end = False
        self.at_end = logical_end
        self.status_var.set(ready_message)

        callback = on_ready
        if callback is None and logical_end:
            callback = self.pending_end_action

        if logical_end:
            self.pending_end_action = None

        if callback is not None:
            self.root.after(30, callback)

    def _recover_last_frame(self) -> None:
        """Restore and pause on the last decodable frame after EndReached."""
        if self.recovering_end or self.busy or not self.queue:
            return

        length_ms = max(
            self.last_known_length_ms,
            int(self.player.get_length()),
            0,
        )
        target_ms = self._last_frame_position(length_ms)

        self._restart_and_seek(
            target_ms,
            pause_after=True,
            logical_end=True,
            ready_message=(
                "Video ended. Last frame restored and paused. "
                "Press Space to replay, rewind, or choose 1, 2, 3, or 4."
            ),
        )

    def toggle_pause(self) -> None:
        if self.busy or not self.queue:
            return

        state = self.player.get_state()

        # The recovered end frame is intentionally paused. Space should replay
        # from zero rather than resume for one frame and immediately end again.
        if self.at_end or self.recovering_end or state == vlc.State.Ended:
            self.pending_end_action = None
            self._restart_and_seek(
                0,
                pause_after=False,
                logical_end=False,
                ready_message="Replaying from the beginning.",
            )
            return

        if self.player.is_playing():
            self.player.set_pause(1)
            self.status_var.set("Paused.")
        else:
            self.player.set_pause(0)
            if self.player.get_state() in {
                vlc.State.NothingSpecial,
                vlc.State.Stopped,
                vlc.State.Error,
            }:
                self.player.play()
            self.status_var.set("Playing.")

    def seek_to(self, target_ms: int) -> None:
        """Seek safely, including when VLC is currently in Ended."""
        if self.busy or not self.queue:
            return

        length_ms = max(
            int(self.player.get_length()),
            self.last_known_length_ms,
            0,
        )
        target_ms = max(0, int(target_ms))
        if length_ms > 0:
            target_ms = min(target_ms, self._last_frame_position(length_ms))

        state = self.player.get_state()
        if self.recovering_end or state == vlc.State.Ended:
            self.pending_end_action = None
            self._restart_and_seek(
                target_ms,
                pause_after=True,
                logical_end=False,
                ready_message=f"Paused at {format_clock(target_ms)}.",
            )
            return

        self.at_end = False
        self.player.set_time(target_ms)
        self.status_var.set(f"Seeked to {format_clock(target_ms)}.")

    def seek_relative(self, offset_ms: int) -> None:
        if self.busy or not self.queue:
            return

        if self.at_end or self.recovering_end:
            current_ms = self._last_frame_position(
                max(self.last_known_length_ms, int(self.player.get_length()), 0)
            )
        else:
            current_ms = max(int(self.player.get_time()), self.last_known_time_ms, 0)

        self.seek_to(current_ms + int(offset_ms))

    # ------------------------------------------------------------------
    # Actions 1, 2, 3, and 4
    # ------------------------------------------------------------------

    def keep_and_next(self) -> None:
        """Option 1: rename to keep_[original filename], then advance."""
        if not self._action_allowed():
            return
        self._begin_rename_and_advance(KEEP_PREFIX)

    def capture_mark_delete_and_next(self) -> None:
        """
        Option 2:
        Save the displayed frame, mark the video for deletion,
        then advance only when the snapshot succeeded.
        """
        if not self._action_allowed():
            return

        def capture_after_ready() -> None:
            if self.busy or not self.queue:
                return
            snapshot = self._capture_current_frame()
            if snapshot is None:
                return
            self._begin_rename_and_advance(
                DELETE_SAVED_FRAME_PREFIX,
                prior_message=f"Saved frame: {snapshot.name}",
            )

        self._run_capture_when_frame_is_available(capture_after_ready)

    def capture_and_stay(self) -> None:
        """Option 3: save the displayed frame and remain on this video."""
        if not self._action_allowed():
            return

        def capture_after_ready() -> None:
            if self.busy or not self.queue:
                return
            snapshot = self._capture_current_frame()
            if snapshot is not None:
                self.status_var.set(
                    f"Saved frame: {snapshot.name} — staying on this video."
                )

        self._run_capture_when_frame_is_available(capture_after_ready)

    def _run_capture_when_frame_is_available(self, callback) -> None:
        """
        Ensure a live VLC video output exists before taking a snapshot.

        At Ended, VLC has already torn down the active decoding/output path.
        Restore the last frame first, then execute the requested capture.
        """
        state = self.player.get_state()

        if self.recovering_end:
            if self.pending_end_action is None:
                self.pending_end_action = callback
                self.status_var.set(
                    "Restoring the last frame, then the requested snapshot "
                    "will be saved."
                )
            else:
                self.status_var.set(
                    "A frame action is already waiting for end recovery."
                )
            return

        if state == vlc.State.Ended:
            self.pending_end_action = callback
            self.at_end = True
            self._recover_last_frame()
            return

        callback()

    def mark_delete_and_next(self) -> None:
        """Option 4: mark for deletion without saving a frame, then advance."""
        if not self._action_allowed():
            return
        self._begin_rename_and_advance(DELETE_NO_FRAME_PREFIX)

    def _action_allowed(self) -> bool:
        if self.busy:
            self.status_var.set("Please wait for the current file operation.")
            return False
        if not self.queue:
            self.status_var.set("The queue is empty.")
            return False
        return True

    def _capture_current_frame(self) -> Path | None:
        """
        Capture VLC's currently displayed decoded frame without pausing.

        The filename uses the playback position returned at the key event,
        with millisecond precision:
            originalName_HH-MM-SS-mmm.png
        """
        path = self.queue[0]
        playback_ms = max(0, int(self.player.get_time()))
        timestamp = format_playback_timestamp(playback_ms)

        snapshot_path = unique_path(
            path.with_name(
                f"{path.stem}_{timestamp}{SNAPSHOT_EXTENSION}"
            )
        )

        try:
            result = self.player.video_take_snapshot(
                0,
                os.fspath(snapshot_path),
                0,
                0,
            )
        except Exception as exc:
            messagebox.showerror(
                "Snapshot failed",
                f"Could not save the current frame.\n\n{exc}",
            )
            self.status_var.set("Snapshot failed; the video was not changed.")
            return None

        if result != 0:
            messagebox.showerror(
                "Snapshot failed",
                "VLC could not capture a video frame. The video may not have "
                "finished opening, or its decoder may not expose snapshots.",
            )
            self.status_var.set("Snapshot failed; the video was not changed.")
            return None

        return snapshot_path

    def _begin_rename_and_advance(
        self,
        prefix: str,
        prior_message: str | None = None,
    ) -> None:
        """
        Stop and detach the media before renaming.

        Detaching is important on Windows because a playing media file can be
        locked by VLC. Renaming is retried briefly in case Windows has not yet
        released the handle.
        """
        self.busy = True
        self._cancel_playback_transition()
        source = self.queue[0]
        requested_target = source.with_name(prefix + source.name)
        target = unique_path(requested_target)

        if prior_message:
            self.status_var.set(f"{prior_message}; preparing video rename…")
        else:
            self.status_var.set("Preparing video rename…")

        self._release_current_media()
        self.root.after(
            180,
            lambda: self._try_rename(source, target, attempts_remaining=12),
        )

    def _try_rename(
        self,
        source: Path,
        target: Path,
        attempts_remaining: int,
    ) -> None:
        try:
            source.rename(target)
        except PermissionError:
            if attempts_remaining > 0:
                self.root.after(
                    120,
                    lambda: self._try_rename(
                        source,
                        target,
                        attempts_remaining - 1,
                    ),
                )
                return

            self.busy = False
            self.status_var.set(
                "Rename failed because Windows still reports the file as "
                "in use. Press the action key again to retry."
            )
            self._load_current_video()
            return
        except OSError as exc:
            self.busy = False
            messagebox.showerror(
                "Rename failed",
                f"Could not rename:\n{source.name}\n\n{exc}",
            )
            self.status_var.set("Rename failed; the queue was not advanced.")
            self._load_current_video()
            return

        # Remove the successfully processed item and start the next one.
        if self.queue and self.queue[0] == source:
            self.queue.pop(0)
        else:
            self.queue = [item for item in self.queue if item != source]

        self.status_var.set(f"Renamed to {target.name}")
        self._refresh_queue_list()
        self.root.after(40, self._load_current_video)

    # ------------------------------------------------------------------
    # Timeline and VLC events
    # ------------------------------------------------------------------

    def _slider_press(self, _event: tk.Event) -> None:
        self.dragging_slider = True

    def _slider_release(self, _event: tk.Event) -> None:
        self.dragging_slider = False
        if not self.queue or self.busy:
            return

        length = int(self.player.get_length())
        if length <= 0:
            return

        fraction = float(self.progress_var.get()) / 1000.0
        self.seek_to(int(length * fraction))

    def _update_ui_timer(self) -> None:
        if hasattr(self, "player") and self.queue and not self.busy:
            current = max(0, int(self.player.get_time()))
            length = max(0, int(self.player.get_length()))

            if current >= 0:
                self.last_known_time_ms = current
            if length > 0:
                self.last_known_length_ms = length
            try:
                fps = float(self.player.get_fps())
                if fps > 0:
                    self.last_known_fps = fps
            except Exception:
                pass

            self.time_var.set(
                f"{format_clock(current)} / {format_clock(length)}"
            )

            if length > 0 and not self.dragging_slider:
                self.progress_var.set((current / length) * 1000.0)

        self.root.after(150, self._update_ui_timer)

    def _on_media_ended_from_vlc_thread(self, _event) -> None:
        # VLC callbacks are not Tkinter's UI thread.
        self.root.after(0, self._handle_media_ended)

    def _handle_media_ended(self) -> None:
        if not self.queue or self.busy:
            return

        # Ignore EndReached generated by an already-running recovery attempt.
        if self.recovering_end:
            return

        self.at_end = True
        self.status_var.set("Video ended. Restoring the last frame…")
        self._recover_last_frame()

    def _on_media_error_from_vlc_thread(self, _event) -> None:
        self.root.after(
            0,
            lambda: self.status_var.set(
                "VLC reported a playback error for this file."
            ),
        )

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def close(self) -> None:
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