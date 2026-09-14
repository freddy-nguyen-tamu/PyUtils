import argparse
import fnmatch
import json
import os
import shutil
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# Default project folders to scan.
# You can override from terminal:
#   python extract_project_files.py "C:\path\to\project"
#   python extract_project_files.py "\\wsl.localhost\Ubuntu\home\projects\nexusbase"
# ---------------------------------------------------------------------------
ROOT_FOLDERS = [
    r"\\wsl.localhost\Ubuntu\home\projects\nexusbase",
]

OUTPUT_DIR = "."
TEXT_DUMP_FILE = "source_code_dump.txt"
MANIFEST_FILE = "manifest.json"


# ---------------------------------------------------------------------------
# Top-level folders that usually contain the actual app, functionality, UI,
# UX, database schema, and static assets.
# ---------------------------------------------------------------------------
IMPORTANT_TOP_LEVEL_DIRS = {
    "src","app","pages","api","server","lib","utils","services","hooks","store","db","prisma",
}


# ---------------------------------------------------------------------------
# Source/config files to include in the text dump and copy.
# ---------------------------------------------------------------------------
TEXT_EXTENSIONS = {
    # Web / Next / React
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".mjs",
    ".cjs",

    # Config / data used by app
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".conf",
    ".env.example",
    ".md",
    ".mdx",

    # Backend / scripts
    ".py",
    ".pyi",
    ".sh",
    ".bash",
    ".zsh",
    ".ps1",
    ".bat",
    ".cmd",

    # Database / schema
    ".sql",
    ".prisma",

    # Other common source files
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".java",
    ".kt",
    ".go",
    ".rs",
    ".cs",
    ".php",
    ".rb",
    ".swift",
}


# ---------------------------------------------------------------------------
# Binary/static UI files to copy, but not write into the text dump.
# These matter for UI/UX because fonts/images/icons can define the look.
# ---------------------------------------------------------------------------
ASSET_EXTENSIONS = set()


IMPORTANT_FILENAMES = {
    # Package managers
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "bun.lockb",

    # TypeScript / Next / Vite / Webpack / Tailwind
    "tsconfig.json",
    "jsconfig.json",
    "next.config.js",
    "next.config.mjs",
    "next.config.ts",
    "vite.config.js",
    "vite.config.ts",
    "webpack.config.js",
    "webpack.config.ts",
    "tailwind.config.js",
    "tailwind.config.ts",
    "postcss.config.js",
    "postcss.config.mjs",
    "eslint.config.js",
    "eslint.config.mjs",
    ".eslintrc",
    ".eslintrc.json",
    ".prettierrc",
    ".prettierrc.json",

    # App/deploy config
    "Dockerfile",
    "docker-compose.yml",
    "vercel.json",
    "netlify.toml",
    "middleware.ts",
    "middleware.js",
    "proxy.ts",
    "proxy.js",

    # Python projects, if present
    "requirements.txt",
    "requirements-dev.txt",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",

    # Docs useful for reconstructing app behavior
    "README.md",
    "README.txt",
}


# ---------------------------------------------------------------------------
# Directories to skip completely.
# ---------------------------------------------------------------------------
SKIP_DIR_NAMES = {
    # Version control
    ".git",
    ".hg",
    ".svn",

    # Editors
    ".idea",
    ".vscode",

    # JS dependencies/build outputs
    "node_modules",
    ".next",
    ".nuxt",
    ".svelte-kit",
    "dist",
    "build",
    "coverage",
    ".turbo",
    ".vercel",

    # Python envs/caches
    ".venv",
    "venv",
    "env",
    "virtualenv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".nox",

    # Generated/noisy outputs
    "logs",
    "log",
    "tmp",
    "temp",
    "output",
    "outputs",
    "result",
    "results",

    # Vendored/generated
    "vendor",
    "third_party",
    "third-party",
    "styles","style","css","scss","sass","assets","images","img","fonts","icons","public",
}


SKIP_DIR_PATTERNS = {
    "*.egg-info",
    "*.dist-info",
    "*__pycache__*",
}


SKIP_FILE_NAMES = {
    ".DS_Store",
    "Thumbs.db",
    "desktop.ini",
    TEXT_DUMP_FILE,
    MANIFEST_FILE,
}


