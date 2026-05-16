from pathlib import Path

FOLDER = Path(r"C:\Users\qacer\Downloads\py\fb_photos")
PREFIXES = ("keep_", "to_be_deleted_", "marked_", "to_be_removed_")


def remove_prefixes(filename: str) -> str:
    changed = True
    name = filename

    # remove repeatedly in case a file got prefixed multiple times
    while changed:
        changed = False
        for prefix in PREFIXES:
            if name.startswith(prefix):
                name = name[len(prefix):]
                changed = True
    return name


def main():
    if not FOLDER.exists():
        raise FileNotFoundError(f"Folder not found: {FOLDER}")

    renamed = 0
    skipped = 0

    for path in FOLDER.iterdir():
        if not path.is_file():
            continue

        new_name = remove_prefixes(path.name)

        if new_name == path.name:
            skipped += 1
            continue

        new_path = path.with_name(new_name)

        if new_path.exists():
            print(f"SKIP already exists: {new_path.name}")
            skipped += 1
            continue

        path.rename(new_path)
        print(f"RESTORED: {path.name} -> {new_path.name}")
        renamed += 1

    print("\nDone.")
    print(f"Renamed: {renamed}")
    print(f"Skipped: {skipped}")


if __name__ == "__main__":
    main()