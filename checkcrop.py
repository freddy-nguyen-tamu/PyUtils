from pathlib import Path

def find_non_image_files():
    input_dir = Path(r"C:\Users\qacer\Downloads\new\new")
    output_dir = Path(r"C:\Users\qacer\Downloads\new\new_cropped")
    
    non_image_files = []
    
    # Check all files in input directory
    for input_file in sorted(input_dir.iterdir()):
        if not input_file.is_file():
            continue
        
        # Expected output filename
        expected_output = output_dir / (input_file.stem + "_cropped.jpg")
        
        # If the cropped version doesn't exist, it might be a non-image
        if not expected_output.exists():
            # Check if it's likely not an image by checking extension
            IMAGE_EXTS = {".jpg", ".jpeg", ".jfif", ".png", ".webp", ".bmp", ".tif", ".tiff"}
            if input_file.suffix.lower() not in IMAGE_EXTS:
                non_image_files.append(input_file.name)
    
    print("=== Files skipped (non-images) ===")
    if non_image_files:
        for i, filename in enumerate(non_image_files, 1):
            print(f"{i}. {filename}")
        print(f"\nTotal non-image files: {len(non_image_files)}")
    else:
        print("No non-image files found.")
    
    return non_image_files

if __name__ == "__main__":
    find_non_image_files()