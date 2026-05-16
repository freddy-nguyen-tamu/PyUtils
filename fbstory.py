import subprocess
import sys

def install_yt_dlp():
    try:
        import yt_dlp
        import yt_dlp_chrome_cookie_unlock   # plugin import
    except ImportError:
        print("Installing yt-dlp and Chrome cookie plugin...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-U", "yt-dlp[default,curl-cffi]", "yt-dlp-chrome-cookie-unlock"])

def download_facebook_story(url, output_filename=None):
    # The plugin auto‑activates when --cookies-from-browser chrome is used
    command = [
        "yt-dlp",
        "--impersonate", "Chrome-116",
        "--cookies-from-browser", "chrome",
        "--no-playlist",
        url,
    ]
    if output_filename:
        command.extend(["-o", output_filename])

    print(f"Running: {' '.join(command)}")
    subprocess.run(command, check=True)

if __name__ == "__main__":
    install_yt_dlp()
    story_url = input("Enter Facebook story URL: ")
    download_facebook_story(story_url)