import shutil
from pathlib import Path
from typing import Optional, Tuple, Union

import numpy as np
from PIL import Image, ImageOps
from ultralytics import YOLO


IMAGE_EXTS = {
    ".jpg",
    ".jpeg",
    ".jfif",
    ".png",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
}


def clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def is_image_file(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTS


def is_cropped_stem(stem: str) -> bool:
    return stem.lower().endswith("_cropped")


def uncropped_stem(stem: str) -> str:
    if is_cropped_stem(stem):
        return stem[:-len("_cropped")]

    return stem


def build_destination_stem_set(output_dir: Path) -> set[str]:
    """
    Return the lowercase stems of cropped images in output_dir.

    Examples:
        image_cropped.jpg
        image_cropped.png
        image_cropped.webp

    All are checked using:
        image_cropped
    """
    stems: set[str] = set()

    if not output_dir.is_dir():
        return stems

    for path in output_dir.iterdir():
        if path.is_file() and is_image_file(path):
            stems.add(path.stem.lower())

    return stems


def get_person_y_bounds_from_seg(
    model: YOLO,
    img_source: Union[str, Path, np.ndarray],
    conf: float = 0.25,
) -> Optional[Tuple[int, int]]:
    results = model.predict(
        source=img_source,
        conf=conf,
        verbose=False,
    )

    if not results:
        return None

    result = results[0]
    person_class_id = 0

    ymins: list[int] = []
    ymaxs: list[int] = []

    # Prefer segmentation masks.
    if result.masks is not None and result.boxes is not None:
        boxes = result.boxes
        mask_polygons = result.masks.xy

        item_count = min(len(boxes), len(mask_polygons))

        for i in range(item_count):
            cls_value = boxes.cls[i]
            cls_id = (
                int(cls_value.item())
                if hasattr(cls_value, "item")
                else int(cls_value)
            )

            if cls_id != person_class_id:
                continue

            polygon = mask_polygons[i]

            if polygon is None or len(polygon) == 0:
                continue

            ys = polygon[:, 1]

            ymins.append(int(np.min(ys)))
            ymaxs.append(int(np.max(ys)))

    # Fall back to bounding boxes if segmentation masks found nothing.
    if (not ymins or not ymaxs) and result.boxes is not None:
        boxes = result.boxes

        for i in range(len(boxes)):
            cls_value = boxes.cls[i]
            cls_id = (
                int(cls_value.item())
                if hasattr(cls_value, "item")
                else int(cls_value)
            )

            if cls_id != person_class_id:
                continue

            _, y1, _, y2 = boxes.xyxy[i].tolist()

            ymins.append(int(y1))
            ymaxs.append(int(y2))

    if not ymins or not ymaxs:
        return None

    return min(ymins), max(ymaxs)


def vertical_crop_keep_width(
    img: Image.Image,
    y_min: int,
    y_max: int,
    pad_ratio: float = 0.08,
) -> Image.Image:
    width, height = img.size

    y_min = clamp(y_min, 0, height - 1)
    y_max = clamp(y_max, 0, height - 1)

    if y_max <= y_min:
        return img.copy()

    person_height = y_max - y_min
    padding = int(person_height * pad_ratio)

    top = clamp(y_min - padding, 0, height)
    bottom = clamp(y_max + padding, 0, height)

    return img.crop((0, top, width, bottom))


def get_save_metadata(img: Image.Image) -> dict:
    """
    Preserve supported image metadata where possible.
    """
    metadata: dict = {}

    exif = img.info.get("exif")
    icc_profile = img.info.get("icc_profile")
    dpi = img.info.get("dpi")

    if exif:
        metadata["exif"] = exif

    if icc_profile:
        metadata["icc_profile"] = icc_profile

    if dpi:
        metadata["dpi"] = dpi

    return metadata


def save_cropped_image(
    cropped: Image.Image,
    out_path: Path,
    metadata: dict,
) -> None:
    """
    Save the cropped image using the original file extension.

    PNG, BMP, TIFF, and lossless WebP do not add lossy compression.
    JPEG must be re-encoded, so quality 100 and subsampling 0 are used
    to minimize additional quality loss.
    """
    extension = out_path.suffix.lower()
    save_args: dict = {}

    if extension in {".jpg", ".jpeg", ".jfif"}:
        output_image = cropped

        if output_image.mode not in {"RGB", "L", "CMYK"}:
            output_image = output_image.convert("RGB")

        save_args.update(
            {
                "format": "JPEG",
                "quality": 100,
                "subsampling": 0,
                "optimize": True,
            }
        )

        for key in ("exif", "icc_profile", "dpi"):
            if key in metadata:
                save_args[key] = metadata[key]

    elif extension == ".png":
        output_image = cropped

        save_args.update(
            {
                "format": "PNG",
                "compress_level": 6,
                "optimize": False,
            }
        )

        for key in ("exif", "icc_profile", "dpi"):
            if key in metadata:
                save_args[key] = metadata[key]

        if "transparency" in cropped.info:
            save_args["transparency"] = cropped.info["transparency"]

    elif extension == ".webp":
        output_image = cropped

        if output_image.mode not in {"RGB", "RGBA"}:
            has_alpha = "A" in output_image.getbands()
            output_image = output_image.convert("RGBA" if has_alpha else "RGB")

        save_args.update(
            {
                "format": "WEBP",
                "lossless": True,
                "quality": 100,
                "method": 6,
            }
        )

        for key in ("exif", "icc_profile"):
            if key in metadata:
                save_args[key] = metadata[key]

    elif extension in {".tif", ".tiff"}:
        output_image = cropped

        save_args.update(
            {
                "format": "TIFF",
                "compression": "tiff_deflate",
            }
        )

        for key in ("exif", "icc_profile", "dpi"):
            if key in metadata:
                save_args[key] = metadata[key]

    elif extension == ".bmp":
        output_image = cropped

        if output_image.mode not in {"RGB", "RGBA", "L", "P"}:
            output_image = output_image.convert("RGB")

        save_args["format"] = "BMP"

    else:
        raise ValueError(f"Unsupported output format: {extension}")

    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Save to a temporary file first to avoid leaving a broken final file.
    temporary_path = out_path.with_name(
        f"{out_path.stem}.temporary{out_path.suffix}"
    )

    try:
        output_image.save(temporary_path, **save_args)
        temporary_path.replace(out_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def rebuild_not_included(
    input_dir: Path,
    output_dir: Path,
    not_included_dir: Path,
) -> tuple[int, int]:
    """
    Rebuild not_included from scratch.

    A source image is included only when a corresponding file named:

        original_name_cropped.*

    exists in output_dir.

    This means the following are copied automatically:
    - Images where no person was detected
    - Images that failed to open
    - Images that failed during processing
    - Source files already ending in _cropped
    - Non-image files
    """
    if not_included_dir.exists():
        shutil.rmtree(not_included_dir)

    not_included_dir.mkdir(parents=True, exist_ok=True)

    destination_stems = build_destination_stem_set(output_dir)

    copied = 0
    included = 0

    for source_path in sorted(input_dir.iterdir()):
        if not source_path.is_file():
            continue

        should_copy = False
        reason = ""

        if not is_image_file(source_path):
            should_copy = True
            reason = "non-image file"

        elif is_cropped_stem(source_path.stem):
            should_copy = True
            reason = "source name already ends with _cropped"

        else:
            expected_cropped_stem = (
                f"{source_path.stem}_cropped".lower()
            )

            if expected_cropped_stem not in destination_stems:
                should_copy = True
                reason = "no cropped counterpart"

        if should_copy:
            destination_path = not_included_dir / source_path.name

            try:
                shutil.copy2(source_path, destination_path)
                copied += 1

                print(
                    f"[NOT INCLUDED] {source_path.name} "
                    f"({reason})"
                )

            except Exception as exc:
                print(
                    f"[WARN] Could not copy {source_path.name} "
                    f"to not_included: {exc}"
                )

        else:
            included += 1

    return copied, included


def main() -> None:
    input_dir = Path(r"C:\Users\qacer\Downloads\new\new")
    output_dir = input_dir.parent / f"{input_dir.name}_cropped"
    not_included_dir = input_dir.parent / "not_included"

    if not input_dir.is_dir():
        raise FileNotFoundError(
            f"Input folder does not exist: {input_dir}"
        )

    output_dir.mkdir(parents=True, exist_ok=True)

    destination_stems = build_destination_stem_set(output_dir)

    model = YOLO("yolov8s-seg.pt")

    processed = 0
    skipped_existing = 0
    skipped_already_cropped_input = 0
    skipped_non_images = 0
    no_person_detected = 0
    failed_to_open = 0
    failed_to_process = 0

    for source_path in sorted(input_dir.iterdir()):
        if not source_path.is_file():
            continue

        if not is_image_file(source_path):
            skipped_non_images += 1
            print(f"[SKIP] Non-image file: {source_path.name}")
            continue

        if is_cropped_stem(source_path.stem):
            skipped_already_cropped_input += 1

            print(
                f"[SKIP] Source file already looks cropped: "
                f"{source_path.name}"
            )
            continue

        base_stem = uncropped_stem(source_path.stem)
        expected_cropped_stem = f"{base_stem}_cropped"

        # Keep the source file extension.
        output_extension = source_path.suffix.lower()
        out_path = output_dir / (
            f"{expected_cropped_stem}{output_extension}"
        )

        # Any existing cropped format counts.
        if expected_cropped_stem.lower() in destination_stems:
            skipped_existing += 1

            print(
                f"[SKIP] Already exists in destination: "
                f"{expected_cropped_stem}.*"
            )
            continue

        try:
            with Image.open(source_path) as opened_image:
                img = ImageOps.exif_transpose(opened_image)
                img.load()

        except Exception as exc:
            failed_to_open += 1

            print(
                f"[WARN] Could not open image: "
                f"{source_path.name} ({exc})"
            )
            continue

        try:
            metadata = get_save_metadata(img)

            # YOLO receives an RGB copy.
            # The cropped output retains the original image mode.
            model_image = np.array(img.convert("RGB"))

            bounds = get_person_y_bounds_from_seg(
                model,
                model_image,
                conf=0.25,
            )

            if bounds is None:
                no_person_detected += 1

                # Do not copy it here. The final not_included scan
                # will copy it because no *_cropped file exists.
                print(
                    f"[NO PERSON] {source_path.name} "
                    f"will be added to not_included"
                )
                continue

            y_min, y_max = bounds

            cropped = vertical_crop_keep_width(
                img,
                y_min,
                y_max,
                pad_ratio=0.10,
            )

            save_cropped_image(
                cropped=cropped,
                out_path=out_path,
                metadata=metadata,
            )

            destination_stems.add(
                expected_cropped_stem.lower()
            )

            processed += 1

            print(
                f"[OK] {source_path.name} -> {out_path.name}"
            )

        except Exception as exc:
            failed_to_process += 1

            print(
                f"[WARN] Failed to process "
                f"{source_path.name}: {exc}"
            )

    print("\n=== Building not_included ===")

    not_included_count, included_count = rebuild_not_included(
        input_dir=input_dir,
        output_dir=output_dir,
        not_included_dir=not_included_dir,
    )

    print("\n=== Done ===")
    print(f"Processed successfully: {processed}")
    print(f"Skipped already in destination: {skipped_existing}")
    print(
        f"Skipped already-cropped source files: "
        f"{skipped_already_cropped_input}"
    )
    print(f"Skipped non-images: {skipped_non_images}")
    print(f"No person detected: {no_person_detected}")
    print(f"Images that could not be opened: {failed_to_open}")
    print(f"Other processing failures: {failed_to_process}")
    print(f"Files with cropped counterparts: {included_count}")
    print(f"Files copied to not_included: {not_included_count}")
    print(f"Cropped output folder: {output_dir}")
    print(f"Not-included folder: {not_included_dir}")


if __name__ == "__main__":
    main()