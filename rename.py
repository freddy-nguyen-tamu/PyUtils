import os
import random
import string

def rename_files_with_random_numbers(folder_path, length=8):
    """
    Renames all files in the given folder to random numeric strings.

    :param folder_path: Path to the folder containing files
    :param length: Length of the random number string (default: 8)
    """
    if not os.path.isdir(folder_path):
        print("Invalid folder path.")
        return
    
    for filename in os.listdir(folder_path):
        file_path = os.path.join(folder_path, filename)

        # Skip directories
        if os.path.isdir(file_path):
            continue
        
        # Get file extension
        _, ext = os.path.splitext(filename)

        # Generate random numeric string
        random_name = ''.join(random.choices(string.digits, k=length))

        # Ensure unique name
        new_file_path = os.path.join(folder_path, random_name + ext)
        while os.path.exists(new_file_path):
            random_name = ''.join(random.choices(string.digits, k=length))
            new_file_path = os.path.join(folder_path, random_name + ext)

        # Rename file
        os.rename(file_path, new_file_path)
        print(f"Renamed: {filename} -> {random_name + ext}")

# Example usage
folder = r"C:\Users\qacer\Downloads\new\new"  # Replace with your folder path
rename_files_with_random_numbers(folder, length=10)