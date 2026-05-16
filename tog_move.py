from pathlib import Path
import shutil

ROOT = Path(r"C:\Users\qacer\Downloads\new\jornpt\tower_of_god_images")

# All files from subfolders will be moved here
OUTPUT_FOLDER = ROOT / "_all_images_flattened"


def get_unique_path(destination_folder: Path, filename: str) -> Path:
    """
    If filename already exists, create:
        file.jpg
        file_1.jpg
        file_2.jpg
        etc.
    """
    target = destination_folder / filename

    if not target.exists():
        return target

    stem = target.stem
    suffix = target.suffix

    counter = 1
    while True:
        new_target = destination_folder / f"{stem}_{counter}{suffix}"
        if not new_target.exists():
            return new_target
        counter += 1


def main():
    if not ROOT.exists():
        print(f"Root folder does not exist: {ROOT}")
        return

    OUTPUT_FOLDER.mkdir(exist_ok=True)

    moved_count = 0

    for item in ROOT.rglob("*"):
        if not item.is_file():
            continue

        # Skip files already inside the output folder
        if OUTPUT_FOLDER in item.parents:
            continue

        destination = get_unique_path(OUTPUT_FOLDER, item.name)

        print(f"Moving:\n  {item}\n-> {destination}\n")

        shutil.move(str(item), str(destination))
        moved_count += 1

    print(f"Done. Moved {moved_count} files into:")
    print(OUTPUT_FOLDER)


if __name__ == "__main__":
    main()