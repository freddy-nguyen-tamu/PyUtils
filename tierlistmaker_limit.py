import argparse
import csv
from pathlib import Path
from typing import Optional

from PIL import Image, ImageOps, UnidentifiedImageError

DEFAULT_SOURCE_FOLDER = Path(r"C:\Users\qacer\Downloads\py\imdb_output")


def default_output_folder(source_folder: Path) -> Path:
    return source_folder.with_name(source_folder.name + "_tiermaker_small")


SOURCE_FOLDER = DEFAULT_SOURCE_FOLDER
OUTPUT_FOLDER = default_output_folder(SOURCE_FOLDER)

# TierMaker shows portrait images around 80x120, so 300x450 is plenty.
MAX_WIDTH = 300
MAX_HEIGHT = 450

# 90 KB each keeps 500 images around 45 MB, under TierMaker's 50 MB batch limit.
TARGET_BYTES = 90 * 1024

QUALITY_START = 85
QUALITY_MIN = 45

SUPPORTED_INPUTS = {
    ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"
}


def format_bytes(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.2f} MB"
    return f"{size / 1024:.1f} KB"


def prepare_image(path: Path, max_width: int, max_height: int) -> Image.Image:
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)

    # Convert transparency to white background for reliable WEBP/JPG output.
    if img.mode in ("RGBA", "LA") or (
        img.mode == "P" and "transparency" in img.info
    ):
        img = img.convert("RGBA")
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        bg.alpha_composite(img)
        img = bg.convert("RGB")
    else:
        img = img.convert("RGB")

    img.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
    return img


def save_webp_under_target(img: Image.Image, out_path: Path) -> tuple[int, int]:
    """
    Returns: final_quality, final_size_bytes
    """
    working = img.copy()

    while True:
        for quality in range(QUALITY_START, QUALITY_MIN - 1, -5):
            working.save(
                out_path,
                "WEBP",
                quality=quality,
                method=6,
                optimize=True,
            )

            size = out_path.stat().st_size
            if size <= TARGET_BYTES:
                return quality, size

        # If quality alone was not enough, shrink dimensions by 10%.
        new_width = int(working.width * 0.90)
        new_height = int(working.height * 0.90)

        if new_width < 80 or new_height < 80:
            # Stop before making images unusably tiny.
            return QUALITY_MIN, out_path.stat().st_size

        working = working.resize(
            (new_width, new_height),
            Image.Resampling.LANCZOS,
        )


def unique_output_path(output_folder: Path, stem: str) -> Path:
    out_path = output_folder / f"{stem}.webp"
    counter = 2

    while out_path.exists():
        out_path = output_folder / f"{stem}_{counter}.webp"
        counter += 1

    return out_path


def main(source_folder: Optional[Path] = None, output_folder: Optional[Path] = None) -> None:
    source_folder = source_folder or SOURCE_FOLDER
    output_folder = output_folder or default_output_folder(source_folder)

    if not source_folder.exists():
        raise FileNotFoundError(f"Folder not found: {source_folder}")

    output_folder.mkdir(parents=True, exist_ok=True)

    images = [
        p for p in source_folder.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_INPUTS
    ]

    if not images:
        print(f"No supported images found in: {source_folder}")
        return

    print(f"Input folder:  {source_folder}")
    print(f"Output folder: {output_folder}")
    print(f"Images found:  {len(images)}")
    print(f"Target size:   {format_bytes(TARGET_BYTES)} per image")
    print()

    report_rows = []
    total_before = 0
    total_after = 0
    converted = 0
    failed = 0

    for index, path in enumerate(images, start=1):
        print(f"[{index}/{len(images)}] Processing: {path.name}")

        try:
            before_size = path.stat().st_size
            img = prepare_image(path, MAX_WIDTH, MAX_HEIGHT)

            out_path = unique_output_path(output_folder, path.stem)
            quality, after_size = save_webp_under_target(img, out_path)

            total_before += before_size
            total_after += after_size
            converted += 1

            print(f"  Original: {format_bytes(before_size)}")
            print(f"  New:      {format_bytes(after_size)}")
            print(f"  Quality:  {quality}")
            print(f"  Saved:    {out_path.name}")
            print()

            report_rows.append({
                "original_file": path.name,
                "new_file": out_path.name,
                "original_size": before_size,
                "new_size": after_size,
                "original_size_readable": format_bytes(before_size),
                "new_size_readable": format_bytes(after_size),
                "quality": quality,
                "width": img.width,
                "height": img.height,
            })

        except UnidentifiedImageError:
            failed += 1
            print("  FAILED: Not a readable image file.")
            print()
        except Exception as e:
            failed += 1
            print(f"  FAILED: {e}")
            print()

    report_path = output_folder / "compression_report.csv"
    with report_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "original_file",
                "new_file",
                "original_size",
                "new_size",
                "original_size_readable",
                "new_size_readable",
                "quality",
                "width",
                "height",
            ],
        )
        writer.writeheader()
        writer.writerows(report_rows)

    print("Done.")
    print(f"Converted:     {converted}")
    print(f"Failed:        {failed}")
    print(f"Before total:  {format_bytes(total_before)}")
    print(f"After total:   {format_bytes(total_after)}")
    print(f"Report:        {report_path}")

    if total_after > 50 * 1024 * 1024:
        print()
        print("WARNING: Total output is still over 50 MB.")
        print("Upload fewer images at once, or lower TARGET_BYTES to 70 * 1024.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "source_folder",
        nargs="?",
        default=str(DEFAULT_SOURCE_FOLDER),
        help="Folder containing source images. Default: imdb_output",
    )
    parser.add_argument(
        "--output-folder",
        default=None,
        help="Folder for compressed WEBP images. Default: <source>_tiermaker_small",
    )
    args = parser.parse_args()

    source_folder = Path(args.source_folder).expanduser().resolve()
    output_folder = (
        Path(args.output_folder).expanduser().resolve()
        if args.output_folder
        else None
    )

    main(source_folder=source_folder, output_folder=output_folder)
