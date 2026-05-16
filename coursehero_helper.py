from pathlib import Path
from docx import Document

# Source folder containing .txt files
source_folder = Path(r"C:\Users\qacer\Downloads\essays_out")

# Output folder for .docx files
output_folder = source_folder / "word_docs"
output_folder.mkdir(exist_ok=True)

for txt_file in source_folder.glob("*.txt"):
    # Read text file
    text = txt_file.read_text(encoding="utf-8", errors="ignore")

    # Create Word document
    doc = Document()

    # Preserve blank lines as paragraph breaks
    for line in text.splitlines():
        doc.add_paragraph(line)

    # Save as .docx
    output_file = output_folder / f"{txt_file.stem}.docx"
    doc.save(output_file)

    print(f"Converted: {txt_file.name} -> {output_file.name}")

print("Done.")