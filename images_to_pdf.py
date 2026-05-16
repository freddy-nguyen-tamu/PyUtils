from pathlib import Path
from PIL import Image
import pillow_avif
import re

FOLDER = Path(
    r"C:\Users\qacer\Downloads\new\jornpt\ambatukam"
)

OUTPUT_PDF = FOLDER.parent / "Cuộc Sống Thường Nhật Của Một Cặp Đôi OneShot - HentaiVN.pdf"

IMAGE_EXTS = {
    ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".avif"
}


def natural_sort_key(path: Path):
    parts = re.split(r"(\d+)", path.name.lower())
    return [int(part) if part.isdigit() else part for part in parts]


def image_to_rgb(path: Path):
    img = Image.open(path)

    if img.mode in ("RGBA", "LA") or "transparency" in img.info:
        background = Image.new("RGB", img.size, "white")
        background.paste(img, mask=img.convert("RGBA").split()[-1])
        img.close()
        return background

    rgb = img.convert("RGB")
    img.close()
    return rgb


def main():
    if not FOLDER.exists():
        print(f"Folder does not exist:\n{FOLDER}")
        return

    image_paths = sorted(
        [
            p for p in FOLDER.iterdir()
            if p.is_file() and p.suffix.lower() in IMAGE_EXTS
        ],
        key=natural_sort_key
    )

    if not image_paths:
        print("No images found.")
        return

    print(f"Found {len(image_paths)} images.")
    print(f"Output PDF:\n{OUTPUT_PDF}")

    images = []

    try:
        for path in image_paths:
            print(f"Adding: {path.name}")
            images.append(image_to_rgb(path))

        first_image = images[0]
        rest_images = images[1:]

        first_image.save(
            OUTPUT_PDF,
            save_all=True,
            append_images=rest_images
        )

        print("Done.")
    finally:
        for img in images:
            img.close()


if __name__ == "__main__":
    main()