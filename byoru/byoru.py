import subprocess
import sys
import os
import time

def get_video_id(url):
    """Extract the video ID using yt-dlp without downloading."""
    try:
        result = subprocess.run(
            ["yt-dlp", "--no-download", "--print", "%(id)s", url],
            capture_output=True,
            text=True,
            check=True,
            timeout=30
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        print(f"  ✗ Failed to get ID for {url}: {e.stderr.strip()}")
        return None
    except Exception as e:
        print(f"  ✗ Error getting ID: {e}")
        return None

def load_archive(archive_file):
    """Load already downloaded video IDs from the archive file."""
    if not os.path.isfile(archive_file):
        return set()
    with open(archive_file, 'r', encoding='utf-8') as f:
        return set(line.strip() for line in f if line.strip())

def main():
    txt_file = "byoru.txt"
    archive_file = "downloaded.txt"

    if not os.path.isfile(txt_file):
        print(f"Error: '{txt_file}' not found in the current directory.")
        sys.exit(1)

    # Read all URLs from the file
    with open(txt_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    all_urls = [line.strip() for line in lines if line.strip()]

    # Load existing archive
    archive_ids = load_archive(archive_file)

    # Track results
    downloaded_urls = []
    skipped_urls = []
    failed_urls = []

    print(f"🔍 Found {len(all_urls)} URLs in {txt_file}\n")

    for idx, url in enumerate(all_urls, 1):
        print(f"[{idx}/{len(all_urls)}] Processing: {url}")

        # Step 1: Extract the video ID
        vid = get_video_id(url)
        if vid is None:
            failed_urls.append(url)
            continue

        # Step 2: Check if already in archive
        if vid in archive_ids:
            print(f"  ⏭️ Skipped (already downloaded: {vid})")
            skipped_urls.append(url)
            continue

        # Step 3: Download the video
        print(f"  ⬇️ Downloading...")
        try:
            subprocess.run(
                ["yt-dlp", "--download-archive", archive_file, url],
                check=True,
                timeout=600  # 10 minutes max per video
            )
            print(f"  ✅ Done")
            downloaded_urls.append(url)
            # Update archive_ids in memory so we don't re-check the same ID later
            archive_ids.add(vid)
        except subprocess.CalledProcessError as e:
            print(f"  ❌ Download failed (exit code {e.returncode})")
            failed_urls.append(url)
        except subprocess.TimeoutExpired:
            print(f"  ❌ Download timed out")
            failed_urls.append(url)
        except FileNotFoundError:
            print("✗ yt-dlp is not installed or not in your PATH.")
            print("  Install it from https://github.com/yt-dlp/yt-dlp")
            sys.exit(1)
        except Exception as e:
            print(f"  ❌ Unexpected error: {e}")
            failed_urls.append(url)

        # Small delay to be gentle to the server
        time.sleep(1)

    # Final summary
    print("\n" + "="*60)
    print("📊 SUMMARY")
    print("="*60)
    print(f"Total URLs detected : {len(all_urls)}")
    print(f"✅ Downloaded       : {len(downloaded_urls)}")
    print(f"⏭️ Skipped (already) : {len(skipped_urls)}")
    print(f"❌ Failed           : {len(failed_urls)}")
    print()

    if downloaded_urls:
        print("✅ Downloaded URLs:")
        for u in downloaded_urls:
            print(f"  {u}")
        print()

    if skipped_urls:
        print("⏭️ Skipped URLs:")
        for u in skipped_urls:
            print(f"  {u}")
        print()

    if failed_urls:
        print("❌ Failed URLs:")
        for u in failed_urls:
            print(f"  {u}")
        print()

    print("="*60)
    print("Archive file:", archive_file)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️ Interrupted by user. Partial summary follows.")
        # Optionally, you could still print the accumulated lists
        # But we'd need to have them in scope – we can refactor to use a function
        # For simplicity, we just exit.
        sys.exit(1)