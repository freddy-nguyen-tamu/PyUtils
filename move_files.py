from pathlib import Path
import csv
import shutil
from datetime import datetime
import argparse

SOURCE = Path(r"C:\Users\qacer\Downloads\new\new")
DESTINATION = Path(r"C:\Users\qacer\Downloads\image_preference_ai_incremental_retention\image_preference_ai_incremental_retention\dataset\current\1")
LOG_FILE = Path(r"C:\Users\qacer\Downloads\move_to_1_log.csv")


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path

    stem = path.stem
    suffix = path.suffix
    parent = path.parent

    i = 1
    while True:
        candidate = parent / f"{stem}_{i}{suffix}"
        if not candidate.exists():
            return candidate
        i += 1


def move_files():
    if not SOURCE.exists():
        raise FileNotFoundError(f"Source folder does not exist: {SOURCE}")

    DESTINATION.mkdir(parents=True, exist_ok=True)

    moved_rows = []

    for file_path in SOURCE.iterdir():
        if not file_path.is_file():
            continue

        target_path = unique_path(DESTINATION / file_path.name)
        shutil.move(str(file_path), str(target_path))

        moved_rows.append({
            "original_path": str(file_path),
            "new_path": str(target_path),
            "moved_at": datetime.now().isoformat(timespec="seconds"),
        })

        print(f"Moved: {file_path} -> {target_path}")

    with LOG_FILE.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["original_path", "new_path", "moved_at"]
        )
        writer.writeheader()
        writer.writerows(moved_rows)

    print()
    print("Move complete.")
    print(f"Log saved to: {LOG_FILE}")


def revert_move():
    if not LOG_FILE.exists():
        raise FileNotFoundError(f"No log file found: {LOG_FILE}")

    with LOG_FILE.open("r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    for row in rows:
        current_path = Path(row["new_path"])
        original_path = Path(row["original_path"])

        if not current_path.exists():
            print(f"Missing, skipped: {current_path}")
            continue

        original_path.parent.mkdir(parents=True, exist_ok=True)

        restore_path = unique_path(original_path)
        shutil.move(str(current_path), str(restore_path))

        print(f"Reverted: {current_path} -> {restore_path}")

    print()
    print("Revert complete.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--revert",
        action="store_true",
        help="Undo the move using the saved CSV log."
    )
    args = parser.parse_args()

    if args.revert:
        revert_move()
    else:
        move_files()


if __name__ == "__main__":
    main()