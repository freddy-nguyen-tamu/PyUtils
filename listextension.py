from pathlib import Path
from collections import Counter

# Folder to scan
folder = Path(r"C:\Users\qacer\Downloads\new\new")

# Collect file extensions
extensions = []

for item in folder.iterdir():
    if item.is_file():
        # Use suffix; handle files without extension
        ext = item.suffix.lower() if item.suffix else "<no extension>"
        extensions.append(ext)

# Count each file type
counts = Counter(extensions)

# Print results
print("File types found:\n")
for ext, count in sorted(counts.items()):
    print(f"{ext}: {count}")