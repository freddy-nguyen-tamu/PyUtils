#!/usr/bin/env python3
"""
delete_images_recursive_progress.py
Recursively delete all image files from Downloads,
printing progress as files are deleted.
"""

from pathlib import Path

IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp",
    ".tif", ".tiff", ".webp", ".heic", ".heif",
    ".svg", ".ico", ".jfif"
}

ROOT_DIR = Path(r"D:\Users\qacer\Downloads")


def main():
    if not ROOT_DIR.exists():
        print("❌ Target directory does not exist.")
        return

    images = [
        p for p in ROOT_DIR.rglob("*")
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    ]

    if not images:
        print("✅ No image files found.")
        return

    print(f"🖼️ Found {len(images)} image files.\n")

    print("⚠️ THIS WILL PERMANENTLY DELETE ALL IMAGES.")
    confirm = input("Type DELETE to continue: ").strip()

    if confirm != "DELETE":
        print("❌ Aborted.")
        return

    deleted = 0
    failed = 0

    for i, img in enumerate(images, start=1):
        try:
            img.unlink()
            deleted += 1
            print(f"[{i}/{len(images)}] 🗑️ Deleted: {img}")
        except Exception as e:
            failed += 1
            print(f"[{i}/{len(images)}] ❌ Failed: {img} ({e})")

    print("\n✅ Deletion complete.")
    print(f"🗑️ Deleted: {deleted}")
    if failed:
        print(f"⚠️ Failed: {failed}")


if __name__ == "__main__":
    main()