from __future__ import annotations

from collections import Counter
from pathlib import Path
import os
import re
import sys
import time


# =====================================================================
# CONFIGURATION
# =====================================================================

# Works whether this script is run from Windows Python against WSL or from
# Python inside WSL itself.
TARGET_CANDIDATES = (
    Path(r"\\wsl.localhost\Ubuntu\home\projects\Cortexo"),
    Path("/home/projects/Cortexo"),
)
TARGET_REPO = next(
    (candidate for candidate in TARGET_CANDIDATES if candidate.exists()),
    TARGET_CANDIDATES[0],
)

SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_FILE = SCRIPT_DIR / "cortexo_source_code_dump.txt"
ERROR_FILE = SCRIPT_DIR / "cortexo_source_dump_errors.txt"

PROGRESS_EVERY_FILES = 100

# This dumper is intentionally strict. It does NOT include arbitrary readable
# text files. A file must be recognized as authored source code or functional
# project/build/runtime configuration.


# =====================================================================
# DIRECTORY FILTERING
# =====================================================================

# Dependency, cache, generated, build, environment, output, model-artifact,
# and dataset directories. These are excluded wherever they appear.
ALWAYS_EXCLUDED_DIR_NAMES = {
    # Version control / IDE
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".vscode",

    # Python caches / packaging / virtual environments
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".hypothesis",
    ".tox",
    ".nox",
    ".cache",
    ".venv",
    "venv",
    "env",
    ".eggs",

    # JS / frontend dependencies and caches
    "node_modules",
    ".next",
    ".nuxt",
    ".svelte-kit",
    ".angular",
    ".turbo",
    ".vite",
    ".parcel-cache",

    # Java / Gradle / Maven generated output
    ".gradle",
    "target",

    # Generic generated/build output
    "dist",
    "build",
    "out",
    "coverage",
    "htmlcov",
    "site",
    "generated",
    "generated-sources",
    "generated-test-sources",
    "tmp",
    "temp",

    # ML/research runtime artifacts, not source
    "artifacts",
    "checkpoints",
    "outputs",
    "output",
    "runs",
    "results",
    "logs",
    "mlruns",
    ".wandb",
    "lightning_logs",

    # Dataset material, not project source
    "datasets",

    # Notebook-generated state
    ".ipynb_checkpoints",
}

# Directory suffixes/patterns that are generated even though each individual
# package name is different.
EXCLUDED_DIR_SUFFIXES = (
    ".egg-info",
    ".dist-info",
)


# =====================================================================
# SOURCE / FUNCTIONAL CONFIG RECOGNITION
# =====================================================================

# Authored programming-language source.
CODE_EXTENSIONS = {
    # Python / Cython
    ".py", ".pyi", ".pyx", ".pxd", ".pxi",

    # Shell / scripting
    ".sh", ".bash", ".zsh", ".fish", ".csh", ".ksh",
    ".ps1", ".bat", ".cmd",

    # C / C++ / Objective-C
    ".c", ".h", ".cc", ".hh", ".cpp", ".hpp", ".cxx", ".hxx",
    ".m", ".mm",

    # JVM
    ".java", ".kt", ".kts", ".scala", ".groovy",

    # JS / TS / web source
    ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx",
    ".vue", ".svelte", ".html", ".htm", ".css", ".scss", ".sass", ".less",

    # Other languages
    ".rs", ".go", ".rb", ".php", ".pl", ".pm", ".lua",
    ".r", ".rmd", ".jl", ".swift", ".dart", ".ex", ".exs",
    ".erl", ".hrl", ".fs", ".fsx", ".vb", ".cs",

    # Query/schema/source definitions
    ".sql", ".graphql", ".gql", ".proto", ".yar", ".yara",

    # Build/source DSLs
    ".cmake", ".make", ".mk", ".gradle", ".bazel", ".bzl",
    ".tf", ".hcl",
}

