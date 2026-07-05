from pathlib import Path
import shutil

CROPPED_DIR = Path(r"C:\Users\qacer\Downloads\new\new_cropped")
SOURCE_DIR = Path(r"C:\Users\qacer\Downloads\new\new")
DEST_DIR = Path(r"C:\Users\qacer\Downloads\new\delete_me")

CROPPED_SUFFIX = "_cropped"


def unique_destination(path: Path) -> Path:
    """
    Avoid overwriting files already in delete_me.
    Example:
        image.jpg -> image_dup1.jpg
    """
    if not path.exists():
        return path

    counter = 1
    while True:
        new_path = path.with_name(f"{path.stem}_dup{counter}{path.suffix}")
        if not new_path.exists():
            return new_path
        counter += 1


def main():
    DEST_DIR.mkdir(parents=True, exist_ok=True)

    if not CROPPED_DIR.exists():
        raise FileNotFoundError(f"Cropped folder not found: {CROPPED_DIR}")

    if not SOURCE_DIR.exists():
        raise FileNotFoundError(f"Source folder not found: {SOURCE_DIR}")

    moved = 0
    missing = 0
    skipped = 0

    # Only checks files directly inside new_cropped, not subfolders.
    cropped_files = [p for p in CROPPED_DIR.iterdir() if p.is_file()]

    for cropped_file in cropped_files:
        stem = cropped_file.stem

        if not stem.endswith(CROPPED_SUFFIX):
            skipped += 1
            continue

        original_stem = stem[: -len(CROPPED_SUFFIX)]

        # Find files directly inside SOURCE_DIR named:
        # [something].any_extension
        matches = [
            p for p in SOURCE_DIR.iterdir()
            if p.is_file() and p.stem == original_stem
        ]

        if not matches:
            print(f"[MISSING] No original found for: {cropped_file.name}")
            missing += 1
            continue

        for original_file in matches:
            dest_path = unique_destination(DEST_DIR / original_file.name)
            shutil.move(str(original_file), str(dest_path))
            print(f"[MOVED] {original_file.name} -> {dest_path}")
            moved += 1

    print()
    print("Done.")
    print(f"Moved originals: {moved}")
    print(f"Missing originals: {missing}")
    print(f"Skipped non-cropped files: {skipped}")
    print(f"Destination: {DEST_DIR}")


if __name__ == "__main__":
    main()