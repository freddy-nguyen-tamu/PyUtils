from __future__ import annotations

from collections import Counter
from pathlib import Path
import os
import sys
import time


# =====================================================================
# CONFIGURATION
# =====================================================================

# AutoStreamYara repository to scan.
TARGET_REPO = Path(
    r"\\wsl.localhost\Ubuntu\home\projects\remote-main-autostreamyara"
)

# Output file will be created beside this script.
SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_FILE = SCRIPT_DIR / "remote-main-autostreamyara_source_code_dump.txt"

# Report source-like files that could not be read.
ERROR_FILE = SCRIPT_DIR / "remote-main-autostreamyara_source_dump_errors.txt"

# Print progress only occasionally. Printing every file is much slower.
PROGRESS_EVERY_FILES = 100

# Unknown text files larger than this are skipped.
# Recognized source files are not subject to this limit.
MAX_UNKNOWN_TEXT_FILE_SIZE = 2 * 1024 * 1024  # 2 MB

# Include readable text files whose extensions are not recognized.
# This helps catch unusual source/configuration files.
INCLUDE_UNKNOWN_TEXT_FILES = True


# =====================================================================
# DIRECTORY FILTERING
# =====================================================================

# These directory names are skipped wherever they appear.
#
# Only clear dependency, environment, cache, and build directories belong
# here. Do not add generic names such as "models" or "experiments" here,
# because AutoStreamYara/models contains real Python source.
ALWAYS_EXCLUDED_DIR_NAMES = {
    # Version control
    ".git",
    ".hg",
    ".svn",

    # IDE metadata
    ".idea",
    ".vscode",

    # Python caches and tooling
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".hypothesis",
    ".tox",
    ".nox",
    ".cache",

    # Virtual environments
    ".venv",
    "venv",
    "env",
    "AutoStreamEnv",

    # JavaScript dependencies and caches
    "node_modules",
    ".next",
    ".nuxt",
    ".turbo",

    # Generated build directories
    "dist",
    "build",
    "htmlcov",
}

# These directories are skipped only when they are direct children of
# TARGET_REPO.
#
# This is important:
#
#     TARGET_REPO / "models"
#
# may contain saved .joblib model files and can be skipped, while:
#
#     TARGET_REPO / "AutoStreamYara" / "models"
#
# contains actual Python source and must be scanned.
EXCLUDED_TOP_LEVEL_DIR_NAMES = {
    "data",
    "dataset",
    "datasets",
    "goodware",
    "malware",
    "test_data",

    "models",
    "results",
    "logs",
    "runs",
    "outputs",
    "checkpoints",
    "artifacts",

    "repo_analysis",
}


# =====================================================================
# SOURCE FILE RECOGNITION
# =====================================================================

SOURCE_EXTENSIONS = {
    # Python
    ".py",
    ".pyi",
    ".pyx",
    ".pxd",
    ".pxi",
    ".ipynb",

    # Shell and job scripts
    ".sh",
    ".bash",
    ".zsh",
    ".fish",
    ".csh",
    ".ksh",
    ".ps1",
    ".bat",
    ".cmd",
    ".slurm",
    ".sbatch",

    # YARA source
    ".yar",
    ".yara",

    # C, C++, and Objective-C
    ".c",
    ".h",
    ".cc",
    ".hh",
    ".cpp",
    ".hpp",
    ".cxx",
    ".hxx",
    ".m",
    ".mm",

    # Java and JVM languages
    ".java",
    ".kt",
    ".kts",
    ".scala",
    ".groovy",

    # JavaScript and TypeScript
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",

    # Web source
    ".html",
    ".htm",
    ".css",
    ".scss",
    ".sass",
    ".less",
    ".vue",
    ".svelte",

    # Other programming languages
    ".rs",
    ".go",
    ".rb",
    ".php",
    ".pl",
    ".pm",
    ".lua",
    ".r",
    ".rmd",
    ".jl",
    ".swift",
    ".dart",
    ".ex",
    ".exs",
    ".erl",
    ".hrl",
    ".fs",
    ".fsx",
    ".vb",
    ".cs",

    # Build and project files
    ".cmake",
    ".make",
    ".mk",
    ".gradle",
    ".bazel",
    ".bzl",
    ".csproj",
    ".fsproj",
    ".sln",

    # Configuration
    ".json",
    ".jsonc",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".conf",
    ".config",
    ".xml",
    ".properties",
    ".env",

    # Documentation and plain text
    ".md",
    ".markdown",
    ".rst",
    ".txt",
    ".tex",

    # Query and schema files
    ".sql",
    ".graphql",
    ".gql",
    ".proto",

    # Infrastructure
    ".tf",
    ".tfvars",
    ".hcl",
}


