from pathlib import Path
from PIL import Image
import numpy as np

INPUT_DIR = Path.home() / "Downloads" / "quarters"
OUTPUT_DIR = Path.home() / "Downloads" / "quarters_cropped"
OUTPUT_DIR.mkdir(exist_ok=True)

SUPPORTED = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

# Pixel must differ from the outer background by this much
COLOR_DISTANCE = 25

# At least this much of a row/column must contain real image content.
# Text/logos usually occupy much less than this.
CONTENT_RATIO = 0.30

# Small safety margin so the actual portrait is never shaved off
PADDING = 2


def longest_true_run(values):
    """
    Find the longest consecutive sequence of True values.
    Returns (start, end), inclusive.
    """
    best_start = None
    best_end = None
    best_length = 0

    current_start = None

    for i, value in enumerate(values):
        if value:
            if current_start is None:
                current_start = i

        elif current_start is not None:
            length = i - current_start

            if length > best_length:
                best_length = length
                best_start = current_start
                best_end = i - 1

            current_start = None

    # Handle run extending to end
    if current_start is not None:
        length = len(values) - current_start

        if length > best_length:
            best_start = current_start
            best_end = len(values) - 1

    return best_start, best_end


def estimate_outer_background(arr):
    """
    Estimate the surrounding junk/background color from the outer edges.
    Works with black, white, gray, etc.
    """
    h, w, _ = arr.shape

    band = max(3, min(h, w) // 50)

    border_pixels = np.concatenate([
        arr[:band, :, :].reshape(-1, 3),
        arr[-band:, :, :].reshape(-1, 3),
        arr[:, :band, :].reshape(-1, 3),
        arr[:, -band:, :].reshape(-1, 3),
    ])

    # Median prevents white text or other small objects from affecting it
    return np.median(border_pixels, axis=0)


def find_photo_box(img):
    rgb = np.array(img.convert("RGB")).astype(np.float32)

    h, w, _ = rgb.shape

    background = estimate_outer_background(rgb)

    # Euclidean RGB distance from surrounding background
    distance = np.linalg.norm(
        rgb - background,
        axis=2
    )

    content = distance > COLOR_DISTANCE

    # ---------------------------------------------------------
    # STEP 1: Find the main vertical photo region
    # ---------------------------------------------------------

    row_content_ratio = content.mean(axis=1)

    good_rows = row_content_ratio >= CONTENT_RATIO

    top, bottom = longest_true_run(good_rows)

    if top is None:
        return (0, 0, w, h)

    # ---------------------------------------------------------
    # STEP 2: Find horizontal photo region ONLY inside those rows
    #
    # This is important:
    # The "PHOTO..." text underneath is no longer considered.
    # ---------------------------------------------------------

    region = content[top:bottom + 1, :]

    column_content_ratio = region.mean(axis=0)

    good_columns = column_content_ratio >= CONTENT_RATIO

    left, right = longest_true_run(good_columns)

    if left is None:
        return (0, top, w, bottom + 1)

    # ---------------------------------------------------------
    # Safety padding
    # ---------------------------------------------------------

    left = max(0, left - PADDING)
    top = max(0, top - PADDING)

    right = min(w - 1, right + PADDING)
    bottom = min(h - 1, bottom + PADDING)

    # PIL uses exclusive right/bottom
    return (
        left,
        top,
        right + 1,
        bottom + 1
    )


for image_path in INPUT_DIR.iterdir():

    if not image_path.is_file():
        continue

    if image_path.suffix.lower() not in SUPPORTED:
        continue

    try:
        with Image.open(image_path) as img:

            box = find_photo_box(img)

            cropped = img.crop(box)

            output_path = OUTPUT_DIR / image_path.name

            # JPEG safety
            if output_path.suffix.lower() in {".jpg", ".jpeg"}:
                cropped.convert("RGB").save(
                    output_path,
                    quality=95
                )
            else:
                cropped.save(output_path)

            print(
                f"{image_path.name}\n"
                f"  Original: {img.size}\n"
                f"  Crop box: {box}\n"
                f"  Result:   {cropped.size}\n"
            )

    except Exception as e:
        print(f"ERROR: {image_path.name}: {e}")


print("\nDone.")
print(f"Output folder: {OUTPUT_DIR}")