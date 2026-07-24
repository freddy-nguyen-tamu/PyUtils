from __future__ import annotations

import ctypes
import datetime as dt
import os
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Iterable

try:
    import pyaudiowpatch as pyaudio
except ImportError:
    pyaudio = None  # type: ignore[assignment]


# ============================================================
# SETTINGS
# ============================================================

# None = use the primary display's current refresh rate.
# Set an integer such as 30 or 60 to cap the capture rate.
FRAME_RATE: int | None = None

# WASAPI loopback is opened on the playback device that is the Windows
# default when recording starts. No Stereo Mix device is required.
AUDIO_CHUNK_SIZE = 1024
AUDIO_BITRATE = "192k"

# Large FFmpeg input queues protect short encoder or disk stalls.
INPUT_QUEUE_PACKETS = 8192
REALTIME_BUFFER_SIZE = "1024M"

# H.264 quality. Lower is better quality and larger output.
VIDEO_QUALITY = 20

# First record into Matroska, then remux to MP4 without re-encoding.
OUTPUT_DIRECTORY = Path("screen_recordings")

# When either PortAudio or FFmpeg reports lost data, retain the recording but
# name it *_CAPTURE_LOSS.mp4 instead of claiming that it is exact.
STRICT_CAPTURE_REPORTING = True


# ============================================================
# SMALL DATA TYPES
# ============================================================

@dataclass(slots=True)
class AudioChunk:
    """One contiguous PCM block, optionally preceded by deliberate silence."""

    data: bytes = b""
    silence_frames: int = 0


@dataclass(slots=True)
class AudioStats:
    callback_status_count: int = 0
    queued_packets: int = 0
    captured_frames: int = 0
    written_frames: int = 0
    initial_silence_frames: int = 0
    tail_silence_frames: int = 0
    writer_error: str | None = None


# ============================================================
# WINDOWS / GENERAL HELPERS
# ============================================================


def require_windows() -> None:
    if sys.platform != "win32":
        raise RuntimeError("This recorder is designed for Windows.")


def require_pyaudiowpatch() -> None:
    if pyaudio is None:
        raise RuntimeError(
            "PyAudioWPatch is not installed. Run:\n"
            "  py -m pip install -U PyAudioWPatch"
        )


def find_ffmpeg() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError(
            "ffmpeg was not found in PATH. Install FFmpeg and restart "
            "Command Prompt or PowerShell."
        )
    return ffmpeg


def raise_python_priority() -> None:
    """Protect the tiny WASAPI forwarding workload from ordinary CPU stalls."""
    if sys.platform != "win32":
        return

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_current_process = kernel32.GetCurrentProcess
        get_current_process.restype = ctypes.c_void_p

        set_priority_class = kernel32.SetPriorityClass
        set_priority_class.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        set_priority_class.restype = ctypes.c_int

        # ABOVE_NORMAL_PRIORITY_CLASS. High enough to protect capture without
        # allowing the recorder to aggressively starve the visible desktop.
        set_priority_class(get_current_process(), 0x00008000)
    except Exception:
        pass


def enable_mmcss_for_current_thread() -> object | None:
    """Register the PortAudio callback thread with Windows MMCSS."""
    if sys.platform != "win32":
        return None

    try:
        avrt = ctypes.WinDLL("avrt", use_last_error=True)
        task_index = ctypes.c_uint32(0)
        function = avrt.AvSetMmThreadCharacteristicsW
        function.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32)]
        function.restype = ctypes.c_void_p

        handle = function("Pro Audio", ctypes.byref(task_index))
        if not handle:
            handle = function("Audio", ctypes.byref(task_index))
        return handle or None
    except Exception:
        return None


def get_primary_refresh_rate() -> int:
    """Read the current primary-display refresh rate from Windows."""
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

        get_dc = user32.GetDC
        get_dc.argtypes = [ctypes.c_void_p]
        get_dc.restype = ctypes.c_void_p

        release_dc = user32.ReleaseDC
        release_dc.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        release_dc.restype = ctypes.c_int

        get_device_caps = gdi32.GetDeviceCaps
        get_device_caps.argtypes = [ctypes.c_void_p, ctypes.c_int]
        get_device_caps.restype = ctypes.c_int

        VREFRESH = 116
        dc = get_dc(None)
        if not dc:
            return 60

        try:
            refresh = int(get_device_caps(dc, VREFRESH))
        finally:
            release_dc(None, dc)

        if 24 <= refresh <= 360:
            return refresh
    except Exception:
        pass

    return 60