SOURCE_FILE_NAMES = {
    # Build/container files
    "dockerfile",
    "containerfile",
    "makefile",
    "gnumakefile",
    "cmakelists.txt",
    "jenkinsfile",

    # Python project files
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "manifest.in",
    "tox.ini",
    "pytest.ini",
    "mypy.ini",
    "ruff.toml",

    # Dependencies
    "requirements.txt",
    "requirements-dev.txt",
    "requirements-test.txt",
    "requirements-prototype-nn.txt",
    "pipfile",
    "environment.yml",
    "environment.yaml",

    # Repository configuration
    ".gitignore",
    ".gitattributes",
    ".dockerignore",
    ".editorconfig",
    ".env",
    ".env.example",

    # Documentation
    "readme",
    "readme.md",
    "readme.rst",
    "license",
    "license.txt",
    "copying",
    "authors",
    "contributors",
    "changelog",
    "changes",
}


# =====================================================================
# BINARY / GENERATED FILE FILTERING
# =====================================================================

BINARY_EXTENSIONS = {
    # Python bytecode
    ".pyc",
    ".pyo",
    ".pyd",

    # Archives
    ".zip",
    ".tar",
    ".gz",
    ".tgz",
    ".bz2",
    ".xz",
    ".7z",
    ".rar",

    # Images
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".ico",
    ".bmp",
    ".tif",
    ".tiff",

    # Audio and video
    ".mp3",
    ".mp4",
    ".mov",
    ".avi",
    ".mkv",
    ".webm",
    ".wav",
    ".flac",

    # Fonts
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".otf",

    # Executables and libraries
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".class",
    ".jar",
    ".a",
    ".lib",
    ".o",
    ".obj",

    # Serialized models and arrays
    ".cpkl",
    ".pkl",
    ".pickle",
    ".joblib",
    ".pt",
    ".pth",
    ".onnx",
    ".h5",
    ".hdf5",
    ".npy",
    ".npz",
    ".safetensors",

    # Tables and databases
    ".csv",
    ".tsv",
    ".parquet",
    ".feather",
    ".sqlite",
    ".sqlite3",
    ".db",

    # Documents
    ".pdf",
    ".doc",
    ".docx",
    ".ppt",
    ".pptx",
    ".xls",
    ".xlsx",

    # Other binary/generated data
    ".bin",
    ".dat",
    ".map",
}


EXCLUDED_FILE_NAMES = {
    OUTPUT_FILE.name,
    ERROR_FILE.name,

    "aivising_repository_dump.txt",
    "autostreamyara_source_code_dump.txt",
    "autostreamyara_source_dump_errors.txt",

    # Dependency locks
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "pipfile.lock",
    "poetry.lock",

    # Coverage output
    ".coverage",
    "coverage.xml",
}


SOURCE_SHEBANG_MARKERS = {
    "python",
    "bash",
    "sh",
    "zsh",
    "fish",
    "csh",
    "ksh",
    "perl",
    "ruby",
    "node",
    "php",
    "lua",
    "rscript",
    "pwsh",
}


# =====================================================================
# PRECOMPUTED LOWERCASE SETS
# =====================================================================

ALWAYS_EXCLUDED_DIR_NAMES_LOWER = {
    value.casefold()
    for value in ALWAYS_EXCLUDED_DIR_NAMES
}

EXCLUDED_TOP_LEVEL_DIR_NAMES_LOWER = {
    value.casefold()
    for value in EXCLUDED_TOP_LEVEL_DIR_NAMES
}

SOURCE_EXTENSIONS_LOWER = {
    value.casefold()
    for value in SOURCE_EXTENSIONS
}

SOURCE_FILE_NAMES_LOWER = {
    value.casefold()
    for value in SOURCE_FILE_NAMES
}

BINARY_EXTENSIONS_LOWER = {
    value.casefold()
    for value in BINARY_EXTENSIONS
}

