import os
import subprocess
import json
import shutil

VIDEO_DIR = r"C:\Users\qacer\Downloads"
TEMP_DIR = os.path.join(VIDEO_DIR, "_qsv_norm")
OUTPUT_FILE = os.path.join(VIDEO_DIR, "merged_output.mp4")

os.makedirs(TEMP_DIR, exist_ok=True)

def probe(file):
    return json.loads(subprocess.check_output([
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=codec_name,width,height,avg_frame_rate",
        "-of", "json",
        file
    ]))

def sig(info):
    s = info["streams"][0]
    return (s["codec_name"], s["width"], s["height"], s["avg_frame_rate"])

files = sorted(
    os.path.join(VIDEO_DIR, f)
    for f in os.listdir(VIDEO_DIR)
    if f.lower().endswith(".mp4")
)

if not files:
    raise SystemExit("No MP4 files found.")

ref_sig = sig(probe(files[0]))
out_files = []

for f in files:
    dst = os.path.join(TEMP_DIR, os.path.basename(f))
    if sig(probe(f)) == ref_sig:
        shutil.copyfile(f, dst)
        print("copy ", os.path.basename(f))
    else:
        print("QSV encode", os.path.basename(f))
        subprocess.run([
            "ffmpeg", "-y",
            "-i", f,
            "-map", "0:v:0", "-map", "0:a?",
            "-c:v", "h264_qsv",
            "-preset", "veryfast",
            "-pix_fmt", "nv12",
            "-r", ref_sig[3],
            "-s", f"{ref_sig[1]}x{ref_sig[2]}",
            "-c:a", "aac",
            dst
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    out_files.append(dst)

list_file = os.path.join(TEMP_DIR, "list.txt")
with open(list_file, "w", encoding="utf-8") as filelist:
    for filename in out_files:
        filelist.write(f"file '{filename}'\n")

subprocess.run([
    "ffmpeg", "-y",
    "-f", "concat",
    "-safe", "0",
    "-i", list_file,
    "-c", "copy",
    OUTPUT_FILE
], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

print("\nCOMPLETE")
print(OUTPUT_FILE)