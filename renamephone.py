import os
import json
import requests
from pathlib import Path, PureWindowsPath
from mutagen.easyid3 import EasyID3
from mutagen.mp3 import MP3
import sys
import time
import win32com.client

# ====================================
# CONFIGURATION
# ====================================
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
BATCH_SIZE = 20
PROGRESS_FILE = "rename_progress.json"

# Groq API settings
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.1-8b-instant"

# ====================================
# HELPER FUNCTIONS
# ====================================

def get_shell_path(path_string):
    """Convert shell namespace path to filesystem path using COM."""
    try:
        shell = win32com.client.Dispatch("Shell.Application")
        # Parse the path
        parts = path_string.replace("This PC\\", "").split("\\")
        
        # Navigate through shell namespace
        folder = shell.Namespace(17)  # 17 = ssfDRIVES (My Computer)
        
        for part in parts:
            if not folder:
                return None
            found = False
            for item in folder.Items():
                if item.Name == part:
                    folder = item.GetFolder
                    found = True
                    break
            if not found:
                return None
        
        if folder:
            return folder.Self.Path
    except:
        pass
    return None


def normalize_path(path_input):
    """Convert various path formats to usable Path object."""
    path_str = str(path_input).strip()
    
    # Try as regular path first
    try:
        p = Path(path_str)
        if p.exists():
            return p
    except:
        pass
    
    # Try shell namespace path
    if "This PC" in path_str or "Computer" in path_str:
        shell_path = get_shell_path(path_str)
        if shell_path:
            p = Path(shell_path)
            if p.exists():
                return p
    
    # Try UNC path
    if path_str.startswith("\\\\"):
        try:
            p = Path(path_str)
            if p.exists():
                return p
        except:
            pass
    
    # Try as raw string
    try:
        p = Path(r"{}".format(path_str))
        if p.exists():
            return p
    except:
        pass
    
    return None


def get_android_path():
    """Get Android device path with improved detection."""
    print(" Searching for storage locations...")
    print("\nAvailable options:")
    
    possible_paths = []
    
    # Check all drive letters (including network drives)
    for drive in "DEFGHIJKLMNOPQRSTUVWXYZ":
        for drive_path_str in [f"{drive}:\\", f"{drive}:/"]:
            try:
                drive_path = Path(drive_path_str)
                if drive_path.exists():
                    # Check if it has MP3 files
                    mp3_found = False
                    try:
                        mp3_count = sum(1 for _ in drive_path.rglob("*.mp3"))
                        if mp3_count > 0:
                            mp3_found = True
                    except:
                        pass
                    
                    possible_paths.append({
                        'path': drive_path,
                        'display': str(drive_path),
                        'has_mp3': mp3_found
                    })
            except:
                pass
    
    # Check common user folders
    user_folder = Path.home()
    user_paths = [
        user_folder / "Music",
        user_folder / "Downloads",
        user_folder / "Phone",
        user_folder / "Android",
    ]
    
    for path in user_paths:
        if path.exists():
            mp3_found = False
            try:
                mp3_count = sum(1 for _ in path.rglob("*.mp3"))
                if mp3_count > 0:
                    mp3_found = True
            except:
                pass
            
            possible_paths.append({
                'path': path,
                'display': str(path),
                'has_mp3': mp3_found
            })
    
    # Display options
    for i, item in enumerate(possible_paths, 1):
        status = " Has MP3 files" if item['has_mp3'] else ""
        print(f"{i}. {item['display']} {status}")
    
    print(f"\n{len(possible_paths) + 1}. Enter custom path (supports 'This PC\\...' format)")
    
    choice = input("\nSelect option: ").strip()
    
    # Try numbered selection
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(possible_paths):
            return possible_paths[idx]['path']
    except ValueError:
        pass
    
    # Try as custom path
    print(f"Attempting to resolve: {choice}")
    normalized = normalize_path(choice)
    
    if normalized:
        print(f" Resolved to: {normalized}")
        return normalized
    
    # Last resort: try raw input
    print(" Could not resolve path automatically. Trying as-is...")
    return Path(choice)