def run_text(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def encoder_probe_command(ffmpeg: str, encoder: str) -> list[str]:
    return [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=64x64:r=1",
        "-frames:v",
        "1",
        "-an",
        "-pix_fmt",
        "yuv420p",
        "-c:v",
        encoder,
        "-f",
        "null",
        os.devnull,
    ]


def choose_video_encoder(ffmpeg: str) -> str:
    """Prefer a working hardware encoder to reduce desktop interference."""
    for encoder in ("h264_nvenc", "h264_qsv", "h264_amf"):
        if run_text(encoder_probe_command(ffmpeg, encoder)).returncode == 0:
            return encoder
    return "libx264"


def encoder_arguments(encoder: str, fps: int) -> list[str]:
    common = [
        "-c:v",
        encoder,
        "-pix_fmt",
        "yuv420p",
        "-g",
        str(max(1, fps * 2)),
        "-bf",
        "0",
    ]

    if encoder == "h264_nvenc":
        return common + [
            "-preset",
            "p1",
            "-tune",
            "ull",
            "-rc",
            "constqp",
            "-qp",
            str(VIDEO_QUALITY),
        ]

    if encoder == "h264_qsv":
        return common + [
            "-preset",
            "veryfast",
            "-global_quality",
            str(VIDEO_QUALITY),
        ]

    if encoder == "h264_amf":
        return common + [
            "-quality",
            "speed",
            "-rc",
            "cqp",
            "-qp_i",
            str(VIDEO_QUALITY),
            "-qp_p",
            str(VIDEO_QUALITY),
        ]

    return common + [
        "-preset",
        "ultrafast",
        "-tune",
        "zerolatency",
        "-crf",
        str(VIDEO_QUALITY),
    ]


def make_output_paths() -> tuple[Path, Path, Path, Path]:
    OUTPUT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    normal_mp4 = (
        OUTPUT_DIRECTORY / f"screen_recording_{timestamp}.mp4"
    ).resolve()
    loss_mp4 = (
        OUTPUT_DIRECTORY / f"screen_recording_{timestamp}_CAPTURE_LOSS.mp4"
    ).resolve()
    temporary_mkv = (
        OUTPUT_DIRECTORY / f".screen_recording_{timestamp}.mkv"
    ).resolve()
    log_path = (
        OUTPUT_DIRECTORY / f"screen_recording_{timestamp}.log"
    ).resolve()

    return normal_mp4, loss_mp4, temporary_mkv, log_path


# ============================================================
# WASAPI LOOPBACK
# ============================================================


def find_default_wasapi_loopback(
    audio_manager: Any,
) -> dict[str, Any]:
    """Find the loopback counterpart of Windows' default output endpoint."""
    try:
        wasapi_info = audio_manager.get_host_api_info_by_type(
            pyaudio.paWASAPI
        )
    except OSError as error:
        raise RuntimeError("Windows WASAPI is unavailable.") from error

    output_index = int(wasapi_info["defaultOutputDevice"])
    if output_index < 0:
        raise RuntimeError("Windows has no default playback device.")

    default_output = audio_manager.get_device_info_by_index(output_index)

    if bool(default_output.get("isLoopbackDevice", False)):
        return dict(default_output)

    try:
        loopback = audio_manager.get_wasapi_loopback_analogue_by_dict(
            default_output
        )
        if loopback is not None:
            return dict(loopback)
    except (AttributeError, OSError, ValueError):
        pass

    output_name = str(default_output.get("name", "")).lower()
    best_match: dict[str, Any] | None = None

    for candidate in audio_manager.get_loopback_device_info_generator():
        candidate_dict = dict(candidate)
        candidate_name = str(candidate_dict.get("name", "")).lower()

        if output_name and output_name in candidate_name:
            return candidate_dict

        if best_match is None:
            best_match = candidate_dict

    if best_match is not None:
        return best_match

    raise RuntimeError(
        "PyAudioWPatch found no WASAPI loopback endpoint for the current "
        "default playback device."
    )


def open_loopback_stream(
    audio_manager: Any,
    loopback: dict[str, Any],
    callback: Any,
) -> tuple[Any, int, int, int]:
    device_index = int(loopback["index"])
    sample_rate = int(round(float(loopback["defaultSampleRate"])))
    max_channels = max(1, int(loopback["maxInputChannels"]))

    # Stereo is preferred. Fall back to the endpoint's full channel count if
    # its driver refuses a two-channel shared-mode conversion.
    channel_candidates: list[int] = []
    if max_channels >= 2:
        channel_candidates.append(2)
    channel_candidates.append(max_channels)
    channel_candidates.append(1)

    seen: set[int] = set()
    last_error: BaseException | None = None

    for channels in channel_candidates:
        if channels in seen:
            continue
        seen.add(channels)

        try:
            stream = audio_manager.open(
                format=pyaudio.paInt16,
                channels=channels,
                rate=sample_rate,
                input=True,
                input_device_index=device_index,
                frames_per_buffer=AUDIO_CHUNK_SIZE,
                stream_callback=callback,
                start=False,
            )
            sample_width = audio_manager.get_sample_size(pyaudio.paInt16)
            return stream, sample_rate, channels, sample_width
        except (OSError, ValueError) as error:
            last_error = error

    raise RuntimeError(
        f"Could not open WASAPI loopback device '{loopback['name']}': "
        f"{last_error or 'unsupported format'}"
    )


# ============================================================
# FFMPEG CAPTURE
# ============================================================


def build_capture_command(
    ffmpeg: str,
    encoder: str,
    fps: int,
    sample_rate: int,
    channels: int,
    temporary_mkv: Path,
) -> list[str]:
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "info",
        "-nostats",
        "-y",

        # FFmpeg captures the desktop itself. gdigrab timestamps each frame at
        # capture time. fps_mode=passthrough prevents FFmpeg from manufacturing
        # duplicate frames to hide a slow capture operation.
        "-thread_queue_size",
        str(INPUT_QUEUE_PACKETS),
        "-rtbufsize",
        REALTIME_BUFFER_SIZE,
        "-f",
        "gdigrab",
        "-draw_mouse",
        "1",
        "-framerate",
        str(fps),
        "-i",
        "desktop",

        # Python supplies native-clocked WASAPI loopback PCM through stdin.
        # PCM duration comes solely from the number of actual sample frames;
        # there is no guessed offset and no periodic async resampling.
        "-thread_queue_size",
        str(INPUT_QUEUE_PACKETS),
        "-f",
        "s16le",
        "-ar",
        str(sample_rate),
        "-ac",
        str(channels),
        "-blocksize",
        str(AUDIO_CHUNK_SIZE * channels * 2),
        "-i",
        "pipe:0",

        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-fps_mode:v",
        "passthrough",
    ]

    command.extend(encoder_arguments(encoder, fps))
    command.extend(
        [
            "-c:a",
            "aac",
            "-b:a",
            AUDIO_BITRATE,
            "-max_muxing_queue_size",
            "8192",
            "-f",
            "matroska",
            str(temporary_mkv),
        ]
    )
    return command


