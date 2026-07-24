from __future__ import annotations

import ctypes
import datetime as dt
import multiprocessing as mp
import queue
import shutil
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path
from typing import Any

import mss
import pyaudiowpatch as pyaudio


# ============================================================
# SETTINGS
# ============================================================

FRAME_RATE = 30

# H.264 quality:
# 18 = very high quality and larger
# 23 = good default
# 28 = smaller
CRF = 23

# ultrafast uses less CPU and helps avoid dropped frames.
# Change to "veryfast" if the computer handles it comfortably.
VIDEO_PRESET = "ultrafast"

# Audio chunks read per operation.
# A somewhat larger block gives WASAPI/PortAudio more scheduling margin
# and prevents tiny periodic gaps on a busy machine.
AUDIO_CHUNK_SIZE = 4096

AUDIO_BITRATE = "192k"

# Move captured audio earlier to compensate for Bluetooth/WASAPI latency.
# Increase if audio is still late; decrease if audio becomes early.
AUDIO_ADVANCE_MS = 250

# True records all monitors as one virtual desktop.
# False records only the primary monitor.
CAPTURE_ALL_MONITORS = True

OUTPUT_DIRECTORY = Path("screen_recordings")


# ============================================================
# GENERAL HELPERS
# ============================================================

def find_executable(name: str) -> str:
    executable = shutil.which(name)

    if executable is None:
        raise RuntimeError(
            f"{name} was not found in PATH. "
            f"Install it and restart Command Prompt."
        )

    return executable


def make_output_paths() -> tuple[Path, Path, Path]:
    OUTPUT_DIRECTORY.mkdir(parents=True, exist_ok=True)

    timestamp = dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    final_path = (
        OUTPUT_DIRECTORY / f"screen_recording_{timestamp}.mp4"
    ).resolve()

    video_path = (
        OUTPUT_DIRECTORY / f".video_{timestamp}.mp4"
    ).resolve()

    audio_path = (
        OUTPUT_DIRECTORY / f".audio_{timestamp}.wav"
    ).resolve()

    return final_path, video_path, audio_path


def remove_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as error:
        print(f"Warning: could not remove {path}: {error}")


# ============================================================
# AUDIO PROCESS
# ============================================================

def raise_audio_process_priority() -> None:
    """Give the dedicated audio process extra CPU scheduling priority."""
    if sys.platform != "win32":
        return

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_current_process = kernel32.GetCurrentProcess
        get_current_process.restype = ctypes.c_void_p

        set_priority_class = kernel32.SetPriorityClass
        set_priority_class.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        set_priority_class.restype = ctypes.c_int

        # ABOVE_NORMAL_PRIORITY_CLASS.  This is enough to protect audio
        # without making the recorder aggressively starve the desktop.
        set_priority_class(get_current_process(), 0x00008000)
    except Exception:
        # Priority elevation is only an optimization.  Recording must still
        # work on systems where it is unavailable.
        pass


def find_default_wasapi_loopback(
    audio_manager: pyaudio.PyAudio,
) -> dict[str, Any]:
    """
    Find the WASAPI loopback equivalent of the current default
    Windows playback device.
    """
    try:
        wasapi_info = audio_manager.get_host_api_info_by_type(
            pyaudio.paWASAPI
        )
    except OSError as error:
        raise RuntimeError(
            "Windows WASAPI is unavailable."
        ) from error

    output_index = int(wasapi_info["defaultOutputDevice"])

    if output_index < 0:
        raise RuntimeError(
            "Windows has no default playback device."
        )

    default_output = audio_manager.get_device_info_by_index(
        output_index
    )

    if bool(default_output.get("isLoopbackDevice", False)):
        return default_output

    # Preferred library helper.
    try:
        loopback = audio_manager.get_wasapi_loopback_analogue_by_dict(
            default_output
        )

        if loopback is not None:
            return loopback
    except (AttributeError, OSError, ValueError):
        pass

    # Fallback: compare names manually.
    output_name = str(default_output["name"]).lower()

    for device in audio_manager.get_loopback_device_info_generator():
        loopback_name = str(device["name"]).lower()

        if output_name in loopback_name:
            return device

    raise RuntimeError(
        "Could not find the WASAPI loopback device corresponding to "
        f"the default playback device: {default_output['name']}"
    )