EXCLUDED_FILE_NAMES_LOWER = {
    value.casefold()
    for value in EXCLUDED_FILE_NAMES
}

SOURCE_VARIANT_MARKERS = tuple(
    extension + "."
    for extension in sorted(
        SOURCE_EXTENSIONS_LOWER,
        key=len,
        reverse=True,
    )
)


# =====================================================================
# HELPERS
# =====================================================================

def relative_path(file_path: Path) -> Path:
    """Return a repository-relative path when possible."""
    try:
        return file_path.relative_to(TARGET_REPO)
    except ValueError:
        return file_path


def should_skip_directory(
    root_path: Path,
    dirname: str,
) -> bool:
    """
    Skip dependency/cache directories anywhere, and skip known large
    data/output directories only when they are at repository root.
    """
    dirname_lower = dirname.casefold()

    if dirname_lower in ALWAYS_EXCLUDED_DIR_NAMES_LOWER:
        return True

    child_path = root_path / dirname

    try:
        child_relative = child_path.relative_to(TARGET_REPO)
    except ValueError:
        return False

    # Only prune EXCLUDED_TOP_LEVEL_DIR_NAMES when the directory is a
    # direct child of TARGET_REPO.
    if (
        len(child_relative.parts) == 1
        and dirname_lower in EXCLUDED_TOP_LEVEL_DIR_NAMES_LOWER
    ):
        return True

    return False


def is_source_filename(file_path: Path) -> bool:
    """
    Recognize source files from their filename without opening the file.

    This also recognizes backup/variant files such as:

        ExpertYARAPaperModel.py.before_incremental_expertyara
        module.py.backup
        script.sh.old
        config.yaml.previous
    """
    name_lower = file_path.name.casefold()
    suffix_lower = file_path.suffix.casefold()

    if name_lower in EXCLUDED_FILE_NAMES_LOWER:
        return False

    if name_lower in SOURCE_FILE_NAMES_LOWER:
        return True

    if suffix_lower in SOURCE_EXTENSIONS_LOWER:
        return True

    if name_lower.startswith("dockerfile"):
        return True

    if name_lower.startswith("makefile"):
        return True

    if (
        name_lower.startswith("requirements")
        and name_lower.endswith(".txt")
    ):
        return True

    # Detect source files with another suffix appended after the real source
    # extension, such as ".py.before_change".
    if any(
        marker in name_lower
        for marker in SOURCE_VARIANT_MARKERS
    ):
        return True

    # Detect common editor backup naming, such as "module.py~".
    if any(
        name_lower.endswith(extension + "~")
        for extension in SOURCE_EXTENSIONS_LOWER
    ):
        return True

    return False


def has_source_shebang(raw_data: bytes) -> bool:
    """Recognize extensionless executable source scripts."""
    if not raw_data.startswith(b"#!"):
        return False

    first_line = (
        raw_data
        .splitlines()[0]
        .decode("ascii", errors="ignore")
        .casefold()
    )

    return any(
        marker in first_line
        for marker in SOURCE_SHEBANG_MARKERS
    )


