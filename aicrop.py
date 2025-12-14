import os
from pathlib import Path
from typing import Optional, Tuple, Union

import numpy as np
from PIL import Image, ImageOps

# YOLOv8 segmentation model as person masks are better than just boxes for "fully retained"
from ultralytics import YOLO


IMAGE_EXTS = {".jpg", ".jpeg", ".jfif", ".png", ".webp", ".bmp", ".tif", ".tiff"}


def clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def is_image_file(p: Path) -> bool:
    return p.suffix.lower() in IMAGE_EXTS


def get_person_y_bounds_from_seg(
    model: YOLO, img_source: Union[str, Path, np.ndarray], conf: float = 0.25
) -> Optional[Tuple[int, int]]:
    """
    Returns (y_min, y_max) for all detected persons combined.
    Uses segmentation masks if available; falls back to boxes if needed.

    img_source can be:
      - path (str/Path), OR
      - numpy array image (H,W,3) in RGB/BGR (Ultralytics will handle it)
    """
    results = model.predict(source=img_source, conf=conf, verbose=False)
    if not results:
        return None

    r = results[0]

    # Class id for "person" in COCO
    PERSON_CLASS_ID = 0

    ymins = []
    ymaxs = []

    # Prefer masks as it's more reliable for "fully retained"
    if r.masks is not None and r.boxes is not None:
        boxes = r.boxes
        masks = r.masks

        # boxes.cls is float tensor-like; convert safely
        for i in range(len(boxes)):
            cls_id = int(boxes.cls[i].item()) if hasattr(boxes.cls[i], "item") else int(boxes.cls[i])
            if cls_id != PERSON_CLASS_ID:
                continue

            # masks.xy[i] is a Nx2 polygon in image coordinates
            poly = masks.xy[i]
            if poly is None or len(poly) == 0:
                continue

            ys = [pt[1] for pt in poly]
            ymins.append(int(min(ys)))
            ymaxs.append(int(max(ys)))

    # If no masks matched, fall back to bounding boxes
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
    """
    Crops only vertically. Width stays the same.
    Adds a little top/bottom padding based on person height.
    """
    w, h = img.size

    y_min = clamp(y_min, 0, h - 1)
    y_max = clamp(y_max, 0, h - 1)

    if y_max <= y_min:
        return img

    person_h = y_max - y_min
    pad = int(person_h * pad_ratio)

    top = clamp(y_min - pad, 0, h)
    bottom = clamp(y_max + pad, 0, h)

    # Keep full width: left=0, right=w
    return img.crop((0, top, w, bottom))


def main():
    input_dir = Path(r"C:\Users\qacer\Downloads\new\new")
    output_dir = input_dir.parent / (input_dir.name + "_cropped")
    output_dir.mkdir(parents=True, exist_ok=True)

    # Segmentation model (good for tight but safe crops)
    # You can switch to "yolov8m-seg.pt" for better accuracy (slower), or "yolov8n-seg.pt" for faster.
    model = YOLO("yolov8s-seg.pt")

    skipped_non_images = 0
    skipped_no_person = 0
    processed = 0

    for p in sorted(input_dir.iterdir()):
        if not p.is_file():
            continue

        if not is_image_file(p):
            skipped_non_images += 1
            continue

        try:
            # Fix orientation from EXIF so bounds match what you see
            img = Image.open(p)
            img = ImageOps.exif_transpose(img).convert("RGB")
        except Exception as e:
            print(f"[WARN] Could not open image: {p.name} ({e})")
            continue

        # run YOLO on the already-transposed pixels
        img_np = np.array(img)

        bounds = get_person_y_bounds_from_seg(model, img_np, conf=0.25)
        if bounds is None:
            skipped_no_person += 1
            print(f"[INFO] No person detected, skipping: {p.name}")
            continue

        y_min, y_max = bounds
        cropped = vertical_crop_keep_width(img, y_min, y_max, pad_ratio=0.10)

        out_path = output_dir / (p.stem + "_cropped.jpg")
        cropped.save(out_path, quality=95)
        processed += 1
        print(f"[OK] {p.name} -> {out_path.name}")

    print("\n=== Done ===")
    print(f"Processed: {processed}")
    print(f"Skipped (non-images): {skipped_non_images}")
    print(f"Skipped (no person detected): {skipped_no_person}")
    print(f"Output folder: {output_dir}")


if __name__ == "__main__":
    main()
