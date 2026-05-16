import os
from PIL import Image
import glob

def convert_jpg_webp_to_webp(input_folder, output_folder=None, quality=80):
    """
    Convert all .jpg-webp files in a folder to standard .webp format
    
    Args:
        input_folder (str): Path to folder containing .jpg-webp files
        output_folder (str): Path to output folder (default: same as input)
        quality (int): WebP quality (1-100, default: 80)
    """
    if output_folder is None:
        output_folder = input_folder
    else:
        os.makedirs(output_folder, exist_ok=True)
    
    # Find all .jpg-webp files
    pattern = os.path.join(input_folder, "*.jpg-webp")
    jpg_webp_files = glob.glob(pattern)
    
    if not jpg_webp_files:
        print(f"No .jpg-webp files found in {input_folder}")
        return
    
    print(f"Found {len(jpg_webp_files)} .jpg-webp files")
    
    converted_count = 0
    error_count = 0
    
    for file_path in jpg_webp_files:
        try:
            # Get filename without extension
            filename = os.path.basename(file_path)
            output_filename = filename.replace('.jpg-webp', '.webp')
            output_path = os.path.join(output_folder, output_filename)
            
            # Open and convert image
            with Image.open(file_path) as img:
                # Convert to RGB if necessary (for PNG with transparency)
                if img.mode in ('RGBA', 'LA', 'P'):
                    background = Image.new('RGB', img.size, (255, 255, 255))
                    if img.mode == 'P':
                        img = img.convert('RGBA')
                    background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
                    img = background
                
                # Save as WebP
                img.save(output_path, 'WEBP', quality=quality)
            
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
    input_dir = r"C:\Users\qacer\Downloads\New folder"  # Change this to your folder path
    output_dir = r"C:\Users\qacer\Downloads"  # Optional: specify different output folder
    
    convert_jpg_webp_to_webp(input_dir, output_dir, quality=85)