import shutil
from pathlib import Path
from typing import Optional, Tuple, Union

import numpy as np
from PIL import Image, ImageOps
from ultralytics import YOLO


IMAGE_EXTS = {".jpg", ".jpeg", ".jfif", ".png", ".webp", ".bmp", ".tif", ".tiff"}


def clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def is_image_file(p: Path) -> bool:
    return p.suffix.lower() in IMAGE_EXTS


def is_cropped_stem(stem: str) -> bool:
    return stem.lower().endswith("_cropped")


def uncropped_stem(stem: str) -> str:
    if is_cropped_stem(stem):
        return stem[:-len("_cropped")]
    return stem


def build_destination_stem_set(output_dir: Path) -> set[str]:
    """
    Reads the destination folder only.

    Example:
      image_cropped.jpg
      image_cropped.webp
      image_cropped.png

    All become:
      image_cropped
    """
    stems = set()

    if not output_dir.exists():
        return stems

    for p in output_dir.iterdir():
        if p.is_file() and is_image_file(p):
            stems.add(p.stem.lower())

    return stems


def get_person_y_bounds_from_seg(
    model: YOLO, img_source: Union[str, Path, np.ndarray], conf: float = 0.25
) -> Optional[Tuple[int, int]]:
    results = model.predict(source=img_source, conf=conf, verbose=False)
    if not results:
        return None

    r = results[0]

    PERSON_CLASS_ID = 0

    ymins = []
    ymaxs = []

    if r.masks is not None and r.boxes is not None:
        boxes = r.boxes
        masks = r.masks

        for i in range(len(boxes)):
            cls_id = int(boxes.cls[i].item()) if hasattr(boxes.cls[i], "item") else int(boxes.cls[i])

            if cls_id != PERSON_CLASS_ID:
                continue

            poly = masks.xy[i]

            if poly is None or len(poly) == 0:
                continue

            ys = [pt[1] for pt in poly]
            ymins.append(int(min(ys)))
            ymaxs.append(int(max(ys)))

    if (not ymins or not ymaxs) and r.boxes is not None:
        boxes = r.boxes

        for i in range(len(boxes)):
            cls_id = int(boxes.cls[i].item()) if hasattr(boxes.cls[i], "item") else int(boxes.cls[i])

            if cls_id != PERSON_CLASS_ID:
                continue

            x1, y1, x2, y2 = boxes.xyxy[i].tolist()
            ymins.append(int(y1))
            ymaxs.append(int(y2))

    if not ymins or not ymaxs:
        return None

    return min(ymins), max(ymaxs)


def vertical_crop_keep_width(
    img: Image.Image, y_min: int, y_max: int, pad_ratio: float = 0.08
) -> Image.Image:
    w, h = img.size

    y_min = clamp(y_min, 0, h - 1)
    y_max = clamp(y_max, 0, h - 1)

    if y_max <= y_min:
        return img

    person_h = y_max - y_min
    pad = int(person_h * pad_ratio)

    top = clamp(y_min - pad, 0, h)
    bottom = clamp(y_max + pad, 0, h)

    return img.crop((0, top, w, bottom))


def main():
    input_dir = Path(r"C:\Users\qacer\Downloads\new\new")
    output_dir = input_dir.parent / (input_dir.name + "_cropped")
    failed_dir = output_dir / "no_person_detected"

    output_dir.mkdir(parents=True, exist_ok=True)
    failed_dir.mkdir(parents=True, exist_ok=True)

    dest_stems = build_destination_stem_set(output_dir)

    model = YOLO("yolov8s-seg.pt")

    skipped_non_images = 0
    skipped_existing = 0
    skipped_already_cropped_input = 0
    skipped_no_person = 0
    processed = 0
    copied_failed = 0

    for p in sorted(input_dir.iterdir()):
        if not p.is_file():
            continue

        if not is_image_file(p):
            skipped_non_images += 1
            continue

        base_stem = uncropped_stem(p.stem)
        expected_cropped_stem = f"{base_stem}_cropped"
        out_path = output_dir / f"{expected_cropped_stem}.jpg"

        # Skip files that are already cropped in the source folder.
        # This prevents image_cropped.webp -> image_cropped_cropped.jpg.
        if is_cropped_stem(p.stem):
            skipped_already_cropped_input += 1
            print(f"[SKIP] Source file already looks cropped: {p.name}")
            continue

        # Skip by checking destination folder only.
        # This works even if the existing cropped file is .jpg, .png, .webp, etc.
        if expected_cropped_stem.lower() in dest_stems:
            skipped_existing += 1
            print(f"[SKIP] Already exists in destination: {expected_cropped_stem}.*")
            continue

        try:
            img = Image.open(p)
            img = ImageOps.exif_transpose(img).convert("RGB")
        except Exception as e:
            print(f"[WARN] Could not open image: {p.name} ({e})")
            continue

        img_np = np.array(img)

        bounds = get_person_y_bounds_from_seg(model, img_np, conf=0.25)

        if bounds is None:
            skipped_no_person += 1

            failed_copy_path = failed_dir / p.name

            if failed_copy_path.exists():
                print(f"[SKIP] No-person copy already exists: {failed_copy_path.name}")
                continue

            try:
                shutil.copy2(p, failed_copy_path)
                copied_failed += 1
                print(f"[INFO] No person detected, copied original to: {failed_copy_path.name}")
            except Exception as e:
                print(f"[WARN] No person detected, but failed to copy {p.name}: {e}")

            continue

        y_min, y_max = bounds
        cropped = vertical_crop_keep_width(img, y_min, y_max, pad_ratio=0.10)

        cropped.save(out_path, quality=95)

        # Update destination cache so duplicate stems later in the same run also skip.
        dest_stems.add(out_path.stem.lower())

        processed += 1
        print(f"[OK] {p.name} -> {out_path.name}")

    print("\n=== Done ===")
    print(f"Processed: {processed}")
    print(f"Skipped already in destination: {skipped_existing}")
    print(f"Skipped already-cropped source files: {skipped_already_cropped_input}")
    print(f"Skipped non-images: {skipped_non_images}")
    print(f"Skipped no person detected: {skipped_no_person}")
    print(f"Copied failed detections: {copied_failed}")
    print(f"Output folder: {output_dir}")
    print(f"Failed-copy folder: {failed_dir}")


if __name__ == "__main__":
    main()