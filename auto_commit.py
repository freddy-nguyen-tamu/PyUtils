#!/usr/bin/env python3
"""
auto_commit.py

Default:
  python auto_commit.py

This runs:
  git add .

Then it sends `git diff --cached` to Groq and generates a commit message.
Large or token-dense diffs are recursively split, summarized, merged, and
compressed until the final commit-message request fits.

The generated commit message is checked with a simple deterministic validator.
If it violates the required format, Groq gets exactly one final repair pass.
That repaired result is used as long as it is non-empty, even when the local
validator still reports warnings. No third formatting request is made.

Groq API keys:
  GROQ_API_KEY
  GROQ_API_KEY1
  ...
  GROQ_API_KEY9

Optional:
  GROQ_MODEL=auto, or set it to an active Groq chat model override
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import time
from pathlib import Path

import requests

import groq_dynamic

OUTPUT_MESSAGE_FILE = "COMMIT_EDITMSG.generated.txt"
OUTPUT_DIFF_FILE = "STAGED_DIFF.generated.patch"
OUTPUT_SUMMARY_FILE = "STAGED_DIFF_SUMMARY.generated.txt"

REQUEST_TIMEOUT = 120
MAX_RETRY_ROUNDS = 8

# These are only initial size targets. Rejected requests are recursively
# reduced again, so token-dense SQL and generated files can still be handled.
MAX_DIRECT_DIFF_CHARS = 18_000
INITIAL_DIFF_CHUNK_CHARS = 9_000
MAX_COMBINED_SUMMARY_CHARS = 12_000
MIN_ADAPTIVE_CHUNK_CHARS = 256
MIN_GROQ_MAX_TOKENS = 96

NEXT_KEY_START_INDEX = 0

# Ignore generated Playwright browser profile folders such as:
#   playwright-outhere-profile
#   tools/playwright-test-profile
IGNORED_DIR_NAME_RE = re.compile(r"^playwright-.+-profile$", re.I)

COMMIT_TYPES = (
    "build",
    "chore",
    "ci",
    "docs",
    "feat",
    "fix",
    "perf",
    "refactor",
    "revert",
    "style",
    "test",
)

COMMIT_SUBJECT_RE = re.compile(
    rf"^(?P<type>{'|'.join(COMMIT_TYPES)})"
    r"(?:\([^)]+\))?!?: (?P<summary>\S.*)$"
)

# Imperative mood cannot be proved perfectly with a small deterministic check.
# These common openings catch obvious non-imperative subjects.
NON_IMPERATIVE_OPENINGS = {
    "added",
    "adding",
    "adds",
    "changed",
    "changing",
    "changes",
    "created",
    "creating",
    "creates",
    "ensured",
    "ensuring",
    "ensures",
    "fixed",
    "fixing",
    "fixes",
    "handled",
    "handling",
    "handles",
    "implemented",
    "implementing",
    "implements",
    "improved",
    "improving",
    "improves",
    "refactored",
    "refactoring",
    "refactors",
    "removed",
    "removing",
    "removes",
    "updated",
    "updating",
    "updates",
}


EXACT_COMMIT_PROMPT = """write git commit message for this commit following these requirements strictly:
Commit messages provide both a summary of the code changes ("What") and the motivation or reason behind them ("Why")
Commit messages have a subject and a body
Separate subject from body with a blank line
Limit the subject line to 50 characters
Capitalize the subject line
Do not end the subject line with a period
Use the imperative mood in the subject line
Wrap the body at 72 characters
Use the body to explain what and why vs. how
Commit messages are:
Rational: the message provides a logical explanation for the changes and clearly states the commit type
Comprehensive: the message includes a summary of the changes and covers all affected files
Concise: the essential information is conveyed succinctly
Expressive: the message has grammatical correctness and fluency
Type: title must contain commit type

