import json
import re
from bs4 import BeautifulSoup

def extract_mp4_from_facebook_html(html_content):
    """
    Given the HTML of a Facebook Reel page, returns the first MP4 video URL found.
    """
    soup = BeautifulSoup(html_content, 'html.parser')
    target_script = None

    # Look for the script containing the Relay preloader for the video query
    for script in soup.find_all('script'):
        if script.string and 'adp_FBUnifiedVideoRootWithEntrypointQueryRelayPreloader' in script.string:
            target_script = script.string
            break

    if not target_script:
        raise ValueError("Could not locate video data in the HTML.")

    # Parse the JSON structure
    try:
        data = json.loads(target_script)
    except json.JSONDecodeError as e:
        raise ValueError("Failed to parse JSON from script.") from e

    # Navigate to the actual video data inside the Relay cache
    try:
        result_data = data['__bbox']['require'][0][3][1]['__bbox']['result']['data']
    except (KeyError, IndexError, TypeError) as e:
        raise ValueError("Unexpected JSON structure – cannot find video data.") from e

    # Recursively search for strings ending with .mp4 that look like video URLs
    mp4_urls = []

    def search(obj):
        if isinstance(obj, dict):
            for key, value in obj.items():
                if isinstance(value, str) and value.endswith('.mp4'):
                    # Optionally filter by key containing 'url' or 'playable'
                    if 'url' in key.lower() or 'playable' in key.lower():
                        mp4_urls.append(value)
                else:
                    search(value)
        elif isinstance(obj, list):
            for item in obj:
                search(item)

    search(result_data)

    if not mp4_urls:
        raise ValueError("No MP4 URL found in the video data.")

    # Return the first URL, unescaping any backslash-escaped slashes
    return mp4_urls[0].replace('\\/', '/')

# Example usage with the file you provided
if __name__ == "__main__":
    with open('webpage.txt', 'r', encoding='utf-8') as f:
        html = f.read()
    try:
        video_url = extract_mp4_from_facebook_html(html)
        print("Found MP4 URL:")
        print(video_url)
    except Exception as e:
        print("Error:", e)