import shutil
from pathlib import Path

# Source and destination directories
src_dir = Path(r"C:\Users\qacer\Downloads\new\new")
dst_dir = src_dir.parent

# Common image extensions (case-insensitive)
image_extensions = {
    ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".tif", ".gif", ".heic"
}

for item in src_dir.iterdir():
    # Only process files, skip folders
    if item.is_file():
        # If file is not an image
        if item.suffix.lower() not in image_extensions:
            dst_file = dst_dir / item.name
            shutil.copy2(item, dst_file)
            print(f"Copied: {item.name}")
