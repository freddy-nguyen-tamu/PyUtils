import os
import subprocess

VIDEO_DIR = r"C:\Users\qacer\Downloads"
OUTPUT_FILE = "merged_output.mp4"

# Collect mp4 files in alphabetical order
mp4_files = sorted(
    f for f in os.listdir(VIDEO_DIR)
    if f.lower().endswith(".mp4")
)

if not mp4_files:
    raise ValueError("No MP4 files found.")

# Create a temporary file list for ffmpeg
list_file = os.path.join(VIDEO_DIR, "filelist.txt")
with open(list_file, "w", encoding="utf-8") as f:
    for file in mp4_files:
        f.write(f"file '{os.path.join(VIDEO_DIR, file)}'\n")

# Run ffmpeg concat
cmd = [
    "ffmpeg", "-y",
    "-f", "concat",
    "-safe", "0",
    "-i", list_file,
    "-c", "copy",
    OUTPUT_FILE
]

subprocess.run(cmd, check=True)

# Cleanup
os.remove(list_file)

print("Merge complete:", OUTPUT_FILE)