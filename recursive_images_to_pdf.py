#!/usr/bin/env python3
"""
images_to_pdf_recursive_progress.py
Recursively convert all images in Downloads and subfolders into one PDF,
printing progress as images are added.
"""

import re
from pathlib import Path
from typing import List, Tuple
from PIL import Image, ImageOps

IMAGE_EXTS = {
    ".jpg", ".jpeg", ".png", ".webp", ".bmp",
    ".tif", ".tiff", ".gif", ".heic", ".heif"
}

ROOT_DIR = Path(r"D:\Users\qacer\Downloads")
OUTPUT_PDF = Path(r"D:\Users\qacer\Downloads\all_images.pdf")


def natural_key(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def find_images_recursive(root: Path):
    return [
        p for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS
    ]


def load_and_prepare_image(path: Path) -> Image.Image:
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)

    if getattr(img, "is_animated", False):
        img.seek(0)

    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        img = Image.alpha_composite(bg, rgba).convert("RGB")
    else:
        img = img.convert("RGB")

    return img


def main():
    images = find_images_recursive(ROOT_DIR)

    if not images:
        print("✅ No images found.")
        return

    images.sort(key=lambda p: natural_key(p.as_posix()))

    print(f"🖼️ Found {len(images)} images.")
    prepared = []
    failures: List[Tuple[Path, str]] = []

    for i, img_path in enumerate(images, start=1):
        try:
            print(f"[{i}/{len(images)}] ➕ Adding to PDF: {img_path}")
            prepared.append(load_and_prepare_image(img_path))
        except Exception as e:
            failures.append((img_path, str(e)))
            print(f"❌ Failed: {img_path} ({e})")

    if not prepared:
        print("❌ No images could be processed.")
        return

    print("💾 Writing PDF...")
    prepared[0].save(OUTPUT_PDF, "PDF", save_all=True, append_images=prepared[1:])

    print(f"✅ PDF created: {OUTPUT_PDF}")
    print(f"📄 Pages: {len(prepared)}")

    if failures:
        print(f"⚠️ Skipped {len(failures)} files.")


if __name__ == "__main__":
    main()