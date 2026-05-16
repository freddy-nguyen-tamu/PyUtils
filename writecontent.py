import os

# Folder to scan
root_folder = r"C:\Users\qacer\Downloads\py"

# Get the folder name safely
folder_name = os.path.basename(root_folder.rstrip("\\/"))

# Build output file name
output_file = f"all_files_contents_{folder_name}.txt"


def is_text_file(file_path):
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            f.read(1024)  # only test a small chunk
        return True
    except Exception:
        return False


with open(output_file, "w", encoding="utf-8") as out:
    for dirpath, dirnames, filenames in os.walk(root_folder):
        for filename in filenames:
            file_path = os.path.join(dirpath, filename)

            if is_text_file(file_path):
                out.write(f"--- {file_path} ---\n")
                try:
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        out.write(f.read())
                    out.write("\n\n")
                except Exception as e:
                    out.write(f"[Could not read file: {e}]\n\n")