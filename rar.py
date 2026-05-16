import os
import zipfile
import rarfile
import shutil

source_folder = r"C:\Users\qacer\Downloads\new\rar"
destination_folder = r"C:\Users\qacer\Downloads\new\new"
os.makedirs(destination_folder, exist_ok=True)

def unique_path(dest_dir, filename):
    """Return a non-colliding filepath by appending (1), (2), ... if needed."""
    base, ext = os.path.splitext(filename)
    candidate = os.path.join(dest_dir, filename)
    i = 1
    while os.path.exists(candidate):
        candidate = os.path.join(dest_dir, f"{base} ({i}){ext}")
        i += 1
    return candidate

def extract_zip_flat(zip_path, dest_dir):
    with zipfile.ZipFile(zip_path, "r") as z:
        for info in z.infolist():
            # Skip directories
            if info.is_dir():
                continue

            # Flatten: keep only the base name
            filename = os.path.basename(info.filename)
            if not filename:
                continue

            out_path = unique_path(dest_dir, filename)

            # Extract file stream and write to out_path
            with z.open(info, "r") as src, open(out_path, "wb") as dst:
                shutil.copyfileobj(src, dst)

def extract_rar_flat(rar_path, dest_dir):
    with rarfile.RarFile(rar_path, "r") as r:
        for info in r.infolist():
            # Skip directories
            if info.isdir():
                continue

            filename = os.path.basename(info.filename)
            if not filename:
                continue

            out_path = unique_path(dest_dir, filename)

            with r.open(info, "r") as src, open(out_path, "wb") as dst:
                shutil.copyfileobj(src, dst)

for filename in os.listdir(source_folder):
    file_path = os.path.join(source_folder, filename)
    if not os.path.isfile(file_path):
        continue

    ext = os.path.splitext(filename)[1].lower()

    try:
        if ext == ".zip":
            extract_zip_flat(file_path, destination_folder)
            print(f"Flatten-extracted ZIP: {filename}")

        elif ext == ".rar":
            extract_rar_flat(file_path, destination_folder)
            print(f"Flatten-extracted RAR: {filename}")

    except Exception as e:
        print(f"Failed on {filename}: {e}")

print("Done. All files extracted without folders.")