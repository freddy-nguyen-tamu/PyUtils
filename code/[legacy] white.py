import os
import numpy as np
from PIL import Image

def make_white_and_grey_pure(input_path, output_path=None,
                             brightness_threshold=240,
                             saturation_threshold=0.15):
    """
    Convert pixels that are near‑white or grey to pure white (255,255,255).

    Near‑white: all RGB channels > brightness_threshold.
    Grey: saturation (in HSV) < saturation_threshold.

    Args:
        input_path (str): Path to input image.
        output_path (str, optional): Output path. If None, appends
            '_pure_white_grey' before the extension.
        brightness_threshold (int): 0-255, default 240.
        saturation_threshold (float): 0.0-1.0, default 0.15.
    """
    # Load and convert to RGB
    img = Image.open(input_path).convert('RGB')
    data = np.array(img, dtype=np.uint8)

    # ---- Near‑white mask (bright) ----
    bright_mask = (data[:, :, 0] > brightness_threshold) & \
                  (data[:, :, 1] > brightness_threshold) & \
                  (data[:, :, 2] > brightness_threshold)

    # ---- Grey mask (low saturation) ----
    # Convert to HSV (float in [0,1]) using a helper
    hsv = np.array(Image.fromarray(data).convert('HSV'), dtype=np.float32)
    hsv[:, :, 0] /= 360.0   # Hue not needed
    hsv[:, :, 1] /= 255.0   # Saturation
    hsv[:, :, 2] /= 255.0   # Value (brightness)

    saturation = hsv[:, :, 1]
    grey_mask = saturation < saturation_threshold

    # Combine both masks
    final_mask = bright_mask | grey_mask

    # Apply pure white
    data[final_mask] = [255, 255, 255]

    # Save result
    result = Image.fromarray(data)
    if output_path is None:
        base, ext = os.path.splitext(input_path)
        output_path = f"{base}_pure_white_grey{ext}"
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
    base_input = "Untitled - 504385185_18036405515652330_2724053534740846979_n_cropped_output"

    try:
        input_file = find_image_file(base_input)
        make_white_and_grey_pure(input_file)
    except FileNotFoundError as e:
        print(e)
        print("Please check the filename and ensure the image is in the current directory.")