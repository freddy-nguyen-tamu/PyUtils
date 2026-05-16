import tkinter as tk
import numpy as np
import sounddevice as sd
import threading
from pathlib import Path

SAMPLE_RATE = 44100
BLOCKSIZE = 1024
DEFAULT_FREQ = 440.0
DEFAULT_VOLUME = 0.2

SAVE_FILE = Path("saved_frequencies.txt")

state_lock = threading.Lock()
current_freq = DEFAULT_FREQ
volume = DEFAULT_VOLUME
phase = 0.0

stream = None
playing = False


def audio_callback(outdata, frames, time, status):
    global phase

    with state_lock:
        freq = current_freq
        vol = volume

    phase_inc = 2.0 * np.pi * freq / SAMPLE_RATE
    phases = phase + phase_inc * np.arange(frames, dtype=np.float64)
    phase = (phases[-1] + phase_inc) % (2.0 * np.pi)

    tone = (np.sin(phases) * vol).astype(np.float32)
    outdata[:] = tone.reshape(-1, 1)


def start_audio():
    global stream, playing
    if playing:
        return

    playing = True
    try:
        stream = sd.OutputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=BLOCKSIZE,
            callback=audio_callback,
        )
        stream.start()
    except Exception as e:
        playing = False
        stream = None
        print("Failed to start audio stream:", e)


def stop_audio():
    global stream, playing
    playing = False
    if stream is not None:
        try:
            stream.stop()
            stream.close()
        except Exception as e:
            print("Error stopping stream:", e)
        finally:
            stream = None


def set_frequency_from_entry():
    global current_freq
    try:
        f = float(freq_entry.get())
        if f <= 0:
            return
        with state_lock:
            current_freq = f
    except ValueError:
        pass


def normalize_freq_str(freq: float) -> str:
    # nice formatting (no trailing .0)
    return f"{freq:g}"


def load_saved_frequencies():
    """Load frequencies from SAVE_FILE into the listbox."""
    freq_listbox.delete(0, tk.END)

    if not SAVE_FILE.exists():
        return

    seen = set()
    for line in SAVE_FILE.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            f = float(s)
        except ValueError:
            continue

        key = normalize_freq_str(f)
        if key in seen:
            continue
        seen.add(key)
        freq_listbox.insert(tk.END, f"{key} Hz")


def append_frequency_to_file(freq: float):
    """Append a frequency (as a raw number) to the save file."""
    SAVE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with SAVE_FILE.open("a", encoding="utf-8") as f:
        f.write(f"{normalize_freq_str(freq)}\n")


def current_listbox_frequencies_set():
    """Return a set of normalized frequency strings already in the listbox."""
    items = freq_listbox.get(0, tk.END)
    out = set()
    for it in items:
        out.add(it.replace(" Hz", "").strip())
    return out


def save_frequency():
    try:
        f = float(freq_entry.get())
        if f <= 0:
            return
    except ValueError:
        return

    key = normalize_freq_str(f)

    # Avoid duplicates in UI and file (simple check against UI state)
    existing = current_listbox_frequencies_set()
    if key not in existing:
        append_frequency_to_file(f)
        freq_listbox.insert(tk.END, f"{key} Hz")


def on_select_frequency(event):
    selection = freq_listbox.curselection()
    if not selection:
        return
    val = freq_listbox.get(selection[0]).replace(" Hz", "").strip()
    freq_entry.delete(0, tk.END)
    freq_entry.insert(0, val)
    set_frequency_from_entry()
    start_audio()


def on_entry_change(_event=None):
    set_frequency_from_entry()


def on_close():
    stop_audio()
    root.destroy()


# ---------- UI ----------
root = tk.Tk()
root.title("Continuous Frequency Player (Persistent Presets)")

tk.Label(root, text="Frequency (Hz):").pack(padx=10, pady=(10, 0))

freq_entry = tk.Entry(root, width=20)
freq_entry.insert(0, str(DEFAULT_FREQ))
freq_entry.pack(padx=10, pady=6)
freq_entry.bind("<KeyRelease>", on_entry_change)
freq_entry.bind("<Return>", lambda e: (set_frequency_from_entry(), start_audio()))

btn_frame = tk.Frame(root)
btn_frame.pack(pady=6)

tk.Button(
    btn_frame,
    text="Play",
    width=10,
    command=lambda: (set_frequency_from_entry(), start_audio())
).grid(row=0, column=0, padx=5)

tk.Button(
    btn_frame,
    text="Stop",
    width=10,
    command=stop_audio
).grid(row=0, column=1, padx=5)

tk.Button(root, text="Save Frequency", command=save_frequency).pack(pady=(0, 8))

tk.Label(root, text="Saved Frequencies (loaded from saved_frequencies.txt):").pack()

freq_listbox = tk.Listbox(root, height=10, width=28)
freq_listbox.pack(padx=10, pady=(0, 10))
freq_listbox.bind("<<ListboxSelect>>", on_select_frequency)

# Load saved presets on startup
load_saved_frequencies()

root.protocol("WM_DELETE_WINDOW", on_close)
root.mainloop()