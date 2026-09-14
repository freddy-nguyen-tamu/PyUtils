import os
from datetime import datetime

# Project root
PROJECT_ROOT = r"C:\Projects\Maelstrom"

# Exact files to dump, relative to PROJECT_ROOT
TARGET_FILES = [
    # React Native JS/TS entry points
    "App.tsx",
    "index.js",
    "app.json",
    "package.json",
    "package-lock.json",
    "metro.config.js",
    "babel.config.js",

    # Android project config
    "android/settings.gradle",
    "android/build.gradle",
    "android/gradle.properties",
    "android/local.properties",
    "android/app/build.gradle",
    "android/app/src/main/AndroidManifest.xml",

    # Android native app entry files, Kotlin or Java depending on project template
    "android/app/src/main/java/com/maelstrom/MainApplication.kt",
    "android/app/src/main/java/com/maelstrom/MainApplication.java",
    "android/app/src/main/java/com/maelstrom/MainActivity.kt",
    "android/app/src/main/java/com/maelstrom/MainActivity.java",

    # React Native generated/autolinking context
    "android/build/generated/autolinking/autolinking.json",

    # The patched dependency that has caused compile/runtime issues
    "node_modules/react-native-track-player/package.json",
    "node_modules/react-native-track-player/android/build.gradle",
    "node_modules/react-native-track-player/android/src/main/AndroidManifest.xml",
    "node_modules/react-native-track-player/android/src/main/java/com/doublesymmetry/trackplayer/module/MusicModule.kt",
    "node_modules/react-native-track-player/android/src/main/java/com/doublesymmetry/trackplayer/service/MusicService.kt",
    "node_modules/react-native-track-player/android/src/main/java/com/doublesymmetry/trackplayer/service/MusicService.kt.bak",
    "node_modules/react-native-track-player/android/src/main/java/com/doublesymmetry/trackplayer/service/MusicService.kt.bak2",

    # Track model/events/utils, useful for fixing the Event/emit signature correctly
    "node_modules/react-native-track-player/android/src/main/java/com/doublesymmetry/trackplayer/module/MusicEvents.kt",
    "node_modules/react-native-track-player/android/src/main/java/com/doublesymmetry/trackplayer/model/Track.kt",
    "node_modules/react-native-track-player/android/src/main/java/com/doublesymmetry/trackplayer/utils/BundleUtils.kt",
]

# Output file
OUTPUT_FILE = "maelstrom_rn_android_context_dump.txt"

# Set to None to never skip by size
MAX_FILE_SIZE_BYTES = None


def is_text_file_readable(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            chunk = f.read(4096)
        return b"\x00" not in chunk
    except Exception:
        return False


def read_text_file(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.readlines()
    except UnicodeDecodeError:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.readlines()


def write_file_content(out_file, full_path: str, display_path: str):
    out_file.write("\n" + "=" * 100 + "\n")
    out_file.write(f"FILE: {display_path}\n")
    out_file.write("=" * 100 + "\n")

    if not os.path.exists(full_path):
        out_file.write("FILE NOT FOUND\n\n")
        return False

    try:
        if MAX_FILE_SIZE_BYTES is not None:
            size = os.path.getsize(full_path)
            if size > MAX_FILE_SIZE_BYTES:
                out_file.write(f"SKIPPED (file too large): {size} bytes\n\n")
                return False

        if not is_text_file_readable(full_path):
            out_file.write("SKIPPED (binary or unreadable text file)\n\n")
            return False

        lines = read_text_file(full_path)

        if not lines:
            out_file.write("(EMPTY FILE)\n\n")
            return True

        for i, line in enumerate(lines, 1):
            out_file.write(f"{i:5}: {line.rstrip(chr(10))}\n")

        out_file.write("\n")
        return True

    except Exception as e:
        out_file.write(f"Error reading file: {e}\n\n")
        return False


def main():
    total_seen = 0
    total_written = 0
    missing_files = []

    with open(OUTPUT_FILE, "w", encoding="utf-8") as out:
        out.write("=" * 100 + "\n")
        out.write("MAELSTROM REACT NATIVE ANDROID CONTEXT CODE DUMP\n")
        out.write("=" * 100 + "\n")
        out.write(f"Generated: {datetime.now().isoformat(sep=' ', timespec='seconds')}\n\n")

        out.write(f"PROJECT ROOT:\n- {PROJECT_ROOT}\n\n")

        out.write("PURPOSE:\n")
        out.write("- Debug React Native Android build/runtime crash\n")
        out.write("- Inspect New Architecture configuration\n")
        out.write("- Inspect react-native-track-player native Android code\n")
        out.write("- Inspect previous MusicService.kt backups if present\n\n")

        out.write("TARGET FILES:\n")
        for rel_path in TARGET_FILES:
            out.write(f"- {rel_path}\n")

        out.write("\n" + "=" * 100 + "\n\n")

        for rel_path in TARGET_FILES:
            total_seen += 1

            full_path = os.path.join(PROJECT_ROOT, rel_path)
            display_path = rel_path

            print(f"Dumping: {display_path}")

            exists = os.path.exists(full_path)
            written = write_file_content(out, full_path, display_path)

            if written:
                total_written += 1

            if not exists:
                missing_files.append(rel_path)

        out.write("\n" + "=" * 100 + "\n")
        out.write(f"TOTAL TARGET FILES: {total_seen}\n")
        out.write(f"TOTAL FILES WRITTEN: {total_written}\n")
        out.write(f"TOTAL MISSING FILES: {len(missing_files)}\n")

        if missing_files:
            out.write("\nMISSING FILES:\n")
            for rel_path in missing_files:
                out.write(f"- {rel_path}\n")

        out.write("=" * 100 + "\n")

    print("\n" + "=" * 80)
    print(f"Dump complete: {OUTPUT_FILE}")
    print(f"Target files: {total_seen}")
    print(f"Files written: {total_written}")
    print(f"Missing files: {len(missing_files)}")

    if missing_files:
        print("\nMissing files:")
        for rel_path in missing_files:
            print(f"- {rel_path}")

    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()