def audio_process_main(
    audio_path_string: str,
    stop_event: Any,
    ready_event: Any,
    start_event: Any,
    result_queue: Any,
) -> None:
    """
    Record system audio in an independent process.

    Using a process instead of a Python thread prevents screen capture,
    frame conversion, and FFmpeg pipe writes from starving audio input.
    """
    audio_path = Path(audio_path_string)

    audio_manager: pyaudio.PyAudio | None = None
    stream: Any = None
    wav_file: wave.Wave_write | None = None

    try:
        raise_audio_process_priority()

        audio_manager = pyaudio.PyAudio()
        loopback = find_default_wasapi_loopback(audio_manager)

        device_index = int(loopback["index"])
        device_name = str(loopback["name"])

        sample_rate = int(round(float(loopback["defaultSampleRate"])))
        channels = int(loopback["maxInputChannels"])

        if channels < 1:
            raise RuntimeError(
                f"Loopback device has no input channels: {device_name}"
            )

        # Stereo is enough for ordinary screen recording.
        channels = min(channels, 2)

        sample_format = pyaudio.paInt16
        sample_width = audio_manager.get_sample_size(sample_format)

        wav_file = wave.open(str(audio_path), "wb")
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(sample_rate)

        # Blocking mode is intentional. PortAudio buffers data internally,
        # while this dedicated process continuously drains that buffer.
        stream = audio_manager.open(
            format=sample_format,
            channels=channels,
            rate=sample_rate,
            input=True,
            input_device_index=device_index,
            frames_per_buffer=AUDIO_CHUNK_SIZE,
            start=False,
        )

        result_queue.put(
            {
                "type": "ready",
                "device_name": device_name,
                "sample_rate": sample_rate,
                "channels": channels,
            }
        )

        # Signal that the audio device is initialized, but wait until
        # video capture is ready before actually starting the stream.
        ready_event.set()

        while not start_event.wait(timeout=0.1):
            if stop_event.is_set():
                return

        stream.start_stream()

        overflow_count = 0

        while not stop_event.is_set():
            try:
                audio_data = stream.read(
                    AUDIO_CHUNK_SIZE,
                    exception_on_overflow=False,
                )
            except OSError as error:
                # Do not silently continue forever if the playback device
                # was disconnected or changed.
                raise RuntimeError(
                    f"WASAPI audio read failed: {error}"
                ) from error

            if audio_data:
                wav_file.writeframesraw(audio_data)

        # Read any final data already buffered by PortAudio.
        for _ in range(4):
            available = int(stream.get_read_available())

            if available <= 0:
                break

            frame_count = min(available, AUDIO_CHUNK_SIZE)

            try:
                final_data = stream.read(
                    frame_count,
                    exception_on_overflow=False,
                )
            except OSError:
                break

            if final_data:
                wav_file.writeframesraw(final_data)

        result_queue.put(
            {
                "type": "finished",
                "overflow_count": overflow_count,
            }
        )

    except BaseException as error:
        result_queue.put(
            {
                "type": "error",
                "message": str(error),
                "error_type": type(error).__name__,
            }
        )
        ready_event.set()

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

        if wav_file is not None:
            try:
                wav_file.close()
            except Exception:
                pass

        if audio_manager is not None:
            try:
                audio_manager.terminate()
            except Exception:
                pass


# ============================================================
# SCREEN CAPTURE
# ============================================================

def get_capture_region(
    screen_capture: mss.MSS,
) -> dict[str, int]:
    monitor_index = 0 if CAPTURE_ALL_MONITORS else 1
    monitor = screen_capture.monitors[monitor_index]

    width = int(monitor["width"])
    height = int(monitor["height"])

    # H.264 yuv420p requires even dimensions.
    width -= width % 2
    height -= height % 2

    return {
        "left": int(monitor["left"]),
        "top": int(monitor["top"]),
        "width": width,
        "height": height,
    }


def build_video_command(
    ffmpeg: str,
    video_path: Path,
    width: int,
    height: int,
) -> list[str]:
    return [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-y",

        # Timestamp each incoming raw frame from its real arrival time.
        # Without this, FFmpeg assumes every received frame was captured at
        # an exact 30 FPS.  Any delayed/missed capture then shortens video
        # time and makes otherwise-correct audio drift behind the picture.
        "-use_wallclock_as_timestamps",
        "1",
        "-fflags",
        "+genpts",

        # Raw BGRA frames arrive through stdin.
        "-f",
        "rawvideo",
        "-pixel_format",
        "bgra",
        "-video_size",
        f"{width}x{height}",
        "-framerate",
        str(FRAME_RATE),
        "-i",
        "pipe:0",

        # Normalize the wall-clock timestamps to start at zero while keeping
        # their real spacing.  VFR is intentional: missed screen frames become
        # a slightly longer held image instead of speeding up the whole video.
        "-vf",
        "setpts=PTS-STARTPTS",

        # Video only at this stage.
        "-an",

        "-c:v",
        "libx264",
        "-preset",
        VIDEO_PRESET,
        "-crf",
        str(CRF),
        "-pix_fmt",
        "yuv420p",

        # Avoid B-frame reordering/latency and preserve wall-clock timing.
        "-tune",
        "zerolatency",
        "-fps_mode",
        "vfr",

        str(video_path),
    ]


