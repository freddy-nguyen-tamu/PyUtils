import os
import sys
import math
import time
import json
import shutil
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional

import requests
import groq_dynamic

# ----------------------------
# CONFIG
# ----------------------------
GROQ_API_KEYS = groq_dynamic.load_groq_keys()
GROQ_BASE_URL = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1")

# Use "auto" to discover the current Groq Whisper model list dynamically.
DEFAULT_MODEL = os.environ.get("GROQ_TRANSCRIBE_MODEL", "auto")

# Chunking controls
CHUNK_SECONDS = int(os.environ.get("CHUNK_SECONDS", "60"))  # 60s chunks
AUDIO_SAMPLE_RATE = 16000

# Endpoint (OpenAI-compatible)
TRANSCRIBE_URL = f"{GROQ_BASE_URL.rstrip('/')}/audio/transcriptions"


# ----------------------------
# Helpers
# ----------------------------
def run(cmd: List[str]) -> None:
    """Run a subprocess command, raising on error."""
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode != 0:
        raise RuntimeError(
            f"Command failed: {' '.join(cmd)}\n\nSTDOUT:\n{p.stdout}\n\nSTDERR:\n{p.stderr}"
        )


def find_first_mp4(directory: Path) -> Path:
    mp4s = sorted([p for p in directory.iterdir() if p.is_file() and p.suffix.lower() == ".mp4"])
    if not mp4s:
        raise FileNotFoundError(f"No .mp4 files found in {directory}")
    return mp4s[0]


def extract_wav(mp4_path: Path, wav_path: Path) -> None:
    """Extract mono 16kHz WAV for ASR."""
    if shutil.which("ffmpeg") is None:
        raise EnvironmentError("ffmpeg not found on PATH. Install ffmpeg first.")

    cmd = [
        "ffmpeg", "-y",
        "-i", str(mp4_path),
        "-vn",
        "-ac", "1",
        "-ar", str(AUDIO_SAMPLE_RATE),
        "-f", "wav",
        str(wav_path),
    ]
    run(cmd)


def get_audio_duration_seconds(wav_path: Path) -> float:
    """Use ffprobe to get duration."""
    if shutil.which("ffprobe") is None:
        raise EnvironmentError("ffprobe not found on PATH. Install ffmpeg (includes ffprobe).")

    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(wav_path),
    ]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"ffprobe failed:\n{p.stderr}")
    return float(p.stdout.strip())


def split_wav_into_chunks(
    wav_path: Path, out_dir: Path, chunk_seconds: int
) -> List[Tuple[Path, float, float]]:
    """Split WAV into chunk files. Returns list of (chunk_path, start_sec, end_sec)."""
    duration = get_audio_duration_seconds(wav_path)
    chunks = []
    n = int(math.ceil(duration / chunk_seconds))

    for i in range(n):
        start = i * chunk_seconds
        end = min(duration, (i + 1) * chunk_seconds)
        out = out_dir / f"chunk_{i:04d}.wav"

        cmd = [
            "ffmpeg", "-y",
            "-i", str(wav_path),
            "-ss", str(start),
            "-t", str(end - start),
            "-ac", "1",
            "-ar", str(AUDIO_SAMPLE_RATE),
            str(out),
        ]
        run(cmd)
        chunks.append((out, start, end))

    return chunks


