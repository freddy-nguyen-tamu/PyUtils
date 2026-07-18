from pathlib import Path
from collections import Counter

# Root directory
ROOT = Path(r"C:\Users\qacer\Downloads\Instagram")

if not ROOT.exists():
    raise FileNotFoundError(f"Directory not found: {ROOT}")

results = []

# Process each immediate subfolder
for folder in ROOT.iterdir():
    if not folder.is_dir():
        continue

    counter = Counter()

    # Count files recursively
    for file in folder.rglob("*"):
        if file.is_file():
            ext = file.suffix.lower() or "[no extension]"
            counter[ext] += 1

    total = sum(counter.values())
    results.append((total, folder.name, counter))

# Sort subfolders from most files to least
results.sort(key=lambda x: x[0], reverse=True)

# Print after everything has been processed
for total, folder_name, counter in results:
    print(f"\n=== {folder_name} ===")
    print(f"Total files: {total}")

    if not counter:
        print("No files found.")
        continue

    for ext, count in sorted(counter.items(), key=lambda x: (-x[1], x[0])):
        print(f"{ext:15} {count}")