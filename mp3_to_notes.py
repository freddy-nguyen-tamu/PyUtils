#!/usr/bin/env python3
"""
Convert a simple monophonic MP3 melody into note text notation.

Requirements:
    pip install numpy librosa soundfile

Usage:
    python mp3_to_notes.py input.mp3 output.txt
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import List, Optional, Tuple

import librosa
import numpy as np


DURATION_SYMBOLS = [
    ("w", 4.0),
    ("h", 2.0),
    ("q", 1.0),
    ("e", 0.5),
    ("s", 0.25),
]

NOTE_NAMES_SHARP = [
    "C", "C#", "D", "D#", "E", "F",
    "F#", "G", "G#", "A", "A#", "B"
]


def midi_to_note_name(midi: int) -> str:
    pitch_class = midi % 12
    octave = (midi // 12) - 1
    return f"{NOTE_NAMES_SHARP[pitch_class]}{octave}"


def hz_to_midi_rounded(freq: float) -> Optional[int]:
    if freq <= 0 or not np.isfinite(freq):
        return None
    midi = int(round(69 + 12 * math.log2(freq / 440.0)))
    return midi


def guess_instrument(y: np.ndarray) -> str:
    """
    Very rough timbre guess based on spectral centroid and zero-crossing rate.
    """
    zcr = float(np.mean(librosa.feature.zero_crossing_rate(y)))
    centroid = float(np.mean(librosa.feature.spectral_centroid(y=y)))

    if zcr < 0.05 and centroid < 1200:
        return "sine"
    if zcr > 0.15 and centroid > 2500:
        return "square"
    if centroid > 1800:
        return "saw"
    return "triangle"


def estimate_tempo(y: np.ndarray, sr: int) -> int:
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    if isinstance(tempo, np.ndarray):
        tempo = float(tempo[0])
    return max(40, min(240, int(round(float(tempo)))))


def quantize_beats_to_symbol(beats: float) -> str:
    best_symbol = "q"
    best_diff = float("inf")
    for symbol, value in DURATION_SYMBOLS:
        diff = abs(beats - value)
        if diff < best_diff:
            best_diff = diff
            best_symbol = symbol
    return best_symbol


def merge_consecutive_events(events: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """
    Merge consecutive identical notes when possible by adding durations in beat units,
    then splitting back into available duration symbols.
    """
    symbol_to_beats = dict(DURATION_SYMBOLS)
    merged: List[Tuple[str, float]] = []

    for note, symbol in events:
        beats = symbol_to_beats[symbol]
        if merged and merged[-1][0] == note:
            merged[-1] = (note, merged[-1][1] + beats)
        else:
            merged.append((note, beats))

    result: List[Tuple[str, str]] = []
    descending = sorted(DURATION_SYMBOLS, key=lambda x: x[1], reverse=True)

    for note, beats in merged:
        remaining = beats
        while remaining > 0.125:
            chosen_symbol = None
            chosen_beats = None

            for symbol, value in descending:
                if value <= remaining + 0.1:
                    chosen_symbol = symbol
                    chosen_beats = value
                    break

            if chosen_symbol is None:
                chosen_symbol, chosen_beats = "s", 0.25

            result.append((note, chosen_symbol))
            remaining -= chosen_beats

    return result


def smooth_labels(labels: List[str], window: int = 5) -> List[str]:
    """
    Smooth framewise note labels using majority voting.
    """
    if not labels:
        return []

    if window < 1:
        return labels[:]

    smoothed: List[str] = []
    half = window // 2

    for i in range(len(labels)):
        start = max(0, i - half)
        end = min(len(labels), i + half + 1)
        segment = labels[start:end]
        values, counts = np.unique(segment, return_counts=True)
        smoothed.append(str(values[np.argmax(counts)]))

    return smoothed


def transcribe_mp3_to_events(path: Path) -> Tuple[int, str, List[Tuple[str, str]]]:
    y, sr = librosa.load(path.as_posix(), sr=22050, mono=True)

    if len(y) == 0:
        raise ValueError("Audio file appears empty.")

    tempo = estimate_tempo(y, sr)
    instrument = guess_instrument(y)

    hop_length = 512
    frame_length = 2048

    f0, voiced_flag, _ = librosa.pyin(
        y,
        sr=sr,
        fmin=librosa.note_to_hz("C2"),
        fmax=librosa.note_to_hz("C7"),
        frame_length=frame_length,
        hop_length=hop_length,
    )

    rms = librosa.feature.rms(
        y=y,
        frame_length=frame_length,
        hop_length=hop_length
    )[0]

    min_len = min(len(rms), len(f0))
    rms = rms[:min_len]
    f0 = f0[:min_len]
    voiced_flag = voiced_flag[:min_len]

    rms_threshold = max(0.01, float(np.percentile(rms, 25)) * 0.8)
    active = rms > rms_threshold

    seconds_per_frame = hop_length / sr
    beats_per_second = tempo / 60.0
    beats_per_frame = seconds_per_frame * beats_per_second

    labels: List[str] = []
    for i in range(len(f0)):
        is_rest = (
            not active[i]
            or not bool(voiced_flag[i])
            or not np.isfinite(f0[i])
        )

        if is_rest:
            labels.append("R")
        else:
            midi = hz_to_midi_rounded(float(f0[i]))
            labels.append("R" if midi is None else midi_to_note_name(midi))

    labels = smooth_labels(labels, window=5)

    events: List[Tuple[str, str]] = []
    current_label = labels[0]
    current_start = 0

    for i in range(1, len(labels)):
        if labels[i] != current_label:
            length_beats = (i - current_start) * beats_per_frame
            symbol = quantize_beats_to_symbol(length_beats)
            events.append((current_label, symbol))
            current_label = labels[i]
            current_start = i

    length_beats = (len(labels) - current_start) * beats_per_frame
    symbol = quantize_beats_to_symbol(length_beats)
    events.append((current_label, symbol))

    events = merge_consecutive_events(events)
    return tempo, instrument, events


def format_output(tempo: int, instrument: str, events: List[Tuple[str, str]]) -> str:
    lines = [
        f"tempo: {tempo}",
        f"instrument: {instrument}",
        "",
    ]
    for note, duration in events:
        lines.append(f"{note} {duration}")
    return "\n".join(lines) + "\n"


def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: python mp3_to_notes.py input.mp3 output.txt")
        sys.exit(1)

    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])

    if not input_path.exists():
        print(f"Input file not found: {input_path}")
        sys.exit(1)

    tempo, instrument, events = transcribe_mp3_to_events(input_path)
    text = format_output(tempo, instrument, events)
    output_path.write_text(text, encoding="utf-8")

    print(f"Wrote {output_path}")
    print()
    print(text)


if __name__ == "__main__":
    main()