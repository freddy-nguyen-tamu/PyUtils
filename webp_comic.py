from pathlib import Path
from PIL import Image

# Change this to your image folder
INPUT_FOLDER = r"C:\Users\qacer\Downloads\Lookism"

# Output PDF path
OUTPUT_PDF = r"C:\Users\qacer\Downloads\comic.pdf"


def natural_sort_key(path: Path):
    import re
    return [
        int(text) if text.isdigit() else text.lower()
        for text in re.split(r"(\d+)", path.name)
    ]


folder = Path(INPUT_FOLDER)

image_paths = sorted(
    folder.glob("*.webp"),
    key=natural_sort_key
)

if not image_paths:
    raise FileNotFoundError("No .webp images found in the folder.")

pages = []

for path in image_paths:
    img = Image.open(path).convert("RGB")
    pages.append(img)

first_page = pages[0]
other_pages = pages[1:]

first_page.save(
    OUTPUT_PDF,
    save_all=True,
    append_images=other_pages,
    resolution=100.0
)

print(f"Saved PDF: {OUTPUT_PDF}")
print(f"Pages: {len(pages)}")