GENERATED_FILE_PATTERNS = {
    "*.map",
    "*.min.js",
    "*.min.css",
    "*.bundle.js",
    "*.bundle.css",
    "*.chunk.js",
    "*.chunk.css",
    "*.generated.ts",
    "*.generated.tsx",
    "*.generated.js",
    "*.generated.jsx",
    "*.gen.ts",
    "*.gen.tsx",
    "*.gen.js",
    "*.d.ts.map",
    "*.cache",
    "*.snap",
    "*.pyc",
    "*.pyo",
    "*.class",
    "*.o",
    "*.obj",
    "*.so",
    "*.dll",
    "*.dylib",
    "*.exe",
    "*.lockb",
}


# Keep this high enough for normal code/assets, low enough to skip huge junk.
MAX_TEXT_FILE_SIZE_BYTES = 2 * 1024 * 1024
MAX_ASSET_FILE_SIZE_BYTES = 10 * 1024 * 1024


def lower_set(values):
    return {v.lower() for v in values}


def matches_any_pattern(name: str, patterns: set[str]) -> bool:
    lower = name.lower()
    return any(fnmatch.fnmatch(lower, pattern.lower()) for pattern in patterns)


def should_skip_dir(dirname: str) -> bool:
    lower = dirname.lower().strip()

    if lower in lower_set(SKIP_DIR_NAMES):
        return True

    if matches_any_pattern(dirname, SKIP_DIR_PATTERNS):
        return True

    return False


def should_skip_path_by_parts(path: Path) -> bool:
    return any(should_skip_dir(part) for part in path.parts)


def is_probably_secret_env_file(path: Path) -> bool:
    """
    Avoid copying real secret files by default.
    Allows examples/templates.
    """
    name = path.name.lower()

    if not name.startswith(".env"):
        return False

    allowed_suffixes = (
        ".example",
        ".sample",
        ".template",
        ".local.example",
        ".development.example",
        ".production.example",
    )

    return not name.endswith(allowed_suffixes)


def is_in_important_top_level(path: Path, root: Path) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return False

    if len(rel.parts) == 0:
        return False

    top = rel.parts[0].lower()
    return top in lower_set(IMPORTANT_TOP_LEVEL_DIRS)


def is_root_important_file(path: Path, root: Path) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return False

    return len(rel.parts) == 1 and path.name.lower() in lower_set(IMPORTANT_FILENAMES)


def is_text_candidate(path: Path) -> bool:
    name = path.name
    lower_name = name.lower()
    suffix = path.suffix.lower()

    if lower_name in lower_set(IMPORTANT_FILENAMES):
        return True

    if suffix in TEXT_EXTENSIONS:
        return True

    if lower_name.endswith(".env.example"):
        return True

    return False


def is_asset_candidate(path: Path) -> bool:
    return path.suffix.lower() in ASSET_EXTENSIONS


def is_style_file(path: Path) -> bool:
    lower = path.name.lower()
    style_extensions = {".css",".scss",".sass",".less"}
    if path.suffix.lower() in style_extensions:
        return True
    style_patterns = (
        ".module.css",".module.scss",".module.sass",".module.less",
    )
    return any(lower.endswith(pattern) for pattern in style_patterns)


def should_include_file(path: Path, root: Path, include_env: bool) -> tuple[bool, str]:
    name = path.name
    lower_name = name.lower()

    if lower_name in lower_set(SKIP_FILE_NAMES):
        return False, "skipped file name"

    if matches_any_pattern(name, GENERATED_FILE_PATTERNS):
        return False, "generated file"

    if should_skip_path_by_parts(path):
        return False, "inside skipped folder"

    if is_probably_secret_env_file(path) and not include_env:
        return False, "secret env file skipped"

    if is_style_file(path):
        return False, "style file"

    if IMPORTANT_TOP_LEVEL_DIRS:
        if not (
            is_in_important_top_level(path, root)
            or is_root_important_file(path, root)
        ):
            return False, "outside functional directories"

    if is_text_candidate(path):
        return True, "text"

    return False, "unsupported extension"


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


def safe_relative_path(path: Path, root: Path) -> Path:
    rel = path.relative_to(root)

    # Prevent odd paths from escaping output folder.
    if any(part in {"..", ""} for part in rel.parts):
        raise ValueError(f"Unsafe relative path: {rel}")

    return rel