# ============================================================
# FINAL MUX
# ============================================================

def mux_recording(
    ffmpeg: str,
    video_path: Path,
    audio_path: Path,
    final_path: Path,
) -> None:
    """Combine video and audio without periodic sample insertion/removal."""
    advance_seconds = max(0, AUDIO_ADVANCE_MS) / 1000.0

    # The WAV already has continuous sample timestamps.  Aggressive
    # aresample=async correction can periodically insert/drop samples and
    # sound like tiny clicks, skips, or joins every few seconds.  Only apply
    # the fixed device-latency trim here.
    audio_filter = (
        f"atrim=start={advance_seconds:.6f},"
        "asetpts=PTS-STARTPTS,"
        "apad=pad_dur=1"
    )

    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        AUDIO_BITRATE,
        "-af",
        audio_filter,
        "-shortest",
        "-movflags",
        "+faststart",
        str(final_path),
    ]

    result = subprocess.run(command, check=False)

    if result.returncode != 0:
        raise RuntimeError(
            "FFmpeg could not combine the video and audio. "
            f"Exit code: {result.returncode}"
        )


# ============================================================
# MAIN
# ============================================================

def main() -> None:
    if sys.platform != "win32":
        print("ERROR: This recorder is designed for Windows.")
        sys.exit(1)

    try:
        ffmpeg = find_executable("ffmpeg")
    except RuntimeError as error:
        print(f"\nERROR: {error}")
        sys.exit(1)

    final_path, video_path, audio_path = make_output_paths()

    # Windows requires spawn. Creating one explicit context also ensures
    # every multiprocessing object belongs to the same context.
    process_context = mp.get_context("spawn")

    audio_stop_event = process_context.Event()
    audio_ready_event = process_context.Event()
    capture_start_event = process_context.Event()
    audio_result_queue = process_context.Queue()

    audio_process = process_context.Process(
        target=audio_process_main,
        args=(
            str(audio_path),
            audio_stop_event,
            audio_ready_event,
            capture_start_event,
            audio_result_queue,
        ),
        name="WASAPILoopbackRecorder",
        daemon=False,
    )

    audio_process.start()

    if not audio_ready_event.wait(timeout=15):
        audio_stop_event.set()
        audio_process.terminate()
        audio_process.join(timeout=5)

        print("\nERROR: System-audio recording did not initialize.")
        sys.exit(1)

    try:
        audio_status = audio_result_queue.get(timeout=3)
    except queue.Empty:
        audio_stop_event.set()
        audio_process.terminate()
        audio_process.join(timeout=5)

        print("\nERROR: Audio process returned no device information.")
        sys.exit(1)

    if audio_status.get("type") == "error":
        audio_stop_event.set()
        audio_process.join(timeout=5)

        print("\nERROR: Could not start system-audio capture.")
        print(audio_status.get("message", "Unknown audio error"))
        sys.exit(1)

    if audio_status.get("type") != "ready":
        audio_stop_event.set()
        audio_process.terminate()
        audio_process.join(timeout=5)

        print(f"\nERROR: Unexpected audio response: {audio_status}")
        sys.exit(1)

    device_name = audio_status["device_name"]
    sample_rate = audio_status["sample_rate"]
    channels = audio_status["channels"]

    recording_stop_event = threading.Event()

    def wait_for_enter() -> None:
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            pass
        finally:
            recording_stop_event.set()

    keyboard_thread = threading.Thread(
        target=wait_for_enter,
        name="EnterKeyListener",
        daemon=True,
    )

    video_process: subprocess.Popen[bytes] | None = None
    video_return_code = -1
    recording_error: BaseException | None = None
    frames_written = 0

    try:
        # mss.MSS replaces the deprecated mss.mss constructor.
        with mss.MSS() as screen_capture:
            region = get_capture_region(screen_capture)

            width = region["width"]
            height = region["height"]

            video_command = build_video_command(
                ffmpeg=ffmpeg,
                video_path=video_path,
                width=width,
                height=height,
            )

            video_process = subprocess.Popen(
                video_command,
                stdin=subprocess.PIPE,
                bufsize=0,
            )

            if video_process.stdin is None:
                raise RuntimeError(
                    "FFmpeg did not open its video input pipe."
                )

            print("=" * 70)
            print("FULL-DESKTOP SCREEN RECORDER")
            print("=" * 70)
            print(f"Capture area:    {width}x{height}")
            print(f"Frame rate:      {FRAME_RATE} FPS")
            print(f"Audio device:    {device_name}")
            print(f"Audio format:    {sample_rate} Hz, {channels} channel(s)")
            print(f"Audio engine:    PyAudioWPatch WASAPI loopback")
            print(f"Audio advance:   {AUDIO_ADVANCE_MS} ms")
            print(f"Final output:    {final_path}")
            print()
            print("Recording is active.")
            print("Press ENTER to stop and safely finalize the MP4.")
            print("=" * 70)

            keyboard_thread.start()

            frame_interval = 1.0 / FRAME_RATE

            # Start audio only after FFmpeg and desktop capture are ready.
            capture_start_event.set()
            next_frame_time = time.perf_counter()

            while not recording_stop_event.is_set():
                if video_process.poll() is not None:
                    raise RuntimeError(
                        "FFmpeg stopped unexpectedly while recording."
                    )

                current_time = time.perf_counter()
                remaining = next_frame_time - current_time

                if remaining > 0:
                    time.sleep(remaining)

                if recording_stop_event.is_set():
                    break

                frame = screen_capture.grab(region)

                try:
                    video_process.stdin.write(frame.raw)
                except BrokenPipeError as error:
                    raise RuntimeError(
                        "FFmpeg closed the video input unexpectedly."
                    ) from error

                frames_written += 1
                next_frame_time += frame_interval

                # If capture/encoding was slow, skip expired capture slots.
                # Do not reset the recording clock and do not send a burst.
                # FFmpeg's wall-clock timestamps preserve the real gap, so
                # audio remains synchronized instead of drifting later.
                current_time = time.perf_counter()
                if next_frame_time < current_time:
                    missed_slots = int(
                        (current_time - next_frame_time) / frame_interval
                    ) + 1
                    next_frame_time += missed_slots * frame_interval

    except KeyboardInterrupt:
        recording_stop_event.set()

    except BaseException as error:
        recording_error = error
        recording_stop_event.set()

    finally:
        print("\nStopping recording...")

        # Stop audio promptly, independently of FFmpeg finalization.
        audio_stop_event.set()

        if video_process is not None:
            if video_process.stdin is not None:
                try:
                    video_process.stdin.close()
                except (BrokenPipeError, OSError):
                    pass

            try:
                video_return_code = video_process.wait(timeout=90)
            except subprocess.TimeoutExpired:
                video_process.terminate()

                try:
                    video_return_code = video_process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    video_process.kill()
                    video_return_code = video_process.wait()

        audio_process.join(timeout=20)

        if audio_process.is_alive():
            audio_process.terminate()
            audio_process.join(timeout=5)

    if recording_error is not None:
        print(f"\nERROR WHILE RECORDING:\n{recording_error}")
        print(f"Temporary video: {video_path}")
        print(f"Temporary audio: {audio_path}")
        sys.exit(1)

    if video_return_code != 0:
        print(
            "\nERROR: FFmpeg video encoding failed with "
            f"exit code {video_return_code}."
        )
        print(f"Temporary audio: {audio_path}")
        sys.exit(1)

    # Check for a late error from the audio process.
    while True:
        try:
            status = audio_result_queue.get_nowait()
        except queue.Empty:
            break

        if status.get("type") == "error":
            print("\nERROR: System-audio capture failed.")
            print(status.get("message", "Unknown audio error"))
            print(f"Temporary video: {video_path}")
            sys.exit(1)

    if not video_path.exists() or video_path.stat().st_size == 0:
        print("\nERROR: No temporary video was created.")
        sys.exit(1)

    if not audio_path.exists() or audio_path.stat().st_size <= 44:
        print("\nERROR: No system audio was recorded.")
        print(f"Temporary video: {video_path}")
        sys.exit(1)

    print("Combining video and system audio...")

    try:
        mux_recording(
            ffmpeg=ffmpeg,
            video_path=video_path,
            audio_path=audio_path,
            final_path=final_path,
        )
    except RuntimeError as error:
        print(f"\nERROR: {error}")
        print(f"Temporary video: {video_path}")
        print(f"Temporary audio: {audio_path}")
        sys.exit(1)

    remove_file(video_path)
    remove_file(audio_path)

    size_mb = final_path.stat().st_size / (1024 * 1024)

    print("\nRecording completed successfully.")
    print(f"File: {final_path}")
    print(f"Size: {size_mb:.2f} MB")


if __name__ == "__main__":
    mp.freeze_support()
    main()