import os
import math
import numpy as np
import librosa
import soundfile as sf
import pyrubberband as pyrb

from scipy.signal import butter, sosfilt, sosfiltfilt, lfilter


# ============================================================
# USER SETTINGS
# ============================================================

VOCAL_PATH = "vocal.wav"
BEAT_PATH = "beat.wav"

OUTPUT_VOCAL_PATH = "final_rapper_vocal.wav"
OUTPUT_MIX_PATH = "final_mix_with_beat.wav"

SR = 44100

# Change these to your song key.
# Common trap/rap choices: A minor, C minor, D# minor, F minor, G minor.
KEY = "A"
SCALE = "minor"

# Auto-Tune style:
# Lower = more robotic / rapper Auto-Tune.
# 40-70 ms = strong rap Auto-Tune.
# 100-160 ms = more natural.
RETUNE_MS = 55

CORRECTION_STRENGTH = 1.0
AUTOTUNE_MIX = 1.0

# Beat timing:
# This nudges syllables closer to the beat.
# Set to False if it sounds glitchy.
ENABLE_TIMING_CORRECTION = True

# Vocal chain:
ENABLE_NOISE_GATE = True
ENABLE_EQ = True
ENABLE_COMPRESSION = True
ENABLE_DEESSER = True
ENABLE_SATURATION = True
ENABLE_DOUBLER = True
ENABLE_DELAY = True
ENABLE_REVERB = True

# Final mix balance:
VOCAL_GAIN_DB = 2.5
BEAT_GAIN_DB = -4.0

# ============================================================
# MUSIC HELPERS
# ============================================================

SCALES = {
    "major": [0, 2, 4, 5, 7, 9, 11],
    "minor": [0, 2, 3, 5, 7, 8, 10],
    "natural_minor": [0, 2, 3, 5, 7, 8, 10],
    "harmonic_minor": [0, 2, 3, 5, 7, 8, 11],
    "melodic_minor": [0, 2, 3, 5, 7, 9, 11],
    "pentatonic_minor": [0, 3, 5, 7, 10],
    "chromatic": list(range(12)),
}

