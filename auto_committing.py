#!/usr/bin/env python3
"""
auto_commit.py

Default:
  python auto_commit.py

This runs:
  git add .

Then sends `git diff --cached` to Groq. If the diff is too large,
it summarizes the diff in chunks first, then asks Groq for the final
commit message using the exact required prompt first.

Important retry behavior:
- Loads GROQ_API_KEY, then GROQ_API_KEY1 through GROQ_API_KEY9.
- Rotates the starting key between requests so chunk 1 does not always
  burn only GROQ_API_KEY.
- Tries every loaded key before sleeping.
- Treats HTTP 429 as rate limit, not as "diff too large".
- Treats HTTP 413 as actual oversized request.

Environment variables:
  GROQ_API_KEY
  GROQ_API_KEY1
  ...
  GROQ_API_KEY9

Optional:
  GROQ_MODEL=llama-3.1-8b-instant
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import time
from pathlib import Path

import requests


GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")

OUTPUT_MESSAGE_FILE = "COMMIT_EDITMSG.generated.txt"
OUTPUT_DIFF_FILE = "STAGED_DIFF.generated.patch"
OUTPUT_SUMMARY_FILE = "STAGED_DIFF_SUMMARY.generated.txt"

REQUEST_TIMEOUT = 120
MAX_RETRY_ROUNDS = 8

# Keep prompts under the 6k TPM limit of llama-3.1-8b-instant.
MAX_DIRECT_DIFF_CHARS = 18_000
MAX_CHUNK_CHARS = 11_000
MAX_SUMMARY_CHARS = 14_000

NEXT_KEY_START_INDEX = 0


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
-	Configure RSpec, Cucumber, and SimpleCov
-	Add testing gems: shoulda-matchers, factory_bot_rails, rails-controller-testing, capybara, database_cleaner
-	Create controller specs for PagesController and Users::SessionsController testing redirects and access
-	Add User model specs validating authentication and OAuth methods
-	Implement feature specs and Cucumber scenarios for complete authentication workflows
-	Configure Devise test helpers and Capybara support"""


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
    pass


def run_cmd(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
    )


def stream_cmd(args: list[str], *, check: bool = True) -> None:
    print(f"\n$ {' '.join(args)}")
    proc = subprocess.run(args, check=False)
    if check and proc.returncode != 0:
        raise SystemExit(proc.returncode)


def load_groq_keys() -> list[tuple[str, str]]:
    keys: list[tuple[str, str]] = []

    base_key = os.getenv("GROQ_API_KEY")
    if base_key:
        keys.append(("GROQ_API_KEY", base_key))

    for i in range(1, 10):
        name = f"GROQ_API_KEY{i}"
        value = os.getenv(name)
        if value:
            keys.append((name, value))

    if not keys:
        raise SystemExit(
            "No Groq API keys found. Set GROQ_API_KEY and optionally "
            "GROQ_API_KEY1 through GROQ_API_KEY9."
        )

    return keys


def rotate_keys(keys: list[tuple[str, str]], start_index: int) -> list[tuple[str, str]]:
    if not keys:
        return keys

    start_index = start_index % len(keys)
    return keys[start_index:] + keys[:start_index]


def parse_retry_after_seconds(resp: requests.Response, fallback: float) -> float:
    retry_after = resp.headers.get("retry-after")
    if retry_after:
        try:
            return float(retry_after)
        except ValueError:
            pass

    try:
        message = resp.json().get("error", {}).get("message", "")
        match = re.search(r"try again in\s+([0-9]*\.?[0-9]+)s", message, re.I)
        if match:
            return float(match.group(1))
    except Exception:
        pass

    return fallback


def make_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def is_request_too_large(resp: requests.Response) -> bool:
    # Only 413 means the request itself is oversized.
    # A 429 mentioning TPM means rate limit; another key may still work.
    return resp.status_code == 413


