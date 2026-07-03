# move_direct_files_to_delete_me.py
from pathlib import Path
import shutil

SOURCE_DIR = Path(r"C:\Users\qacer\Downloads\new\new")
DEST_DIR = SOURCE_DIR / "delete_me"

def unique_destination(path: Path) -> Path:
    """
    If filename already exists in delete_me, create:
    file (1).ext, file (2).ext, etc.
    """
    if not path.exists():
        return path

    stem = path.stem
    suffix = path.suffix
    parent = path.parent

    counter = 1
    while True:
        new_path = parent / f"{stem} ({counter}){suffix}"
        if not new_path.exists():
            return new_path
        counter += 1

def main():
    if not SOURCE_DIR.exists():
        print(f"[ERROR] Folder does not exist: {SOURCE_DIR}")
        return

    if not SOURCE_DIR.is_dir():
        print(f"[ERROR] Not a folder: {SOURCE_DIR}")
        return

    DEST_DIR.mkdir(exist_ok=True)

    moved = 0
    skipped_dirs = 0

    for item in SOURCE_DIR.iterdir():
        if item == DEST_DIR:
            print(f"[SKIP DESTINATION FOLDER] {item}")
            skipped_dirs += 1
            continue

        if item.is_file():
            dest = unique_destination(DEST_DIR / item.name)
            print(f"[MOVE] {item} -> {dest}")
            shutil.move(str(item), str(dest))
            moved += 1
        elif item.is_dir():
            print(f"[SKIP FOLDER] {item}")
            skipped_dirs += 1
        else:
            print(f"[SKIP OTHER] {item}")

    print()
    print("Done.")
    print(f"Moved files: {moved}")
    print(f"Skipped folders: {skipped_dirs}")

if __name__ == "__main__":
    main()