#!/usr/bin/env python3
"""
pdf_to_images.py
Convert a PDF into a folder of images (one per page).

Usage:
  python pdf_to_images.py --pdf input.pdf --outdir out_images
  python pdf_to_images.py --pdf input.pdf --outdir out_images --format png --dpi 300
"""

import argparse
from pathlib import Path

import fitz  # PyMuPDF


def dpi_to_matrix(dpi: int) -> fitz.Matrix:
    # PDF points are 72 dpi. Scale factor = dpi / 72
    scale = dpi / 72.0
    return fitz.Matrix(scale, scale)


def main():
    ap = argparse.ArgumentParser(description="Convert a PDF into images (one image per page).")
    ap.add_argument("--pdf", required=True, help="Input PDF path")
    ap.add_argument("--outdir", required=True, help="Output directory for images")
    ap.add_argument("--format", choices=["png", "jpg", "jpeg", "webp"], default="png",
                    help="Output image format")
    ap.add_argument("--dpi", type=int, default=200, help="Render DPI (higher = sharper, bigger files)")
    ap.add_argument("--prefix", default="page_", help="Output filename prefix")
    ap.add_argument("--start", type=int, default=1, help="First page to export (1-based)")
    ap.add_argument("--end", type=int, default=None, help="Last page to export (1-based, inclusive)")
    ap.add_argument("--quality", type=int, default=90,
                    help="JPEG/WebP quality (ignored for PNG)")
    args = ap.parse_args()

    pdf_path = Path(args.pdf).expanduser().resolve()
    outdir = Path(args.outdir).expanduser().resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    if not pdf_path.exists() or not pdf_path.is_file():
        raise SystemExit(f"PDF not found: {pdf_path}")

    doc = fitz.open(pdf_path)
    total_pages = doc.page_count

    start = max(1, args.start)
    end = args.end if args.end is not None else total_pages
    end = min(end, total_pages)

    if start > end:
        raise SystemExit(f"Invalid page range: start={start} end={end} total_pages={total_pages}")

    matrix = dpi_to_matrix(args.dpi)
    ext = "jpg" if args.format == "jpeg" else args.format

    print(f" PDF: {pdf_path}")
    print(f" Exporting pages {start}..{end} of {total_pages} at {args.dpi} DPI as {ext.upper()}")

    for page_num in range(start, end + 1):
        page = doc.load_page(page_num - 1)  # 0-based
        pix = page.get_pixmap(matrix=matrix, alpha=False)

        filename = f"{args.prefix}{page_num:04d}.{ext}"
        outpath = outdir / filename

        # PNG output
        if ext == "png":
            pix.save(outpath.as_posix())
        else:
            # For jpg/webp, use PIL to apply quality if desired
            from PIL import Image
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            save_kwargs = {}
            if ext in ("jpg", "webp"):
                save_kwargs["quality"] = args.quality
            img.save(outpath, format=ext.upper() if ext != "jpg" else "JPEG", **save_kwargs)

        print(f" {outpath.name}")

    doc.close()
    print(f" Done. Images saved in: {outdir}")


if __name__ == "__main__":
    main()