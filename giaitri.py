import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import time
import html

# Configuration
BASE_URL = "https://giaitri321.pro/"
OUTPUT_FILE = "giaitri321_full_text.html"
USER_AGENT = "Mozilla/5.0 (compatible; GiaiTri321Crawler/1.0)"
REQUEST_DELAY = 1  # seconds between requests
MAX_PAGES = None   # set a number to limit pages, None for all reachable

# Track visited URLs
visited = set()
queue = [BASE_URL]

# Extract domain for filtering internal links
base_domain = urlparse(BASE_URL).netloc


def is_internal(url):
    """Check if URL belongs to the same domain."""
    parsed = urlparse(url)
    return parsed.netloc == base_domain or parsed.netloc == ""


def clean_text(html_content):
    """Extract visible text from HTML, remove scripts/styles."""
    soup = BeautifulSoup(html_content, "html.parser")

    for element in soup(["script", "style", "noscript", "iframe"]):
        element.decompose()

    text = soup.get_text(separator="\n", strip=True)
    return text


def write_html_header(f):
    f.write("""<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>GiaiTri321 Crawl Output</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            line-height: 1.6;
            margin: 40px;
            background: #f7f7f7;
            color: #222;
        }
        .page {
            background: white;
            border: 1px solid #ddd;
            padding: 24px;
            margin-bottom: 30px;
            border-radius: 8px;
            box-shadow: 0 1px 4px rgba(0,0,0,0.05);
        }
        .url {
            font-size: 18px;
            font-weight: bold;
            margin-bottom: 12px;
            word-break: break-all;
        }
        pre {
            white-space: pre-wrap;
            word-wrap: break-word;
            font-family: inherit;
            margin: 0;
        }
    </style>
</head>
<body>
    <h1>GiaiTri321 Crawl Output</h1>
""")

def write_html_footer(f):
    f.write("""
</body>
</html>
""")


def crawl():
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        write_html_header(f)

        page_count = 0

        while queue:
            if MAX_PAGES and page_count >= MAX_PAGES:
                break

            url = queue.pop(0)
            if url in visited:
                continue

            print(f"[{page_count + 1}] Crawling: {url}")

            try:
                headers = {"User-Agent": USER_AGENT}
                resp = requests.get(url, headers=headers, timeout=10)

                if resp.status_code != 200:
                    print(f"  Skipped (status {resp.status_code})")
                    visited.add(url)
                    continue

                # Force UTF-8 if website encoding detection is bad
                if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
                    resp.encoding = resp.apparent_encoding or "utf-8"

                visited.add(url)
                page_count += 1

                text_content = clean_text(resp.text)

                # Write one page block into HTML
                f.write('<div class="page">\n')
                f.write(f'  <div class="url">URL: <a href="{html.escape(url)}" target="_blank">{html.escape(url)}</a></div>\n')
                f.write(f"  <pre>{html.escape(text_content)}</pre>\n")
                f.write("</div>\n")

                # Parse links
                soup = BeautifulSoup(resp.text, "html.parser")
                for link in soup.find_all("a", href=True):
                    href = link["href"].strip()
                    absolute = urljoin(url, href)
                    absolute = absolute.split("#")[0]

                    if not is_internal(absolute):
                        continue
                    if absolute in visited or absolute in queue:
                        continue
                    if absolute.lower().endswith(
                        (".jpg", ".png", ".gif", ".pdf", ".zip", ".rar", ".apk", ".jar", ".sis", ".sisx")
                    ):
                        continue

                    queue.append(absolute)

                time.sleep(REQUEST_DELAY)

            except Exception as e:
                print(f"  Error: {e}")
                visited.add(url)

        write_html_footer(f)

    print(f"\nDone! Crawled {page_count} pages. Output saved to {OUTPUT_FILE}")


if __name__ == "__main__":
    crawl()