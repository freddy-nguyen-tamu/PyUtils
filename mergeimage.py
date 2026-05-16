from pathlib import Path
import re
from itertools import product
from PIL import Image

# Folder containing images
FOLDER = Path(r"C:\Users\qacer\Downloads\qtsm")

# Output folder
OUT_DIR = FOLDER / "merged"
OUT_DIR.mkdir(exist_ok=True)

# Supported image extensions
EXTS = {".jpg", ".jpeg", ".png", ".webp"}

# Find exactly ONE 1–2 digit number anywhere in filename
NUMBER_RE = re.compile(r"\b(\d{1,2})\b")

files_by_num = {}

for p in FOLDER.iterdir():
    if not p.is_file() or p.suffix.lower() not in EXTS:
        continue

    nums = NUMBER_RE.findall(p.stem)

    # Only accept filenames with exactly one number
    if len(nums) != 1:
        continue

    n = int(nums[0])
    files_by_num.setdefault(n, []).append(p)

def merge_images(top_path, bottom_path, out_path):
    top = Image.open(top_path).convert("RGBA")
    bottom = Image.open(bottom_path).convert("RGBA")

    width = max(top.width, bottom.width)
    height = top.height + bottom.height

    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))

    canvas.paste(top, ((width - top.width) // 2, 0), top)
    canvas.paste(bottom, ((width - bottom.width) // 2, top.height), bottom)

    canvas.save(out_path, "WEBP", lossless=True)

# Merge n and n+1
for n in sorted(files_by_num):
    if (n + 1) not in files_by_num or (n + 1) <= 1:
        continue

    for top_img, bottom_img in product(files_by_num[n], files_by_num[n + 1]):
        out_name = f"merged_{n}_{n+1}__{top_img.stem}__{bottom_img.stem}.webp"
        out_path = OUT_DIR / out_name

        if not out_path.exists():
            merge_images(top_img, bottom_img, out_path)

print(f"Done. Merged images saved to: {OUT_DIR}")