def sec_to_srt_time(t: float) -> str:
    """Format seconds -> SRT timestamp HH:MM:SS,mmm"""
    if t < 0:
        t = 0.0
    whole = int(t)
    ms = int(round((t - whole) * 1000))
    s = whole % 60
    m = (whole // 60) % 60
    h = whole // 3600
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def groq_transcribe_verbose_json(
    audio_path: Path,
    context_prompt: str,
    model: Optional[str] = DEFAULT_MODEL,
    language: Optional[str] = None,
    temperature: float = 0.0,
) -> Dict[str, Any]:
    """
    Call Groq OpenAI-compatible transcription endpoint.
    Requests verbose_json to get per-segment timestamps.
    """
    if not GROQ_API_KEYS:
        raise EnvironmentError("GROQ_API_KEY env var not set.")

    model_override = (model or "").strip()
    if model_override and model_override.lower() not in {"auto", "dynamic"}:
        models = [model_override]
    else:
        models = groq_dynamic.groq_transcription_model_candidates(GROQ_API_KEYS)

    disabled_keys = set()
    last_error = "unknown Groq transcription error"
    backoff = 1.0

    for round_number in range(1, 4):
        waits: List[float] = []

        for model_id in models:
            for key_name, api_key in GROQ_API_KEYS:
                if key_name in disabled_keys:
                    continue

                headers = {"Authorization": f"Bearer {api_key}"}
                data = {
                    "model": model_id,
                    "prompt": context_prompt,
                    "response_format": "verbose_json",
                    "temperature": str(temperature),
                }
                if language:
                    data["language"] = language

                with open(audio_path, "rb") as f:
                    files = {"file": (audio_path.name, f, "audio/wav")}
                    r = requests.post(
                        TRANSCRIBE_URL,
                        headers=headers,
                        data=data,
                        files=files,
                        timeout=300,
                    )

                if r.status_code == 200:
                    groq_dynamic.save_successful_transcription_model(model_id)
                    return r.json()

                last_error = f"{key_name} {model_id} HTTP {r.status_code}: {r.text[:500]}"

                if r.status_code in (401, 403):
                    disabled_keys.add(key_name)
                    continue

                if r.status_code == 429:
                    waits.append(max(groq_dynamic.parse_retry_after_seconds(r, backoff), backoff))
                    continue

                if r.status_code in (408, 409, 500, 502, 503, 504):
                    waits.append(max(groq_dynamic.parse_retry_after_seconds(r, backoff), backoff))
                    continue

        if round_number < 3:
            wait_s = max(waits) if waits else backoff
            print(
                "All Groq transcription key/model combinations failed. "
                f"Sleeping {wait_s:.2f}s before retry round {round_number}/3."
            )
            time.sleep(wait_s)
            backoff = min(backoff * 1.7, 30.0)

    raise RuntimeError(f"Groq transcription failed: {last_error}")


def segments_from_verbose_json(resp: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Expected schema (Whisper verbose_json-style):
      { "segments": [ { "start": 0.0, "end": 2.3, "text": "..." }, ... ], ... }
    """
    segs = resp.get("segments")
    if isinstance(segs, list) and segs:
        return segs

    # Fallback if only text returned (rare / misconfigured response_format)
    text = resp.get("text")
    if isinstance(text, str) and text.strip():
        return [{"start": 0.0, "end": 2.0, "text": text.strip()}]

    raise ValueError(f"Unexpected transcription response: {json.dumps(resp)[:1200]}")


def write_srt(segments: List[Dict[str, Any]], out_path: Path) -> None:
    """Write segments to .srt."""
    lines: List[str] = []
    idx = 1

    for seg in segments:
        text = (seg.get("text") or "").strip()
        if not text:
            continue

        start = float(seg.get("start", 0.0))
        end = float(seg.get("end", start + 2.0))
        if end <= start:
            end = start + 2.0

        lines.append(str(idx))
        lines.append(f"{sec_to_srt_time(start)} --> {sec_to_srt_time(end)}")
        lines.append(text)
        lines.append("")
        idx += 1

    out_path.write_text("\n".join(lines), encoding="utf-8")


def mp4_dir_to_srt(directory: str, context_prompt: str, language: Optional[str] = None) -> Path:
    dir_path = Path(directory).expanduser().resolve()
    if not dir_path.exists() or not dir_path.is_dir():
        raise NotADirectoryError(f"Not a directory: {dir_path}")

    mp4_path = find_first_mp4(dir_path)

    work_dir = dir_path / ".sub_work"
    work_dir.mkdir(exist_ok=True)

    wav_path = work_dir / f"{mp4_path.stem}.wav"
    extract_wav(mp4_path, wav_path)

    chunks_dir = work_dir / "chunks"
    chunks_dir.mkdir(exist_ok=True)

    chunks = split_wav_into_chunks(wav_path, chunks_dir, CHUNK_SECONDS)

    all_segments: List[Dict[str, Any]] = []

    for chunk_path, chunk_start, _chunk_end in chunks:
        resp = groq_transcribe_verbose_json(
            chunk_path,
            context_prompt=context_prompt,
            model=DEFAULT_MODEL,
            language=language,
            temperature=0.0,
        )

        segs = segments_from_verbose_json(resp)

        # offset chunk-local -> global timeline
        for s in segs:
            st = float(s.get("start", 0.0)) + chunk_start
            en = float(s.get("end", st + 2.0)) + chunk_start
            tx = (s.get("text") or "").strip()
            if tx:
                all_segments.append({"start": st, "end": en, "text": tx})

        time.sleep(0.15)  # small pacing to be nice to rate limits

    all_segments.sort(key=lambda x: (x["start"], x["end"]))

    out_srt = dir_path / f"{mp4_path.stem}.srt"
    write_srt(all_segments, out_srt)
    return out_srt


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(
            "Usage:\n"
            "  python mp4_to_srt_groq.py /path/to/dir \"context prompt here\" [language_code]\n\n"
            "Example:\n"
            "  python mp4_to_srt_groq.py ./videos "
            "\"This is a cardiology lecture; spell drug names correctly\" en\n"
        )
        sys.exit(1)

    directory = sys.argv[1]
    prompt = sys.argv[2]
    lang = sys.argv[3] if len(sys.argv) >= 4 else None

    srt_path = mp4_dir_to_srt(directory, prompt, language=lang)
    print(f"Wrote subtitles to: {srt_path}")
