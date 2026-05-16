from pathlib import Path
from PIL import Image
from rembg import remove, new_session

def batch_remove_bg(in_dir: str, out_dir: str, exts=(".png", ".jpg", ".jpeg", ".webp")):
    in_path = Path(in_dir)
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    # Reuse one session for all images (BIG speedup)
    session = new_session("u2net")  # or try "u2netp" for faster (see below)

    for p in in_path.rglob("*"):
        if p.is_file() and p.suffix.lower() in exts:
            img = Image.open(p).convert("RGBA")

            # Use the same session each time
            cutout = remove(img, session=session)

            out_file = out_path / (p.stem + ".png")

            # Faster PNG writing (lower compression = faster)
            cutout.save(out_file, format="PNG", compress_level=1)
            print("Saved:", out_file)

if __name__ == "__main__":
    batch_remove_bg(r"C:\Users\qacer\Downloads\yn", r"C:\Users\qacer\Downloads\out")