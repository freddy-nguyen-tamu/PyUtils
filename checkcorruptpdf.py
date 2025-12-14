import os
from PyPDF2 import PdfReader

directory = r"C:\Users\qacer\Downloads\25-FALL-CSCE-629-601-ANALYSIS-OF-ALGORITHMS-2025-Dec-08_15-24-00-176\25-FALL-CSCE-629-601-ANALYSIS-OF-ALGORITHMS-2025-Dec-08_15-24-00-176\viewer\files"

pdf_files = sorted(
    [f for f in os.listdir(directory) if f.lower().endswith(".pdf")]
)

for name in pdf_files:
    path = os.path.join(directory, name)
    print(f"Checking: {path}")
    try:
        reader = PdfReader(path)
        # force access to all pages so errors show up here
        _ = len(reader.pages)
        print("  OK")
    except Exception as e:
        print(f"  ERROR: {e}")
