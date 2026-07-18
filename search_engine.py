from ddgs import DDGS
import requests
import sys
import mimetypes

QUERY = "sample query"
OUTPUT_FILE = "first_image.jpg"
MAX_ATTEMPTS = 10

def main():
    print(f"Searching images for: {QUERY}")

    # Get multiple results
    with DDGS() as ddgs:
        image_urls = [r["image"] for r in ddgs.images(QUERY, max_results=MAX_ATTEMPTS) if r.get("image")]

    if not image_urls:
        print("ERROR: No image results found.")
        sys.exit(1)

    print(f"Got {len(image_urls)} image candidates. Trying each until one works...")

    # Try downloading each URL in order
    for idx, url in enumerate(image_urls, 1):
        print(f"  [{idx}/{len(image_urls)}] Attempting {url} ... ", end="")
        try:
            # Stream request with a small timeout
            resp = requests.get(url, stream=True, timeout=10)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"FAIL ({e})")
            continue

        # Check that the content is an image
        content_type = resp.headers.get("Content-Type", "")
        if not content_type.startswith("image/"):
            print(f"SKIP (not an image, got {content_type})")
            resp.close()
            continue

        # Download and save
        try:
            with open(OUTPUT_FILE, "wb") as f:
                for chunk in resp.iter_content(8192):
                    f.write(chunk)
            print(f"SUCCESS -> saved '{OUTPUT_FILE}'")
            resp.close()
            return
        except Exception as e:
            print(f"WRITE ERROR ({e})")
            resp.close()
            continue

    # If we exit the loop, no image was saved
    print(f"ERROR: Could not download any valid image from the first {MAX_ATTEMPTS} results.")
    sys.exit(1)

if __name__ == "__main__":
    main()