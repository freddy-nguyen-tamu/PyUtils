import shutil
from pathlib import Path

# Folders
src_dir = Path(r"C:\Users\qacer\Downloads\new\new")
cropped_dir = Path(r"C:\Users\qacer\Downloads\new\new_cropped")
dst_dir = Path(r"C:\Users\qacer\Downloads\new\not_included")

# Create destination folder if it does not exist
dst_dir.mkdir(parents=True, exist_ok=True)

# Check that the required folders exist
if not src_dir.is_dir():
    raise FileNotFoundError(f"Source folder does not exist: {src_dir}")

if not cropped_dir.is_dir():
    raise FileNotFoundError(f"Cropped folder does not exist: {cropped_dir}")

for item in src_dir.iterdir():
    # Skip folders
    if not item.is_file():
        continue

    # Example:
    # photo.jpg -> photo_cropped.jpg
    cropped_filename = f"{item.stem}_cropped{item.suffix}"
    cropped_file = cropped_dir / cropped_filename

    # Copy the original when its cropped counterpart is missing
    if not cropped_file.is_file():
        destination = dst_dir / item.name
        shutil.copy2(item, destination)
        print(f"Copied: {item.name}")
    else:
        print(f"Found cropped version: {cropped_filename}")