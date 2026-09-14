import argparse
import fnmatch
import os
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# Default folders to scan.
# You can override this from terminal:
#   python source_code_dump.py "C:\path\to\project"
#   python source_code_dump.py "\\wsl.localhost\Ubuntu\home\projects\AutoStreamYara"
# ---------------------------------------------------------------------------
ROOT_FOLDERS = [
    r"\\wsl.localhost\Ubuntu\home\projects\AutoStreamYara",
]

OUTPUT_FILE = "source_code_dump.txt"


# ---------------------------------------------------------------------------
# Include only files that are likely to be functional source code or required
# project configuration.
# ---------------------------------------------------------------------------
SOURCE_CODE_EXTENSIONS = {
    # Python
    ".py",
    ".pyi",

    # Shell / scripts
    ".sh",
    ".bash",
    ".zsh",
    ".ps1",
    ".bat",
    ".cmd",

    # Web / frontend, if present
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".mjs",
    ".cjs",
    ".html",
    ".css",
    ".scss",

    # Common compiled-language source files
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".cc",
    ".cxx",
    ".java",
    ".kt",
    ".kts",
    ".go",
    ".rs",
    ".cs",
    ".php",
    ".rb",
    ".swift",
    ".scala",
    ".r",
    ".lua",
    ".pl",

    # SQL / schemas
    ".sql",

    # Project config that affects how the code runs
    ".toml",
    ".yaml",
    ".yml",
    ".json",
    ".ini",
    ".cfg",
    ".conf",
    ".env.example",
}


# Files with no extension but still functional.
SOURCE_CODE_FILENAMES = {
    "Dockerfile",
    "dockerfile",
    "Makefile",
    "makefile",
    "requirements.txt",
    "requirements-dev.txt",
    "constraints.txt",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "Pipfile",
    "Pipfile.lock",
    "poetry.lock",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "tsconfig.json",
    "webpack.config.js",
    "vite.config.js",
    "next.config.js",
    "tailwind.config.js",
    "postcss.config.js",
    ".env.example",
}


# ---------------------------------------------------------------------------
# Skip directories that are not source code:
# - git / version-control internals
# - virtual environments
# - dependency folders
# - caches
# - build outputs
# - generated experiment outputs
# - malware/goodware data folders
# ---------------------------------------------------------------------------
SKIP_DIR_NAMES = {
    # Version control
    ".git",
    ".hg",
    ".svn",

    # IDE/editor
    ".idea",
    ".vscode",

    # Python virtual environments
    ".venv",
    "venv",
    "env",
    "virtualenv",
    "AutoStreamEnv",

    # Python caches/builds
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    "htmlcov",
    "site-packages",
    "dist",
    "build",
    "wheels",

    # JS dependencies/builds
    "node_modules",
    ".next",
    ".nuxt",
    ".svelte-kit",
    "coverage",

    # Generated project outputs
    "results",
    "result",
    "outputs",
    "output",
    "logs",
    "log",
    "tmp",
    "temp",

    # Data folders: not source code
    "data",
    "datasets",
    "dataset",
    "raw",
    "samples",
    "malware",
    "goodware",

    # Misc vendored/generated
    "vendor",
    "third_party",
    "third-party",
}


# Directory name patterns to skip.
SKIP_DIR_PATTERNS = {
    "*.egg-info",
    "*.dist-info",
    "*__pycache__*",
}


# ---------------------------------------------------------------------------
# Skip non-source/generated files.
# ---------------------------------------------------------------------------
SKIP_FILE_NAMES = {
    ".DS_Store",
    "Thumbs.db",
    "desktop.ini",

    # This script's own output files
    OUTPUT_FILE,
    "icra_folder_dump.txt",
    "source_code_dump.txt",

    # Usually documentation, not functional code
    "README",
    "README.md",
    "README.txt",
    "LICENSE",
    "LICENSE.txt",
    "CHANGELOG",
    "CHANGELOG.md",

    # Python packaging generated metadata
    "PKG-INFO",
    "SOURCES.txt",
    "dependency_links.txt",
    "requires.txt",
    "top_level.txt",
    "RECORD",
    "WHEEL",
    "INSTALLER",
    "METADATA",

    # Common generated reports
    "metrics.csv",
    "classification_report.txt",
}


