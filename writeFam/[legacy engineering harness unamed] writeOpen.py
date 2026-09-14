from pathlib import Path
import os

# =====================================================================
# CONFIGURATION
# =====================================================================

# WaveStack repository to scan
TARGET_REPO = Path(r"\\wsl.localhost\Ubuntu\home\projects\INSERT_NAME_engineering_harness")

# Output file will be created beside this script
SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_FILE = SCRIPT_DIR / "openphoto_repository_dump.txt"

# Directories to completely skip
EXCLUDED_DIRS = {
    "node_modules",
    ".next",
    ".git",
    "dist",
    "build",
    "out",
    "coverage",
    ".turbo",
    ".cache",
    "__pycache__",
    ".venv",
    "venv",
    "target",
    ".idea",
    ".vscode",
}

# Generated files to skip
EXCLUDED_FILES = {
    "wavestack_repository_dump.txt",
    "aivising_repository_dump.txt",
    "README.md",
    "readme.md",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
}

# Binary/generated extensions to skip
EXCLUDED_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
    ".ico", ".pdf", ".zip", ".tar", ".gz", ".7z",
    ".mp3", ".mp4", ".mov", ".avi", ".mkv",
    ".woff", ".woff2", ".ttf", ".eot",
    ".exe", ".dll", ".so", ".dylib",
    ".class", ".jar",
    ".pyc",
    ".map",
    ".lock",
    ".log",
}

# =====================================================================
# HELPERS
# =====================================================================

def should_skip(path: Path) -> bool:
    if path.name in EXCLUDED_FILES:
        return True

    if path.suffix.lower() in EXCLUDED_EXTENSIONS:
        return True

    return False


def is_text_file(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            chunk = f.read(4096)

        if b"\x00" in chunk:
            return False

        chunk.decode("utf-8")
        return True

    except Exception:
        return False


# =====================================================================
# MAIN
# =====================================================================

def main():
    print("=" * 70)
    print("Repository Dumper Starting")
    print("=" * 70)

    print(f"Script location : {SCRIPT_DIR}")
    print(f"Target repo     : {TARGET_REPO}")
    print(f"Output file     : {OUTPUT_FILE}")

    exists = TARGET_REPO.exists()
    print(f"Repo exists     : {exists}")

    if not exists:
        print("\nERROR: Repository path not found.")
        input("Press Enter to exit...")
        return

    print("\nBeginning scan...\n")

    processed = 0
    skipped = 0

    with open(OUTPUT_FILE, "w", encoding="utf-8") as output:

        output.write("Repository Dump\n")
        output.write(f"Repository: {TARGET_REPO}\n\n")

        for root, dirs, files in os.walk(TARGET_REPO):

            # IMPORTANT:
            # Prevent descending into huge generated folders
            dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]

            root_path = Path(root)

            print(f"Scanning directory: {root_path}")

            for filename in files:
                file_path = root_path / filename

                try:
                    if should_skip(file_path):
                        skipped += 1
                        continue

                    if not is_text_file(file_path):
                        skipped += 1
                        continue

                    relative_path = file_path.relative_to(TARGET_REPO)

                    processed += 1

                    print(
                        f"[{processed}] Processing: {relative_path}",
                        flush=True
                    )

                    try:
                        content = file_path.read_text(
                            encoding="utf-8",
                            errors="replace"
                        )

                        output.write("\n")
                        output.write("=" * 120 + "\n")
                        output.write(f"FILE: {relative_path}\n")
                        output.write("=" * 120 + "\n\n")
                        output.write(content)
                        output.write("\n\n")

                    except Exception as e:
                        output.write("\n")
                        output.write("=" * 120 + "\n")
                        output.write(f"FILE: {relative_path}\n")
                        output.write("=" * 120 + "\n")
                        output.write(f"[ERROR READING FILE: {e}]\n\n")

                except Exception as e:
                    print(
                        f"ERROR processing {file_path}: {e}",
                        flush=True
                    )

    print("\n")
    print("=" * 70)
    print("COMPLETE")
    print("=" * 70)
    print(f"Files processed : {processed}")
    print(f"Files skipped   : {skipped}")
    print(f"Output written  : {OUTPUT_FILE}")
    print("=" * 70)

    input("Press Enter to exit...")


if __name__ == "__main__":
    main()
