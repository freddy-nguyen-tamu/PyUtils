import os

# Folder to scan
root_folder = r"C:\Users\qacer\Downloads\ytb"

# Get the folder name safely
folder_name = os.path.basename(root_folder.rstrip("\\/"))

# Build output file name
output_file = f"all_files_contents_{folder_name}.txt"

def is_text_file(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            f.read()
        return True
    except:
        return False

with open(output_file, 'w', encoding='utf-8') as out:
    for dirpath, dirnames, filenames in os.walk(root_folder):
        for filename in filenames:
            file_path = os.path.join(dirpath, filename)
            if is_text_file(file_path):
            # if filename.lower().endswith(".py") and is_text_file(file_path):
                out.write(f"--- {file_path} ---\n")
                with open(file_path, 'r', encoding='utf-8') as f:
                    out.write(f.read())
                out.write("\n\n")  # separate files