def copy_file_to_extract(path: Path, root: Path, extract_root: Path) -> Path:
    rel = safe_relative_path(path, root)
    dest = extract_root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, dest)
    return dest


def write_text_dump_header(out, roots: list[Path]):
    out.write("=" * 100 + "\n")
    out.write("PROJECT FUNCTIONALITY + UI/UX FILE EXTRACT\n")
    out.write("=" * 100 + "\n")
    out.write(f"Generated: {datetime.now().isoformat(sep=' ', timespec='seconds')}\n\n")

    out.write("ROOT FOLDERS:\n")
    for root in roots:
        out.write(f"- {root}\n")

    out.write("\nWHAT THIS INCLUDES:\n")
    out.write("- App source: src/app/pages/components/lib/hooks/types/styles\n")
    out.write("- API/backend routes and helpers\n")
    out.write("- Prisma/database schema\n")
    out.write("- Next/Tailwind/PostCSS/TypeScript/package config\n")
    out.write("- Public UI assets, fonts, icons, and images copied to extract folder\n")

    out.write("\nWHAT THIS SKIPS:\n")
    out.write("- node_modules\n")
    out.write("- .next/build/dist/cache folders\n")
    out.write("- real .env secret files unless --include-env is used\n")
    out.write("- very large/generated files\n\n")


def write_file_to_text_dump(out, full_path: Path, display_path: str) -> bool:
    out.write("\n" + "=" * 100 + "\n")
    out.write(f"FILE: {display_path}\n")
    out.write("=" * 100 + "\n")

    try:
        size = full_path.stat().st_size

        if size > MAX_TEXT_FILE_SIZE_BYTES:
            out.write(f"SKIPPED IN TEXT DUMP: text file too large ({size} bytes)\n")
            return False

        if not is_text_file_readable(full_path):
            out.write("SKIPPED IN TEXT DUMP: binary or unreadable text file\n")
            return False

        lines = read_text_file(full_path)

        if not lines:
            out.write("(EMPTY FILE)\n")
            return True

        for i, line in enumerate(lines, 1):
            out.write(f"{i:5}: {line.rstrip(chr(10))}\n")

        return True

    except Exception as e:
        out.write(f"ERROR READING FILE: {e}\n")
        return False


def iter_project_files(root: Path, include_env: bool):
    for dirpath, dirnames, filenames in os.walk(root):
        current_dir = Path(dirpath)

        dirnames[:] = sorted(
            d for d in dirnames
            if not should_skip_dir(d)
            and not should_skip_path_by_parts(current_dir / d)
        )

        for fname in sorted(filenames):
            full_path = current_dir / fname
            include, reason = should_include_file(full_path, root, include_env)

            if include:
                yield full_path, reason


def dedupe_roots(root_folders: list[str]) -> list[Path]:
    roots = []
    seen = set()

    for root in root_folders:
        path = Path(root).expanduser()
        norm = os.path.normcase(os.path.abspath(str(path)))

        if norm not in seen:
            roots.append(path)
            seen.add(norm)

    return roots


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Extract only the files needed to understand app functionality "
            "and UI/UX from a project folder."
        )
    )

    parser.add_argument(
        "roots",
        nargs="*",
        help="Project root folder(s). If omitted, ROOT_FOLDERS is used.",
    )

    parser.add_argument(
        "-o",
        "--output-dir",
        default="",
        help=f"Folder to copy extracted files into. Default: {OUTPUT_DIR}",
    )

    parser.add_argument(
        "--include-env",
        action="store_true",
        help="Also include real .env files. Not recommended because they may contain secrets.",
    )

    parser.add_argument(
        "--max-text-size",
        type=int,
        default=MAX_TEXT_FILE_SIZE_BYTES,
        help=(
            "Maximum text file size to write into source_code_dump.txt. "
            "Use 0 for no limit."
        ),
    )

    parser.add_argument(
        "--max-asset-size",
        type=int,
        default=MAX_ASSET_FILE_SIZE_BYTES,
        help=(
            "Maximum asset size to copy. "
            "Use 0 for no limit."
        ),
    )

    return parser.parse_args()