def looks_like_text(raw_data: bytes) -> bool:
    """
    Quickly reject obvious binary data.

    NUL bytes and a high ratio of control characters are treated as binary.
    """
    if not raw_data:
        return True

    # UTF byte-order marks may legitimately be associated with NUL bytes.
    if raw_data.startswith(
        (
            b"\xef\xbb\xbf",
            b"\xff\xfe",
            b"\xfe\xff",
            b"\xff\xfe\x00\x00",
            b"\x00\x00\xfe\xff",
        )
    ):
        return True

    if b"\x00" in raw_data:
        return False

    sample = raw_data[:8192]

    control_characters = 0

    for byte in sample:
        if byte in (9, 10, 12, 13):
            continue

        if byte < 32:
            control_characters += 1

    # More than approximately 2% control characters usually means binary.
    return control_characters <= max(4, len(sample) // 50)


def decode_text(
    raw_data: bytes,
    *,
    force_source: bool,
) -> tuple[str | None, str]:
    """
    Decode a file that has already been read.

    Recognized source files use replacement characters as a final fallback
    so an unusual encoding cannot silently remove the entire source file.
    """
    if not raw_data:
        return "", "empty"

    if raw_data.startswith(b"\xef\xbb\xbf"):
        try:
            return raw_data.decode("utf-8-sig"), "utf-8-sig"
        except UnicodeDecodeError:
            pass

    if raw_data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return raw_data.decode("utf-16"), "utf-16"
        except UnicodeDecodeError:
            pass

    if raw_data.startswith(
        (
            b"\xff\xfe\x00\x00",
            b"\x00\x00\xfe\xff",
        )
    ):
        try:
            return raw_data.decode("utf-32"), "utf-32"
        except UnicodeDecodeError:
            pass

    try:
        return raw_data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass

    try:
        return raw_data.decode("cp1252"), "cp1252"
    except UnicodeDecodeError:
        pass

    if force_source:
        return (
            raw_data.decode("utf-8", errors="replace"),
            "utf-8 with replacement",
        )

    return None, "unsupported text encoding"


def add_reason(
    reasons: Counter[str],
    reason: str,
) -> None:
    reasons[reason] += 1


# =====================================================================
# MAIN
# =====================================================================

def main() -> int:
    start_time = time.perf_counter()

    print("=" * 78)
    print("AutoStreamYara Fast Recursive Source/Text Dumper")
    print("=" * 78)
    print(f"Script location : {SCRIPT_DIR}")
    print(f"Target repo     : {TARGET_REPO}")
    print(f"Output file     : {OUTPUT_FILE}")
    print(f"Error report    : {ERROR_FILE}")
    print("=" * 78)

    if not TARGET_REPO.exists():
        print("\nERROR: Repository path does not exist.")
        return 2

    if not TARGET_REPO.is_dir():
        print("\nERROR: Repository path is not a directory.")
        return 2

    processed = 0
    processed_source = 0
    processed_other_text = 0

    scanned_files = 0
    excluded_directories = 0
    skipped_files = 0

    source_read_errors: list[tuple[Path, str]] = []

    skipped_reasons: Counter[str] = Counter()
    extension_counts: Counter[str] = Counter()

    try:
        output = OUTPUT_FILE.open(
            "w",
            encoding="utf-8",
            newline="\n",
            buffering=1024 * 1024,
        )
    except OSError as exc:
        print(f"\nERROR: Could not create output file: {exc}")
        return 2

    with output:
        output.write("AutoStreamYara Fast Recursive Source/Text Dump\n")
        output.write(f"Repository: {TARGET_REPO}\n")
        output.write(
            "Mode: one recursive filesystem pass; no Git scan; "
            "no symlink directory traversal\n"
        )
        output.write(
            "Source handling: known source files have no size limit and "
            "cannot be silently rejected for encoding errors\n\n"
        )

        for root, dirs, files in os.walk(
            TARGET_REPO,
            topdown=True,
            followlinks=False,
        ):
            root_path = Path(root)

            retained_directories: list[str] = []

            for dirname in dirs:
                if should_skip_directory(root_path, dirname):
                    excluded_directories += 1
                else:
                    retained_directories.append(dirname)

            # Modify dirs in place so os.walk does not enter excluded folders.
            dirs[:] = retained_directories

            for filename in files:
                scanned_files += 1

                file_path = root_path / filename
                relative = relative_path(file_path)

                name_lower = file_path.name.casefold()
                suffix_lower = file_path.suffix.casefold()

                if name_lower in EXCLUDED_FILE_NAMES_LOWER:
                    skipped_files += 1
                    add_reason(
                        skipped_reasons,
                        "excluded filename",
                    )
                    continue

                source_by_name = is_source_filename(file_path)

                # Known binary formats are skipped unless the filename was
                # positively recognized as a source variant.
                if (
                    not source_by_name
                    and suffix_lower in BINARY_EXTENSIONS_LOWER
                ):
                    skipped_files += 1
                    add_reason(
                        skipped_reasons,
                        f"binary extension {suffix_lower or '<none>'}",
                    )
                    continue

                try:
                    file_size = file_path.stat().st_size
                except OSError as exc:
                    skipped_files += 1
                    reason = f"cannot stat: {type(exc).__name__}"
                    add_reason(skipped_reasons, reason)

                    if source_by_name:
                        source_read_errors.append(
                            (relative, f"cannot stat file: {exc}")
                        )

                    continue

                if (
                    not source_by_name
                    and file_size > MAX_UNKNOWN_TEXT_FILE_SIZE
                ):
                    skipped_files += 1
                    add_reason(
                        skipped_reasons,
                        "large unknown non-source file",
                    )
                    continue

                try:
                    # Read the file exactly once.
                    raw_data = file_path.read_bytes()
                except OSError as exc:
                    skipped_files += 1
                    reason = f"cannot read: {type(exc).__name__}"
                    add_reason(skipped_reasons, reason)

                    if source_by_name:
                        source_read_errors.append(
                            (relative, f"cannot read file: {exc}")
                        )

                    continue

                # Extensionless scripts may still be source code.
                source_file = (
                    source_by_name
                    or has_source_shebang(raw_data)
                )

                if not source_file:
                    if not INCLUDE_UNKNOWN_TEXT_FILES:
                        skipped_files += 1
                        add_reason(
                            skipped_reasons,
                            "unknown file type",
                        )
                        continue

                    if not looks_like_text(raw_data):
                        skipped_files += 1
                        add_reason(
                            skipped_reasons,
                            "binary content",
                        )
                        continue

                content, encoding = decode_text(
                    raw_data,
                    force_source=source_file,
                )

                if content is None:
                    skipped_files += 1
                    add_reason(
                        skipped_reasons,
                        "unsupported text encoding",
                    )
                    continue

                processed += 1

                if source_file:
                    processed_source += 1
                    file_kind = "SOURCE"
                else:
                    processed_other_text += 1
                    file_kind = "TEXT"

                extension_key = suffix_lower or "<no extension>"
                extension_counts[extension_key] += 1

                output.write("\n")
                output.write("=" * 120 + "\n")
                output.write(f"FILE: {relative}\n")
                output.write(f"KIND: {file_kind}\n")
                output.write(f"ENCODING: {encoding}\n")
                output.write("=" * 120 + "\n\n")
                output.write(content)

                if content and not content.endswith("\n"):
                    output.write("\n")

                output.write("\n")

                if (
                    PROGRESS_EVERY_FILES > 0
                    and processed % PROGRESS_EVERY_FILES == 0
                ):
                    elapsed = time.perf_counter() - start_time

                    print(
                        f"Processed {processed} files "
                        f"({scanned_files} scanned, "
                        f"{elapsed:.1f} seconds)",
                        flush=True,
                    )

    # Write only source read failures. This is not a second repository scan.
    try:
        with ERROR_FILE.open(
            "w",
            encoding="utf-8",
            newline="\n",
        ) as error_output:
            error_output.write(
                "AutoStreamYara Source Dump Read Errors\n"
            )
            error_output.write(
                f"Repository: {TARGET_REPO}\n\n"
            )

            if source_read_errors:
                for relative, error in source_read_errors:
                    error_output.write(
                        f"{relative} :: {error}\n"
                    )
            else:
                error_output.write(
                    "No recognized source files failed to read.\n"
                )

    except OSError as exc:
        print(
            f"WARNING: Could not write error report: {exc}"
        )

    elapsed = time.perf_counter() - start_time

    print("\n")
    print("=" * 78)
    print("COMPLETE")
    print("=" * 78)
    print(f"Files scanned          : {scanned_files}")
    print(f"Source files processed : {processed_source}")
    print(f"Other text processed   : {processed_other_text}")
    print(f"Total files processed  : {processed}")
    print(f"Files skipped          : {skipped_files}")
    print(f"Directories excluded   : {excluded_directories}")
    print(f"Source read errors     : {len(source_read_errors)}")
    print(f"Elapsed time           : {elapsed:.2f} seconds")
    print(f"Output written         : {OUTPUT_FILE}")
    print(f"Error report           : {ERROR_FILE}")
    print("=" * 78)

    if extension_counts:
        print("\nMost common processed extensions:")

        for extension, count in extension_counts.most_common(20):
            print(f"  {count:>7}  {extension}")

    if skipped_reasons:
        print("\nSkipped summary:")

        for reason, count in skipped_reasons.most_common():
            print(f"  {count:>7}  {reason}")

    print("=" * 78)

    if source_read_errors:
        print(
            "\nWARNING: Some recognized source files could not be read."
        )
        print(
            f"Review: {ERROR_FILE}"
        )
        return 1

    print(
        "\nNo recognized source files failed to read."
    )
    return 0


if __name__ == "__main__":
    exit_code = main()

    if sys.stdin.isatty():
        input("Press Enter to exit...")

    raise SystemExit(exit_code)