SKIP_EXTENSIONS = {
    # Compiled Python
    ".pyc",
    ".pyo",
    ".pyd",

    # Compiled binaries/libs
    ".so",
    ".dll",
    ".dylib",
    ".exe",
    ".bin",
    ".o",
    ".obj",
    ".a",
    ".lib",
    ".class",
    ".jar",

    # Archives/packages
    ".zip",
    ".tar",
    ".gz",
    ".bz2",
    ".xz",
    ".7z",
    ".rar",
    ".whl",

    # Data / generated outputs
    ".csv",
    ".tsv",
    ".xlsx",
    ".xls",
    ".parquet",
    ".pickle",
    ".pkl",
    ".cpkl",
    ".npy",
    ".npz",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".log",
    ".out",

    # Media/docs
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".bmp",
    ".webp",
    ".ico",
    ".svg",
    ".pdf",
    ".doc",
    ".docx",
    ".ppt",
    ".pptx",
    ".mp3",
    ".mp4",
    ".wav",
    ".avi",
    ".mov",
}


# File name patterns that are usually generated.
SKIP_FILE_PATTERNS = {
    "*.min.js",
    "*.min.css",
    "*.bundle.js",
    "*.bundle.css",
    "*.map",
    "*.lockb",
    "*.egg-info",
    "*.dist-info",
    "*.cpython-*.pyc",
}


# Set to None to never skip by size.
# I recommend 2 MB because source code files larger than this are often generated.
MAX_FILE_SIZE_BYTES = 2 * 1024 * 1024


def normalize_case(path_part: str) -> str:
    return path_part.lower().strip()


def matches_any_pattern(name: str, patterns: set[str]) -> bool:
    lower = name.lower()
    return any(fnmatch.fnmatch(lower, pattern.lower()) for pattern in patterns)


def should_skip_dir(dirname: str) -> bool:
    d = normalize_case(dirname)

    if d in {x.lower() for x in SKIP_DIR_NAMES}:
        return True

    if matches_any_pattern(dirname, SKIP_DIR_PATTERNS):
        return True

    return False


def should_skip_path_by_parts(path: Path) -> bool:
    """
    Extra safety: skip a file if any parent folder is noisy/generated.
    This catches nested paths even if os.walk starts inside a subdirectory.
    """
    for part in path.parts:
        if should_skip_dir(part):
            return True
    return False


def should_include_file(path: Path) -> bool:
    name = path.name
    lower_name = name.lower()
    suffix = path.suffix.lower()

    if lower_name in {x.lower() for x in SKIP_FILE_NAMES}:
        return False

    if suffix in SKIP_EXTENSIONS:
        return False

    if matches_any_pattern(name, SKIP_FILE_PATTERNS):
        return False

    if should_skip_path_by_parts(path):
        return False

    if name in SOURCE_CODE_FILENAMES:
        return True

    if lower_name in {x.lower() for x in SOURCE_CODE_FILENAMES}:
        return True

    if suffix in SOURCE_CODE_EXTENSIONS:
        return True

    # Special case: ".env.example" has suffix ".example", so path.suffix alone
    # does not catch it reliably.
    if lower_name.endswith(".env.example"):
        return True

    return False


