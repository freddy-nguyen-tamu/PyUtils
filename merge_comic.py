from PIL import Image

files = [
    "maschenny1.jpg",  # top
    "maschenny2.jpg",  # middle
    "maschenny3.jpg",  # bottom
]

images = [Image.open(f).convert("RGB") for f in files]

# Use the widest image as final width
max_width = max(img.width for img in images)

# Resize narrower images to match width, keeping aspect ratio
resized = []
for img in images:
    if img.width != max_width:
        new_height = int(img.height * max_width / img.width)
        img = img.resize((max_width, new_height), Image.LANCZOS)
    resized.append(img)

total_height = sum(img.height for img in resized)

merged = Image.new("RGB", (max_width, total_height), "white")

y = 0
for img in resized:
    merged.paste(img, (0, y))
    y += img.height

merged.save("maschenny_merged.jpg", "JPEG", quality=95)

print("Saved as maschenny_merged.jpg")