from pathlib import Path
from collections import Counter

ROOT = Path(r"C:\Users\qacer\Downloads\new")

IMAGE_EXTS = {".jpg", ".jpeg", ".jfif", ".png", ".webp", ".bmp", ".tif", ".tiff"}

places = [
    ROOT / "new",
    ROOT / "new" / "delete_me",
    ROOT / "new_cropped",
    ROOT / "new_cropped" / "no_person_detected",
    ROOT / "new_cropped" / "double_cropped_review",
    ROOT / "keep_safe",
]

all_cropped = []

for folder in places:
    if not folder.exists():
        print(f"[MISSING FOLDER] {folder}")
        continue

    count = 0

    for p in folder.rglob("*"):
        if not p.is_file():
            continue

        if p.suffix.lower() not in IMAGE_EXTS:
            continue

        if p.stem.lower().endswith("_cropped"):
            all_cropped.append(p)
            count += 1

    print(f"[COUNT] {folder} -> {count} cropped-looking files")

print("\n=== Summary by parent folder ===")
parent_counts = Counter(str(p.parent) for p in all_cropped)

for parent, count in parent_counts.most_common():
    print(f"{count:5}  {parent}")

print(f"\nTotal cropped-looking image files found: {len(all_cropped)}")

print("\n=== First 100 matches ===")
for p in all_cropped[:100]:
    print(p)