from bs4 import BeautifulSoup, NavigableString, Comment
from pathlib import Path
import html
import re

INPUT_FILE = "input.html"          # change to your file path
OUTPUT_TEXT_FILE = "extracted.txt"
OUTPUT_HTML_FILE = "clean_output.html"


def normalize_space(text: str) -> str:
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def is_visible_text(node) -> bool:
    if not isinstance(node, NavigableString):
        return False
    if isinstance(node, Comment):
        return False

    parent = node.parent
    if parent is None:
        return False

    hidden_tags = {
        "script", "style", "noscript", "svg", "canvas",
        "meta", "link", "iframe", "object", "template"
    }
    if parent.name in hidden_tags:
        return False

    text = str(node).strip()
    if not text:
        return False

    return True


def extract_raw_text(html_content: str) -> tuple[str, str]:
    soup = BeautifulSoup(html_content, "html.parser")

    # Get title if available
    title = "Extracted Document"
    if soup.title and soup.title.string:
        title = normalize_space(soup.title.string)

    # Remove obvious junk
    for tag in soup([
        "script", "style", "noscript", "svg", "canvas",
        "meta", "link", "iframe", "object", "template"
    ]):
        tag.decompose()

    # Prefer likely content containers if present
    content_root = (
        soup.select_one(".doc-content")
        or soup.select_one(".doc")
        or soup.body
        or soup
    )

    texts = []
    seen = set()

    for node in content_root.descendants:
        if is_visible_text(node):
            text = normalize_space(str(node))
            if text and text not in seen:
                texts.append(text)
                seen.add(text)

    merged_text = "\n".join(texts)
    merged_text = normalize_space(merged_text)

    return title, merged_text


def text_to_clean_html(title: str, merged_text: str) -> str:
    paragraphs = [p.strip() for p in merged_text.split("\n") if p.strip()]

    body_html = "\n".join(
        f"<p>{html.escape(p)}</p>" for p in paragraphs
    )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    body {{
      font-family: Arial, sans-serif;
      line-height: 1.6;
      max-width: 900px;
      margin: 40px auto;
      padding: 0 20px;
      color: #222;
      background: #fff;
    }}
    h1 {{
      font-size: 2rem;
      margin-bottom: 1rem;
    }}
    p {{
      margin: 0 0 1em 0;
      white-space: pre-wrap;
    }}
  </style>
</head>
<body>
  <h1>{html.escape(title)}</h1>
  {body_html}
</body>
</html>
"""


def main():
    html_content = Path(INPUT_FILE).read_text(encoding="utf-8", errors="ignore")
    title, merged_text = extract_raw_text(html_content)

    Path(OUTPUT_TEXT_FILE).write_text(merged_text, encoding="utf-8")
    clean_html = text_to_clean_html(title, merged_text)
    Path(OUTPUT_HTML_FILE).write_text(clean_html, encoding="utf-8")

    print(f"Done.")
    print(f"Title: {title}")
    print(f"Raw text saved to: {OUTPUT_TEXT_FILE}")
    print(f"Clean HTML saved to: {OUTPUT_HTML_FILE}")


if __name__ == "__main__":
    main()