CAPTURE_LOSS_PATTERNS = (
    "frame dropped",
    "buffer queue overflow",
    "input buffer overrun",
    "too full or near too full",
    "non-monotonous dts",
    "non monotonically increasing dts",
    "queue input is backward in time",
    "real-time buffer",
)


def contains_capture_loss(line: str) -> bool:
    lower = line.lower()
    return any(pattern in lower for pattern in CAPTURE_LOSS_PATTERNS)


def creation_flags() -> int:
    if sys.platform != "win32":
        return 0

    flags = 0
    flags |= int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    flags |= int(getattr(subprocess, "ABOVE_NORMAL_PRIORITY_CLASS", 0))
    return flags


def stream_stderr(
    process: subprocess.Popen[bytes],
    log_path: Path,
    video_ready_event: threading.Event,
    video_ready_clock: list[float],
    capture_loss_event: threading.Event,
) -> None:
    assert process.stderr is not None

    with log_path.open("w", encoding="utf-8", errors="replace") as log_file:
        while True:
            raw_line = process.stderr.readline()
            if not raw_line:
                break

            line = raw_line.decode("utf-8", errors="replace")
            print(line, end="")
            log_file.write(line)
            log_file.flush()

            lower = line.lower()
            if (
                not video_ready_event.is_set()
                and "gdigrab" in lower
                and "capturing" in lower
            ):
                video_ready_clock[:] = [time.perf_counter()]
                video_ready_event.set()

            if contains_capture_loss(line):
                capture_loss_event.set()


