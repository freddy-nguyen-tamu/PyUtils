from pathlib import Path
from PIL import Image

input_path = Path(
    r"AQNhI2o6mamx3K9dkOGjXuUReHlfygxF5SbjGFVZwwMW8onz1_GX2IrrYa3suP8EuNyH8NiOd2nNcBKu6Pq8jBuXwH1Xkcypb4eNBs8 - frame at 0m1s.jpg"
)

if not input_path.exists():
    raise FileNotFoundError(f"Image not found: {input_path.resolve()}")

with Image.open(input_path) as image:
    width, height = image.size
    middle_y = height // 2

    sections = {
        "top": (0, 0, width, middle_y),
        "bottom": (0, middle_y, width, height),
    }

    for section_name, crop_box in sections.items():
        cropped_image = image.crop(crop_box)

        output_path = input_path.with_name(
            f"{input_path.stem}_{section_name}{input_path.suffix}"
        )

        cropped_image.save(output_path, quality=100, subsampling=0)
        print(f"Saved: {output_path}")