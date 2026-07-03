from pathlib import Path
import shutil

base = Path(r"C:\Users\qacer\Downloads\new\keep_safe")

cropped_dir = base / "cropped_files"
rest_dir = base / "rest_files"

cropped_dir.mkdir(exist_ok=True)
rest_dir.mkdir(exist_ok=True)

for item in base.iterdir():
    # Only process files directly inside keep_safe
    if not item.is_file():
        continue

    # Skip anything already inside subfolders automatically because iterdir() is not recursive
    if item.stem.endswith("_cropped"):
        destination = cropped_dir / item.name
    else:
        destination = rest_dir / item.name

    # Avoid overwriting existing files
    if destination.exists():
        counter = 1
        while True:
            new_name = f"{item.stem}_{counter}{item.suffix}"
            new_destination = destination.parent / new_name
            if not new_destination.exists():
                destination = new_destination
                break
            counter += 1

    shutil.move(str(item), str(destination))
    print(f"Moved: {item.name} -> {destination.parent.name}")

print("Done.")