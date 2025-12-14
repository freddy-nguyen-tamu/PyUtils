import os
import subprocess

BASE_FOLDER = r"C:\Users\qacer\Downloads"
TARGET = os.path.join(BASE_FOLDER, "new")
STATE_FILE = os.path.join(BASE_FOLDER, ".hidden_state_new")

def unhide_folder(path):
    # Remove hidden attribute recursively from folder + files
    subprocess.run(["attrib", "-h", path, "/s", "/d"], shell=True)

def hide_folder(path):
    # Add hidden attribute recursively to folder + files
    subprocess.run(["attrib", "+h", path, "/s", "/d"], shell=True)

def main():
    if os.path.exists(STATE_FILE):
        hide_folder(TARGET)
        os.remove(STATE_FILE)
        print(f"'{TARGET}' and all contents are hidden again.")
    else:
        unhide_folder(TARGET)
        with open(STATE_FILE, "w") as f:
            f.write("unhidden")
        print(f"'{TARGET}' and all contents are now visible.")

if __name__ == "__main__":
    main()