NOTE_TO_MIDI_CLASS = {
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


def db_to_amp(db):
    return 10 ** (db / 20.0)


def amp_to_db(x, eps=1e-9):
    return 20 * np.log10(np.maximum(np.abs(x), eps))


def normalize_peak(y, peak=0.95):
    max_val = np.max(np.abs(y))
    if max_val > 0:
        return y / max_val * peak
    return y


def match_length(y, target_len):
    if len(y) > target_len:
        return y[:target_len]
    if len(y) < target_len:
        return np.pad(y, (0, target_len - len(y)))
    return y


def hz_to_midi_safe(f):
    f = np.asarray(f)
    out = np.full_like(f, np.nan, dtype=float)
    valid = np.isfinite(f) & (f > 0)
    out[valid] = librosa.hz_to_midi(f[valid])
    return out


def nearest_scale_midi(midi_note, key="A", scale="minor"):
    key = key.upper()
    scale = scale.lower()

    if key not in NOTE_TO_MIDI_CLASS:
        raise ValueError(f"Unknown key: {key}")

    if scale not in SCALES:
        raise ValueError(f"Unknown scale: {scale}")

    root = NOTE_TO_MIDI_CLASS[key]
    scale_notes = SCALES[scale]

    candidates = []

    for octave in range(-2, 12):
        base = octave * 12 + root
        for interval in scale_notes:
            candidates.append(base + interval)

    candidates = np.array(candidates)
    return candidates[np.argmin(np.abs(candidates - midi_note))]


# ============================================================
# BASIC AUDIO FILTERS
# ============================================================

def highpass(y, sr, cutoff=80.0, order=4):
    sos = butter(order, cutoff, btype="highpass", fs=sr, output="sos")
    return sosfiltfilt(sos, y)


def lowpass(y, sr, cutoff=16000.0, order=4):
    sos = butter(order, cutoff, btype="lowpass", fs=sr, output="sos")
    return sosfiltfilt(sos, y)


def bandpass(y, sr, low=5000.0, high=10000.0, order=4):
    sos = butter(order, [low, high], btype="bandpass", fs=sr, output="sos")
    return sosfiltfilt(sos, y)


def low_shelf_simple(y, sr, cutoff=180.0, gain_db=-2.0):
    """
    Simple low shelf approximation using lowpass blend.
    Negative gain reduces low end.
    """
    low = lowpass(y, sr, cutoff=cutoff)
    return y + low * (db_to_amp(gain_db) - 1.0)


def high_shelf_simple(y, sr, cutoff=6000.0, gain_db=2.5):
    """
    Simple high shelf approximation using highpass blend.
    Positive gain brightens vocal.
    """
    high = highpass(y, sr, cutoff=cutoff)
    return y + high * (db_to_amp(gain_db) - 1.0)


def peaking_simple(y, sr, center=2500.0, width=1200.0, gain_db=2.0):
    """
    Simple peaking EQ approximation using bandpass blend.
    """
    low = max(30.0, center - width / 2)
    high = min(sr / 2 - 100, center + width / 2)
    band = bandpass(y, sr, low=low, high=high)
    return y + band * (db_to_amp(gain_db) - 1.0)


# ============================================================
# NOISE GATE
# ============================================================

def noise_gate(y, sr, threshold_db=-45.0, attack_ms=5.0, release_ms=90.0):
    """
    Reduces quiet room noise between vocal phrases.
    """
    frame = int(0.01 * sr)
    if frame < 1:
        frame = 1

    rms = np.sqrt(np.convolve(y ** 2, np.ones(frame) / frame, mode="same"))
    level_db = amp_to_db(rms)

    target = np.where(level_db > threshold_db, 1.0, 0.15)

    attack_coeff = math.exp(-1.0 / max(1, int(sr * attack_ms / 1000)))
    release_coeff = math.exp(-1.0 / max(1, int(sr * release_ms / 1000)))

    env = np.zeros_like(target)
    env[0] = target[0]

    for i in range(1, len(target)):
        if target[i] > env[i - 1]:
            coeff = attack_coeff
        else:
            coeff = release_coeff
        env[i] = coeff * env[i - 1] + (1 - coeff) * target[i]

    return y * env


# ============================================================
# PITCH DETECTION + AUTO-TUNE
# ============================================================

def detect_chunk_pitch(chunk, sr, fmin="C2", fmax="C6"):
    """
    Detect median pitch of a chunk.
    Returns MIDI pitch or None.
    """
    if len(chunk) < 1024:
        return None

    try:
        f0, voiced_flag, voiced_prob = librosa.pyin(
            chunk,
            fmin=librosa.note_to_hz(fmin),
            fmax=librosa.note_to_hz(fmax),
            sr=sr,
            frame_length=2048,
            hop_length=256,
        )
    except Exception:
        return None

    midi = hz_to_midi_safe(f0)
    valid = np.isfinite(midi)

    if np.sum(valid) < 2:
        return None

    return float(np.nanmedian(midi[valid]))


def pitch_shift_fixed_length(chunk, sr, semitones):
    """
    Pitch shift with Rubber Band, then trim/pad to original length.
    """
    original_len = len(chunk)

    if abs(semitones) < 0.01:
        return chunk.copy()

    try:
        shifted = pyrb.pitch_shift(chunk, sr, semitones)
    except Exception:
        shifted = librosa.effects.pitch_shift(
            chunk,
            sr=sr,
            n_steps=semitones,
        )

    return match_length(shifted, original_len)


def hard_autotune(
    vocal,
    sr,
    key="A",
    scale="minor",
    retune_ms=55,
    correction_strength=1.0,
    mix=1.0,
    fmin="C2",
    fmax="C6",
):
    """
    Rapper-style hard Auto-Tune.

    retune_ms:
        40-70 = obvious modern rap effect
        80-120 = medium
        130+ = natural

    correction_strength:
        1.0 = full snap
        0.5 = partial snap

    mix:
        1.0 = fully tuned
        0.7 = blended
    """

    chunk_size = int(sr * retune_ms / 1000)
    chunk_size = max(chunk_size, 1024)

    hop_size = chunk_size // 2

    output = np.zeros(len(vocal) + chunk_size)
    weight = np.zeros(len(vocal) + chunk_size)

    window = np.hanning(chunk_size)

    for start in range(0, len(vocal), hop_size):
        end = start + chunk_size
        chunk = vocal[start:end]

        if len(chunk) < chunk_size:
            chunk = np.pad(chunk, (0, chunk_size - len(chunk)))

        windowed = chunk * window

        detected_midi = detect_chunk_pitch(
            windowed,
            sr,
            fmin=fmin,
            fmax=fmax,
        )

        if detected_midi is None:
            tuned = windowed
        else:
            target_midi = nearest_scale_midi(
                detected_midi,
                key=key,
                scale=scale,
            )

            semitone_shift = target_midi - detected_midi
            semitone_shift *= correction_strength

            tuned = pitch_shift_fixed_length(
                windowed,
                sr,
                semitone_shift,
            )

        output[start:start + chunk_size] += tuned * window
        weight[start:start + chunk_size] += window ** 2

    output = output[:len(vocal)]
    weight = weight[:len(vocal)]

    valid = weight > 1e-8
    output[valid] /= weight[valid]

    result = vocal * (1.0 - mix) + output * mix
    return normalize_peak(result, 0.95)


# ============================================================
# TIMING CORRECTION
# ============================================================

def align_vocal_timing_to_beat(
    vocal,
    instrumental,
    sr,
    max_shift_seconds=0.055,
    snap_strength=0.45,
):
    """
    Moves vocal phrase chunks slightly toward beat grid.
    This is not perfect, but it can make rap vocals feel tighter.
    """

    try:
        tempo, beat_frames = librosa.beat.beat_track(y=instrumental, sr=sr)
        beat_times = librosa.frames_to_time(beat_frames, sr=sr)
    except Exception:
        return vocal

    try:
        onset_frames = librosa.onset.onset_detect(
            y=vocal,
            sr=sr,
            backtrack=True,
            units="frames",
        )
        onset_times = librosa.frames_to_time(onset_frames, sr=sr)
    except Exception:
        return vocal

    if len(beat_times) == 0 or len(onset_times) == 0:
        return vocal

    boundaries = np.concatenate(([0.0], onset_times, [len(vocal) / sr]))
    boundaries = np.unique(boundaries)

    segments = []

    for i in range(len(boundaries) - 1):
        start_t = boundaries[i]
        end_t = boundaries[i + 1]

        start_sample = int(start_t * sr)
        end_sample = int(end_t * sr)

        segment = vocal[start_sample:end_sample]

        if len(segment) < 256:
            continue

        nearest_beat = beat_times[np.argmin(np.abs(beat_times - start_t))]
        shift = nearest_beat - start_t
        shift = np.clip(shift, -max_shift_seconds, max_shift_seconds)

        shifted_start_t = start_t + shift * snap_strength

        if i < len(boundaries) - 2:
            next_start_t = boundaries[i + 1]
            nearest_next_beat = beat_times[np.argmin(np.abs(beat_times - next_start_t))]
            next_shift = nearest_next_beat - next_start_t
            next_shift = np.clip(next_shift, -max_shift_seconds, max_shift_seconds)

            shifted_next_t = next_start_t + next_shift * snap_strength

            old_duration = end_t - start_t
            new_duration = max(0.025, shifted_next_t - shifted_start_t)

            stretch_ratio = old_duration / new_duration

            if 0.70 <= stretch_ratio <= 1.35:
                try:
                    segment = pyrb.time_stretch(segment, sr, stretch_ratio)
                except Exception:
                    pass

        segments.append(segment)

    if not segments:
        return vocal

    aligned = np.concatenate(segments)
    aligned = match_length(aligned, len(vocal))

    return normalize_peak(aligned, 0.95)


# ============================================================
# VOCAL MIXING EFFECTS
# ============================================================

def vocal_eq(y, sr):
    """
    Rap vocal EQ:
    - removes rumble
    - reduces mud
    - adds presence
    - adds brightness
    """
    y = highpass(y, sr, cutoff=85.0)
    y = low_shelf_simple(y, sr, cutoff=220.0, gain_db=-2.5)
    y = peaking_simple(y, sr, center=450.0, width=300.0, gain_db=-1.5)
    y = peaking_simple(y, sr, center=2500.0, width=1600.0, gain_db=2.5)
    y = high_shelf_simple(y, sr, cutoff=6500.0, gain_db=2.0)
    return normalize_peak(y, 0.95)


def compressor(
    y,
    sr,
    threshold_db=-18.0,
    ratio=3.5,
    attack_ms=8.0,
    release_ms=90.0,
    makeup_gain_db=3.0,
):
    """
    Simple vocal compressor.
    Makes vocal more consistent and upfront.
    """
    abs_y = np.abs(y) + 1e-9

    frame = int(0.005 * sr)
    frame = max(frame, 1)

    rms = np.sqrt(np.convolve(y ** 2, np.ones(frame) / frame, mode="same"))
    level_db = amp_to_db(rms)

    over_db = level_db - threshold_db
    gain_reduction_db = np.where(
        over_db > 0,
        over_db * (1.0 - 1.0 / ratio),
        0.0,
    )

    target_gain_db = -gain_reduction_db

    attack_coeff = math.exp(-1.0 / max(1, int(sr * attack_ms / 1000)))
    release_coeff = math.exp(-1.0 / max(1, int(sr * release_ms / 1000)))

    smooth_gain_db = np.zeros_like(target_gain_db)
    smooth_gain_db[0] = target_gain_db[0]

    for i in range(1, len(target_gain_db)):
        if target_gain_db[i] < smooth_gain_db[i - 1]:
            coeff = attack_coeff
        else:
            coeff = release_coeff

        smooth_gain_db[i] = coeff * smooth_gain_db[i - 1] + (1 - coeff) * target_gain_db[i]

    gain = db_to_amp(smooth_gain_db + makeup_gain_db)
    compressed = y * gain

    return normalize_peak(compressed, 0.95)


def deesser(
    y,
    sr,
    threshold_db=-25.0,
    reduction_db=6.0,
    low=5500.0,
    high=9500.0,
):
    """
    Reduces harsh S/T sounds.
    """
    sibilance = bandpass(y, sr, low=low, high=high)

    frame = int(0.005 * sr)
    frame = max(frame, 1)

    rms = np.sqrt(np.convolve(sibilance ** 2, np.ones(frame) / frame, mode="same"))
    level_db = amp_to_db(rms)

    reduction = np.where(level_db > threshold_db, db_to_amp(-reduction_db), 1.0)

    # Smooth reduction
    smooth = np.zeros_like(reduction)
    smooth[0] = reduction[0]

    coeff = math.exp(-1.0 / max(1, int(sr * 0.015)))

    for i in range(1, len(reduction)):
        smooth[i] = coeff * smooth[i - 1] + (1 - coeff) * reduction[i]

    high_band = highpass(y, sr, cutoff=low)
    low_band = y - high_band

    processed_high = high_band * smooth

    return normalize_peak(low_band + processed_high, 0.95)


def saturation(y, drive=1.8, mix=0.35):
    """
    Adds harmonic distortion so the vocal cuts through the beat.
    """
    driven = np.tanh(y * drive)
    out = y * (1.0 - mix) + driven * mix
    return normalize_peak(out, 0.95)


def stereo_doubler_mono_to_stereo(y, sr, delay_ms=18.0, detune_cents=5.0, width=0.35):
    """
    Creates a stereo widened vocal from mono.
    """
    delay_samples = int(sr * delay_ms / 1000)

    delayed = np.pad(y, (delay_samples, 0))[:len(y)]

    try:
        detuned = pyrb.pitch_shift(delayed, sr, detune_cents / 100.0)
        detuned = match_length(detuned, len(y))
    except Exception:
        detuned = delayed

    left = y + detuned * width
    right = y - detuned * width

    stereo = np.stack([left, right], axis=1)
    stereo = stereo / max(1.0, np.max(np.abs(stereo)) / 0.95)

    return stereo


def stereo_delay(stereo, sr, delay_ms=260.0, feedback=0.22, mix=0.18):
    """
    Adds rap-style delay.
    """
    delay_samples = int(sr * delay_ms / 1000)
    out = stereo.copy()

    delayed = np.zeros_like(stereo)

    for ch in range(2):
        for i in range(delay_samples, len(stereo)):
            delayed[i, ch] = stereo[i - delay_samples, ch] + delayed[i - delay_samples, ch] * feedback

    # Slight left/right difference
    delayed[:, 0] *= 0.85
    delayed[:, 1] *= 1.0

    out = stereo * (1.0 - mix) + delayed * mix

    return normalize_stereo(out)


def simple_reverb(stereo, sr, mix=0.16):
    """
    Simple artificial reverb using multiple echoes.
    """
    delays_ms = [37, 61, 89, 127, 173, 211]
    gains = [0.30, 0.24, 0.20, 0.16, 0.12, 0.09]

    wet = np.zeros_like(stereo)

    for d_ms, g in zip(delays_ms, gains):
        d = int(sr * d_ms / 1000)
        echo = np.pad(stereo, ((d, 0), (0, 0)))[:len(stereo)]
        wet += echo * g

    # Darken reverb so it sits behind vocal
    wet_left = lowpass(wet[:, 0], sr, cutoff=6500.0)
    wet_right = lowpass(wet[:, 1], sr, cutoff=6500.0)
    wet = np.stack([wet_left, wet_right], axis=1)

    out = stereo * (1.0 - mix) + wet * mix
    return normalize_stereo(out)


def normalize_stereo(stereo, peak=0.95):
    max_val = np.max(np.abs(stereo))
    if max_val > 0:
        stereo = stereo / max_val * peak
    return stereo


def limiter(y, ceiling=0.95):
    """
    Simple hard limiter.
    """
    return np.clip(y, -ceiling, ceiling)


# ============================================================
# FULL PIPELINE
# ============================================================

def process_rapper_vocal(
    vocal_path,
    beat_path=None,
    output_vocal_path="final_rapper_vocal.wav",
    output_mix_path="final_mix_with_beat.wav",
    sr=44100,
    key="A",
    scale="minor",
):
    if not os.path.exists(vocal_path):
        raise FileNotFoundError(f"Could not find vocal file: {vocal_path}")

    print("Loading vocal...")
    vocal, _ = librosa.load(vocal_path, sr=sr, mono=True)
    vocal = normalize_peak(vocal, 0.90)

    beat = None
    if beat_path is not None and os.path.exists(beat_path):
        print("Loading beat...")
        beat, _ = librosa.load(beat_path, sr=sr, mono=False)

        if beat.ndim == 1:
            beat_mono = beat
        else:
            beat_mono = np.mean(beat, axis=0)

        beat_mono = match_length(beat_mono, len(vocal))
    else:
        beat_mono = None

    if ENABLE_NOISE_GATE:
        print("Applying noise gate...")
        vocal = noise_gate(vocal, sr)

    if ENABLE_TIMING_CORRECTION and beat_mono is not None:
        print("Tightening vocal timing to beat...")
        vocal = align_vocal_timing_to_beat(
            vocal,
            beat_mono,
            sr,
            max_shift_seconds=0.055,
            snap_strength=0.45,
        )

    print("Applying rapper-style Auto-Tune...")
    vocal = hard_autotune(
        vocal,
        sr,
        key=key,
        scale=scale,
        retune_ms=RETUNE_MS,
        correction_strength=CORRECTION_STRENGTH,
        mix=AUTOTUNE_MIX,
        fmin="C2",
        fmax="C6",
    )

    if ENABLE_EQ:
        print("Applying EQ...")
        vocal = vocal_eq(vocal, sr)

    if ENABLE_COMPRESSION:
        print("Applying compression...")
        vocal = compressor(
            vocal,
            sr,
            threshold_db=-19.0,
            ratio=3.8,
            attack_ms=7.0,
            release_ms=85.0,
            makeup_gain_db=3.5,
        )

    if ENABLE_DEESSER:
        print("Applying de-esser...")
        vocal = deesser(
            vocal,
            sr,
            threshold_db=-26.0,
            reduction_db=5.5,
            low=5400.0,
            high=9800.0,
        )

    if ENABLE_SATURATION:
        print("Applying saturation...")
        vocal = saturation(
            vocal,
            drive=1.7,
            mix=0.32,
        )

    vocal *= db_to_amp(VOCAL_GAIN_DB)
    vocal = normalize_peak(vocal, 0.92)

    print("Creating stereo vocal...")
    if ENABLE_DOUBLER:
        stereo_vocal = stereo_doubler_mono_to_stereo(
            vocal,
            sr,
            delay_ms=17.0,
            detune_cents=5.0,
            width=0.32,
        )
    else:
        stereo_vocal = np.stack([vocal, vocal], axis=1)

    if ENABLE_DELAY:
        print("Adding delay...")
        stereo_vocal = stereo_delay(
            stereo_vocal,
            sr,
            delay_ms=260.0,
            feedback=0.20,
            mix=0.16,
        )

    if ENABLE_REVERB:
        print("Adding reverb...")
        stereo_vocal = simple_reverb(
            stereo_vocal,
            sr,
            mix=0.14,
        )

    stereo_vocal = limiter(stereo_vocal, ceiling=0.95)
    stereo_vocal = normalize_stereo(stereo_vocal, 0.95)

    print(f"Saving vocal: {output_vocal_path}")
    sf.write(output_vocal_path, stereo_vocal, sr)

    if beat_path is not None and os.path.exists(beat_path):
        print("Creating final mix with beat...")

        beat_loaded, _ = librosa.load(beat_path, sr=sr, mono=False)

        if beat_loaded.ndim == 1:
            beat_stereo = np.stack([beat_loaded, beat_loaded], axis=1)
        else:
            beat_stereo = beat_loaded.T

        target_len = max(len(beat_stereo), len(stereo_vocal))

        beat_stereo = np.pad(
            beat_stereo,
            ((0, max(0, target_len - len(beat_stereo))), (0, 0)),
        )[:target_len]

        stereo_vocal_mix = np.pad(
            stereo_vocal,
            ((0, max(0, target_len - len(stereo_vocal))), (0, 0)),
        )[:target_len]

        beat_stereo = beat_stereo * db_to_amp(BEAT_GAIN_DB)

        final_mix = beat_stereo + stereo_vocal_mix
        final_mix = limiter(final_mix, ceiling=0.98)
        final_mix = normalize_stereo(final_mix, 0.98)

        print(f"Saving final mix: {output_mix_path}")
        sf.write(output_mix_path, final_mix, sr)

    print("Done.")


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    process_rapper_vocal(
        vocal_path=VOCAL_PATH,
        beat_path=BEAT_PATH,
        output_vocal_path=OUTPUT_VOCAL_PATH,
        output_mix_path=OUTPUT_MIX_PATH,
        sr=SR,
        key=KEY,
        scale=SCALE,
    )