# Functional configuration used by the application/build/training/evaluation.
# These are still subject to generated-file and secret-file checks below.
FUNCTIONAL_CONFIG_EXTENSIONS = {
    ".json", ".jsonc", ".yaml", ".yml", ".toml",
    ".ini", ".cfg", ".conf", ".config", ".xml", ".properties",
    ".tfvars",
}

# Exact functional files that may not be captured well by extension alone.
FUNCTIONAL_FILE_NAMES = {
    # Containers/builds
    "dockerfile",
    "containerfile",
    "makefile",
    "gnumakefile",
    "cmakelists.txt",
    "jenkinsfile",

    # Python packaging/testing/build
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "manifest.in",
    "tox.ini",
    "pytest.ini",
    "mypy.ini",
    "ruff.toml",

    # JS/TS package and compiler config
    "package.json",
    "tsconfig.json",
    "jsconfig.json",

    # Common runtime/deployment config
    "render.yaml",
    "render.yml",
}

SOURCE_SHEBANG_MARKERS = {
    "python", "bash", "sh", "zsh", "fish", "csh", "ksh",
    "perl", "ruby", "node", "php", "lua", "rscript", "pwsh",
}


# =====================================================================
# GENERATED / NON-SOURCE FILE FILTERING
# =====================================================================

# Binary/output formats are never dumped.
BINARY_EXTENSIONS = {
    # Python / JVM / native compiled output
    ".pyc", ".pyo", ".pyd", ".class", ".jar",
    ".exe", ".dll", ".so", ".dylib", ".a", ".lib", ".o", ".obj",

    # Archives
    ".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar",

    # Images/media/fonts
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".bmp",
    ".tif", ".tiff", ".mp3", ".mp4", ".mov", ".avi", ".mkv", ".webm",
    ".wav", ".flac", ".woff", ".woff2", ".ttf", ".eot", ".otf",

    # ML weights / serialized data
    ".cpkl", ".pkl", ".pickle", ".joblib", ".pt", ".pth", ".onnx",
    ".h5", ".hdf5", ".npy", ".npz", ".safetensors",

    # Tables / databases / documents
    ".csv", ".tsv", ".parquet", ".feather", ".sqlite", ".sqlite3", ".db",
    ".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx",

    # Other generated/binary data
    ".bin", ".dat", ".map",
}

# Files that are machine-generated dependency/build state rather than authored
# source. Lockfiles are intentionally excluded per the user's source-only rule.
EXCLUDED_FILE_NAMES = {
    OUTPUT_FILE.name,
    ERROR_FILE.name,

    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "bun.lock",
    "bun.lockb",
    "pipfile.lock",
    "poetry.lock",
    "uv.lock",

    ".coverage",
    "coverage.xml",
    "lcov.info",

    # Vite creates this declaration file automatically.
    "vite-env.d.ts",
}

# Typical editor backups, rejects, temporary copies and generated bundles.
EXCLUDED_NAME_SUFFIXES = (
    "~",
    ".bak",
    ".backup",
    ".old",
    ".orig",
    ".rej",
    ".tmp",
    ".temp",
    ".swp",
    ".swo",
    ".tsbuildinfo",
)

GENERATED_NAME_MARKERS = (
    ".min.js",
    ".min.css",
    ".bundle.js",
    ".bundle.css",
    ".generated.",
    "_generated.",
    ".g.dart",
    ".freezed.dart",
)

# Common generated-code filename conventions.
GENERATED_FILE_REGEXES = (
    re.compile(r".*_pb2(?:_grpc)?\.py$", re.IGNORECASE),
    re.compile(r".*\.designer\.cs$", re.IGNORECASE),
    re.compile(r".*\.g\.cs$", re.IGNORECASE),
    re.compile(r".*\.generated\.cs$", re.IGNORECASE),
)

# Header markers checked near the beginning of source-like text files.
GENERATED_HEADER_PATTERNS = (
    re.compile(r"\bdo not edit\b", re.IGNORECASE),
    re.compile(r"\bauto[- ]generated\b", re.IGNORECASE),
    re.compile(r"\bautomatically generated\b", re.IGNORECASE),
    re.compile(r"\bcode generated .* do not edit\b", re.IGNORECASE),
    re.compile(r"@generated\b", re.IGNORECASE),
)


