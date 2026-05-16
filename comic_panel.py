from pathlib import Path
from PIL import Image
import re

ROOT = Path(r"C:\Users\qacer\Downloads\new\jornpt\tower_of_god_images")

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
PAIR_SUFFIX = "_PAIR"
TRIPLE_SUFFIX = "_TRIPLE"


def extract_number(stem: str):
    """
    Extract the last number from a filename stem.
    Examples:
        020 -> 20
        page_020 -> 20
        img021a -> 21
    """
    matches = re.findall(r"\d+", stem)
    if not matches:
        return None
    return int(matches[-1])


def open_as_rgb(path: Path):
    img = Image.open(path)
    return img.convert("RGB")


def combine_vertical(image_paths, output_path: Path):
    imgs = [open_as_rgb(p) for p in image_paths]
    try:
        width = max(img.width for img in imgs)
        height = sum(img.height for img in imgs)

        # White background, no margin, no gap
        combined = Image.new("RGB", (width, height), "white")

        y = 0
        for img in imgs:
            # paste at x=0 so there is no indentation
            combined.paste(img, (0, y))
            y += img.height

        combined.save(output_path, quality=95)
    finally:
        for img in imgs:
            img.close()


def process_folder(folder: Path):
    images = [
        p for p in folder.iterdir()
        if p.is_file()
        and p.suffix.lower() in IMAGE_EXTS
        and PAIR_SUFFIX not in p.stem
        and TRIPLE_SUFFIX not in p.stem
    ]

    numbered = []
    for img in images:
        num = extract_number(img.stem)
        if num is not None:
            numbered.append((num, img))

    # sort by numeric value, then by name
    numbered.sort(key=lambda x: (x[0], x[1].name.lower()))

    # group by number in case multiple files share same numeric index
    by_number = {}
    for num, path in numbered:
        by_number.setdefault(num, []).append(path)

    numbers = sorted(by_number.keys())

    # ----- Pairs: n + (n+1) -----
    for n in numbers:
        n1 = n + 1
        if n1 not in by_number:
            continue

        for p1 in by_number[n]:
            for p2 in by_number[n1]:
                output_name = f"{p1.stem}+{p2.stem}{PAIR_SUFFIX}.jpg"
                output_path = folder / output_name

                if output_path.exists():
                    print(f"Skipping existing pair: {output_path.name}")
                    continue

                print(f"Creating pair: {p1.name} + {p2.name}")
                combine_vertical([p1, p2], output_path)

    # ----- Triples: n + (n+1) + (n+2) -----
    for n in numbers:
        n1 = n + 1
        n2 = n + 2
        if n1 not in by_number or n2 not in by_number:
            continue

        for p1 in by_number[n]:
            for p2 in by_number[n1]:
                for p3 in by_number[n2]:
                    output_name = f"{p1.stem}+{p2.stem}+{p3.stem}{TRIPLE_SUFFIX}.jpg"
                    output_path = folder / output_name

                    if output_path.exists():
                        print(f"Skipping existing triple: {output_path.name}")
                        continue

                    print(f"Creating triple: {p1.name} + {p2.name} + {p3.name}")
                    combine_vertical([p1, p2, p3], output_path)


def main():
    if not ROOT.exists():
        print(f"Folder does not exist: {ROOT}")
        return

    for folder in [ROOT] + [p for p in ROOT.rglob("*") if p.is_dir()]:
        process_folder(folder)

    print("Done.")


if __name__ == "__main__":
    main()