def find_all_mp3_files(root_path):
    """Recursively find all MP3 files with better error handling."""
    print(f" Scanning {root_path} for MP3 files...")
    mp3_files = []
    
    try:
        # Try recursive search
        for ext in ["*.mp3", "*.MP3"]:
            try:
                found = list(root_path.rglob(ext))
                mp3_files.extend(found)
                print(f"  Found {len(found)} files with extension {ext}")
            except Exception as e:
                print(f"   Error searching for {ext}: {e}")
    except Exception as e:
        print(f"   Error during recursive search: {e}")
        # Fallback: try non-recursive search
        try:
            for ext in ["*.mp3", "*.MP3"]:
                found = list(root_path.glob(ext))
                mp3_files.extend(found)
        except Exception as e2:
            print(f"   Fallback search also failed: {e2}")
    
    return mp3_files


def load_progress():
    """Load progress tracking file."""
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"completed": [], "failed": []}


def save_progress(progress):
    """Save progress tracking file."""
    with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
        json.dump(progress, f, indent=2, ensure_ascii=False)


def capitalize_title(title):
    """Capitalize first letter of each word in title."""
    lowercase_words = {'a', 'an', 'and', 'as', 'at', 'but', 'by', 'for', 'in', 
                      'of', 'on', 'or', 'the', 'to', 'with', 'ft', 'feat'}
    
    words = title.split()
    capitalized = []
    
    for i, word in enumerate(words):
        if i == 0 or i == len(words) - 1:
            capitalized.append(word.capitalize())
        elif word.lower() in lowercase_words:
            capitalized.append(word.lower())
        else:
            capitalized.append(word.capitalize())
    
    return ' '.join(capitalized)


def format_artist_name(artist):
    """Format artist name with 'ft.' separator."""
    if not artist or artist.lower() == 'unknown':
        return artist
    
    artist = artist.replace(' feat. ', ' ft. ')
    artist = artist.replace(' featuring ', ' ft. ')
    artist = artist.replace(' feat ', ' ft. ')
    artist = artist.replace(' & ', ' ft. ')
    artist = artist.replace(', ', ' ft. ')
    artist = artist.replace(' x ', ' ft. ')
    
    parts = artist.split(' ft. ')
    formatted_parts = [capitalize_title(part.strip()) for part in parts]
    
    return ' ft. '.join(formatted_parts)


def ask_groq_batch(file_paths):
    """Ask Groq API to extract metadata for a batch of files."""
    if not GROQ_API_KEY:
        print(" GROQ_API_KEY environment variable not set!")
        sys.exit(1)
    
    file_names = [f.name for f in file_paths]
    
    system_message = (
        "You are an MP3 metadata expert. Extract song titles and artists from filenames. "
        "Output ONLY valid JSON with no markdown, no code blocks, no explanation."
    )
    
    user_prompt = f"""
Extract metadata from these MP3 filenames:

{json.dumps(file_names, ensure_ascii=False, indent=2)}

Rules:
1. Keep file_name EXACTLY as provided
2. Extract clean title (remove: 'Official Music Video', 'Lyric Video', '(Official Audio)', '[OFFICIAL VIDEO]', 'MV', 'HD', '4K', 'lyrics', 'audio', etc.)
3. Remove file extension from title
4. Remove common prefixes like 'yt1s.com - ', 'youtube - ', etc.
5. Extract artist from patterns like "Artist - Song" or "Song by Artist"
6. If multiple artists, separate with 'ft.' (e.g., "Artist1 ft. Artist2")
7. If artist is unclear, use "Unknown"
8. Title format: Capitalize first letter of each word
9. Artist format: Capitalize names, use 'ft.' as separator

Output format (JSON only, no markdown):
{{
  "metadata_list": [
    {{
      "file_name": "exact_filename.mp3",
      "title": "Properly Capitalized Title",
      "artist": "Artist1 ft. Artist2"
    }}
  ]
}}
"""
    
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.1
    }
    
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    
    try:
        response = requests.post(GROQ_API_URL, headers=headers, json=payload, timeout=30)
        
        if response.status_code != 200:
            print(f" Groq API error {response.status_code}: {response.text}")
            return []
        
        resp_json = response.json()
        json_text = resp_json["choices"][0]["message"]["content"].strip()
        
        if "```" in json_text:
            parts = json_text.split("```")
            for part in parts:
                part = part.strip()
                if part.startswith("json"):
                    json_text = part[4:].strip()
                    break
                elif part.startswith("{"):
                    json_text = part
                    break
        
        data = json.loads(json_text)
        return data.get("metadata_list", [])
        
    except Exception as e:
        print(f" Error calling Groq API: {e}")
        return []