def groq_chat_try_all_keys(messages: list[dict[str, str]], *, max_tokens: int = 900) -> str:
    """
    Try all loaded Groq keys before sleeping.

    Starts each separate Groq request at the next key, so chunked
    summaries spread usage across all 10 keys instead of always starting
    from GROQ_API_KEY.
    """
    global NEXT_KEY_START_INDEX

    keys = load_groq_keys()
    ordered_keys = rotate_keys(keys, NEXT_KEY_START_INDEX)

    print("Loaded Groq keys: " + ", ".join(name for name, _ in keys))
    print(f"Starting this request with: {ordered_keys[0][0]}")

    payload = {
        "model": GROQ_MODEL,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }

    backoff = 1.0

    for round_no in range(1, MAX_RETRY_ROUNDS + 1):
        waits: list[float] = []
        last_error = ""

        for key_name, api_key in ordered_keys:
            print(f"Trying {key_name}...")

            try:
                resp = requests.post(
                    GROQ_API_URL,
                    headers=make_headers(api_key),
                    json=payload,
                    timeout=REQUEST_TIMEOUT,
                )
            except requests.RequestException as exc:
                last_error = f"{key_name} network error: {exc}"
                print(f"[NET] {last_error}")
                print("Trying next key before sleeping...")
                continue

            if resp.status_code == 200:
                print(f"[OK] Groq succeeded using {key_name}.")

                # Next Groq request starts after the key that just succeeded.
                original_index = next(
                    i for i, (name, _) in enumerate(keys)
                    if name == key_name
                )
                NEXT_KEY_START_INDEX = (original_index + 1) % len(keys)

                data = resp.json()
                return data["choices"][0]["message"]["content"].strip()

            if is_request_too_large(resp):
                raise RequestTooLargeError(
                    f"Groq request too large using {key_name}: "
                    f"{resp.status_code} {resp.text}"
                )

            if resp.status_code == 429:
                wait_s = max(parse_retry_after_seconds(resp, backoff), backoff)
                waits.append(wait_s)
                last_error = f"{key_name} rate limited: {resp.text[:500]}"
                print(f"[429] {key_name} rate limited.")
                print("Trying next key before sleeping...")
                continue

            if resp.status_code in (408, 409, 500, 502, 503, 504):
                wait_s = max(parse_retry_after_seconds(resp, backoff), backoff)
                waits.append(wait_s)
                last_error = f"{key_name} temporary API error {resp.status_code}: {resp.text[:500]}"
                print(f"[TEMP] {last_error}")
                print("Trying next key before sleeping...")
                continue

            raise RuntimeError(
                f"Groq API error using {key_name}: "
                f"{resp.status_code} {resp.text}"
            )

        if round_no == MAX_RETRY_ROUNDS:
            raise RuntimeError(
                "All Groq keys failed too many times. Last error: "
                + (last_error or "unknown")
            )

        wait_s = max(waits) if waits else backoff
        print(
            f"All {len(keys)} loaded Groq key(s) failed this round. "
            f"Sleeping {wait_s:.2f}s, then retrying all keys. "
            f"Round {round_no}/{MAX_RETRY_ROUNDS}"
        )
        time.sleep(wait_s)
        backoff = min(backoff * 1.7, 30.0)

    raise RuntimeError("Groq API error: too many retries.")


