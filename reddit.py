import os
import requests
from urllib.parse import urlparse

def download_images_from_txt(txt_path, output_dir="downloaded_images"):
    """
    Reads a .txt file with one image URL per line and downloads each image.
    
    Args:
        txt_path (str): Path to the .txt file containing image URLs.
        output_dir (str): Directory where images will be saved (created if missing).
    """
    # Create output directory if it doesn't exist
    os.makedirs(output_dir, exist_ok=True)

    # Read URLs from the text file
    with open(txt_path, 'r') as file:
        urls = [line.strip() for line in file if line.strip()]

    if not urls:
        print("No URLs found in the file.")
        return

    for idx, url in enumerate(urls, start=1):
        try:
            # Send GET request
            response = requests.get(url, stream=True, timeout=10)
            response.raise_for_status()  # Raise error for bad status codes

            # Extract filename from URL or generate one
            parsed = urlparse(url)
            filename = os.path.basename(parsed.path)
            if not filename or '.' not in filename:
                # Fallback to index + extension from content-type
                content_type = response.headers.get('content-type', '')
                if 'jpeg' in content_type or 'jpg' in content_type:
                    ext = '.jpg'
                elif 'png' in content_type:
                    ext = '.png'
                else:
                    ext = '.img'
                filename = f"image_{idx}{ext}"

            # Sanitize filename (remove any path separators)
            filename = os.path.basename(filename)
            filepath = os.path.join(output_dir, filename)

            # Prevent overwriting by adding counter if file exists
            base, ext = os.path.splitext(filename)
            counter = 1
            while os.path.exists(filepath):
                filepath = os.path.join(output_dir, f"{base}_{counter}{ext}")
                counter += 1

            # Download and save image
            with open(filepath, 'wb') as img_file:
                for chunk in response.iter_content(chunk_size=8192):
                    img_file.write(chunk)

            print(f"✓ Downloaded: {filename}")

        except requests.exceptions.RequestException as e:
            print(f"✗ Failed to download {url}: {e}")
        except Exception as e:
            print(f"✗ Unexpected error for {url}: {e}")

    print(f"\nAll downloads finished. Images saved in '{output_dir}'")

if __name__ == "__main__":
    # Example usage – change the path to your .txt file
    txt_file = "reddit.txt"
    download_images_from_txt(txt_file)