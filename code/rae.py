from pathlib import Path
from PIL import Image

input_path = Path(
    r"AQPFXDey5duK54GBMXbZbubO9kBhsgqYjO_vlmRVkIjxoTOp5PMXHX4ZNkee3re4Nrr0QrQjS5JFwkJkW-Awq6NKwFUxjIILIAvgcqQ - frame at 0m2s.jpg"
)

if not input_path.exists():
    raise FileNotFoundError(f"Image not found: {input_path.resolve()}")

with Image.open(input_path) as image:
    width, height = image.size
    middle_x = width // 2
    middle_y = height // 2

    sections = {
        "top_left": (0, 0, middle_x, middle_y),
        "top_right": (middle_x, 0, width, middle_y),
        "bottom_left": (0, middle_y, middle_x, height),
        "bottom_right": (middle_x, middle_y, width, height),
    }

    for section_name, crop_box in sections.items():
        cropped_image = image.crop(crop_box)

        output_path = input_path.with_name(
            f"{input_path.stem}_{section_name}{input_path.suffix}"
        )

        cropped_image.save(output_path, quality=100, subsampling=0)
        print(f"Saved: {output_path}")