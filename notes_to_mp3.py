#!/usr/bin/env python3
"""
Convert a simple note text format into an MP3.

Requirements:
    pip install numpy pydub

Also requires ffmpeg installed on your system for MP3 export.

Usage:
    python notes_to_mp3.py input.txt output.mp3
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import List, Tuple

import numpy as np
from pydub import AudioSegment


SAMPLE_RATE = 44100
DEFAULT_TEMPO = 120
DEFAULT_INSTRUMENT = "sine"
DEFAULT_VOLUME = 0.3

DURATION_MAP = {
    "w": 4.0,   # whole
    "h": 2.0,   # half
    "q": 1.0,   # quarter
    "e": 0.5,   # eighth
    "s": 0.25,  # sixteenth
}


NOTE_TO_SEMITONE = {
    "C": 0,
    "C#": 1,
    "DB": 1,
    "D": 2,
    "D#": 3,
    "EB": 3,
    "E": 4,
    "F": 5,
    "F#": 6,
    "GB": 6,
    "G": 7,
    "G#": 8,
    "AB": 8,
    "A": 9,
    "A#": 10,
    "BB": 10,
    "B": 11,
}


def note_to_frequency(note: str) -> float:
    """
    Convert note like C4, F#3, Bb5 to frequency in Hz.
    A4 = 440 Hz.
    """
    note = note.strip().upper()
    if note == "R":
        return 0.0

    if len(note) < 2:
        raise ValueError(f"Invalid note: {note}")

    if note[1] in ("#", "B"):
        pitch = note[:2]
        octave_str = note[2:]
    else:
        pitch = note[0]
        octave_str = note[1:]

    if pitch not in NOTE_TO_SEMITONE:
        raise ValueError(f"Invalid pitch: {pitch}")

    try:
        octave = int(octave_str)
    except ValueError as exc:
        raise ValueError(f"Invalid octave in note: {note}") from exc

    midi_number = (octave + 1) * 12 + NOTE_TO_SEMITONE[pitch]
    return 440.0 * (2.0 ** ((midi_number - 69) / 12.0))


def duration_to_seconds(duration_symbol: str, tempo: int) -> float:
    """
    Convert duration symbols like q, h, e into seconds.
    Quarter note duration = 60 / tempo.
    """
    duration_symbol = duration_symbol.strip().lower()
    if duration_symbol not in DURATION_MAP:
        raise ValueError(f"Unknown duration symbol: {duration_symbol}")

    quarter_note_seconds = 60.0 / tempo
    beats = DURATION_MAP[duration_symbol]
    return beats * quarter_note_seconds


def adsr_envelope(length: int) -> np.ndarray:
    """
    Simple attack/decay/sustain/release envelope to reduce clicks.
    """
    if length <= 0:
        return np.array([], dtype=np.float32)

    attack = max(1, int(length * 0.02))
    decay = max(1, int(length * 0.05))
    release = max(1, int(length * 0.08))
    sustain = max(0, length - attack - decay - release)

    attack_curve = np.linspace(0.0, 1.0, attack, endpoint=False)
    decay_curve = np.linspace(1.0, 0.8, decay, endpoint=False)
    sustain_curve = np.full(sustain, 0.8, dtype=np.float32)
    release_curve = np.linspace(0.8, 0.0, release, endpoint=True)

    env = np.concatenate([attack_curve, decay_curve, sustain_curve, release_curve])
    if len(env) < length:
        env = np.pad(env, (0, length - len(env)))
    return env[:length].astype(np.float32)


def generate_wave(
    frequency: float,
    seconds: float,
    instrument: str = DEFAULT_INSTRUMENT,
    volume: float = DEFAULT_VOLUME,
    sample_rate: int = SAMPLE_RATE,
) -> np.ndarray:
    """
    Generate mono PCM float samples in [-1, 1].
    """
    length = max(1, int(seconds * sample_rate))
    t = np.linspace(0, seconds, length, endpoint=False)

    if frequency == 0.0:
        return np.zeros(length, dtype=np.float32)

    instrument = instrument.lower()

    if instrument == "sine":
        wave = np.sin(2 * np.pi * frequency * t)
    elif instrument == "square":
        wave = np.sign(np.sin(2 * np.pi * frequency * t))
    elif instrument == "saw":
        wave = 2 * (t * frequency - np.floor(0.5 + t * frequency))
    elif instrument == "triangle":
        wave = 2 * np.abs(2 * (t * frequency - np.floor(t * frequency + 0.5))) - 1
    else:
        raise ValueError(f"Unsupported instrument: {instrument}")

    env = adsr_envelope(length)
    return (wave * env * volume).astype(np.float32)


def parse_input(text: str) -> Tuple[int, str, List[Tuple[str, str]]]:
    """
    Parse simple note format.

    Header lines:
        tempo: 120
        instrument: sine

    Note lines:
        C4 q
        D#4 e
        R h
    """
    tempo = DEFAULT_TEMPO
    instrument = DEFAULT_INSTRUMENT
    notes: List[Tuple[str, str]] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if ":" in line:
            key, value = [part.strip() for part in line.split(":", 1)]
            key = key.lower()
            if key == "tempo":
                tempo = int(value)
            elif key == "instrument":
                instrument = value.lower()
            else:
                raise ValueError(f"Unknown header field: {key}")
            continue

        parts = line.split()
        if len(parts) != 2:
            raise ValueError(f"Invalid note line: {line}")

        note_name, duration_symbol = parts
        notes.append((note_name, duration_symbol))

    return tempo, instrument, notes


def float_samples_to_audio_segment(samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> AudioSegment:
    """
    Convert float mono samples [-1,1] into a pydub AudioSegment.
    """
    clipped = np.clip(samples, -1.0, 1.0)
    int16_samples = (clipped * 32767).astype(np.int16)

    return AudioSegment(
        data=int16_samples.tobytes(),
        sample_width=2,
        frame_rate=sample_rate,
        channels=1,
    )


def synthesize_score(tempo: int, instrument: str, notes: List[Tuple[str, str]]) -> np.ndarray:
    """
    Generate full song waveform by concatenating notes.
    """
    chunks: List[np.ndarray] = []

    for note_name, duration_symbol in notes:
        freq = note_to_frequency(note_name)
        seconds = duration_to_seconds(duration_symbol, tempo)
        chunk = generate_wave(freq, seconds, instrument=instrument)
        chunks.append(chunk)

    if not chunks:
        return np.zeros(1, dtype=np.float32)

    return np.concatenate(chunks)


def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: python notes_to_mp3.py input.txt output.mp3")
        sys.exit(1)

    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])

    if not input_path.exists():
        print(f"Input file not found: {input_path}")
        sys.exit(1)

    text = input_path.read_text(encoding="utf-8")
    tempo, instrument, notes = parse_input(text)
    samples = synthesize_score(tempo, instrument, notes)
    audio = float_samples_to_audio_segment(samples)

    audio.export(output_path, format="mp3")
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()