import os
import shutil

# Paths
faulty_folder = r"C:\Users\qacer\Downloads\new\new_cropped\faulty cropped"
original_folder = r"C:\Users\qacer\Downloads\new\new"
destination_folder = r"C:\Users\qacer\Downloads\new"

# Loop through files in faulty folder
for filename in os.listdir(faulty_folder):
    
    # Remove "_cropped" from filename
    original_name = filename.replace("_cropped", "")
    
    source_path = os.path.join(original_folder, original_name)
    
    # Check if file exists in original folder
    if os.path.exists(source_path):
        dest_path = os.path.join(destination_folder, original_name)
        shutil.copy2(source_path, dest_path)
        print(f"Copied: {original_name}")
    else:
        print(f"Not found: {original_name}")

print("Done.")