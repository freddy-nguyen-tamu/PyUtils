import os

def sync_contents(messy_root, clean_root):
    for dirpath, _, filenames in os.walk(messy_root):
        # Skip .git folders
        if ".git" in dirpath.split(os.sep):
            continue

        for filename in filenames:
            messy_path = os.path.join(dirpath, filename)
            
            # Skip if this is inside a .git folder
            if ".git" in messy_path.split(os.sep):
                continue

            # Figure out relative path
            rel_path = os.path.relpath(messy_path, messy_root)
            clean_path = os.path.join(clean_root, rel_path)

            os.makedirs(os.path.dirname(clean_path), exist_ok=True)

            with open(messy_path, "rb") as f:
                content = f.read()

            with open(clean_path, "wb") as f:
                f.write(content)

            print(f"Copied content from {messy_path} → {clean_path}")


if __name__ == "__main__":
    messy_folder = r"C:\Users\qacer\Downloads\foo1"
    clean_folder = r"\\wsl.localhost\Ubuntu\home\qacer6973\projects\foo1"
    
    sync_contents(messy_folder, clean_folder)
    print("\nSync complete! Now run `git add . && git commit -m 'Synced contents from messy folder'` inside the clean repo.")
    input("\nPress Enter to exit...")