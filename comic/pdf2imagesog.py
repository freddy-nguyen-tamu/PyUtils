from pathlib import Path
import sys
import traceback

try:
    import fitz  # PyMuPDF
except ImportError:
    print("ERROR: PyMuPDF is not installed.")
    print("Install it with:")
    print("    pip install pymupdf")
    sys.exit(1)


# Folder containing your PDFs
PDF_FOLDER = Path(r"C:\Users\qacer\Downloads\conan_chapters_pdfs")

# Image quality / render resolution
# Higher DPI = sharper images but larger files.
DPI = 200

# JPG quality: 1-100
JPG_QUALITY = 95


def safe_print(message):
    print(message, flush=True)


def convert_pdf_to_images(pdf_path: Path):
    pdf_stem = pdf_path.stem
    output_folder = pdf_path.parent / pdf_stem

    safe_print("=" * 80)
    safe_print(f"Processing PDF: {pdf_path}")
    safe_print(f"PDF name without extension: {pdf_stem}")
    safe_print(f"Output folder: {output_folder}")

    if output_folder.exists():
        safe_print(f"Output folder already exists: {output_folder}")
    else:
        output_folder.mkdir(parents=True, exist_ok=True)
        safe_print(f"Created output folder: {output_folder}")

    created_images = []

    try:
        doc = fitz.open(pdf_path)
        total_pages = doc.page_count

        safe_print(f"Opened PDF successfully.")
        safe_print(f"Total pages found: {total_pages}")

        if total_pages == 0:
            safe_print("PDF has 0 pages. Skipping delete for safety.")
            doc.close()
            return False

        zoom = DPI / 72
        matrix = fitz.Matrix(zoom, zoom)

        for page_index in range(total_pages):
            page_number = page_index + 1

            # File name format:
            # [original PDF name] + [page number].jpg
            #
            # Example:
            # chapter_5_001.jpg
            image_name = f"{pdf_stem}_{page_number:03d}.jpg"
            image_path = output_folder / image_name

            safe_print(f"Rendering page {page_number}/{total_pages}...")
            page = doc.load_page(page_index)
            pix = page.get_pixmap(matrix=matrix, alpha=False)

            safe_print(f"Saving image: {image_path}")
            pix.save(image_path, jpg_quality=JPG_QUALITY)

            if not image_path.exists() or image_path.stat().st_size == 0:
                raise RuntimeError(f"Image was not saved correctly: {image_path}")

            created_images.append(image_path)
            safe_print(f"Saved page {page_number} as: {image_name}")

        doc.close()

        safe_print(f"Finished converting PDF: {pdf_path.name}")
        safe_print(f"Images created: {len(created_images)}")

        if len(created_images) != total_pages:
            safe_print("ERROR: Number of created images does not match number of PDF pages.")
            safe_print("The PDF will NOT be deleted.")
            return False

        safe_print(f"Deleting processed PDF: {pdf_path}")
        pdf_path.unlink()
        safe_print(f"Deleted PDF: {pdf_path.name}")

        return True

    except Exception as e:
        safe_print("")
        safe_print("ERROR while processing this PDF.")
        safe_print(f"PDF was NOT deleted: {pdf_path}")
        safe_print(f"Reason: {e}")
        safe_print("")
        traceback.print_exc()

        safe_print("")
        safe_print("Images that may have been created before the error:")
        for image_path in created_images:
            safe_print(f"  {image_path}")

        return False


def main():
    safe_print("Starting PDF-to-image conversion.")
    safe_print(f"Target folder: {PDF_FOLDER}")
    safe_print(f"DPI: {DPI}")
    safe_print(f"JPG quality: {JPG_QUALITY}")

    if not PDF_FOLDER.exists():
        safe_print("")
        safe_print("ERROR: Folder does not exist:")
        safe_print(str(PDF_FOLDER))
        sys.exit(1)

    if not PDF_FOLDER.is_dir():
        safe_print("")
        safe_print("ERROR: This path is not a folder:")
        safe_print(str(PDF_FOLDER))
        sys.exit(1)

    pdf_files = sorted(PDF_FOLDER.glob("*.pdf"))

    safe_print("")
    safe_print(f"PDF files found: {len(pdf_files)}")

    if not pdf_files:
        safe_print("No PDF files found. Nothing to do.")
        return

    success_count = 0
    failure_count = 0

    for pdf_path in pdf_files:
        success = convert_pdf_to_images(pdf_path)

        if success:
            success_count += 1
        else:
            failure_count += 1

    safe_print("=" * 80)
    safe_print("All done.")
    safe_print(f"Successfully processed and deleted PDFs: {success_count}")
    safe_print(f"Failed or skipped PDFs: {failure_count}")

    if failure_count > 0:
        safe_print("")
        safe_print("Some PDFs were not deleted because conversion failed or was skipped.")
        safe_print("Check the messages above for details.")


if __name__ == "__main__":
    main()