def copy_to_clipboard(text: str) -> bool:
    clipboard_commands: list[list[str]] = [
        ["clip"],
        ["clip.exe"],
        ["pbcopy"],
        ["xclip", "-selection", "clipboard"],
        ["xsel", "--clipboard", "--input"],
        ["wl-copy"],
    ]

    for cmd in clipboard_commands:
        try:
            subprocess.run(
                cmd,
                input=text,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            return True
        except Exception:
            continue

    return False


def get_staged_diff() -> str:
    result = run_cmd(["git", "diff", "--cached"], check=True)
    return result.stdout


def validate_git_repo() -> None:
    result = run_cmd(["git", "rev-parse", "--is-inside-work-tree"], check=False)
    if result.returncode != 0 or result.stdout.strip() != "true":
        raise SystemExit("This folder is not inside a Git repository.")


def get_git_root() -> Path:
    result = run_cmd(["git", "rev-parse", "--show-toplevel"], check=True)
    return Path(result.stdout.strip()).resolve()


def get_self_git_path() -> str | None:
    """Return this script's path relative to the repo root, if applicable."""
    try:
        script_path = Path(__file__).resolve()
        git_root = get_git_root()
        return script_path.relative_to(git_root).as_posix()
    except ValueError:
        # The script is being run from outside the current repository.
        return None


def unstage_self() -> None:
    """Make sure this automation script is never part of the commit."""
    self_git_path = get_self_git_path()
    if not self_git_path:
        return

    result = run_cmd(
        ["git", "diff", "--cached", "--name-only", "--", self_git_path],
        check=True,
    )

    if result.stdout.strip():
        print(f"Excluding this script from staged changes: {self_git_path}")
        stream_cmd(["git", "reset", "--", self_git_path])


def stage_changes(mode: str) -> None:
    if mode == "update":
        stream_cmd(["git", "add", "-u"])
    elif mode == "all":
        stream_cmd(["git", "add", "."])
    else:
        raise ValueError(f"Unknown stage mode: {mode}")

    unstage_self()


def split_large_text(text: str, max_chars: int) -> list[str]:
    chunks: list[str] = []
    start = 0

    while start < len(text):
        end = min(start + max_chars, len(text))

        if end < len(text):
            newline = text.rfind("\n", start, end)
            if newline > start + max_chars // 2:
                end = newline

        chunks.append(text[start:end].strip())
        start = end

    return [chunk for chunk in chunks if chunk]


def split_diff_by_file(diff_text: str) -> list[str]:
    """
    Split a git diff into file-level chunks, then hard-split huge file diffs.
    """
    parts = re.split(r"(?=^diff --git )", diff_text, flags=re.M)
    chunks: list[str] = []

    for part in parts:
        part = part.strip()
        if not part:
            continue

        if len(part) <= MAX_CHUNK_CHARS:
            chunks.append(part)
            continue

        chunks.extend(split_large_text(part, MAX_CHUNK_CHARS))

    return chunks


def summarize_diff_chunk(chunk: str, index: int, total: int) -> str:
    messages = [
        {
            "role": "system",
            "content": (
                "Summarize staged git diff chunks for a later commit message. "
                "Be concise, factual, and include file names and functional changes. "
                "Do not write the final commit message."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Summarize chunk {index}/{total} of this staged git diff.\n"
                "Focus on what changed, why it matters, and affected files.\n"
                "Use short bullets only.\n\n"
                + chunk
            ),
        },
    ]

    return groq_chat_try_all_keys(messages, max_tokens=450)


def summarize_large_diff(diff_text: str) -> str:
    chunks = split_diff_by_file(diff_text)
    print(f"Diff is large. Summarizing in {len(chunks)} chunk(s)...")

    summaries: list[str] = []

    for i, chunk in enumerate(chunks, start=1):
        print(f"\nSummarizing diff chunk {i}/{len(chunks)}...")
        summary = summarize_diff_chunk(chunk, i, len(chunks))
        summaries.append(f"Chunk {i}/{len(chunks)}:\n{summary}")

    combined = "\n\n".join(summaries)

    if len(combined) <= MAX_SUMMARY_CHARS:
        return combined

    print("\nCombined summaries are still large. Compressing summaries once more...")

    messages = [
        {
            "role": "system",
            "content": (
                "Compress git diff summaries into a concise commit-message brief. "
                "Keep affected files, major changes, and motivation. "
                "Do not write the final commit message."
            ),
        },
        {
            "role": "user",
            "content": combined[:MAX_SUMMARY_CHARS],
        },
    ]

    return groq_chat_try_all_keys(messages, max_tokens=650)


def build_final_messages(change_context: str, *, context_is_summary: bool) -> list[dict[str, str]]:
    context_label = (
        "Here is a concise summary of the staged git diff. "
        "Use this summary to write the commit message:"
        if context_is_summary
        else "Here is the staged git diff:"
    )

    # IMPORTANT:
    # The user's exact commit prompt is inserted first, unchanged.
    # Extra instructions are appended only after it.
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
                "You write high-quality Git commit messages. "
                "Output only the commit message."
            ),
        },
        {
            "role": "user",
            "content": user_content,
        },
    ]