# =====================================================================
# SECRET / CREDENTIAL FILTERING
# =====================================================================

# Credential/key material is never included, even if textual.
SECRET_MATERIAL_EXTENSIONS = {
    ".pem", ".key", ".p12", ".pfx", ".jks", ".keystore",
    ".der", ".cer", ".crt", ".csr",
}

# Exact credential-bearing filenames. We deliberately do NOT blacklist a
# normal source module such as ml/.../common/secrets.py; that is application
# source code, not a credential file.
SECRET_FILE_NAMES = {
    ".env",
    ".npmrc",
    ".pypirc",
    ".netrc",
    "credentials.json",
    "credential.json",
    "secrets.json",
    "secret.json",
    "tokens.json",
    "token.json",
    "service-account.json",
    "service_account.json",
    "application-default-credentials.json",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
}

# Config/data file stems strongly associated with credential material. This is
# applied to config-like files, not ordinary .py/.java/.ts source modules.
SECRET_CONFIG_STEM_MARKERS = (
    "credential",
    "credentials",
    "client_secret",
    "client-secret",
    "service_account",
    "service-account",
    "private_key",
    "private-key",
    "passwords",
    "passwd",
    "tokens",
)

# Private key bodies mean the whole file is skipped, because merely redacting
# one assignment would not reliably remove the key material.
PRIVATE_KEY_MARKER = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
    re.IGNORECASE,
)

# High-confidence token/key shapes. These are redacted inside otherwise valid
# source/config files instead of causing the entire functioning file to vanish.
HIGH_CONFIDENCE_SECRET_PATTERNS = (
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                         # AWS
    re.compile(r"\bASIA[0-9A-Z]{16}\b"),                         # AWS STS
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),               # GitHub classic-ish
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),             # GitHub fine-grained
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),                    # common API-key shape
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),             # Slack
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),                   # Google API key
    re.compile(r"\bsk_live_[0-9A-Za-z]{16,}\b"),                 # Stripe secret key
)

# Redact quoted/bare literal values assigned to obviously sensitive variables.
# Environment references such as ${OPENAI_API_KEY} are intentionally not
# treated as secret values.
SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?ix)"
    r"(?P<prefix>\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|"
    r"client[_-]?secret|private[_-]?key|password|passwd|secret|token)\b"
    r"\s*(?:[:=]|=>)\s*)"
    r"(?P<quote>[\"']?)"
    r"(?P<value>[^\s\"'#,;}{\]\[] {8,})"
    r"(?P=quote)"
)

# URL credentials such as postgres://user:password@host.
URI_CREDENTIAL_RE = re.compile(
    r"(?i)(\b[a-z][a-z0-9+.-]*://[^\s:/@]+:)([^\s/@]+)(@)"
)

PLACEHOLDER_PREFIXES = (
    "${", "$", "{{", "<", "your_", "your-", "example", "placeholder",
    "changeme", "change_me", "dummy", "test_", "test-",
)


# =====================================================================
# PRECOMPUTED LOWERCASE SETS
# =====================================================================

ALWAYS_EXCLUDED_DIR_NAMES_LOWER = {value.casefold() for value in ALWAYS_EXCLUDED_DIR_NAMES}
CODE_EXTENSIONS_LOWER = {value.casefold() for value in CODE_EXTENSIONS}
FUNCTIONAL_CONFIG_EXTENSIONS_LOWER = {value.casefold() for value in FUNCTIONAL_CONFIG_EXTENSIONS}
FUNCTIONAL_FILE_NAMES_LOWER = {value.casefold() for value in FUNCTIONAL_FILE_NAMES}
BINARY_EXTENSIONS_LOWER = {value.casefold() for value in BINARY_EXTENSIONS}
EXCLUDED_FILE_NAMES_LOWER = {value.casefold() for value in EXCLUDED_FILE_NAMES}
SECRET_MATERIAL_EXTENSIONS_LOWER = {value.casefold() for value in SECRET_MATERIAL_EXTENSIONS}
SECRET_FILE_NAMES_LOWER = {value.casefold() for value in SECRET_FILE_NAMES}


