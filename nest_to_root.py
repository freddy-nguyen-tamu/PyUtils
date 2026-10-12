from pathlib import Path
import shutil
import tkinter as tk
from tkinter import filedialog, messagebox

# Open folder browser
root = tk.Tk()
root.withdraw()
root.attributes("-topmost", True)

selected_folder = filedialog.askdirectory(
    title="Select folder to flatten",
    initialdir=str(Path.home() / "Downloads")
)

if not selected_folder:
    print("No folder selected. Exiting.")
    raise SystemExit

ROOT = Path(selected_folder)

moved = 0

# Collect all files from subfolders recursively
files = [
    p for p in ROOT.rglob("*")
    if p.is_file() and p.parent != ROOT
]

for src in files:
    dest = ROOT / src.name

    # Prevent overwriting existing files
    if dest.exists():
        stem = src.stem
        suffix = src.suffix
        counter = 1

        while dest.exists():
            dest = ROOT / f"{stem}_{counter}{suffix}"
            counter += 1

    shutil.move(str(src), str(dest))
    print(f"Moved: {src} -> {dest.name}")
    moved += 1

# Remove empty subfolders, deepest first
folders = sorted(
    (p for p in ROOT.rglob("*") if p.is_dir()),
    key=lambda p: len(p.parts),
    reverse=True
)

for folder in folders:
    try:
        folder.rmdir()
        print(f"Removed empty folder: {folder}")
    except OSError:
        pass

messagebox.showinfo(
    "Completed",
    f"Moved {moved} files into:\n{ROOT}\n\nEmpty subfolders removed."
)

root.destroy()
print(f"\nDone! Moved {moved} files to {ROOT}")