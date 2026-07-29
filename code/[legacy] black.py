import os
import numpy as np
from PIL import Image

def make_black_pure(input_path, output_path=None,
                    darkness_threshold=30,
                    value_threshold=0.15):
    """
    Convert pixels that are near‑black or dark grey to pure black (0,0,0).

    Near‑black: all RGB channels < darkness_threshold.
    Dark grey: Value (in HSV) < value_threshold.

    Args:
        input_path (str): Path to input image.
        output_path (str, optional): Output path. If None, appends
            '_pure_black' before the extension.
        darkness_threshold (int): 0-255, default 30.
        value_threshold (float): 0.0-1.0, default 0.15 (Value in HSV).
    """
    # Load and convert to RGB
    img = Image.open(input_path).convert('RGB')
    data = np.array(img, dtype=np.uint8)

    # ---- Near‑black mask (dark RGB) ----
    dark_mask = (data[:, :, 0] < darkness_threshold) & \
                (data[:, :, 1] < darkness_threshold) & \
                (data[:, :, 2] < darkness_threshold)

    # ---- Dark grey mask (low value in HSV) ----
    # Convert to HSV (Value is the 3rd channel)
    hsv = np.array(Image.fromarray(data).convert('HSV'), dtype=np.float32)
    hsv[:, :, 0] /= 360.0   # Hue (not needed)
    hsv[:, :, 1] /= 255.0   # Saturation (not needed)
    hsv[:, :, 2] /= 255.0   # Value (brightness)

    value = hsv[:, :, 2]
    low_value_mask = value < value_threshold

    # Combine both masks
    final_mask = dark_mask | low_value_mask

    # Apply pure black
    data[final_mask] = [0, 0, 0]

    # Save result
    result = Image.fromarray(data)
    if output_path is None:
        base, ext = os.path.splitext(input_path)
        output_path = f"{base}_pure_black{ext}"
    result.save(output_path)
    print(f"Saved result to: {output_path}")

def find_image_file(base_name):
    """Try common extensions to locate the image file."""
    extensions = ['', '.png', '.jpg', '.jpeg']
    for ext in extensions:
        candidate = base_name + ext
        if os.path.isfile(candidate):
            return candidate
    raise FileNotFoundError(f"No image file found for base name: {base_name}")

if __name__ == "__main__":
    base_input = "Untitled - 475844849_1322620182207945_2266092519404724249_n_cropped_output"

    try:
        input_file = find_image_file(base_input)
        make_black_pure(input_file)
    except FileNotFoundError as e:
        print(e)
        print("Please check the filename and ensure the image is in the current directory.")