# =====================================================================
# HELPERS
# =====================================================================

def relative_path(file_path: Path) -> Path:
    try:
        return file_path.relative_to(TARGET_REPO)
    except ValueError:
        return file_path


def should_skip_directory(dirname: str) -> tuple[bool, str]:
    lower = dirname.casefold()

    if lower in ALWAYS_EXCLUDED_DIR_NAMES_LOWER:
        return True, "dependency/cache/build/generated/data directory"

    if lower.endswith(EXCLUDED_DIR_SUFFIXES):
        return True, "generated package metadata directory"

    return False, ""


def is_secret_filename(file_path: Path) -> bool:
    name = file_path.name.casefold()
    suffix = file_path.suffix.casefold()
    stem = file_path.stem.casefold()

    if suffix in SECRET_MATERIAL_EXTENSIONS_LOWER:
        return True

    if name in SECRET_FILE_NAMES_LOWER:
        return True

    # Every .env variant is treated as sensitive, including .env.local,
    # .env.production, and even examples/templates per the strict request.
    if name == ".env" or name.startswith(".env."):
        return True

    # Credential-named JSON/YAML/TOML/etc files are excluded. Do not apply this
    # rule to source modules like secrets.py.
    if suffix in FUNCTIONAL_CONFIG_EXTENSIONS_LOWER:
        if any(marker in stem for marker in SECRET_CONFIG_STEM_MARKERS):
            return True

    return False


def is_generated_filename(file_path: Path) -> bool:
    name = file_path.name.casefold()

    if name in EXCLUDED_FILE_NAMES_LOWER:
        return True

    if any(name.endswith(suffix) for suffix in EXCLUDED_NAME_SUFFIXES):
        return True

    if any(marker in name for marker in GENERATED_NAME_MARKERS):
        return True

    return any(pattern.fullmatch(file_path.name) for pattern in GENERATED_FILE_REGEXES)


def is_recognized_source(file_path: Path) -> bool:
    name = file_path.name.casefold()
    suffix = file_path.suffix.casefold()

    if suffix in CODE_EXTENSIONS_LOWER:
        return True

    if suffix in FUNCTIONAL_CONFIG_EXTENSIONS_LOWER:
        return True

    if name in FUNCTIONAL_FILE_NAMES_LOWER:
        return True

    if name.startswith("dockerfile"):
        return True

    if name.startswith("makefile"):
        return True

    if name.startswith("requirements") and name.endswith(".txt"):
        return True

    if name.startswith("docker-compose") and name.endswith((".yml", ".yaml")):
        return True

    if name.startswith("tsconfig") and name.endswith(".json"):
        return True

    if name.startswith("vite.config."):
        return True

    return False


def has_source_shebang(raw_data: bytes) -> bool:
    if not raw_data.startswith(b"#!"):
        return False

    first_line = raw_data.splitlines()[0].decode("ascii", errors="ignore").casefold()
    return any(marker in first_line for marker in SOURCE_SHEBANG_MARKERS)


def looks_generated_by_header(text: str) -> bool:
    # Generated notices are normally at the top. Restrict the scan so a source
    # file discussing generated code does not get excluded accidentally.
    header = text[:8192]
    return any(pattern.search(header) for pattern in GENERATED_HEADER_PATTERNS)


def decode_source(raw_data: bytes) -> tuple[str, str]:
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

    if raw_data.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
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

    # Source code should not silently disappear due to an odd encoding.
    return raw_data.decode("utf-8", errors="replace"), "utf-8 with replacement"


def _redact_sensitive_assignment(match: re.Match[str]) -> str:
    value = match.group("value")
    lower = value.casefold()

    # Environment/template references are safe and important to understanding
    # how the code works, so preserve them.
    if lower.startswith(PLACEHOLDER_PREFIXES):
        return match.group(0)

    quote = match.group("quote") or ""
    return f'{match.group("prefix")}{quote}<REDACTED_SECRET>{quote}'