def generate_commit_message(diff_text: str) -> str:
    if len(diff_text) <= MAX_DIRECT_DIFF_CHARS:
        messages = build_final_messages(diff_text, context_is_summary=False)
        return groq_chat_try_all_keys(messages).strip()

    summary = summarize_large_diff(diff_text)
    save_text(OUTPUT_SUMMARY_FILE, summary)
    print(f"\nSaved staged diff summary to: {OUTPUT_SUMMARY_FILE}")

    messages = build_final_messages(summary, context_is_summary=True)

    try:
        return groq_chat_try_all_keys(messages).strip()
    except RequestTooLargeError:
        print("Final summary prompt was still too large. Truncating summary and retrying...")
        shorter_summary = summary[:MAX_SUMMARY_CHARS // 2]
        messages = build_final_messages(shorter_summary, context_is_summary=True)
        return groq_chat_try_all_keys(messages).strip()


def save_text(path: str | Path, text: str) -> None:
    Path(path).write_text(text.rstrip() + "\n", encoding="utf-8")


def ask_yes_no(question: str, *, default: bool = False) -> bool:
    suffix = " [Y/n]: " if default else " [y/N]: "
    answer = input(question + suffix).strip().lower()

    if not answer:
        return default

    return answer in ("y", "yes")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Stage changes, ask Groq for a commit message, then optionally commit and push."
    )
    parser.add_argument(
        "--stage",
        choices=["update", "all"],
        default=None,
        help="update = git add -u, all = git add .; default is all",
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Run git commit -F after generating the message.",
    )
    parser.add_argument(
        "--push",
        action="store_true",
        help="Run git push after committing. Implies --commit.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Do not prompt before commit or push.",
    )
    args = parser.parse_args()

    validate_git_repo()

    # Running `python auto_commit.py` alone defaults to `git add .`.
    # Use `--stage update` for `git add -u`.
    stage_mode = args.stage or "all"
    stage_changes(stage_mode)

    diff_text = get_staged_diff()
    if not diff_text.strip():
        raise SystemExit("No staged diff found after git add.")

    save_text(OUTPUT_DIFF_FILE, diff_text)
    print(f"\nSaved staged diff to: {OUTPUT_DIFF_FILE}")

    try:
        commit_message = generate_commit_message(diff_text).strip()
    except RequestTooLargeError as e:
        raise SystemExit(
            "The diff is still too large even after chunking. "
            "Commit fewer files at once or use --stage update.\n"
            f"{e}"
        )

    if not commit_message:
        raise SystemExit("Groq returned an empty commit message.")

    save_text(OUTPUT_MESSAGE_FILE, commit_message)
    copied = copy_to_clipboard(commit_message)

    print("\n" + "=" * 72)
    print("Generated commit message")
    print("=" * 72)
    print(commit_message)
    print("=" * 72)
    print(f"\nSaved to: {OUTPUT_MESSAGE_FILE}")

    if copied:
        print("Copied commit message to clipboard.")
    else:
        print("Could not copy to clipboard. Use the saved message file instead.")

    should_commit = args.commit or args.push
    if not should_commit and not args.yes:
        should_commit = ask_yes_no("Run git commit using this message now?", default=False)

    if should_commit:
        if args.yes or ask_yes_no("Confirm git commit -F now?", default=True):
            stream_cmd(["git", "commit", "-F", OUTPUT_MESSAGE_FILE])
        else:
            print("Commit skipped.")

    should_push = args.push
    if should_commit and not should_push and not args.yes:
        should_push = ask_yes_no("Run git push now?", default=False)

    if should_push:
        if args.yes or ask_yes_no("Confirm git push now?", default=True):
            stream_cmd(["git", "push"])
        else:
            print("Push skipped.")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