def update_mp3_metadata(file_path, title, artist):
    """Update MP3 ID3 tags."""
    try:
        try:
            audio = MP3(file_path, ID3=EasyID3)
        except:
            audio = MP3(file_path)
            audio.add_tags()
            audio = MP3(file_path, ID3=EasyID3)
        
        if title:
            audio["title"] = capitalize_title(title)
        if artist:
            audio["artist"] = format_artist_name(artist)
        
        audio.save()
        return True
        
    except Exception as e:
        print(f"   Failed to update tags: {e}")
        return False


def main():
    print("=" * 60)
    print(" ANDROID MP3 BATCH RENAMER WITH GROQ AI")
    print("=" * 60)
    
    android_path = get_android_path()
    
    if not android_path or not android_path.exists():
        print(f" Path does not exist: {android_path}")
        print("\n Troubleshooting:")
        print("  1. For phone: Make sure USB debugging or File Transfer mode is enabled")
        print("  2. Try copying files to a local folder (e.g., C:\\Users\\YourName\\Music)")
        print("  3. Try entering the full path with drive letter (e.g., D:\\Music)")
        return
    
    print(f" Using path: {android_path}")
    
    all_mp3_files = find_all_mp3_files(android_path)
    
    if not all_mp3_files:
        print(" No MP3 files found!")
        return
    
    print(f"\n Found {len(all_mp3_files)} MP3 files")
    
    progress = load_progress()
    completed_paths = set(progress["completed"])
    
    pending_files = [f for f in all_mp3_files if str(f) not in completed_paths]
    
    print(f" Already processed: {len(completed_paths)}")
    print(f" Remaining: {len(pending_files)}")
    
    if not pending_files:
        print("\n All files already processed!")
        return
    
    print("\n Sample files to process:")
    for i, f in enumerate(pending_files[:5], 1):
        print(f"  {i}. {f.name}")
    if len(pending_files) > 5:
        print(f"  ... and {len(pending_files) - 5} more")
    
    response = input(f"\n Process {len(pending_files)} files in batches of {BATCH_SIZE}? (y/n): ").lower()
    if response != 'y':
        print("Cancelled.")
        return
    
    total_batches = (len(pending_files) + BATCH_SIZE - 1) // BATCH_SIZE
    success_count = 0
    fail_count = 0
    
    for batch_num in range(total_batches):
        start_idx = batch_num * BATCH_SIZE
        end_idx = min(start_idx + BATCH_SIZE, len(pending_files))
        batch = pending_files[start_idx:end_idx]
        
        print(f"\n{'=' * 60}")
        print(f" BATCH {batch_num + 1}/{total_batches}")
        print(f"   Processing files {start_idx + 1} to {end_idx} of {len(pending_files)}")
        print(f"{'=' * 60}")
        
        print(" Calling Groq API...")
        metadata_list = ask_groq_batch(batch)
        
        if not metadata_list:
            print(" No metadata received, skipping batch")
            time.sleep(2)
            continue
        
        metadata_dict = {m["file_name"]: m for m in metadata_list}
        
        for file_path in batch:
            metadata = metadata_dict.get(file_path.name)
            
            if not metadata:
                print(f"\n {file_path.name}")
                print("  No metadata received")
                progress["failed"].append(str(file_path))
                fail_count += 1
                continue
            
            title = metadata.get("title", "").strip()
            artist = metadata.get("artist", "").strip()
            
            print(f"\n {file_path.name}")
            print(f"  Title:  {capitalize_title(title)}")
            print(f"  Artist: {format_artist_name(artist)}")
            
            if update_mp3_metadata(file_path, title, artist):
                print("   Updated successfully")
                progress["completed"].append(str(file_path))
                success_count += 1
            else:
                progress["failed"].append(str(file_path))
                fail_count += 1
            
            save_progress(progress)
        
        if batch_num < total_batches - 1:
            print(f"\nWaiting 2 seconds before next batch...")
            time.sleep(2)
    
    print("\n" + "=" * 60)
    print(" FINAL SUMMARY")
    print("=" * 60)
    print(f" Successfully processed: {success_count}")
    print(f" Failed: {fail_count}")
    print(f" Total files: {len(all_mp3_files)}")
    print(f" Progress saved to: {PROGRESS_FILE}")
    
    if fail_count > 0:
        print(f"\n {fail_count} files failed. Run the script again to retry.")
    else:
        print("\n All files processed successfully!")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n Interrupted by user. Progress has been saved.")
    except Exception as e:
        print(f"\n Fatal error: {e}")
        import traceback
        traceback.print_exc()