def main():
    global MAX_TEXT_FILE_SIZE_BYTES
    global MAX_ASSET_FILE_SIZE_BYTES

    args = parse_args()

    MAX_TEXT_FILE_SIZE_BYTES = (
        float("inf") if args.max_text_size == 0 else args.max_text_size
    )
    MAX_ASSET_FILE_SIZE_BYTES = (
        float("inf") if args.max_asset_size == 0 else args.max_asset_size
    )

    root_inputs = args.roots if args.roots else ROOT_FOLDERS
    roots = dedupe_roots(root_inputs)

    script_dir = Path(__file__).resolve().parent

    if args.output_dir:
        output_dir = Path(args.output_dir).resolve()
    else:
        output_dir = script_dir

    files_dir = output_dir
    text_dump_path = output_dir / TEXT_DUMP_FILE
    manifest_path = output_dir / MANIFEST_FILE

    output_dir.mkdir(parents=True, exist_ok=True)
    files_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "generated": datetime.now().isoformat(sep=" ", timespec="seconds"),
        "output_dir": str(output_dir),
        "roots": [str(root) for root in roots],
        "copied_files": [],
        "skipped_files": [],
        "missing_roots": [],
        "summary": {
            "text_files_copied": 0,
            "asset_files_copied": 0,
            "text_files_written_to_dump": 0,
            "files_skipped": 0,
        },
    }

    with text_dump_path.open("w", encoding="utf-8") as out:
        write_text_dump_header(out, roots)

        for root_index, root in enumerate(roots, 1):
            out.write("\n" + "#" * 100 + "\n")
            out.write(f"ROOT {root_index}: {root}\n")
            out.write("#" * 100 + "\n\n")

            if not root.exists() or not root.is_dir():
                out.write("ROOT FOLDER NOT FOUND OR NOT A DIRECTORY\n")
                manifest["missing_roots"].append(str(root))
                continue

            root_extract_dir = files_dir
            if len(roots) > 1:
                root_extract_dir = files_dir / f"root_{root_index}_{root.name}"

            for full_path, kind in iter_project_files(root, args.include_env):
                try:
                    rel = safe_relative_path(full_path, root)
                    display_path = str(rel).replace(os.sep, "/")
                    size = full_path.stat().st_size

                    if kind == "asset" and size > MAX_ASSET_FILE_SIZE_BYTES:
                        manifest["skipped_files"].append(
                            {
                                "path": display_path,
                                "reason": f"asset too large ({size} bytes)",
                            }
                        )
                        manifest["summary"]["files_skipped"] += 1
                        continue

                    copied_to = copy_file_to_extract(full_path, root, root_extract_dir)

                    entry = {
                        "path": display_path,
                        "kind": kind,
                        "size_bytes": size,
                        "copied_to": str(copied_to),
                    }
                    manifest["copied_files"].append(entry)

                    if kind == "text":
                        manifest["summary"]["text_files_copied"] += 1
                        print(f"Dumping text: {display_path}")

                        if write_file_to_text_dump(out, full_path, display_path):
                            manifest["summary"]["text_files_written_to_dump"] += 1

                    elif kind == "asset":
                        manifest["summary"]["asset_files_copied"] += 1
                        print(f"Copying asset: {display_path}")

                        out.write("\n" + "=" * 100 + "\n")
                        out.write(f"ASSET COPIED: {display_path}\n")
                        out.write("=" * 100 + "\n")
                        out.write(f"Binary/static UI asset copied to: {copied_to}\n")
                        out.write(f"Size: {size} bytes\n")

                except Exception as e:
                    try:
                        display_path = str(full_path.relative_to(root)).replace(os.sep, "/")
                    except Exception:
                        display_path = str(full_path)

                    manifest["skipped_files"].append(
                        {
                            "path": display_path,
                            "reason": f"error: {e}",
                        }
                    )
                    manifest["summary"]["files_skipped"] += 1

        out.write("\n" + "=" * 100 + "\n")
        out.write("SUMMARY\n")
        out.write("=" * 100 + "\n")
        out.write(json.dumps(manifest["summary"], indent=2))
        out.write("\n")

    with manifest_path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print("\n" + "=" * 80)
    print("Extract complete")
    print(f"Copied files folder: {files_dir}")
    print(f"Text dump: {text_dump_path}")
    print(f"Manifest: {manifest_path}")
    print(json.dumps(manifest["summary"], indent=2))
    print("=" * 80)


if __name__ == "__main__":
    main()