def is_text_file_readable(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            chunk = f.read(8192)

        if b"\x00" in chunk:
            return False

        return True
    except Exception:
        return False


def read_text_file(path: Path) -> list[str]:
    try:
        with path.open("r", encoding="utf-8") as f:
            return f.readlines()
    except UnicodeDecodeError:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            return f.readlines()


def write_file_content(out_file, full_path: Path, display_path: str) -> bool:
    out_file.write("\n" + "=" * 100 + "\n")
    out_file.write(f"FILE: {display_path}\n")
    out_file.write("=" * 100 + "\n")

    if not full_path.exists():
        out_file.write("FILE NOT FOUND\n\n")
        return False

    try:
        if MAX_FILE_SIZE_BYTES is not None:
            size = full_path.stat().st_size
            if size > MAX_FILE_SIZE_BYTES:
                out_file.write(f"SKIPPED: file too large ({size} bytes)\n\n")
                return False

        if not is_text_file_readable(full_path):
            out_file.write("SKIPPED: binary or unreadable text file\n\n")
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
        out_file.write(f"ERROR READING FILE: {e}\n\n")
        return False


def iter_source_files(root_folder: Path):
    for dirpath, dirnames, filenames in os.walk(root_folder):
        current_dir = Path(dirpath)

        # Remove skipped directories in-place so os.walk will not descend into them.
        dirnames[:] = sorted(
            d for d in dirnames
            if not should_skip_dir(d)
            and not should_skip_path_by_parts(current_dir / d)
        )

        for fname in sorted(filenames):
            full_path = current_dir / fname

            if should_include_file(full_path):
                rel_path = full_path.relative_to(root_folder)
                display_path = str(rel_path).replace(os.sep, "/")
                yield full_path, display_path


def dedupe_roots(root_folders: list[str]) -> list[Path]:
    roots: list[Path] = []
    seen: set[str] = set()

    for root in root_folders:
        path = Path(root).expanduser()
        norm = os.path.normcase(os.path.abspath(str(path)))

        if norm not in seen:
            roots.append(path)
            seen.add(norm)

    return roots


def parse_args():
    parser = argparse.ArgumentParser(
        description="Dump only functional source code/config files from one or more project folders."
    )

    parser.add_argument(
        "roots",
        nargs="*",
        help="Root folder(s) to scan. If omitted, ROOT_FOLDERS from the script are used.",
    )

    parser.add_argument(
        "-o",
        "--output",
        default=OUTPUT_FILE,
        help=f"Output text file. Default: {OUTPUT_FILE}",
    )

    parser.add_argument(
        "--max-size",
        type=int,
        default=MAX_FILE_SIZE_BYTES,
        help=(
            "Maximum file size in bytes. Use 0 for no limit. "
            f"Default: {MAX_FILE_SIZE_BYTES}"
        ),
    )

    return parser.parse_args()


def main():
    global MAX_FILE_SIZE_BYTES

    args = parse_args()
    MAX_FILE_SIZE_BYTES = None if args.max_size == 0 else args.max_size

    root_inputs = args.roots if args.roots else ROOT_FOLDERS
    roots = dedupe_roots(root_inputs)

    output_path = Path(args.output)

    total_matched = 0
    total_written = 0
    skipped_missing_roots = 0

    with output_path.open("w", encoding="utf-8") as out:
        out.write("=" * 100 + "\n")
        out.write("SOURCE CODE ONLY DUMP\n")
        out.write("=" * 100 + "\n")
        out.write(f"Generated: {datetime.now().isoformat(sep=' ', timespec='seconds')}\n\n")

        out.write("ROOT FOLDERS:\n")
        for root in roots:
            out.write(f"- {root}\n")

        out.write("\nIncluded source extensions:\n")
        for ext in sorted(SOURCE_CODE_EXTENSIONS):
            out.write(f"- {ext}\n")

        out.write("\nIncluded special filenames:\n")
        for name in sorted(SOURCE_CODE_FILENAMES):
            out.write(f"- {name}\n")

        out.write("\nSkipped directory names:\n")
        for name in sorted(SKIP_DIR_NAMES, key=str.lower):
            out.write(f"- {name}\n")

        out.write("\n" + "=" * 100 + "\n\n")

        for root in roots:
            out.write("\n" + "#" * 100 + "\n")
            out.write(f"ROOT: {root}\n")
            out.write("#" * 100 + "\n\n")

            if not root.exists():
                out.write("ROOT FOLDER NOT FOUND\n\n")
                skipped_missing_roots += 1
                continue

            if not root.is_dir():
                out.write("ROOT IS NOT A DIRECTORY\n\n")
                skipped_missing_roots += 1
                continue

            for full_path, display_path in iter_source_files(root):
                total_matched += 1
                print(f"Dumping: {display_path}")

                if write_file_content(out, full_path, display_path):
                    total_written += 1

        out.write("\n" + "=" * 100 + "\n")
        out.write(f"TOTAL MATCHED SOURCE FILES: {total_matched}\n")
        out.write(f"TOTAL FILES WRITTEN: {total_written}\n")

        if skipped_missing_roots:
            out.write(f"ROOTS MISSING/INVALID: {skipped_missing_roots}\n")

        out.write("=" * 100 + "\n")

    print("\n" + "=" * 80)
    print(f"Dump complete: {output_path}")
    print(f"Matched source files: {total_matched}")
    print(f"Files written: {total_written}")

    if skipped_missing_roots:
        print(f"Missing/invalid root folders: {skipped_missing_roots}")

    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()
