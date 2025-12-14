from pathlib import Path
from send2trash import send2trash

def main():
    target_dir = Path(r"C:\Users\qacer\Downloads\new\new")

    # to be moved to Recycle Bin
    files_to_trash = {}

    trashed = 0
    missing = 0

    for name in files_to_trash:
        file_path = target_dir / name
        if file_path.exists():
            try:
                send2trash(str(file_path))
                trashed += 1
                print(f"[MOVED TO RECYCLE BIN] {name}")
            except Exception as e:
                print(f"[ERROR] Could not trash {name}: {e}")
        else:
            missing += 1
            print(f"[NOT FOUND] {name}")

    print("\n=== Done ===")
    print(f"Moved to Recycle Bin: {trashed}")
    print(f"Not found: {missing}")

if __name__ == "__main__":
    main()
