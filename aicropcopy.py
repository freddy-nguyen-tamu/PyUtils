import shutil
from pathlib import Path

# Source directory
src_dir = Path(r"C:\Users\qacer\Downloads\new\new")

# Destination directory
dst_dir = src_dir.parent

# Files skipped due to no person detected
files_to_copy = []

for filename in files_to_copy:
    src_file = src_dir / filename
    dst_file = dst_dir / filename

    if src_file.exists():
        shutil.copy2(src_file, dst_file)
        print(f"Copied: {filename}")
    else:
        print(f"Missing: {filename}")
