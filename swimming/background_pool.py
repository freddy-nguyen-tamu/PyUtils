import os
from PIL import Image

# Folder containing all images
folder = r"C:\Users\qacer\Downloads\adobe\swimming"

# Paths
pool_path = os.path.join(folder, "pool.jpg")

# Check if pool.jpg exists
if not os.path.isfile(pool_path):
    print(f"Error: pool.jpg not found in {folder}")
    exit(1)

# Load pool background once
pool = Image.open(pool_path).convert("RGB")

# Process every PNG file in the folder
for filename in os.listdir(folder):
    if filename.lower().endswith(".png"):
        png_path = os.path.join(folder, filename)
        
        # Skip the pool if it's somehow a PNG (unlikely, but just in case)
        if filename.lower() == "pool.jpg":
            continue

        try:
            # Open person image with transparency
            person = Image.open(png_path).convert("RGBA")
            w, h = person.size

            # Resize pool background to match this person's size
            pool_resized = pool.resize((w, h), Image.LANCZOS)

            # Composite person onto the resized background
            pool_resized.paste(person, (0, 0), person)

            # Save output as PNG to preserve quality
            out_name = f"{os.path.splitext(filename)[0]}_output.png"
            out_path = os.path.join(folder, out_name)
            pool_resized.save(out_path)
            print(f"Saved {out_path} ({w}x{h})")

        except Exception as e:
            print(f"Failed to process {filename}: {e}")

print("Done.")