import os
from PIL import Image
import glob

def convert_webp_to_jpg(input_folder, output_folder=None, quality=95):
    """
    Convert all .webp files in a folder to .jpg format
    
    Args:
        input_folder (str): Path to folder containing .webp files
        output_folder (str): Path to output folder (default: same as input)
        quality (int): JPEG quality (1-100, default: 95)
    """
    if output_folder is None:
        output_folder = input_folder
    else:
        os.makedirs(output_folder, exist_ok=True)
    
    # Find all .webp files (including .jpg-webp if needed)
    patterns = [
        os.path.join(input_folder, "*.webp"),
        os.path.join(input_folder, "*.jpg-webp")  # Include the hybrid format too
    ]
    
    webp_files = []
    for pattern in patterns:
        webp_files.extend(glob.glob(pattern))
    
    if not webp_files:
        print(f"No WebP files found in {input_folder}")
        return
    
    print(f"Found {len(webp_files)} WebP files")
    
    converted_count = 0
    error_count = 0
    
    for file_path in webp_files:
        try:
            # Get filename and create output name
            filename = os.path.basename(file_path)
            # Remove both .webp and .jpg-webp extensions
            base_name = filename.replace('.webp', '').replace('.jpg-webp', '')
            output_filename = base_name + '.jpg'
            output_path = os.path.join(output_folder, output_filename)
            
            # Open and convert image
            with Image.open(file_path) as img:
                # Convert to RGB mode (JPEG doesn't support transparency)
                if img.mode in ('RGBA', 'LA', 'P'):
                    # Create white background for transparent images
                    rgb_img = Image.new('RGB', img.size, (255, 255, 255))
                    if img.mode == 'P':
                        img = img.convert('RGBA')
                    rgb_img.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
                    img = rgb_img
                elif img.mode != 'RGB':
                    img = img.convert('RGB')
                
                # Save as JPEG
                img.save(output_path, 'JPEG', quality=quality, optimize=True)
            
            print(f"✓ Converted: {filename} → {output_filename}")
            converted_count += 1
            
        except Exception as e:
            print(f"✗ Error converting {filename}: {str(e)}")
            error_count += 1
    
    print(f"\nConversion complete!")
    print(f"Successfully converted: {converted_count} files")
    print(f"Errors: {error_count} files")

# Usage example
if __name__ == "__main__":
    input_dir = r"C:\Users\qacer\Downloads"  # Change this to your folder path
    output_dir = r"C:\Users\qacer\Downloads"  # Optional: specify different output folder
    
    convert_webp_to_jpg(input_dir, output_dir, quality=90)