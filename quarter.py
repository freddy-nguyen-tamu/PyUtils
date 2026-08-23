from pathlib import Path
from PIL import Image

# Folder containing the original images
INPUT_DIR = Path.home() / "Downloads"

# Output folder
OUTPUT_DIR = INPUT_DIR / "quarters"
OUTPUT_DIR.mkdir(exist_ok=True)

SUPPORTED = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

for image_path in INPUT_DIR.iterdir():
    if not image_path.is_file():
        continue

    if image_path.suffix.lower() not in SUPPORTED:
        continue

    try:
        with Image.open(image_path) as img:
            width, height = img.size

            # Ensures all four outputs have exactly the same dimensions.
            # If width/height is odd, the center-most pixel is simply excluded.
            half_w = width // 2
            half_h = height // 2

            crops = {
                "top_left": (
                    0,
                    0,
                    half_w,
                    half_h
                ),
                "top_right": (
                    width - half_w,
                    0,
                    width,
                    half_h
                ),
                "bottom_left": (
                    0,
                    height - half_h,
                    half_w,
                    height
                ),
                "bottom_right": (
                    width - half_w,
                    height - half_h,
                    width,
                    height
                ),
            }

            for name, box in crops.items():
                quarter = img.crop(box)

                output_path = (
                    OUTPUT_DIR
                    / f"{image_path.stem}_{name}{image_path.suffix}"
                )

                quarter.save(output_path)
                print(f"Created: {output_path}")

    except Exception as e:
        print(f"Skipped {image_path.name}: {e}")

print("\nDone.")
print(f"Output folder: {OUTPUT_DIR}")