def write_all(pipe: BinaryIO, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = pipe.write(view)
        if written is None:
            return
        if written <= 0:
            raise BrokenPipeError("FFmpeg audio pipe accepted no data.")
        view = view[written:]


def write_silence(
    pipe: BinaryIO,
    frame_count: int,
    bytes_per_frame: int,
) -> None:
    if frame_count <= 0:
        return

    # Keep individual allocations small while allowing the writer to catch up
    # quickly after a temporary scheduling delay.
    block_frames = 16384
    zero_block = bytes(block_frames * bytes_per_frame)

    remaining = frame_count
    while remaining > 0:
        current = min(remaining, block_frames)
        write_all(pipe, zero_block[: current * bytes_per_frame])
        remaining -= current


def audio_writer_main(
    process: subprocess.Popen[bytes],
    packets: queue.Queue[AudioChunk | None],
    bytes_per_frame: int,
    stats: AudioStats,
    writer_finished: threading.Event,
    capture_loss_event: threading.Event,
) -> None:
    """Forward WASAPI PCM in capture order without per-buffer surgery.

    PortAudio callback timestamps can move by a few samples from one callback
    to the next. Treating that harmless clock noise as real gaps/overlaps causes
    a tiny silence insertion or sample deletion every buffer, which is heard as
    a metallic echo or vibrating voice. Only the initial offset and final tail
    are represented as silence; captured PCM remains byte-for-byte contiguous.
    """
    assert process.stdin is not None

    try:
        while True:
            chunk = packets.get()
            if chunk is None:
                break

            if chunk.silence_frames > 0:
                write_silence(
                    process.stdin,
                    chunk.silence_frames,
                    bytes_per_frame,
                )
                stats.written_frames += chunk.silence_frames

            if chunk.data:
                usable_bytes = (len(chunk.data) // bytes_per_frame) * bytes_per_frame
                if usable_bytes <= 0:
                    continue
                write_all(process.stdin, chunk.data[:usable_bytes])
                stats.written_frames += usable_bytes // bytes_per_frame

    except (BrokenPipeError, OSError, ValueError) as error:
        stats.writer_error = str(error)
        capture_loss_event.set()
    finally:
        try:
            process.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        writer_finished.set()


def stop_ffmpeg_cleanly(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return

    # stdin carries PCM and therefore cannot also carry FFmpeg's interactive
    # 'q'. A separate Windows process group lets CTRL_BREAK request a normal
    # trailer write instead of corrupting the Matroska file.
    if sys.platform == "win32" and hasattr(signal, "CTRL_BREAK_EVENT"):
        try:
            process.send_signal(signal.CTRL_BREAK_EVENT)
            process.wait(timeout=90)
            return
        except (OSError, subprocess.TimeoutExpired):
            pass

    process.terminate()
    try:
        process.wait(timeout=10)
        return
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def remux_to_mp4(ffmpeg: str, source_mkv: Path, target_mp4: Path) -> None:
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-y",
        "-i",
        str(source_mkv),
        "-map",
        "0",
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(target_mp4),
    ]
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            "FFmpeg could not remux the recording to MP4 "
            f"(exit code {result.returncode})."
        )


def print_command(command: Iterable[str]) -> None:
    print("FFmpeg command:")
    print(subprocess.list2cmdline(list(command)))
    print()


# ============================================================
# MAIN
# ============================================================


def main() -> None:
    try:
        require_windows()
        require_pyaudiowpatch()
        ffmpeg = find_ffmpeg()
        raise_python_priority()
    except RuntimeError as error:
        print(f"\nERROR: {error}")
        sys.exit(1)

    audio_manager: Any = None
    stream: Any = None
    process: subprocess.Popen[bytes] | None = None

    normal_mp4, loss_mp4, temporary_mkv, log_path = make_output_paths()
    capture_loss_event = threading.Event()
    video_ready_event = threading.Event()
    video_ready_clock: list[float] = []
    writer_finished = threading.Event()
    stop_audio_event = threading.Event()
    packets: queue.Queue[AudioChunk | None] = queue.Queue()
    stats = AudioStats()

    callback_state: dict[str, Any] = {
        "mmcss_initialized": False,
        "first_packet_initialized": False,
        "captured_frames": 0,
    }

    try:
        audio_manager = pyaudio.PyAudio()
        loopback = find_default_wasapi_loopback(audio_manager)
        device_name = str(loopback["name"])

        # The callback is declared before the stream opens. The first WASAPI
        # buffer is aligned once to the video start; after that, PCM is forwarded
        # continuously in capture order with no per-buffer insertions or cuts.
        def audio_callback(
            in_data: bytes,
            frame_count: int,
            time_info: dict[str, float],
            status_flags: int,
        ) -> tuple[None, int]:
            if not callback_state["mmcss_initialized"]:
                enable_mmcss_for_current_thread()
                callback_state["mmcss_initialized"] = True

            if status_flags:
                stats.callback_status_count += 1
                capture_loss_event.set()

            if in_data:
                # Align the beginning once. Do NOT recalculate a position for
                # every callback: WASAPI/PortAudio callback timestamps contain
                # small scheduling jitter, and repeatedly inserting/trimming
                # samples at those boundaries creates the audible shaking echo.
                if not callback_state["first_packet_initialized"]:
                    callback_perf = time.perf_counter()
                    adc_time = float(
                        time_info.get("input_buffer_adc_time", 0.0)
                    )
                    current_time = float(time_info.get("current_time", 0.0))

                    if adc_time > 0.0 and current_time >= adc_time:
                        first_sample_perf = callback_perf - (
                            current_time - adc_time
                        )
                    else:
                        first_sample_perf = callback_perf

                    video_zero = (
                        video_ready_clock[0]
                        if video_ready_clock
                        else first_sample_perf
                    )
                    initial_silence = max(
                        0,
                        round((first_sample_perf - video_zero) * sample_rate),
                    )

                    # A very large value indicates an unusable clock report.
                    # Clamp it rather than blocking the real-time callback.
                    initial_silence = min(initial_silence, sample_rate * 2)
                    if initial_silence:
                        packets.put(
                            AudioChunk(silence_frames=initial_silence)
                        )
                    stats.initial_silence_frames = initial_silence
                    callback_state["first_packet_initialized"] = True

                packets.put(AudioChunk(data=in_data))
                stats.queued_packets += 1
                stats.captured_frames += frame_count
                callback_state["captured_frames"] += frame_count

            return_code = (
                pyaudio.paComplete
                if stop_audio_event.is_set()
                else pyaudio.paContinue
            )
            return (None, return_code)

        # Open once to learn the exact native format, then launch FFmpeg with
        # matching raw-PCM parameters.
        # sample_rate is assigned by open_loopback_stream and referenced by the
        # callback only after the stream has been fully created.
        sample_rate = 0
        stream, sample_rate, channels, sample_width = open_loopback_stream(
            audio_manager,
            loopback,
            audio_callback,
        )
        bytes_per_frame = channels * sample_width

        fps = FRAME_RATE if FRAME_RATE is not None else get_primary_refresh_rate()
        fps = max(1, int(fps))
        encoder = choose_video_encoder(ffmpeg)

        command = build_capture_command(
            ffmpeg=ffmpeg,
            encoder=encoder,
            fps=fps,
            sample_rate=sample_rate,
            channels=channels,
            temporary_mkv=temporary_mkv,
        )

        print("=" * 74)
        print("WINDOWS SCREEN RECORDER — WASAPI LOOPBACK")
        print("=" * 74)
        print("Desktop capture: FFmpeg gdigrab (entire virtual desktop)")
        print(f"Maximum FPS:     {fps}")
        print(f"System audio:    {device_name}")
        print(f"Audio format:    {sample_rate} Hz, {channels} channel(s), 16-bit")
        print("Audio source:    Windows WASAPI loopback (no Stereo Mix needed)")
        print(f"Video encoder:   {encoder}")
        print(f"Temporary file:  {temporary_mkv}")
        print(f"Capture log:     {log_path}")
        print()
        print("Press ENTER to stop and safely finalize the MP4.")
        print("=" * 74)
        print_command(command)

        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            bufsize=0,
            creationflags=creation_flags(),
        )

        stderr_thread = threading.Thread(
            target=stream_stderr,
            args=(
                process,
                log_path,
                video_ready_event,
                video_ready_clock,
                capture_loss_event,
            ),
            name="FFmpegLogReader",
            daemon=True,
        )
        stderr_thread.start()

        # gdigrab is the first FFmpeg input. Waiting for its initialization
        # before starting WASAPI keeps the two first samples/frames naturally
        # aligned without a hard-coded millisecond correction.
        video_ready_event.wait(timeout=10)
        if not video_ready_clock:
            video_ready_clock[:] = [time.perf_counter()]
            video_ready_event.set()

        writer_thread = threading.Thread(
            target=audio_writer_main,
            args=(
                process,
                packets,
                bytes_per_frame,
                stats,
                writer_finished,
                capture_loss_event,
            ),
            name="WASAPIPcmWriter",
            daemon=True,
        )
        writer_thread.start()

        stream.start_stream()

        try:
            input()
        except (EOFError, KeyboardInterrupt):
            pass

        stop_perf = time.perf_counter()
        print("\nStopping recording...")

        stop_audio_event.set()
        try:
            if stream.is_active():
                stream.stop_stream()
        except Exception:
            pass

        # Add silence only at the end, never between captured callbacks.
        # This keeps the audio track aligned with the recording stop time while
        # preserving every captured sample as one uninterrupted PCM sequence.
        final_frame = max(
            0,
            round((stop_perf - video_ready_clock[0]) * sample_rate),
        )
        captured_total = (
            stats.initial_silence_frames
            + int(callback_state["captured_frames"])
        )
        tail_silence = max(0, final_frame - captured_total)
        tail_silence = min(tail_silence, sample_rate * 2)
        if tail_silence:
            packets.put(AudioChunk(silence_frames=tail_silence))
        stats.tail_silence_frames = tail_silence
        packets.put(None)

        writer_thread.join(timeout=15)
        if not writer_finished.is_set():
            capture_loss_event.set()
            try:
                if process.stdin is not None:
                    process.stdin.close()
            except (BrokenPipeError, OSError):
                pass

        stop_ffmpeg_cleanly(process)
        stderr_thread.join(timeout=10)

        expected_interrupt_codes = {
            0,
            255,
            -1073741510,  # Windows STATUS_CONTROL_C_EXIT as a signed value.
            3221225786,   # The same status as an unsigned value.
        }
        if process.returncode not in expected_interrupt_codes:
            capture_loss_event.set()
            print(
                f"Warning: FFmpeg exited with code {process.returncode}; "
                "the output will be validated and labeled CAPTURE_LOSS."
            )

        if not temporary_mkv.exists() or temporary_mkv.stat().st_size == 0:
            raise RuntimeError(
                f"FFmpeg created no recording. See {log_path}"
            )

        capture_loss = capture_loss_event.is_set()
        target_mp4 = (
            loss_mp4
            if capture_loss and STRICT_CAPTURE_REPORTING
            else normal_mp4
        )

        remux_to_mp4(ffmpeg, temporary_mkv, target_mp4)

        try:
            temporary_mkv.unlink()
        except OSError:
            pass

        size_mb = target_mp4.stat().st_size / (1024 * 1024)
        duration_seconds = (
            stats.written_frames / sample_rate if sample_rate else 0.0
        )

        if capture_loss:
            print("\nCAPTURE LOSS OR AN AUDIO CALLBACK WARNING WAS REPORTED.")
            print(
                "The file was deliberately labeled CAPTURE_LOSS rather than "
                "being presented as exact."
            )
        else:
            print("\nRecording completed without a reported capture overrun.")

        print(f"File:              {target_mp4}")
        print(f"Log:               {log_path}")
        print(f"Size:              {size_mb:.2f} MB")
        print(f"Audio timeline:    {duration_seconds:.3f} seconds")
        print(f"WASAPI callbacks:  {stats.queued_packets}")
        print(f"Captured audio:    {stats.captured_frames} frames")
        print(f"Initial silence:   {stats.initial_silence_frames} frames")
        print(f"Tail silence:      {stats.tail_silence_frames} frames")
        if stats.writer_error:
            print(f"Audio pipe error:  {stats.writer_error}")

        if capture_loss:
            sys.exit(2)

    except Exception as error:
        print(f"\nERROR: {error}")

        if process is not None and process.poll() is None:
            try:
                stop_audio_event.set()
                packets.put(None)
            except Exception:
                pass
            stop_ffmpeg_cleanly(process)

        if temporary_mkv.exists():
            print(f"Recoverable temporary recording: {temporary_mkv}")
        print(f"Diagnostic log: {log_path}")
        sys.exit(1)

    finally:
        if stream is not None:
            try:
                if stream.is_active():
                    stream.stop_stream()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass

        if audio_manager is not None:
            try:
                audio_manager.terminate()
            except Exception:
                pass


if __name__ == "__main__":
    main()