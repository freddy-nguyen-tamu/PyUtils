"""Choose any number of photos once and arrange up to four true-size 4x6 photos per PDF page.

Requires: pip install pillow

Default paper is custom 8x12 inches: four 4x6 prints fit exactly, with no overlap.
Change PAPER to 'A4' for ~0.31 inch overlap, or 'LETTER' for 1 inch overlap.
At print time choose Actual size / 100%; 8x12 paper (or a larger sheet) is needed
for the default format if all four photos must remain uncropped.
"""

from pathlib import Path
from tkinter import Tk, filedialog, messagebox
from PIL import Image, ImageOps

DPI = 300
PAPER = "8x12"  # Choose: "8x12", "A4", "LETTER"
PAPER_INCHES = {
    "8x12": (8.0, 12.0),
    "A4": (8.2677, 11.6929),
    "LETTER": (8.5, 11.0),
}
PHOTOS_PER_PAGE = 4


def load_photo(path):
    with Image.open(path) as src:
        photo = ImageOps.exif_transpose(src)
        rgba = photo.convert("RGBA")
        white = Image.new("RGB", rgba.size, "white")
        white.paste(rgba, mask=rgba.getchannel("A"))
        return white


def two_positions(page_length, slot_length):
    """Place two slots symmetrically; overlap only if the paper is too short."""
    if page_length >= 2 * slot_length:
        left = (page_length - 2 * slot_length) // 2
        return (left, left + slot_length)
    return (0, page_length - slot_length)


def render_page(group, paper=PAPER):
    landscape_count = sum(img.width > img.height for img in group)
    landscape_page = landscape_count > len(group) / 2

    paper_w, paper_h = PAPER_INCHES[paper]
    if landscape_page:
        paper_w, paper_h = paper_h, paper_w
        photo_w, photo_h = 6, 4
    else:
        photo_w, photo_h = 4, 6

    page_w, page_h = round(paper_w * DPI), round(paper_h * DPI)
    slot_w, slot_h = photo_w * DPI, photo_h * DPI
    x_positions = two_positions(page_w, slot_w)
    y_positions = two_positions(page_h, slot_h)

    page = Image.new("RGB", (page_w, page_h), "white")

    # Images are placed row-first, in the order selected in the file browser.
    for i, original in enumerate(group):
        photo = original.copy()
        if landscape_page and photo.height > photo.width:
            photo = photo.rotate(90, expand=True)
        elif not landscape_page and photo.width > photo.height:
            photo = photo.rotate(90, expand=True)

        # Preserve the whole photograph, its aspect ratio, and its native orientation
        # when possible. The 4x6/6x4 print area is exactly sized in PDF inches.
        photo = ImageOps.contain(
            photo, (slot_w, slot_h), method=Image.Resampling.LANCZOS
        )
        print_area = Image.new("RGB", (slot_w, slot_h), "white")
        print_area.paste(
            photo,
            ((slot_w - photo.width) // 2, (slot_h - photo.height) // 2),
        )
        x, y = x_positions[i % 2], y_positions[i // 2]
        page.paste(print_area, (x, y))

    return page


def create_pdf(paths, output, paper=PAPER):
    if paper not in PAPER_INCHES:
        raise ValueError(f"Unknown paper size: {paper}")
    if not paths:
        raise ValueError("Select at least one image")

    images = [load_photo(path) for path in paths]
    pages = [
        render_page(images[i:i + PHOTOS_PER_PAGE], paper)
        for i in range(0, len(images), PHOTOS_PER_PAGE)
    ]
    pages[0].save(
        str(output), "PDF", save_all=True, append_images=pages[1:],
        resolution=DPI, quality=95,
    )
    return len(images), len(pages)


def main():
    root = Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        # Exactly ONE selection dialog for ALL source photos.
        paths = filedialog.askopenfilenames(
            parent=root,
            title="Select all photos (Ctrl/Shift-click for multiple)",
            filetypes=[
                ("Images", "*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff"),
                ("All files", "*.*"),
            ],
        )
        if not paths:
            return

        output = filedialog.asksaveasfilename(
            parent=root,
            title="Save the combined PDF",
            initialdir=str(Path(paths[0]).parent),
            initialfile="photos_4x6.pdf",
            defaultextension=".pdf",
            filetypes=[("PDF", "*.pdf")],
        )
        if not output:
            return

        count, page_count = create_pdf(paths, output)
        messagebox.showinfo(
            "PDF created",
            f"{count} photos on {page_count} PDF page(s).\n\nSaved to:\n{output}\n\n"
            f"Paper size: {PAPER} at {DPI} DPI.\nPrint at Actual size (100%).",
            parent=root,
        )
    except Exception as exc:
        messagebox.showerror("Could not create PDF", str(exc), parent=root)
        raise
    finally:
        root.destroy()


if __name__ == "__main__":
    main()
