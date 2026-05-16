import shutil
import json
from pathlib import Path
import logging

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(message)s')

# Source directory where original images are
src_dir = Path(r"C:\Users\qacer\Downloads\new\new")
# Destination directory where to copy skipped images
dst_dir = src_dir.parent / "skipped_images"
dst_dir.mkdir(exist_ok=True)  # Create destination directory if it doesn't exist

# First approach: Extract skipped filenames directly from the log output
# These are the files that showed "[INFO] No person detected, skipping:" in your output
skipped_files_from_log = {
    "0083_fbid_1922145748162129.jpg",
    "399167493_304780389034256_1007815162944520219_n.jpg",
    "_n1.n4___1766997690_3798174374541883008_32797346465.jpg",
    "_n1.n4___1766997690_3798174374541912792_32797346465.jpg",
    "huemanity____1687632949_3132414677335274069_1936933801.jpg",
    "huemanity____1688748236_3141770379488676100_1936933801.jpg",
    "huemanity____1688748236_3141770379488911460_1936933801.jpg",
    "huemanity____1703499649_3265514197504014587_1936933801.jpg",
    "huemanity____1720616292_3409099006467689144_1936933801.jpg",
    "huemanity____1720616292_3409099006476049549_1936933801.jpg",
    "huemanity____1720616292_3409099006518025146_1936933801.jpg",
    "huemanity____1721554762_3416971462194937872_1936933801.jpg",
    "huemanity____1721554762_3416971462219972890_1936933801.jpg",
    "huemanity____1721554762_3416971462228323204_1936933801.jpg",
    "huemanity____1721554762_3416971462228499872_1936933801.jpg",
    "huemanity____1735016283_3529894886365350981_1936933801.jpg",
    "huemanity____1735016283_3529894886365422486_1936933801.jpg",
    "huemanity____1735016283_3529894886390368569_1936933801.jpg",
    "nemtranhoang_1766979153_5589277839 - frame at 0m0s.jpg",
    "tu_hhao_1766815502_3796646066860016226_268293831.jpg"
}

# Alternative approach: Read all files from source directory and identify which weren't processed
def get_all_skipped_images_automatically():
    """Automatically find all images in source directory that weren't processed"""
    all_files = list(src_dir.glob("*.jpg")) + list(src_dir.glob("*.jpeg")) + list(src_dir.glob("*.png"))
    processed_files = []
    
    # Read the cropped files from the output directory to see what was processed
    cropped_dir = src_dir.parent  # Assuming cropped files are in parent directory
    for cropped_file in cropped_dir.glob("*_cropped.jpg"):
        # Extract original filename by removing '_cropped'
        original_name = cropped_file.stem.replace('_cropped', '') + cropped_file.suffix
        processed_files.append(original_name)
    
    # Find files that exist in source but not in processed list
    skipped = []
    for file_path in all_files:
        if file_path.name not in processed_files:
            skipped.append(file_path.name)
    
    return skipped

# Main function to copy skipped images
def copy_skipped_images():
    print("=" * 60)
    print("Copying Skipped Images")
    print("=" * 60)
    
    # Method 1: Use the manually extracted list from log
    print(f"\nMethod 1: Using manually extracted list ({len(skipped_files_from_log)} files)")
    copied_count = 0
    missing_count = 0
    
    for filename in skipped_files_from_log:
        src_file = src_dir / filename
        dst_file = dst_dir / filename
        
        if src_file.exists():
            shutil.copy2(src_file, dst_file)
            copied_count += 1
            logging.info(f"Copied: {filename}")
        else:
            missing_count += 1
            logging.warning(f"Missing: {filename}")
    
    print(f"\nMethod 1 Results:")
    print(f"  Successfully copied: {copied_count} files")
    print(f"  Missing files: {missing_count} files")
    
    # Method 2: Automatic detection (uncomment to use)
    """
    print(f"\nMethod 2: Automatic detection")
    auto_skipped = get_all_skipped_images_automatically()
    print(f"  Found {len(auto_skipped)} potentially skipped files")
    print(f"  First 10: {auto_skipped[:10]}")
    
    # Copy automatically detected files to a different folder
    auto_dst_dir = dst_dir.parent / "auto_skipped"
    auto_dst_dir.mkdir(exist_ok=True)
    
    auto_copied = 0
    for filename in auto_skipped:
        src_file = src_dir / filename
        dst_file = auto_dst_dir / filename
        if src_file.exists():
            shutil.copy2(src_file, dst_file)
            auto_copied += 1
    
    print(f"  Auto-copied: {auto_copied} files to {auto_dst_dir}")
    """
    
    # Create a summary report
    report_file = dst_dir.parent / "skipped_images_report.txt"
    with open(report_file, 'w') as f:
        f.write("SKIPPED IMAGES REPORT\n")
        f.write("=" * 40 + "\n")
        f.write(f"Total files in manual list: {len(skipped_files_from_log)}\n")
        f.write(f"Successfully copied: {copied_count}\n")
        f.write(f"Missing files: {missing_count}\n")
        f.write("\nFiles copied:\n")
        for filename in skipped_files_from_log:
            src_file = src_dir / filename
            if src_file.exists():
                f.write(f"{filename}\n")
            else:
                f.write(f"{filename} (MISSING)\n")
    
    print(f"\n" + "=" * 60)
    print(f"SUMMARY")
    print(f"=" * 60)
    print(f"Source directory: {src_dir}")
    print(f"Destination directory: {dst_dir}")
    print(f"Report saved to: {report_file}")
    print(f"\nTotal files in list: {len(skipped_files_from_log)}")
    print(f"Successfully copied: {copied_count}")
    print(f"Missing files: {missing_count}")
    
    if missing_count > 0:
        print(f"\nNote: {missing_count} files were not found in the source directory.")
        print("They might have been processed differently or have different extensions.")

# Run the script
if __name__ == "__main__":
    copy_skipped_images()