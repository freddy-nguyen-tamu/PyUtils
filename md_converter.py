from pathlib import Path

folder = Path(r"C:\Users\qacer\Downloads\aireceptionistpart2")
output_file = folder / "combined.md"

md_files = sorted(
    file for file in folder.glob("*.md")
    if file.name != output_file.name
)

with output_file.open("w", encoding="utf-8") as out:
    for i, file in enumerate(md_files):
        print(f"Adding: {file.name}")

        content = file.read_text(encoding="utf-8")

        # Mark where each original file begins
        out.write(f"<!-- SOURCE FILE: {file.name} -->\n\n")
        out.write(content.rstrip())

        # Separation between files
        if i < len(md_files) - 1:
            out.write("\n\n---\n\n")

print(f"\nCombined {len(md_files)} Markdown files.")
print(f"Output: {output_file}")