Example:
test: Add comprehensive authentication test suite
Implement complete test coverage for user authentication to ensure sign-in, sign-up, and sign-out flows work correctly and remain stable during future development.
Changes include:
-\tConfigure RSpec, Cucumber, and SimpleCov
-\tAdd testing gems: shoulda-matchers, factory_bot_rails, rails-controller-testing, capybara, database_cleaner
-\tCreate controller specs for PagesController and Users::SessionsController testing redirects and access
-\tAdd User model specs validating authentication and OAuth methods
-\tImplement feature specs and Cucumber scenarios for complete authentication workflows
-\tConfigure Devise test helpers and Capybara support"""


EXTRA_OUTPUT_INSTRUCTIONS = """
Additional output rules:
Return only the commit message.
Do not include markdown fences.
Do not include explanations.
Do not include "Here is".
Do not include extra commentary before or after.
Do not include commands.
Do not include quotes around the message.
The output must be directly usable as a Git commit message file.
"""


class RequestTooLargeError(RuntimeError):
    """The current Groq payload must be made smaller."""

    def __init__(
        self,
        message: str,
        *,
        requested_tokens: int | None = None,
        limit_tokens: int | None = None,
    ) -> None:
        super().__init__(message)
        self.requested_tokens = requested_tokens
        self.limit_tokens = limit_tokens


def run_cmd(
    args: list[str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
    )


def stream_cmd(args: list[str], *, check: bool = True) -> None:
    print(f"\n$ {' '.join(args)}")
    process = subprocess.run(args, check=False)

    if check and process.returncode != 0:
        raise SystemExit(process.returncode)


def save_text(path: str | Path, text: str) -> None:
    Path(path).write_text(
        text.rstrip() + "\n",
        encoding="utf-8",
    )


def load_groq_keys() -> list[tuple[str, str]]:
    keys: list[tuple[str, str]] = []

    base_key = os.getenv("GROQ_API_KEY")
    if base_key:
        keys.append(("GROQ_API_KEY", base_key))

    for index in range(1, 10):
        name = f"GROQ_API_KEY{index}"
        value = os.getenv(name)

        if value:
            keys.append((name, value))

    if not keys:
        raise SystemExit(
            "No Groq API keys found. Set GROQ_API_KEY and optionally "
            "GROQ_API_KEY1 through GROQ_API_KEY9."
        )

    return keys


def rotate_keys(
    keys: list[tuple[str, str]],
    start_index: int,
) -> list[tuple[str, str]]:
    if not keys:
        return keys

    start_index %= len(keys)
    return keys[start_index:] + keys[:start_index]


def make_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def get_groq_error_message(response: requests.Response) -> str:
    try:
        payload = response.json()
        return str(payload.get("error", {}).get("message", ""))
    except Exception:
        return response.text or ""


def parse_retry_after_seconds(
    response: requests.Response,
    fallback: float,
) -> float:
    retry_after = response.headers.get("retry-after")

    if retry_after:
        try:
            return float(retry_after)
        except ValueError:
            pass

    message = get_groq_error_message(response)
    match = re.search(
        r"try again in\s+([0-9]*\.?[0-9]+)s",
        message,
        re.I,
    )

    if match:
        return float(match.group(1))

    return fallback


def parse_request_token_counts(
    response: requests.Response,
) -> tuple[int | None, int | None]:
    message = get_groq_error_message(response)

    requested_match = re.search(
        r"\bRequested\s+(\d+)",
        message,
        re.I,
    )
    limit_match = re.search(
        r"\bLimit\s+(\d+)",
        message,
        re.I,
    )

    requested_tokens = (
        int(requested_match.group(1))
        if requested_match
        else None
    )
    limit_tokens = (
        int(limit_match.group(1))
        if limit_match
        else None
    )

    return requested_tokens, limit_tokens


def is_request_too_large(response: requests.Response) -> bool:
    """
    Return True when the exact payload must be made smaller.

    This includes Groq responses like:

      HTTP 413
      Request too large...
      Limit 6000, Requested 8008

    Another API key cannot make the same payload smaller, so the caller
    should recursively split or summarize the input.
    """
    if response.status_code == 413:
        return True

    message = get_groq_error_message(response).lower()

    size_markers = (
        "request too large",
        "context length",
        "context_length_exceeded",
        "maximum context",
        "too many tokens",
    )

    # Groq may expose an oversized request as 400 or 429 depending on the
    # model and service tier. Only classify 429 as a size problem when the
    # error explicitly says the request itself is too large.
    if response.status_code in (400, 429):
        return any(
            marker in message
            for marker in size_markers
        )

    return False


def make_request_too_large_error(
    response: requests.Response,
    key_name: str,
) -> RequestTooLargeError:
    requested_tokens, limit_tokens = parse_request_token_counts(response)

    return RequestTooLargeError(
        f"Groq request too large using {key_name}: "
        f"{response.status_code} {response.text}",
        requested_tokens=requested_tokens,
        limit_tokens=limit_tokens,
    )


def groq_chat_try_all_keys(
    messages: list[dict[str, str]],
    *,
    max_tokens: int = 900,
) -> str:
    """
    Try every configured Groq key before sleeping.

    Request-size failures return immediately to the adaptive caller because
    another key cannot make the same payload smaller.

    Normal 429 rate limits still rotate through every loaded key before
    sleeping and retrying.
    """
    data = groq_dynamic.groq_chat_try_all_models(
        messages,
        max_tokens=max_tokens,
        temperature=0.2,
        timeout=REQUEST_TIMEOUT,
        max_retry_rounds=MAX_RETRY_ROUNDS,
        request_too_large_error_cls=RequestTooLargeError,
        is_request_too_large_func=is_request_too_large,
        make_request_too_large_error_func=make_request_too_large_error,
    )

    return str(
        data["choices"][0]["message"]["content"]
    ).strip()


def copy_to_clipboard(text: str) -> bool:
    clipboard_commands: list[list[str]] = [
        ["clip"],
        ["clip.exe"],
        ["pbcopy"],
        ["xclip", "-selection", "clipboard"],
        ["xsel", "--clipboard", "--input"],
        ["wl-copy"],
    ]

    for command in clipboard_commands:
        try:
            subprocess.run(
                command,
                input=text,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            return True
        except Exception:
            continue

    return False


def validate_git_repo() -> None:
    result = run_cmd(
        [
            "git",
            "rev-parse",
            "--is-inside-work-tree",
        ],
        check=False,
    )

    if (
        result.returncode != 0
        or result.stdout.strip() != "true"
    ):
        raise SystemExit(
            "This folder is not inside a Git repository."
        )


def get_git_root() -> Path:
    result = run_cmd(
        [
            "git",
            "rev-parse",
            "--show-toplevel",
        ]
    )

    return Path(
        result.stdout.strip()
    ).resolve()


def get_self_git_path() -> str | None:
    """
    Return this script's path relative to the current repository.
    """
    try:
        script_path = Path(__file__).resolve()
        git_root = get_git_root()

        return script_path.relative_to(
            git_root
        ).as_posix()
    except ValueError:
        return None


def unstage_self() -> None:
    """
    Make sure this automation script is not included in its own commit.
    """
    self_git_path = get_self_git_path()

    if not self_git_path:
        return

    result = run_cmd(
        [
            "git",
            "diff",
            "--cached",
            "--name-only",
            "--",
            self_git_path,
        ]
    )

    if result.stdout.strip():
        print(
            "Excluding this script from staged "
            f"changes: {self_git_path}"
        )

        stream_cmd(
            [
                "git",
                "reset",
                "--",
                self_git_path,
            ]
        )


def path_has_ignored_dir(git_path: str) -> bool:
    return any(
        IGNORED_DIR_NAME_RE.match(path_part)
        for path_part in Path(git_path).parts
    )


def get_ignored_staged_paths() -> list[str]:
    result = run_cmd(
        [
            "git",
            "diff",
            "--cached",
            "--name-only",
        ]
    )

    staged_paths = [
        line.strip().replace("\\", "/")
        for line in result.stdout.splitlines()
        if line.strip()
    ]

    return [
        path
        for path in staged_paths
        if path_has_ignored_dir(path)
    ]


def unstage_ignored_dirs() -> None:
    ignored_paths = get_ignored_staged_paths()

    if not ignored_paths:
        return

    print(
        "\nExcluding generated Playwright "
        "profile folder contents:"
    )

    for path in ignored_paths:
        print(f"  - {path}")

    process = subprocess.run(
        [
            "git",
            "reset",
            "--pathspec-from-file=-",
        ],
        input="\n".join(ignored_paths) + "\n",
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    if process.stdout.strip():
        print(process.stdout.strip())

    if process.returncode != 0:
        if process.stderr.strip():
            print(process.stderr.strip())

        raise SystemExit(process.returncode)


def stage_changes(mode: str) -> None:
    if mode == "update":
        stream_cmd(
            [
                "git",
                "add",
                "-u",
            ]
        )
    elif mode == "all":
        stream_cmd(
            [
                "git",
                "add",
                ".",
            ]
        )
    else:
        raise ValueError(
            f"Unknown stage mode: {mode}"
        )

    unstage_self()
    unstage_ignored_dirs()


def get_staged_diff() -> str:
    result = run_cmd(
        [
            "git",
            "diff",
            "--cached",
        ]
    )

    return result.stdout or ""


def get_staged_file_manifest() -> str:
    """
    Return a compact deterministic list of all staged file changes.
    """
    result = run_cmd(
        [
            "git",
            "diff",
            "--cached",
            "--name-status",
        ]
    )

    lines = [
        line.rstrip()
        for line in result.stdout.splitlines()
        if line.strip()
    ]

    if not lines:
        return ""

    return (
        "Affected staged files:\n"
        + "\n".join(
            f"- {line}"
            for line in lines
        )
    )


def split_large_text(
    text: str,
    max_chars: int,
) -> list[str]:
    """
    Split text near newline boundaries while guaranteeing progress.
    """
    text = text.strip()

    if not text:
        return []

    if max_chars < 1:
        max_chars = 1

    chunks: list[str] = []
    start = 0

    while start < len(text):
        end = min(
            start + max_chars,
            len(text),
        )

        if end < len(text):
            newline = text.rfind(
                "\n",
                start,
                end,
            )

            if newline > start + max_chars // 2:
                end = newline

        if end <= start:
            end = min(
                start + max_chars,
                len(text),
            )

        if end <= start:
            end = start + 1

        chunk = text[start:end].strip()

        if chunk:
            chunks.append(chunk)

        start = end

    return chunks


def split_diff_by_file(
    diff_text: str,
) -> list[str]:
    """
    Split a diff at file boundaries, then hard-split oversized files.
    """
    file_parts = re.split(
        r"(?=^diff --git )",
        diff_text,
        flags=re.M,
    )

    chunks: list[str] = []

    for file_part in file_parts:
        file_part = file_part.strip()

        if not file_part:
            continue

        if len(file_part) <= INITIAL_DIFF_CHUNK_CHARS:
            chunks.append(file_part)
        else:
            chunks.extend(
                split_large_text(
                    file_part,
                    INITIAL_DIFF_CHUNK_CHARS,
                )
            )

    return chunks


def choose_smaller_chunk_size(
    text: str,
    error: RequestTooLargeError,
) -> int:
    """
    Estimate a safer character budget from Groq's token error.
    """
    text_length = len(text)

    if text_length <= 1:
        return 1

    if (
        error.requested_tokens
        and error.limit_tokens
        and error.requested_tokens > 0
    ):
        fit_ratio = (
            error.limit_tokens
            / error.requested_tokens
        )

        target = int(
            text_length
            * fit_ratio
            * 0.65
        )
    else:
        target = text_length // 2

    target = max(
        1,
        min(
            target,
            text_length - 1,
        ),
    )

    if text_length > MIN_ADAPTIVE_CHUNK_CHARS:
        target = max(
            MIN_ADAPTIVE_CHUNK_CHARS,
            target,
        )

        target = min(
            target,
            text_length - 1,
        )

    return target


def split_after_size_error(
    text: str,
    error: RequestTooLargeError,
) -> list[str]:
    max_chars = choose_smaller_chunk_size(
        text,
        error,
    )

    parts = split_large_text(
        text,
        max_chars,
    )

    if len(parts) >= 2:
        return parts

    # Guarantee progress when no useful newline boundary exists.
    midpoint = max(
        1,
        len(text) // 2,
    )

    parts = [
        text[:midpoint].strip(),
        text[midpoint:].strip(),
    ]

    return [
        part
        for part in parts
        if part
    ]


def build_diff_summary_messages(
    chunk: str,
    label: str,
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "Summarize staged Git diff chunks for a "
                "later commit message. Be concise and "
                "factual. Preserve file names, functional "
                "changes, and motivation. Do not write the "
                "final commit message."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Summarize {label}.\n"
                "Focus on what changed, why it matters, "
                "and affected files.\n"
                "Use concise bullets only.\n\n"
                + chunk
            ),
        },
    ]


def build_compression_messages(
    text: str,
    purpose: str,
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "Compress staged-change summaries for a "
                "later Git commit message. Preserve every "
                "unique affected file, major change, and "
                "motivation. Do not write the final commit "
                "message."
            ),
        },
        {
            "role": "user",
            "content": (
                purpose
                + "\nUse concise factual bullets. Remove "
                "repetition while preserving distinct "
                "changes and reasons.\n\n"
                + text
            ),
        },
    ]


def shrink_output_token_budget(
    current_max_tokens: int,
    error: RequestTooLargeError,
) -> int:
    """
    Reduce requested output tokens when the input is already small.
    """
    if (
        error.requested_tokens
        and error.limit_tokens
        and error.requested_tokens > 0
    ):
        fit_ratio = (
            error.limit_tokens
            / error.requested_tokens
        )

        target = int(
            current_max_tokens
            * fit_ratio
            * 0.65
        )
    else:
        target = current_max_tokens // 2

    return max(
        MIN_GROQ_MAX_TOKENS,
        min(
            target,
            current_max_tokens - 1,
        ),
    )


def compress_text_adaptively(
    text: str,
    *,
    purpose: str,
    max_tokens: int = 500,
    depth: int = 0,
) -> str:
    """
    Compress text and recursively split any rejected payload.

    There is no fixed recursion count. Every request-size failure reduces
    either the text partition or requested output-token budget.
    """
    text = text.strip()

    if not text:
        return ""

    messages = build_compression_messages(
        text,
        purpose,
    )

    try:
        result = groq_chat_try_all_keys(
            messages,
            max_tokens=max_tokens,
        ).strip()

        return result or text

    except RequestTooLargeError as error:
        indent = "  " * depth

        print(
            f"{indent}[ADAPT] Compression payload "
            "is still too large; reducing it again."
        )

        if len(text) <= MIN_ADAPTIVE_CHUNK_CHARS:
            smaller_max_tokens = (
                shrink_output_token_budget(
                    max_tokens,
                    error,
                )
            )

            if smaller_max_tokens < max_tokens:
                print(
                    f"{indent}[ADAPT] Reducing output "
                    f"budget from {max_tokens} to "
                    f"{smaller_max_tokens} tokens."
                )

                return compress_text_adaptively(
                    text,
                    purpose=purpose,
                    max_tokens=smaller_max_tokens,
                    depth=depth + 1,
                )

        if len(text) <= 1:
            raise

        parts = split_after_size_error(
            text,
            error,
        )

        child_max_tokens = max(
            MIN_GROQ_MAX_TOKENS,
            min(
                max_tokens,
                320,
            ),
        )

        partial_summaries: list[str] = []

        for part_index, part in enumerate(
            parts,
            start=1,
        ):
            print(
                f"{indent}[ADAPT] Compressing "
                f"subpart {part_index}/{len(parts)}..."
            )

            partial = compress_text_adaptively(
                part,
                purpose=(
                    f"Compress subpart "
                    f"{part_index}/{len(parts)} "
                    "for a later merged staged-change "
                    "summary."
                ),
                max_tokens=child_max_tokens,
                depth=depth + 1,
            ).strip()

            if partial:
                partial_summaries.append(
                    f"Subpart "
                    f"{part_index}/{len(parts)}:\n"
                    f"{partial}"
                )

        combined = "\n\n".join(
            partial_summaries
        ).strip()

        if not combined:
            return text

        # Merge child summaries. If this merge is still oversized, this
        # same function splits and reduces it again.
        merged = compress_text_adaptively(
            combined,
            purpose=purpose,
            max_tokens=max_tokens,
            depth=depth + 1,
        ).strip()

        # Normally compression is shorter. Guarantee progress if a model
        # unexpectedly expands the supplied text.
        if (
            len(merged) >= len(text)
            and len(text) > MIN_ADAPTIVE_CHUNK_CHARS
        ):
            print(
                f"{indent}[ADAPT] Compression did not "
                "shrink enough; compressing a tighter "
                "deterministic brief."
            )

            tighter_limit = max(
                MIN_ADAPTIVE_CHUNK_CHARS,
                len(text) // 2,
            )

            tighter_source = combined[
                :tighter_limit
            ]

            return compress_text_adaptively(
                tighter_source,
                purpose=purpose,
                max_tokens=max(
                    MIN_GROQ_MAX_TOKENS,
                    min(
                        max_tokens,
                        320,
                    ),
                ),
                depth=depth + 1,
            )

        return merged or text


def summarize_diff_chunk(
    chunk: str,
    index: int,
    total: int,
    *,
    max_tokens: int = 450,
    depth: int = 0,
) -> str:
    """
    Summarize one diff chunk, recursively reducing rejected payloads.
    """
    label = f"diff chunk {index}/{total}"

    messages = build_diff_summary_messages(
        chunk,
        label,
    )

    try:
        return groq_chat_try_all_keys(
            messages,
            max_tokens=max_tokens,
        ).strip()

    except RequestTooLargeError as error:
        indent = "  " * depth

        print(
            f"{indent}[ADAPT] {label} is too large "
            "or token-dense; splitting it again."
        )

        if len(chunk) <= MIN_ADAPTIVE_CHUNK_CHARS:
            smaller_max_tokens = (
                shrink_output_token_budget(
                    max_tokens,
                    error,
                )
            )

            if smaller_max_tokens < max_tokens:
                return summarize_diff_chunk(
                    chunk,
                    index,
                    total,
                    max_tokens=smaller_max_tokens,
                    depth=depth + 1,
                )

        if len(chunk) <= 1:
            raise

        parts = split_after_size_error(
            chunk,
            error,
        )

        child_summaries: list[str] = []

        child_max_tokens = max(
            MIN_GROQ_MAX_TOKENS,
            min(
                max_tokens,
                320,
            ),
        )

        for part_index, part in enumerate(
            parts,
            start=1,
        ):
            print(
                f"{indent}[ADAPT] Summarizing "
                f"{label} subchunk "
                f"{part_index}/{len(parts)}..."
            )

            child_summary = summarize_diff_chunk(
                part,
                part_index,
                len(parts),
                max_tokens=child_max_tokens,
                depth=depth + 1,
            ).strip()

            if child_summary:
                child_summaries.append(
                    f"{label} subchunk "
                    f"{part_index}/{len(parts)}:\n"
                    f"{child_summary}"
                )

        combined = "\n\n".join(
            child_summaries
        ).strip()

        if not combined:
            return ""

        return compress_text_adaptively(
            combined,
            purpose=(
                f"Merge all summaries for {label} "
                "into one concise factual brief "
                "without losing affected files or "
                "distinct changes."
            ),
            max_tokens=max_tokens,
            depth=depth + 1,
        ).strip()


def summarize_large_diff(
    diff_text: str,
    file_manifest: str,
) -> str:
    chunks = split_diff_by_file(diff_text)

    print(
        f"Diff is large. Summarizing in "
        f"{len(chunks)} chunk(s)..."
    )

    summaries: list[str] = []

    for index, chunk in enumerate(
        chunks,
        start=1,
    ):
        print(
            f"\nSummarizing diff chunk "
            f"{index}/{len(chunks)}..."
        )

        summary = summarize_diff_chunk(
            chunk,
            index,
            len(chunks),
        ).strip()

        if summary:
            summaries.append(
                f"Chunk {index}/{len(chunks)}:\n"
                f"{summary}"
            )

    combined_parts: list[str] = []

    if file_manifest.strip():
        combined_parts.append(
            file_manifest.strip()
        )

    combined_parts.extend(summaries)

    combined = "\n\n".join(
        combined_parts
    ).strip()

    if (
        len(combined)
        <= MAX_COMBINED_SUMMARY_CHARS
    ):
        return combined

    print(
        "\nCombined summaries are still large. "
        "Compressing them recursively..."
    )

    return compress_text_adaptively(
        combined,
        purpose=(
            "Create one concise commit-message brief "
            "from all staged-change summaries. Preserve "
            "the complete affected-file manifest, major "
            "changes, and reasons."
        ),
        max_tokens=650,
    ).strip()


def build_final_messages(
    change_context: str,
    *,
    context_is_summary: bool,
) -> list[dict[str, str]]:
    context_label = (
        "Here is a recursively prepared summary "
        "of the staged Git diff. Use it to write "
        "the commit message:"
        if context_is_summary
        else "Here is the staged Git diff:"
    )

    # Keep the required commit prompt first and unchanged.
    user_content = (
        EXACT_COMMIT_PROMPT
        + "\n\n"
        + EXTRA_OUTPUT_INSTRUCTIONS
        + "\n\n"
        + context_label
        + "\n\n"
        + change_context
    )

    return [
        {
            "role": "system",
            "content": (
                "You write high-quality Git commit "
                "messages. Output only the commit message."
            ),
        },
        {
            "role": "user",
            "content": user_content,
        },
    ]


def reduce_context_for_final_request(
    context: str,
    error: RequestTooLargeError,
) -> str:
    """
    Reduce final-message context while retaining all partitions.
    """
    parts = split_after_size_error(
        context,
        error,
    )

    reduced_parts: list[str] = []

    for index, part in enumerate(
        parts,
        start=1,
    ):
        print(
            f"[ADAPT] Reducing final context "
            f"part {index}/{len(parts)}..."
        )

        reduced = compress_text_adaptively(
            part,
            purpose=(
                f"Reduce final commit context part "
                f"{index}/{len(parts)}. Preserve "
                "affected files, what changed, and why."
            ),
            max_tokens=320,
        ).strip()

        if reduced:
            reduced_parts.append(
                f"Context part "
                f"{index}/{len(parts)}:\n"
                f"{reduced}"
            )

    combined = "\n\n".join(
        reduced_parts
    ).strip()

    if not combined:
        return context

    return compress_text_adaptively(
        combined,
        purpose=(
            "Merge all reduced final-context parts "
            "into one concise, complete "
            "commit-message brief."
        ),
        max_tokens=550,
    ).strip()


def generate_final_commit_message_adaptively(
    context: str,
    *,
    context_is_summary: bool,
    max_tokens: int = 900,
) -> str:
    """
    Generate the final commit message.

    Every request-size rejection recursively reduces the supplied context
    until Groq returns the final message.
    """
    context = context.strip()

    messages = build_final_messages(
        context,
        context_is_summary=context_is_summary,
    )

    try:
        return groq_chat_try_all_keys(
            messages,
            max_tokens=max_tokens,
        ).strip()

    except RequestTooLargeError as error:
        print(
            "[ADAPT] Final commit-message request "
            "is still too large; reducing its "
            "context again."
        )

        if len(context) <= MIN_ADAPTIVE_CHUNK_CHARS:
            smaller_max_tokens = (
                shrink_output_token_budget(
                    max_tokens,
                    error,
                )
            )

            if smaller_max_tokens < max_tokens:
                print(
                    "[ADAPT] Final context is already "
                    "small; reducing the output budget "
                    f"from {max_tokens} to "
                    f"{smaller_max_tokens} tokens."
                )

                return (
                    generate_final_commit_message_adaptively(
                        context,
                        context_is_summary=True,
                        max_tokens=smaller_max_tokens,
                    )
                )

        if len(context) <= 1:
            raise

        reduced_context = (
            reduce_context_for_final_request(
                context,
                error,
            )
        )

        # Guarantee progress if an unusual compression response expands
        # instead of reducing the context.
        if len(reduced_context) >= len(context):
            deterministic_limit = max(
                MIN_ADAPTIVE_CHUNK_CHARS,
                len(context) // 2,
            )

            reduced_context = reduced_context[
                :deterministic_limit
            ].strip()

        return generate_final_commit_message_adaptively(
            reduced_context,
            context_is_summary=True,
            max_tokens=max_tokens,
        )


def generate_commit_message(
    diff_text: str,
    file_manifest: str,
) -> str:
    if len(diff_text) <= MAX_DIRECT_DIFF_CHARS:
        direct_context = diff_text

        if file_manifest.strip():
            direct_context = (
                file_manifest.strip()
                + "\n\n"
                + diff_text
            )

        return (
            generate_final_commit_message_adaptively(
                direct_context,
                context_is_summary=False,
            ).strip()
        )

    summary = summarize_large_diff(
        diff_text,
        file_manifest,
    )

    save_text(
        OUTPUT_SUMMARY_FILE,
        summary,
    )

    print(
        "\nSaved staged diff summary to: "
        f"{OUTPUT_SUMMARY_FILE}"
    )

    return (
        generate_final_commit_message_adaptively(
            summary,
            context_is_summary=True,
        ).strip()
    )


def strip_model_wrappers(text: str) -> str:
    """
    Remove obvious markdown or quote wrappers without rewriting content.
    """
    cleaned = text.strip()

    fence_match = re.fullmatch(
        r"```(?:text|gitcommit|commit)?\s*\n?"
        r"(.*?)\n?```",
        cleaned,
        flags=re.I | re.S,
    )

    if fence_match:
        cleaned = fence_match.group(1).strip()

    if (
        len(cleaned) >= 2
        and cleaned[0] == cleaned[-1]
        and cleaned[0] in ('"', "'")
        and "\n" in cleaned
    ):
        cleaned = cleaned[1:-1].strip()

    return cleaned


def validate_commit_message(
    message: str,
) -> list[str]:
    """
    Run a simple deterministic check against the required format.
    """
    violations: list[str] = []

    normalized = (
        message
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .strip()
    )

    if not normalized:
        return [
            "The commit message is empty."
        ]

    if "```" in normalized:
        violations.append(
            "Remove markdown code fences."
        )

    lines = normalized.split("\n")
    subject = lines[0].rstrip()

    if len(subject) > 50:
        violations.append(
            "Limit the subject to 50 characters; "
            f"it currently has {len(subject)}."
        )

    if subject.endswith("."):
        violations.append(
            "Do not end the subject with a period."
        )

    subject_match = (
        COMMIT_SUBJECT_RE.fullmatch(subject)
    )

    if not subject_match:
        violations.append(
            "Start the subject with a valid commit "
            "type such as feat:, fix:, refactor:, "
            "test:, docs:, chore:, build:, ci:, "
            "perf:, style:, or revert:."
        )
    else:
        summary = subject_match.group("summary")

        if (
            summary
            and summary[0].isalpha()
            and not summary[0].isupper()
        ):
            violations.append(
                "Capitalize the first word after "
                "the commit type."
            )

        first_word_match = re.match(
            r"[A-Za-z]+",
            summary,
        )

        if first_word_match:
            first_word = (
                first_word_match
                .group(0)
                .lower()
            )

            if first_word in NON_IMPERATIVE_OPENINGS:
                violations.append(
                    "Use imperative mood in the subject, "
                    "such as Add, Fix, Update, Refactor, "
                    "or Remove."
                )

    if (
        len(lines) < 2
        or lines[1].strip() != ""
    ):
        violations.append(
            "Separate the subject and body with "
            "exactly one blank line."
        )

    if (
        len(lines) >= 2
        and lines[1].strip() == ""
    ):
        body_lines = lines[2:]
    else:
        body_lines = []

    if not any(
        line.strip()
        for line in body_lines
    ):
        violations.append(
            "Include a non-empty commit-message body."
        )
    else:
        long_lines = [
            (
                line_number,
                len(line),
            )
            for line_number, line in enumerate(
                body_lines,
                start=3,
            )
            if len(line) > 72
        ]

        if long_lines:
            preview = ", ".join(
                f"line {line_number} ({length})"
                for line_number, length
                in long_lines[:5]
            )

            if len(long_lines) > 5:
                preview += ", ..."

            violations.append(
                "Wrap body lines at 72 characters; "
                "overlong lines: "
                + preview
            )

        body_text = " ".join(
            line.strip()
            for line in body_lines
            if line.strip()
        )

        body_word_count = len(
            re.findall(
                r"\b\w+\b",
                body_text,
            )
        )

        if body_word_count < 8:
            violations.append(
                "Expand the body enough to explain "
                "both what changed and why."
            )

    if normalized.lower().startswith("here is"):
        violations.append(
            "Remove introductory commentary such "
            "as 'Here is'."
        )

    return violations


def print_validation_warnings(
    heading: str,
    violations: list[str],
) -> None:
    if not violations:
        print(f"\n{heading}: passed")
        return

    print(
        f"\n{heading}: "
        f"{len(violations)} warning(s)"
    )

    for violation in violations:
        print(f"  - {violation}")


def build_repair_messages(
    original_message: str,
    violations: list[str],
    file_manifest: str,
) -> list[dict[str, str]]:
    violations_text = "\n".join(
        f"- {item}"
        for item in violations
    )

    manifest_section = ""

    if file_manifest.strip():
        manifest_section = (
            "\n\nThe commit must remain comprehensive "
            "for these staged files:\n"
            + file_manifest.strip()
        )

    user_content = (
        EXACT_COMMIT_PROMPT
        + "\n\n"
        + EXTRA_OUTPUT_INSTRUCTIONS
        + "\n\n"
        + "This is the one and only final "
        "formatting-repair pass.\n"
        + "Do not summarize the request and do not "
        "discuss the corrections.\n"
        + "Rewrite the existing commit message so it "
        "follows the required format while preserving "
        "its factual meaning.\n"
        + "Return a complete subject and body, not a "
        "critique.\n\n"
        + "Deterministic format violations:\n"
        + violations_text
        + manifest_section
        + "\n\nExisting commit message to repair:\n\n"
        + original_message.strip()
    )

    return [
        {
            "role": "system",
            "content": (
                "Repair the supplied Git commit "
                "message. Output only the full repaired "
                "commit message."
            ),
        },
        {
            "role": "user",
            "content": user_content,
        },
    ]


def repair_commit_message_once(
    original_message: str,
    violations: list[str],
    file_manifest: str,
) -> str:
    """
    Make exactly one final Groq formatting-repair request.

    Any non-empty result is accepted by the caller, even when the
    deterministic checker still reports warnings.
    """
    print(
        "\nCommit message missed the deterministic "
        "format check. Calling Groq one final time "
        "to repair the message..."
    )

    messages = build_repair_messages(
        original_message,
        violations,
        file_manifest,
    )

    return groq_chat_try_all_keys(
        messages,
        max_tokens=900,
    ).strip()


def finalize_commit_message(
    generated_message: str,
    file_manifest: str,
) -> str:
    generated_message = strip_model_wrappers(
        generated_message
    )

    first_violations = validate_commit_message(
        generated_message
    )

    if not first_violations:
        print_validation_warnings(
            "Deterministic commit-message check",
            [],
        )

        return generated_message

    print_validation_warnings(
        "Initial deterministic commit-message check",
        first_violations,
    )

    repaired_message = repair_commit_message_once(
        generated_message,
        first_violations,
        file_manifest,
    )

    repaired_message = strip_model_wrappers(
        repaired_message
    )

    if not repaired_message:
        print(
            "\n[WARN] The one final repair response "
            "was empty. Using the original non-empty "
            "generated message."
        )

        return generated_message

    final_violations = validate_commit_message(
        repaired_message
    )

    if final_violations:
        print_validation_warnings(
            "Final repaired-message check",
            final_violations,
        )

        print(
            "[WARN] Using the non-empty repaired result "
            "as requested. No additional Groq formatting "
            "call will be made."
        )
    else:
        print_validation_warnings(
            "Final repaired-message check",
            [],
        )

    return repaired_message


def ask_yes_no(
    question: str,
    *,
    default: bool = False,
) -> bool:
    suffix = (
        " [Y/n]: "
        if default
        else " [y/N]: "
    )

    answer = input(
        question + suffix
    ).strip().lower()

    if not answer:
        return default

    return answer in (
        "y",
        "yes",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Stage changes, ask Groq for a commit "
            "message, validate it, repair it once if "
            "needed, then optionally commit and push."
        )
    )

    parser.add_argument(
        "--stage",
        choices=[
            "update",
            "all",
        ],
        default=None,
        help=(
            "update = git add -u, all = git add .; "
            "default is all"
        ),
    )

    parser.add_argument(
        "--commit",
        action="store_true",
        help=(
            "Run git commit -F after generating "
            "the message."
        ),
    )

    parser.add_argument(
        "--push",
        action="store_true",
        help=(
            "Run git push after committing. "
            "Implies --commit."
        ),
    )

    parser.add_argument(
        "--yes",
        action="store_true",
        help=(
            "Do not prompt before commit or push."
        ),
    )

    args = parser.parse_args()

    validate_git_repo()

    stage_mode = args.stage or "all"
    stage_changes(stage_mode)

    diff_text = get_staged_diff()

    if not diff_text.strip():
        raise SystemExit(
            "No staged diff found after git add."
        )

    file_manifest = get_staged_file_manifest()

    save_text(
        OUTPUT_DIFF_FILE,
        diff_text,
    )

    print(
        "\nSaved staged diff to: "
        f"{OUTPUT_DIFF_FILE}"
    )

    generated_message = generate_commit_message(
        diff_text,
        file_manifest,
    ).strip()

    if not generated_message:
        raise SystemExit(
            "Groq returned an empty commit message."
        )

    commit_message = finalize_commit_message(
        generated_message,
        file_manifest,
    ).strip()

    if not commit_message:
        raise SystemExit(
            "No usable commit message was produced."
        )

    save_text(
        OUTPUT_MESSAGE_FILE,
        commit_message,
    )

    copied = copy_to_clipboard(
        commit_message
    )

    print("\n" + "=" * 72)
    print("Generated commit message")
    print("=" * 72)
    print(commit_message)
    print("=" * 72)

    print(
        "\nSaved to: "
        f"{OUTPUT_MESSAGE_FILE}"
    )

    if copied:
        print(
            "Copied commit message to clipboard."
        )
    else:
        print(
            "Could not copy to clipboard. Use the "
            "saved message file instead."
        )

    should_commit = (
        args.commit
        or args.push
    )

    if (
        not should_commit
        and not args.yes
    ):
        should_commit = ask_yes_no(
            "Run git commit using this message now?",
            default=False,
        )

    if should_commit:
        if (
            args.yes
            or ask_yes_no(
                "Confirm git commit -F now?",
                default=True,
            )
        ):
            stream_cmd(
                [
                    "git",
                    "commit",
                    "-F",
                    OUTPUT_MESSAGE_FILE,
                ]
            )
        else:
            print("Commit skipped.")

    should_push = args.push

    if (
        should_commit
        and not should_push
        and not args.yes
    ):
        should_push = ask_yes_no(
            "Run git push now?",
            default=False,
        )

    if should_push:
        if (
            args.yes
            or ask_yes_no(
                "Confirm git push now?",
                default=True,
            )
        ):
            stream_cmd(
                [
                    "git",
                    "push",
                ]
            )
        else:
            print("Push skipped.")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
