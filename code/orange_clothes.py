#!/usr/bin/env python3
"""
Batch clothing segmentation.

This script:

1. Finds every readable image in the same directory as this script.
2. Detects clothing while excluding human body parts.
3. Colors clothing exact orange: RGB (255, 165, 0).
4. Creates a separate results folder for every image.
5. Places the following inside each folder:
   - The original image
   - The orange-clothing output
   - The black-and-white clothing mask

The model is loaded only once and reused for every image.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

# This model is public. Do not attach a cached or environment Hugging Face
# token, because an expired/broken token can turn normal public-file probes
# into a misleading 401 Unauthorized response. These variables must be set
# before importing transformers/huggingface_hub.
os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
os.environ.pop("HF_TOKEN", None)
os.environ.pop("HUGGING_FACE_HUB_TOKEN", None)

print("Starting clothing processor and loading libraries...", flush=True)

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image, ImageOps, UnidentifiedImageError
from transformers import AutoImageProcessor, AutoModelForSemanticSegmentation


MODEL_NAME = "mattmdjaga/segformer_b2_clothes"

# Exact output color.
ORANGE_RGB = np.array([255, 165, 0], dtype=np.uint8)

# Minimum confidence required before a pixel is accepted as clothing.
MIN_CONFIDENCE = 0.50

# Whether bags should also be treated as clothing.
INCLUDE_ACCESSORIES = False

# Clothing labels from the human-parsing model.
CLOTHING_LABELS = {
    "hat",
    "sunglasses",
    "upper-clothes",
    "skirt",
    "pants",
    "dress",
    "belt",
    "left-shoe",
    "right-shoe",
    "scarf",
}

OPTIONAL_ACCESSORY_LABELS = {
    "bag",
}

# Prevent previously generated output files from being processed again
# if old versions exist directly in the main directory.
GENERATED_FILE_SUFFIXES = {
    "_orange_clothes",
    "_clothing_mask",
}


def normalize_label(label: str) -> str:
    """Normalize model label names for comparison."""
    return label.strip().lower().replace("_", "-").replace(" ", "-")


def choose_device() -> torch.device:
    """Use GPU when available; otherwise use CPU."""
    if torch.cuda.is_available():
        return torch.device("cuda")

    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return torch.device("mps")

    return torch.device("cpu")


def load_clothing_model(
    device: torch.device,
) -> tuple[AutoImageProcessor, AutoModelForSemanticSegmentation]:
    """Load the public model without using possibly broken HF credentials."""
    try:
        processor = AutoImageProcessor.from_pretrained(
            MODEL_NAME,
            token=False,
        )
        model = AutoModelForSemanticSegmentation.from_pretrained(
            MODEL_NAME,
            token=False,
        )

    except TypeError as error:
        # Compatibility with older transformers versions that still use the
        # deprecated ``use_auth_token`` keyword instead of ``token``.
        if "token" not in str(error):
            raise

        processor = AutoImageProcessor.from_pretrained(
            MODEL_NAME,
            use_auth_token=False,
        )
        model = AutoModelForSemanticSegmentation.from_pretrained(
            MODEL_NAME,
            use_auth_token=False,
        )

    except OSError as error:
        raise RuntimeError(
            "Could not download the public clothing model anonymously.\n"
            "Check your internet connection, proxy/VPN, and system clock.\n"
            "To repair Hugging Face authentication globally, run:\n"
            "  unset HF_TOKEN HUGGING_FACE_HUB_TOKEN\n"
            "  hf auth logout\n"
            "Then run this script again.\n\n"
            f"Original model-loading error: {error}"
        ) from error

    model.to(device)
    model.eval()
    return processor, model


def is_generated_file(path: Path) -> bool:
    """Return True when the image appears to be an old generated output."""
    lowercase_stem = path.stem.lower()

    return any(
        lowercase_stem.endswith(suffix)
        for suffix in GENERATED_FILE_SUFFIXES
    )


def is_readable_image(path: Path) -> bool:
    """
    Determine whether a file is an image that Pillow can read.

    This does not depend on the filename extension. Any image format
    supported by the installed Pillow version can be processed.
    """
    if not path.is_file():
        return False

    if is_generated_file(path):
        return False

    try:
        with Image.open(path) as image:
            image.verify()

        return True

    except (UnidentifiedImageError, OSError, ValueError):
        return False


def find_input_images(directory: Path) -> list[Path]:
    """
    Find all readable images directly inside the specified directory.

    This is not recursive. Images inside subdirectories are ignored.
    """
    return sorted(
        (
            path
            for path in directory.iterdir()
            if is_readable_image(path)
        ),
        key=lambda path: path.name.lower(),
    )


def create_result_folder_name(input_path: Path) -> str:
    """
    Create a unique results folder name.

    Including the format prevents collisions such as:
        photo.jpg
        photo.png
    """
    extension = input_path.suffix.lstrip(".").lower()

    if not extension:
        extension = "image"

    return f"{input_path.stem}_{extension}_results"


def build_clothing_class_ids(
    model: AutoModelForSemanticSegmentation,
    include_accessories: bool,
) -> tuple[set[int], dict[int, str]]:
    """Find the model class IDs representing clothing."""
    id_to_label = {
        int(class_id): str(label)
        for class_id, label in model.config.id2label.items()
    }

    selected_labels = set(CLOTHING_LABELS)

    if include_accessories:
        selected_labels.update(OPTIONAL_ACCESSORY_LABELS)

    clothing_ids = {
        class_id
        for class_id, label in id_to_label.items()
        if normalize_label(label) in selected_labels
    }

    if not clothing_ids:
        available_labels = ", ".join(
            f"{class_id}: {label}"
            for class_id, label in sorted(id_to_label.items())
        )

        raise RuntimeError(
            "No clothing labels matched the model configuration.\n"
            f"Available labels: {available_labels}"
        )

    return clothing_ids, id_to_label


def create_clothing_mask(
    image: Image.Image,
    processor: AutoImageProcessor,
    model: AutoModelForSemanticSegmentation,
    clothing_ids: set[int],
    device: torch.device,
    min_confidence: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Create a Boolean clothing mask for one image."""
    width, height = image.size

    inputs = processor(
        images=image,
        return_tensors="pt",
    )

    inputs = {
        key: value.to(device)
        for key, value in inputs.items()
    }

    with torch.inference_mode():
        outputs = model(**inputs)

        # Resize segmentation results to the original image resolution.
        logits = F.interpolate(
            outputs.logits,
            size=(height, width),
            mode="bilinear",
            align_corners=False,
        )

        probabilities = torch.softmax(logits, dim=1)
        confidence, predicted_classes = probabilities.max(dim=1)

    class_map = predicted_classes[0].cpu().numpy()
    confidence_map = confidence[0].cpu().numpy()

    clothing_mask = np.isin(
        class_map,
        np.asarray(
            sorted(clothing_ids),
            dtype=class_map.dtype,
        ),
    )

    # Reject uncertain pixels to reduce clothing/body confusion.
    clothing_mask &= confidence_map >= min_confidence

    return clothing_mask, class_map


