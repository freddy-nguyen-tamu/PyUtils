from pathlib import Path
import re
import mpyq

MAP_FILE = Path(r"C:\Users\qacer\Downloads\Where 08 Eng 5000 v11.w3x")
FIXED_MAP = MAP_FILE.with_name(MAP_FILE.stem + "_mpq_fixed" + MAP_FILE.suffix)
OUT_DIR = MAP_FILE.with_name("wc3_extracted_music")

audio_exts = (".mp3", ".wav", ".ogg", ".flac", ".mid", ".midi")

known_files = [
    "(listfile)",
    "war3map.j",
    "scripts\\war3map.j",
    "Scripts\\war3map.j",
    "war3map.wts",
    "war3map.w3i",
    "war3map.imp",
]

possible_audio_names = [
    "war3mapImported\\music.mp3",
    "war3mapImported\\Music.mp3",
    "war3mapImported\\bgm.mp3",
    "war3mapImported\\BGM.mp3",
    "war3mapImported\\sound.mp3",
    "war3mapImported\\Sound.mp3",
    "war3mapImported\\1.mp3",
    "war3mapImported\\2.mp3",
    "war3mapImported\\3.mp3",
    "war3mapImported\\music.wav",
    "war3mapImported\\bgm.wav",
    "war3mapImported\\BGM.wav",
]

def make_mpq_readable(path: Path) -> Path:
    data = path.read_bytes()

    print("File:", path)
    print("Size:", len(data), "bytes")
    print("First 64 bytes:", data[:64])

    if data.startswith(b"MPQ\x1a"):
        return path

    mpq_offset = data.find(b"MPQ\x1a")
    if mpq_offset == -1:
        raise RuntimeError("No MPQ header found.")

    print("MPQ header found at offset:", mpq_offset)
    FIXED_MAP.write_bytes(data[mpq_offset:])
    print("Wrote fixed MPQ copy:", FIXED_MAP)
    return FIXED_MAP

def read_if_exists(archive, name):
    try:
        data = archive.read_file(name)
    except Exception:
        return None

    if data is None:
        return None

    return data

def save_file(name, data):
    OUT_DIR.mkdir(exist_ok=True)
    safe_name = name.replace("\\", "_").replace("/", "_").replace(":", "_")
    out_path = OUT_DIR / safe_name
    out_path.write_bytes(data)
    print("Extracted:", out_path)

def main():
    readable_map = make_mpq_readable(MAP_FILE)
    archive = mpyq.MPQArchive(str(readable_map))

    print("\n=== Listed files from MPQ ===")
    if archive.files:
        for name in archive.files:
            print(name)
    else:
        print("No listfile found, so mpyq cannot enumerate files.")

    found_audio = []

    print("\n=== Audio files from visible list ===")
    for name in archive.files:
        if name.lower().endswith(audio_exts):
            data = read_if_exists(archive, name)
            if data:
                found_audio.append(name)
                save_file(name, data)

    if not found_audio:
        print("No audio found from visible file list.")

    print("\n=== Known internal Warcraft III files ===")
    all_text = ""

    for name in known_files:
        data = read_if_exists(archive, name)
        if not data:
            print("Missing:", name)
            continue

        print("Found:", name, "size:", len(data))

        if name.lower().endswith((".j", ".wts", ".imp")) or name == "(listfile)":
            text = data.decode("utf-8", errors="ignore")
            all_text += "\n" + text

            print(f"\n--- Text preview from {name} ---")
            print(text[:3000])

    print("\n=== Music/script references found ===")
    refs = set()

    for match in re.findall(
        r'["\']([^"\']+\.(?:mp3|wav|ogg|flac|mid|midi))["\']',
        all_text,
        flags=re.I,
    ):
        refs.add(match)

    for line in all_text.splitlines():
        line_clean = line.strip()
        if any(k in line_clean for k in [
            "PlayMusic",
            "SetMapMusic",
            "PlayThematicMusic",
            "CreateSound",
            "gg_snd",
            ".mp3",
            ".wav",
            ".ogg",
        ]):
            refs.add(line_clean)

    if refs:
        for r in sorted(refs):
            print(r)
    else:
        print("No music references found in readable script/list files.")

    print("\n=== Trying common imported audio names ===")
    for name in possible_audio_names:
        data = read_if_exists(archive, name)
        if data:
            found_audio.append(name)
            save_file(name, data)

    if not found_audio:
        print("\nNo audio could be extracted with mpyq.")
        print("This map is probably protected or has removed/hidden filenames.")
        print("Next best tool: open the .w3x with Ladik's MPQ Editor, then use a Warcraft III listfile.")
    else:
        print("\nDone. Extracted audio folder:")
        print(OUT_DIR)

if __name__ == "__main__":
    main()