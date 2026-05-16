import os
import pikepdf

directory = r"C:\Users\qacer\Downloads\25-FALL-CSCE-629-601-ANALYSIS-OF-ALGORITHMS-2025-Dec-08_15-24-00-176\25-FALL-CSCE-629-601-ANALYSIS-OF-ALGORITHMS-2025-Dec-08_15-24-00-176\viewer\files"
output_file = r"C:\Users\qacer\Downloads\merged_output.pdf"

def merge_with_pikepdf(directory, output_file):
    pdf_out = pikepdf.Pdf.new()
    skipped = []

    # All PDFs, name ascending
    pdf_files = sorted(
        f for f in os.listdir(directory) if f.lower().endswith(".pdf")
    )

    for name in pdf_files:
        path = os.path.join(directory, name)
        print("Merging:", path)
        try:
            with pikepdf.Pdf.open(path) as src:
                pdf_out.pages.extend(src.pages)
        except Exception as e:
            print(f"  !! Skipping (error): {e}")
            skipped.append((path, str(e)))

    pdf_out.save(output_file)
    pdf_out.close()

    print(f"\nMerged PDF saved as: {output_file}")
    if skipped:
        print("\nSkipped files:")
        for path, reason in skipped:
            print(" -", path, "=>", reason)

if __name__ == "__main__":
    merge_with_pikepdf(directory, output_file)