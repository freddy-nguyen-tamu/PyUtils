import os
from PIL import Image
from rembg import remove, new_session

def replace_background_ai(pool_path, person_path, output_path):
    # 1. Load the pool background
    pool_img = Image.open(pool_path)

    # 2. Load the person image and ensure RGBA
    person_img = Image.open(person_path).convert("RGBA")
    original_w, original_h = person_img.size

    # 3. Remove background – use human‑specific model for better results on faceless/zoomed shots
    print(f"   Processing {os.path.basename(person_path)} ...")
    session = new_session("u2net_human_seg")   # better for human body parts without face
    person_transparent = remove(person_img, session=session)

    # 4. Resize pool to cover the person's canvas
    pool_w, pool_h = pool_img.size
    ratio = max(original_w / pool_w, original_h / pool_h)
    new_pool_w, new_pool_h = int(pool_w * ratio), int(pool_h * ratio)
    resized_pool = pool_img.resize((new_pool_w, new_pool_h), Image.Resampling.LANCZOS)

    # Center‑crop
    left = (new_pool_w - original_w) // 2
    top = (new_pool_h - original_h) // 2
    cropped_pool = resized_pool.crop((left, top, left + original_w, top + original_h)).convert("RGB")

    # 5. Composite
    final_image = Image.new("RGB", (original_w, original_h))
    final_image.paste(cropped_pool, (0, 0))
    final_image.paste(person_transparent, (0, 0), person_transparent)

    # 6. Save
    final_image.save(output_path)
    print(f"   ✅ Saved: {output_path}")

def find_person_images(directory="."):
    """Return a list of all image files starting with 'person' (case‑insensitive)."""
    valid_extensions = ('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif', '.tiff')
    matches = []
    for file in os.listdir(directory):
        if file.lower().startswith('person') and file.lower().endswith(valid_extensions):
            matches.append(file)
    return sorted(matches)  # predictable order

def find_all_images(directory="."):
    """Fallback: return all image files if no person* files are found."""
    valid_extensions = ('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif', '.tiff')
    return sorted([f for f in os.listdir(directory) if f.lower().endswith(valid_extensions)])

# --- MAIN ---
if __name__ == "__main__":
    pool_file = "pool.jpg"   # change if needed
    if not os.path.exists(pool_file):
        print(f"❌ Background image '{pool_file}' not found.")
        exit(1)

    person_files = find_person_images()

    # If no person* files exist, fall back to ALL images in the folder
    if not person_files:
        print("⚠️  No files starting with 'person' found. Falling back to all image files in the folder.")
        person_files = find_all_images()
        if not person_files:
            print("❌ No image files found at all.")
            exit(1)

    print(f"🔍 Found {len(person_files)} image(s) to process:")
    for f in person_files:
        print(f"   - {f}")

    for person_file in person_files:
        # Build output name: final_<original_base>.jpg
        base_name = os.path.splitext(person_file)[0]
        output_file = f"final_{base_name}.jpg"
        replace_background_ai(pool_file, person_file, output_file)

    print("\n✨ All done!")