def redact_embedded_secrets(text: str) -> tuple[str, int]:
    redactions = 0

    def token_replacer(match: re.Match[str]) -> str:
        nonlocal redactions
        redactions += 1
        return "<REDACTED_SECRET>"

    for pattern in HIGH_CONFIDENCE_SECRET_PATTERNS:
        text = pattern.sub(token_replacer, text)

    before = text
    text = SENSITIVE_ASSIGNMENT_RE.sub(_redact_sensitive_assignment, text)
    if text != before:
        # Count assignment redactions approximately by inserted markers. Exact
        # count is not important; it is only printed as a safety summary.
        redactions += max(0, text.count("<REDACTED_SECRET>") - before.count("<REDACTED_SECRET>"))

    def uri_replacer(match: re.Match[str]) -> str:
        nonlocal redactions
        redactions += 1
        return f"{match.group(1)}<REDACTED_SECRET>{match.group(3)}"

    text = URI_CREDENTIAL_RE.sub(uri_replacer, text)
    return text, redactions


def add_reason(reasons: Counter[str], reason: str) -> None:
    reasons[reason] += 1


# =====================================================================
# MAIN
# =====================================================================

def main() -> int:
    start_time = time.perf_counter()

    print("=" * 78)
    print("Cortexo Strict Authored Source Dumper")
    print("=" * 78)
    print(f"Script location : {SCRIPT_DIR}")
    print(f"Target repo     : {TARGET_REPO}")
    print(f"Output file     : {OUTPUT_FILE}")
    print(f"Error report    : {ERROR_FILE}")
    print("=" * 78)

    if not TARGET_REPO.exists():
        print("\nERROR: Cortexo repository path does not exist.")
        print("Checked:")
        for candidate in TARGET_CANDIDATES:
            print(f"  - {candidate}")
        return 2

    if not TARGET_REPO.is_dir():
        print("\nERROR: Repository path is not a directory.")
        return 2

    processed = 0
    scanned_files = 0
    skipped_files = 0
    excluded_directories = 0
    total_redactions = 0

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
        output.write("Cortexo Strict Authored Source Code Dump\n")
        output.write(f"Repository: {TARGET_REPO}\n")
        output.write(
            "Mode: authored source + functional project/build/runtime config only; "
            "dependencies, generated output, datasets/artifacts, backups, and credential files excluded\n"
        )
        output.write(
            "Secret safety: obvious credential files/private-key files are omitted; "
            "high-confidence embedded secret literals are redacted\n\n"
        )

        for root, dirs, files in os.walk(
            TARGET_REPO,
            topdown=True,
            followlinks=False,
        ):
            root_path = Path(root)

            retained_dirs: list[str] = []
            for dirname in dirs:
                skip, reason = should_skip_directory(dirname)
                if skip:
                    excluded_directories += 1
                    add_reason(skipped_reasons, reason)
                else:
                    retained_dirs.append(dirname)
            dirs[:] = retained_dirs

            for filename in files:
                scanned_files += 1
                file_path = root_path / filename
                relative = relative_path(file_path)
                suffix_lower = file_path.suffix.casefold()

                if file_path.is_symlink():
                    skipped_files += 1
                    add_reason(skipped_reasons, "symlink file")
                    continue

                if is_secret_filename(file_path):
                    skipped_files += 1
                    add_reason(skipped_reasons, "secret/credential filename")
                    continue

                if is_generated_filename(file_path):
                    skipped_files += 1
                    add_reason(skipped_reasons, "generated/backup/lock filename")
                    continue

                if suffix_lower in BINARY_EXTENSIONS_LOWER:
                    skipped_files += 1
                    add_reason(skipped_reasons, f"binary/non-source extension {suffix_lower or '<none>'}")
                    continue

                source_by_name = is_recognized_source(file_path)

                # For extensionless scripts we have to inspect only the first
                # bytes before deciding whether they are source.
                if not source_by_name and suffix_lower:
                    skipped_files += 1
                    add_reason(skipped_reasons, "unrecognized non-source file")
                    continue

                try:
                    raw_data = file_path.read_bytes()
                except OSError as exc:
                    skipped_files += 1
                    add_reason(skipped_reasons, f"cannot read: {type(exc).__name__}")
                    if source_by_name:
                        source_read_errors.append((relative, f"cannot read file: {exc}"))
                    continue

                source_file = source_by_name or has_source_shebang(raw_data)
                if not source_file:
                    skipped_files += 1
                    add_reason(skipped_reasons, "extensionless non-source file")
                    continue

                content, encoding = decode_source(raw_data)

                # Never emit private-key material, even if somebody accidentally
                # pasted it into an otherwise source-like file.
                if PRIVATE_KEY_MARKER.search(content):
                    skipped_files += 1
                    add_reason(skipped_reasons, "contains private key material")
                    continue

                if looks_generated_by_header(content):
                    skipped_files += 1
                    add_reason(skipped_reasons, "generated-code header")
                    continue

                content, redactions = redact_embedded_secrets(content)
                total_redactions += redactions

                processed += 1
                extension_counts[suffix_lower or "<no extension>"] += 1

                output.write("\n")
                output.write("=" * 120 + "\n")
                output.write(f"FILE: {relative}\n")
                output.write("KIND: AUTHORED_SOURCE\n")
                output.write(f"ENCODING: {encoding}\n")
                if redactions:
                    output.write(f"SECRET_REDACTIONS: {redactions}\n")
                output.write("=" * 120 + "\n\n")
                output.write(content)

                if content and not content.endswith("\n"):
                    output.write("\n")
                output.write("\n")

                if PROGRESS_EVERY_FILES > 0 and processed % PROGRESS_EVERY_FILES == 0:
                    elapsed = time.perf_counter() - start_time
                    print(
                        f"Processed {processed} authored source files "
                        f"({scanned_files} scanned, {elapsed:.1f} seconds)",
                        flush=True,
                    )

    try:
        with ERROR_FILE.open("w", encoding="utf-8", newline="\n") as error_output:
            error_output.write("Cortexo Authored Source Dump Read Errors\n")
            error_output.write(f"Repository: {TARGET_REPO}\n\n")
            if source_read_errors:
                for relative, error in source_read_errors:
                    error_output.write(f"{relative} :: {error}\n")
            else:
                error_output.write("No recognized authored source files failed to read.\n")
    except OSError as exc:
        print(f"WARNING: Could not write error report: {exc}")

    elapsed = time.perf_counter() - start_time

    print("\n" + "=" * 78)
    print("COMPLETE")
    print("=" * 78)
    print(f"Files scanned          : {scanned_files}")
    print(f"Authored source dumped : {processed}")
    print(f"Files skipped          : {skipped_files}")
    print(f"Directories excluded   : {excluded_directories}")
    print(f"Secret values redacted : {total_redactions}")
    print(f"Source read errors     : {len(source_read_errors)}")
    print(f"Elapsed time           : {elapsed:.2f} seconds")
    print(f"Output written         : {OUTPUT_FILE}")
    print(f"Error report           : {ERROR_FILE}")

    if extension_counts:
        print("\nMost common dumped extensions:")
        for extension, count in extension_counts.most_common(20):
            print(f"  {count:>7}  {extension}")

    if skipped_reasons:
        print("\nSkipped summary:")
        for reason, count in skipped_reasons.most_common():
            print(f"  {count:>7}  {reason}")

    print("=" * 78)

    if source_read_errors:
        print("\nWARNING: Some recognized authored source files could not be read.")
        print(f"Review: {ERROR_FILE}")
        return 1

    print("\nNo recognized authored source files failed to read.")
    return 0


if __name__ == "__main__":
    exit_code = main()

    if sys.stdin.isatty():
        input("Press Enter to exit...")

    raise SystemExit(exit_code)
