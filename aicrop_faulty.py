from pathlib import Path
import shutil

FAULTY_DIR = Path(r"C:\Users\qacer\Downloads\new\new_cropped\faulty cropped")
SOURCE_DIR = Path(r"C:\Users\qacer\Downloads\new\new")

IMAGE_EXTS = {
    ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif",
    ".tif", ".tiff", ".heic", ".avif"
}

RECURSIVE_SOURCE_SEARCH = False  # change to True if /new has subfolders


def image_files(folder: Path, recursive: bool = False):
    pattern = "**/*" if recursive else "*"

    for path in folder.glob(pattern):
        if path.is_file() and path.suffix.lower() in IMAGE_EXTS:
            yield path


def main():
    if not FAULTY_DIR.exists():
        raise FileNotFoundError(f"Faulty folder not found: {FAULTY_DIR}")

    if not SOURCE_DIR.exists():
        raise FileNotFoundError(f"Source folder not found: {SOURCE_DIR}")

    source_files = list(image_files(SOURCE_DIR, RECURSIVE_SOURCE_SEARCH))

    source_by_stem = {}
    for file in source_files:
        source_by_stem.setdefault(file.stem.lower(), []).append(file)

    moved = 0
    skipped = 0
    missing = 0

    for cropped_file in image_files(FAULTY_DIR):
        stem = cropped_file.stem

        if not stem.lower().endswith("_cropped"):
            continue

        original_stem = stem[:-len("_cropped")]
        matches = source_by_stem.get(original_stem.lower(), [])

        if not matches:
            print(f"[MISSING] No original found for: {cropped_file.name}")
            missing += 1
            continue

        same_ext_matches = [
            m for m in matches
            if m.suffix.lower() == cropped_file.suffix.lower()
        ]

        if same_ext_matches:
            chosen = same_ext_matches[0]
        elif len(matches) == 1:
            chosen = matches[0]
        else:
            print(f"[SKIP] Multiple possible originals for: {cropped_file.name}")
            for m in matches:
                print(f"       - {m}")
            skipped += 1
            continue

        destination = FAULTY_DIR / chosen.name

        if destination.exists():
            print(f"[SKIP] Already exists in faulty folder: {destination.name}")
            skipped += 1
            continue

        shutil.move(str(chosen), str(destination))
        print(f"[MOVED] {chosen.name} -> {FAULTY_DIR}")
        moved += 1

    print()
    print(f"Done.")
    print(f"Moved: {moved}")
    print(f"Missing: {missing}")
    print(f"Skipped: {skipped}")


if __name__ == "__main__":
    main()