def process_one_image(
    input_path: Path,
    results_directory: Path,
    processor: AutoImageProcessor,
    model: AutoModelForSemanticSegmentation,
    clothing_ids: set[int],
    id_to_label: dict[int, str],
    device: torch.device,
    min_confidence: float,
) -> None:
    """Process one image and save all three requested files."""
    results_directory.mkdir(parents=True, exist_ok=True)

    # Copy the original file without changing it.
    copied_original_path = results_directory / input_path.name
    shutil.copy2(input_path, copied_original_path)

    output_path = results_directory / (
        f"{input_path.stem}_orange_clothes.png"
    )

    mask_output_path = results_directory / (
        f"{input_path.stem}_clothing_mask.png"
    )

    with Image.open(input_path) as opened_image:
        image = ImageOps.exif_transpose(opened_image).convert("RGB")

    clothing_mask, class_map = create_clothing_mask(
        image=image,
        processor=processor,
        model=model,
        clothing_ids=clothing_ids,
        device=device,
        min_confidence=min_confidence,
    )

    # Create the orange-clothing result.
    result = np.asarray(image).copy()

    # Every accepted clothing pixel becomes exactly RGB (255, 165, 0).
    result[clothing_mask] = ORANGE_RGB

    # Omitting the deprecated mode argument removes the Pillow warning.
    Image.fromarray(result).save(output_path)

    # Create the binary clothing mask:
    # white = clothing
    # black = everything else
    binary_mask = np.zeros(
        clothing_mask.shape,
        dtype=np.uint8,
    )
    binary_mask[clothing_mask] = 255

    Image.fromarray(binary_mask).save(mask_output_path)

    detected_ids = sorted(
        int(class_id)
        for class_id in np.unique(class_map)
        if int(class_id) in clothing_ids
    )

    detected_names = [
        id_to_label[class_id]
        for class_id in detected_ids
    ]

    pixel_count = int(clothing_mask.sum())

    print(f"  Clothing pixels: {pixel_count:,}")
    print(
        "  Detected classes: "
        + (
            ", ".join(detected_names)
            if detected_names
            else "none"
        )
    )
    print(f"  Original: {copied_original_path.name}")
    print(f"  Orange output: {output_path.name}")
    print(f"  Clothing mask: {mask_output_path.name}")


def main() -> None:
    # Use the directory containing this script, regardless of the
    # directory from which the terminal command was executed.
    script_directory = Path(__file__).resolve().parent

    print(f"Scanning directory: {script_directory}", flush=True)

    input_images = find_input_images(script_directory)

    if not input_images:
        print("No readable images were found.")
        raise SystemExit(0)

    print(
        f"Found {len(input_images)} image(s).",
        flush=True,
    )

    device = choose_device()
    print(f"Using device: {device}", flush=True)

    print(
        "Loading clothing segmentation model once...",
        flush=True,
    )

    processor, model = load_clothing_model(device)

    clothing_ids, id_to_label = build_clothing_class_ids(
        model=model,
        include_accessories=INCLUDE_ACCESSORIES,
    )

    clothing_names = [
        id_to_label[class_id]
        for class_id in sorted(clothing_ids)
    ]

    print(
        "Classes treated as clothing: "
        + ", ".join(clothing_names)
    )

    successful = 0
    failed = 0

    for image_number, input_path in enumerate(
        input_images,
        start=1,
    ):
        print()
        print(
            f"[{image_number}/{len(input_images)}] "
            f"Processing: {input_path.name}",
            flush=True,
        )

        results_directory = script_directory / create_result_folder_name(
            input_path
        )

        try:
            process_one_image(
                input_path=input_path,
                results_directory=results_directory,
                processor=processor,
                model=model,
                clothing_ids=clothing_ids,
                id_to_label=id_to_label,
                device=device,
                min_confidence=MIN_CONFIDENCE,
            )

            successful += 1
            print(f"  Results folder: {results_directory.name}")

        except Exception as error:
            failed += 1
            print(f"  ERROR: {error}")

            # Continue with the remaining images instead of stopping.
            continue

    print()
    print("=" * 60)
    print("Processing complete.")
    print(f"Successful: {successful}")
    print(f"Failed: {failed}")
    print(f"Total: {len(input_images)}")
    print("=" * 60)


if __name__ == "__main__":
    main()