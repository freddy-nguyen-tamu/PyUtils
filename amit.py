#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup


REPO_URL = (
    "https://github.com/"
    "amitshekhariitbhu/"
    "ai-engineering-interview-questions.git"
)

REPO_DIR = Path("ai-engineering-interview-questions")
OUTPUT_HTML = Path("ai_engineering_interview_questions.html")
CACHE_FILE = Path("answer_cache.json")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/151.0 Safari/537.36"
)

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "en-US,en;q=0.9",
}

TIMEOUT = 30


# ============================================================
# DATA
# ============================================================

@dataclass
class Question:
    id: str
    section: str
    question: str
    answer_url: str | None = None
    answer_label: str | None = None
    answer: str = ""
    source_type: str = "none"
    extraction_status: str = "missing"


# ============================================================
# GENERAL HELPERS
# ============================================================

def run(*args: str, check=True, capture=False) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        check=check,
        text=True,
        capture_output=capture,
    )


def stable_id(section: str, question: str) -> str:
    return hashlib.sha256(
        f"{section}\n{question}".encode("utf-8")
    ).hexdigest()[:16]


def normalize_space(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Trim horizontal whitespace while preserving paragraphs.
    lines = [re.sub(r"[ \t]+", " ", x).strip() for x in text.split("\n")]

    out = []
    blank = False

    for line in lines:
        if not line:
            if out and not blank:
                out.append("")
            blank = True
            continue

        out.append(line)
        blank = False

    return "\n".join(out).strip()


URL_RE = re.compile(
    r"""
    (?:
        https?://[^\s<>"')\]]+
        |
        www\.[^\s<>"')\]]+
    )
    """,
    re.I | re.X,
)


def remove_urls(text: str) -> str:
    """
    The final HTML must contain NO URLs, even as plain text.
    """

    text = URL_RE.sub("", text)

    # Remove markdown links but keep labels:
    # [Useful label](anything)
    text = re.sub(
        r"\[([^\]]+)\]\([^)]+\)",
        r"\1",
        text,
    )

    return normalize_space(text)


def clean_node(node) -> str:
    """
    Convert an article/post DOM subtree into readable text.

    Links become only their visible text because get_text()
    ignores href attributes.
    """

    clone = BeautifulSoup(str(node), "html.parser")

    # Remove content that is obviously not the answer.
    for bad in clone.select(
        """
        script,
        style,
        noscript,
        nav,
        footer,
        header,
        form,
        iframe,
        button,
        svg,
        canvas,
        aside
        """
    ):
        bad.decompose()

    # Put boundaries around block elements.
    for element in clone.find_all(
        [
            "p", "div", "section",
            "h1", "h2", "h3", "h4", "h5",
            "li", "pre", "blockquote",
            "table", "tr",
        ]
    ):
        element.insert_before("\n")
        element.insert_after("\n")

    text = clone.get_text(separator=" ", strip=False)

    return remove_urls(text)


def request(url: str) -> requests.Response:
    last_error = None

    for attempt in range(3):
        try:
            r = requests.get(
                url,
                headers=HEADERS,
                timeout=TIMEOUT,
                allow_redirects=True,
            )

            r.raise_for_status()
            return r

        except Exception as exc:
            last_error = exc

            if attempt != 2:
                time.sleep(1.5 * (attempt + 1))

    raise RuntimeError(
        f"Unable to retrieve page: {last_error}"
    )


# ============================================================
# PERSISTENT ANSWER CACHE
# ============================================================

def load_disk_cache() -> dict[str, dict[str, str]]:
    if not CACHE_FILE.exists():
        return {}

    try:
        raw = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[cache] Ignoring unreadable cache: {exc}")
        return {}

    if not isinstance(raw, dict):
        print("[cache] Ignoring cache because it is not a JSON object")
        return {}

    cleaned: dict[str, dict[str, str]] = {}

    for source_url, item in raw.items():
        if not isinstance(source_url, str) or not isinstance(item, dict):
            continue

        answer = item.get("answer", "")
        kind = item.get("source_type", "unknown")
        status = item.get("status", "failed")

        if not isinstance(answer, str):
            continue

        cleaned[source_url] = {
            "answer": remove_urls(answer),
            "source_type": str(kind),
            "status": str(status),
        }

    return cleaned


def save_disk_cache(cache: dict[str, dict[str, str]]) -> None:
    temp = CACHE_FILE.with_suffix(CACHE_FILE.suffix + ".tmp")

    temp.write_text(
        json.dumps(
            cache,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    temp.replace(CACHE_FILE)


# ============================================================
# REPOSITORY
# ============================================================

def clone_or_update():
    if (REPO_DIR / ".git").exists():
        print("[repo] Updating existing clone")
        run(
            "git",
            "-C",
            str(REPO_DIR),
            "pull",
            "--ff-only",
        )

    else:
        if REPO_DIR.exists():
            raise RuntimeError(
                f"{REPO_DIR} exists but is not a Git repository."
            )

        print("[repo] Cloning repository")

        run(
            "git",
            "clone",
            REPO_URL,
            str(REPO_DIR),
        )


# ============================================================
# README PARSER
# ============================================================

MARKDOWN_LINK_RE = re.compile(
    r"\[([^\]]+)\]\((https?://[^)]+)\)"
)


def parse_readme() -> list[Question]:
    readme = REPO_DIR / "README.md"

    if not readme.exists():
        raise RuntimeError("README.md was not found.")

    lines = readme.read_text(
        encoding="utf-8"
    ).splitlines()

    # These are the interview sections from the repository TOC.
    section_names = []
    inside_toc = False

    for line in lines:
        if line.strip() == "## Table of Contents":
            inside_toc = True
            continue

        if inside_toc and line.startswith("### "):
            break

        if inside_toc:
            m = re.match(
                r"^-\s+\[([^\]]+)\]\(#[^)]+\)",
                line,
            )

            if m:
                name = m.group(1).strip()

                if name.casefold() != "must know":
                    section_names.append(name)

    allowed_sections = set(section_names)

    questions: list[Question] = []

    current_section = None
    current_question: Question | None = None

    for line in lines:

        # Section heading
        m = re.match(r"^###\s+(.+?)\s*$", line)

        if m:
            heading = re.sub(
                r"\*\*",
                "",
                m.group(1),
            ).strip()

            if heading in allowed_sections:
                current_section = heading
            else:
                current_section = None

            current_question = None
            continue

        if current_section is None:
            continue

        # Top-level question.
        if re.match(r"^-\s+\S", line):
            raw = re.sub(r"^-\s+", "", line).strip()

            # Remove URL mechanics from question text.
            raw = MARKDOWN_LINK_RE.sub(
                lambda m: m.group(1),
                raw,
            )

            raw = raw.replace("**", "").replace("`", "")

            q = Question(
                id=stable_id(current_section, raw),
                section=current_section,
                question=raw,
            )

            questions.append(q)
            current_question = q
            continue

        # Nested answer.
        if current_question:
            m = re.match(
                r"^\s+-\s+Answer:\s*(.+?)\s*$",
                line,
                flags=re.I,
            )

            if not m:
                continue

            answer_source = m.group(1)

            link = MARKDOWN_LINK_RE.search(answer_source)

            if link:
                current_question.answer_label = link.group(1).strip()
                current_question.answer_url = link.group(2).strip()
            else:
                # If repo itself contains direct answer text,
                # preserve it.
                direct = re.sub(
                    r"^Explained in this video:\s*",
                    "",
                    answer_source,
                    flags=re.I,
                )

                current_question.answer = remove_urls(direct)

                if current_question.answer:
                    current_question.source_type = "readme"
                    current_question.extraction_status = "ok"

    if not questions:
        raise RuntimeError("Parsed zero questions.")

    return questions


# ============================================================
# OUTCOME SCHOOL BLOG
# ============================================================

def extract_outcome_school(url: str) -> str:
    r = request(url)

    soup = BeautifulSoup(r.text, "html.parser")

    candidates = [
        soup.select_one("article"),
        soup.select_one("main article"),
        soup.select_one("main"),
    ]

    node = next(
        (
            candidate
            for candidate in candidates
            if candidate is not None
            and len(candidate.get_text(" ", strip=True)) > 400
        ),
        None,
    )

    if node is None:
        raise RuntimeError("No substantial article container found.")

    # Remove recurring non-article elements if they occur
    # inside main/article.
    for bad in node.select(
        """
        nav,
        footer,
        form,
        script,
        style,
        iframe
        """
    ):
        bad.decompose()

    text = clean_node(node)

    # Outcome School pages can append author/social/footer
    # material after the article. These textual boundaries are
    # much more deterministic than CSS class names.
    stop_markers = [
        "\nTags\n",
        "\nFollow Outcome School on:",
        "\nYou can connect with me on:",
        "\n© 2026 Outcome",
    ]

    positions = [
        text.find(marker)
        for marker in stop_markers
        if text.find(marker) >= 0
    ]

    if positions:
        text = text[:min(positions)]

    return remove_urls(text)


# ============================================================
# SUBSTACK
# ============================================================

def extract_substack(url: str) -> str:
    r = request(url)

    soup = BeautifulSoup(r.text, "html.parser")

    selectors = [
        "article .available-content",
        "article .body.markup",
        "article",
    ]

    node = None

    for selector in selectors:
        candidate = soup.select_one(selector)

        if (
            candidate is not None
            and len(candidate.get_text(" ", strip=True)) > 300
        ):
            node = candidate
            break

    if node is None:
        raise RuntimeError(
            "Could not find Substack article body."
        )

    for bad in node.select(
        """
        .subscription-widget-wrap,
        .subscribe-widget,
        .post-ufi,
        .share-dialog,
        button,
        form,
        script,
        style
        """
    ):
        bad.decompose()

    return clean_node(node)


# ============================================================
# LINKEDIN
# ============================================================

def meta_content(
    soup: BeautifulSoup,
    *,
    prop: str | None = None,
    name: str | None = None,
) -> str:
    if prop:
        node = soup.find(
            "meta",
            attrs={"property": prop},
        )
    else:
        node = soup.find(
            "meta",
            attrs={"name": name},
        )

    if node and node.get("content"):
        return str(node["content"]).strip()

    return ""


def extract_linkedin(url: str) -> str:
    """
    LinkedIn changes CSS classes frequently, so do not make
    classes the primary contract.

    Priority:
      1. Structured/meta post description
      2. Visible document text bounded before recommendations

    If neither yields enough text, fail visibly.
    """

    r = request(url)

    soup = BeautifulSoup(r.text, "html.parser")

    candidates = [
        meta_content(soup, prop="og:description"),
        meta_content(soup, name="description"),
    ]

    candidates = [
        remove_urls(x)
        for x in candidates
        if x
    ]

    candidates = [
        x
        for x in candidates
        if len(x) >= 80
    ]

    if candidates:
        # Prefer the longest public metadata field.
        return max(
            candidates,
            key=len,
        )

    # Fallback to visible page text.
    text = soup.get_text("\n", strip=True)
    text = remove_urls(text)

    stop_strings = [
        "More Relevant Posts",
        "More from this author",
        "Explore content categories",
    ]

    stops = [
        text.find(x)
        for x in stop_strings
        if text.find(x) >= 0
    ]

    if stops:
        text = text[:min(stops)]

    # Remove LinkedIn header/login garbage by locating the
    # post title if possible.
    title = meta_content(soup, prop="og:title")

    if title:
        title = remove_urls(title)

        pos = text.find(title)

        if pos >= 0:
            text = text[pos:]

    if len(text) < 80:
        raise RuntimeError(
            "LinkedIn exposed too little public post text."
        )

    return text


# ============================================================
# X / TWITTER
# ============================================================

X_ID_RE = re.compile(
    r"(?:x\.com|twitter\.com)/[^/]+/status/(\d+)",
    re.I,
)


def twitter_syndication_token(tweet_id: str) -> str:
    """
    X's embed endpoint commonly accepts token=0 too.
    Use the deterministic calculated token as the first try.
    """

    # Python integers preserve Tweet IDs exactly.
    n = int(tweet_id)

    # JS algorithm conceptually does:
    #
    # ((Number(id)/1e15) * PI).toString(36)
    #
    # Python has no native base-36 float formatting, so our
    # first request simply uses token=0, which the endpoint
    # currently accepts for many public tweets.
    return "0"


def extract_x(url: str) -> str:
    m = X_ID_RE.search(url)

    if not m:
        raise RuntimeError("Could not extract X status ID.")

    tweet_id = m.group(1)

    endpoint = (
        "https://cdn.syndication.twimg.com/"
        f"tweet-result?id={tweet_id}&token=0"
    )

    r = request(endpoint)

    try:
        data = r.json()
    except Exception as exc:
        raise RuntimeError(
            f"X syndication response was not JSON: {exc}"
        )

    text = data.get("text", "")

    if not isinstance(text, str) or not text.strip():
        raise RuntimeError(
            "X syndication response contained no post text."
        )

    return remove_urls(text)


# ============================================================
# YOUTUBE
# ============================================================

YOUTUBE_ID_PATTERNS = [
    re.compile(r"[?&]v=([A-Za-z0-9_-]{6,})"),
    re.compile(r"youtu\.be/([A-Za-z0-9_-]{6,})"),
]


def youtube_id(url: str) -> str:
    for regex in YOUTUBE_ID_PATTERNS:
        m = regex.search(url)

        if m:
            return m.group(1)

    raise RuntimeError("Could not determine YouTube video ID.")


def strip_vtt(vtt: str) -> str:
    """
    Convert captions into plain text while removing duplicate
    rolling-caption fragments.
    """

    lines = []

    for line in vtt.splitlines():
        line = line.strip()

        if not line:
            continue

        if line == "WEBVTT":
            continue

        if line.startswith(("Kind:", "Language:")):
            continue

        if "-->" in line:
            continue

        if re.fullmatch(r"\d+", line):
            continue

        # Remove VTT tags.
        line = re.sub(r"<[^>]+>", "", line)

        # HTML entities.
        line = html.unescape(line)

        line = normalize_space(line)

        if line:
            lines.append(line)

    # YouTube automatic VTT often repeats previous caption
    # lines. Remove consecutive duplicates.
    deduped = []

    for line in lines:
        if not deduped or line != deduped[-1]:
            deduped.append(line)

    return remove_urls("\n".join(deduped))


def extract_youtube(url: str) -> str:
    if shutil.which("yt-dlp") is None:
        raise RuntimeError(
            "yt-dlp is required for YouTube transcript extraction."
        )

    vid = youtube_id(url)

    with tempfile.TemporaryDirectory() as tempdir:
        template = str(
            Path(tempdir) / "%(id)s.%(ext)s"
        )

        command = [
            "yt-dlp",
            "--skip-download",

            # Prefer uploader-provided subtitles and fall
            # back to automatic captions.
            "--write-subs",
            "--write-auto-subs",

            "--sub-langs",
            "en.*,en",

            "--sub-format",
            "vtt",

            "-o",
            template,

            url,
        ]

        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
        )

        files = list(Path(tempdir).glob(f"{vid}*.vtt"))

        if not files:
            raise RuntimeError(
                "No English subtitle/caption file found.\n"
                + result.stderr[-1000:]
            )

        # Prefer a manually supplied English caption if several
        # files are returned; otherwise just choose largest.
        files.sort(
            key=lambda x: x.stat().st_size,
            reverse=True,
        )

        transcript = strip_vtt(
            files[0].read_text(
                encoding="utf-8",
                errors="replace",
            )
        )

        if len(transcript) < 50:
            raise RuntimeError(
                "Caption extraction produced too little text."
            )

        return transcript


# ============================================================
# ROUTER
# ============================================================

def source_type(url: str) -> str:
    host = urlparse(url).netloc.casefold()

    if host.endswith("outcomeschool.com"):
        return "outcomeschool"

    if host.endswith("substack.com"):
        return "substack"

    if "linkedin.com" in host:
        return "linkedin"

    if host in {
        "x.com",
        "www.x.com",
        "twitter.com",
        "www.twitter.com",
    }:
        return "x"

    if "youtube.com" in host or "youtu.be" in host:
        return "youtube"

    return "unknown"


def extract_answer(q: Question):
    if not q.answer_url:
        if q.answer:
            return

        q.extraction_status = "no-source"
        q.source_type = "none"
        return

    kind = source_type(q.answer_url)
    q.source_type = kind

    print(
        f"[fetch] {kind:14s} | "
        f"{q.question[:65]}"
    )

    try:
        if kind == "outcomeschool":
            q.answer = extract_outcome_school(q.answer_url)

        elif kind == "substack":
            q.answer = extract_substack(q.answer_url)

        elif kind == "linkedin":
            q.answer = extract_linkedin(q.answer_url)

        elif kind == "x":
            q.answer = extract_x(q.answer_url)

        elif kind == "youtube":
            q.answer = extract_youtube(q.answer_url)

        else:
            raise RuntimeError(
                "Unknown answer-source domain."
            )

        q.answer = remove_urls(q.answer)

        if len(q.answer.strip()) < 20:
            raise RuntimeError(
                "Extracted answer was suspiciously short."
            )

        q.extraction_status = "ok"

    except Exception as exc:
        safe_reason = remove_urls(str(exc))

        q.answer = (
            "[Automatic extraction failed. "
            "Edit this answer manually.]\n\n"
            f"Reason: {safe_reason}"
        )

        q.extraction_status = "failed"


# ============================================================
# OUTPUT HTML
# ============================================================

def js_string(value: str) -> str:
    """
    JSON is safe for embedding in JavaScript after escaping
    closing script tokens.
    """

    value = value.replace(
        "</script>",
        r"<\/script>",
    )

    return json.dumps(
        value,
        ensure_ascii=False,
    )


def build_html(questions: list[Question]) -> str:
    sections = []

    section_order = []

    for q in questions:
        if q.section not in section_order:
            section_order.append(q.section)

    for section in section_order:
        section_questions = [
            q for q in questions
            if q.section == section
        ]

        cards = []

        for q in section_questions:
            status = q.extraction_status

            if status == "ok":
                status_label = "Answer loaded"
            elif status == "failed":
                status_label = "Needs manual edit"
            else:
                status_label = "No supplied answer"

            cards.append(
                f"""
<article
    class="question-card"
    data-question-id="{html.escape(q.id)}"
    data-search="{html.escape((q.question + ' ' + q.answer).lower(), quote=True)}"
>
    <div class="question-row">
        <div class="question-text">
            {html.escape(q.question)}
        </div>

        <span class="status status-{html.escape(status)}">
            {html.escape(status_label)}
        </span>
    </div>

    <button
        type="button"
        class="toggle-answer"
        aria-expanded="false"
    >
        Show answer
    </button>

    <div class="answer-area" hidden>

        <div
            class="answer-display"
            data-answer-display
        ></div>

        <textarea
            class="answer-editor"
            data-answer-editor
            spellcheck="true"
            hidden
        ></textarea>

        <div class="answer-actions">
            <button
                type="button"
                class="edit-answer"
            >
                Edit answer
            </button>

            <button
                type="button"
                class="save-answer"
                hidden
            >
                Save answer
            </button>

            <button
                type="button"
                class="cancel-edit"
                hidden
            >
                Cancel
            </button>
        </div>
    </div>
</article>
"""
            )

        sections.append(
            f"""
<section class="topic">
    <h2>
        {html.escape(section)}
        <span>{len(section_questions)}</span>
    </h2>

    {''.join(cards)}
</section>
"""
        )

    initial_answers = {
        q.id: q.answer
        for q in questions
    }

    questions_by_id = {
        q.id: q.question
        for q in questions
    }

    # No source URLs are included in this object.
    initial_answers_json = json.dumps(
        initial_answers,
        ensure_ascii=False,
    ).replace("</script>", r"<\/script>")

    question_names_json = json.dumps(
        questions_by_id,
        ensure_ascii=False,
    ).replace("</script>", r"<\/script>")

    document = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta
    name="viewport"
    content="width=device-width, initial-scale=1"
>

<title>AI Engineering Interview Bank</title>

<style>
:root {{
    color-scheme: dark;

    --bg: #0d1117;
    --card: #161b22;
    --card2: #0d1117;
    --border: #30363d;
    --text: #e6edf3;
    --muted: #8b949e;
    --accent: #58a6ff;
    --good: #3fb950;
    --warn: #d29922;
    --bad: #f85149;
}}

* {{
    box-sizing: border-box;
}}

body {{
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family:
        system-ui,
        -apple-system,
        "Segoe UI",
        sans-serif;
    line-height: 1.55;
}}

header {{
    padding: 35px 20px 25px;
    border-bottom: 1px solid var(--border);
}}

.inner {{
    width: min(1100px, calc(100% - 32px));
    margin: auto;
}}

h1 {{
    margin: 0 0 8px;
}}

.subtitle {{
    color: var(--muted);
}}

.toolbar-wrap {{
    position: sticky;
    top: 0;
    z-index: 50;
    background: rgba(13, 17, 23, .96);
    border-bottom: 1px solid var(--border);
}}

.toolbar {{
    width: min(1100px, calc(100% - 32px));
    margin: auto;
    padding: 10px 0;
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
}}

input[type="search"] {{
    flex: 1;
    min-width: 220px;
}}

button,
input,
textarea {{
    font: inherit;
}}

button,
input[type="search"] {{
    border: 1px solid var(--border);
    background: var(--card);
    color: var(--text);
    border-radius: 7px;
    padding: 8px 11px;
}}

button {{
    cursor: pointer;
}}

button:hover {{
    border-color: var(--accent);
}}

main {{
    width: min(1100px, calc(100% - 32px));
    margin: 25px auto 80px;
}}

.topic {{
    margin-bottom: 48px;
}}

.topic h2 {{
    display: flex;
    justify-content: space-between;
    border-bottom: 1px solid var(--border);
    padding-bottom: 8px;
}}

.topic h2 span {{
    color: var(--muted);
    font-size: 14px;
}}

.question-card {{
    padding: 16px;
    margin: 10px 0;
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 10px;
}}

.question-card.search-hidden {{
    display: none;
}}

.question-row {{
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 15px;
}}

.question-text {{
    font-weight: 650;
    font-size: 17px;
}}

.status {{
    flex: 0 0 auto;
    border: 1px solid;
    border-radius: 999px;
    padding: 2px 7px;
    font-size: 11px;
}}

.status-ok {{
    color: var(--good);
}}

.status-failed {{
    color: var(--bad);
}}

.status-no-source {{
    color: var(--warn);
}}

.toggle-answer {{
    margin-top: 12px;
}}

.answer-area {{
    margin-top: 13px;
    border-left: 3px solid var(--accent);
    padding-left: 14px;
}}

.answer-display {{
    white-space: pre-wrap;
    overflow-wrap: anywhere;
}}

.answer-editor {{
    display: block;
    width: 100%;
    min-height: 350px;
    resize: vertical;
    border: 1px solid var(--border);
    border-radius: 7px;
    background: var(--card2);
    color: var(--text);
    padding: 12px;
    line-height: 1.55;
}}

.answer-actions {{
    margin-top: 12px;
    display: flex;
    gap: 7px;
}}

.saved-notice {{
    position: fixed;
    right: 16px;
    bottom: 16px;
    background: var(--card);
    border: 1px solid var(--good);
    padding: 10px 14px;
    border-radius: 7px;
    display: none;
}}

.file-input {{
    display: none;
}}

@media (max-width: 650px) {{
    .question-row {{
        display: block;
    }}

    .status {{
        display: inline-block;
        margin-top: 8px;
    }}
}}
</style>
</head>

<body>

<header>
    <div class="inner">
        <h1>AI Engineering Interview Bank</h1>

        <div class="subtitle">
            {len(questions)} questions.
            Answers can be edited and saved locally.
            No source links are included in this document.
        </div>
    </div>
</header>


<div class="toolbar-wrap">
    <div class="toolbar">

        <input
            id="search"
            type="search"
            placeholder="Search questions and answers"
        >

        <button id="expandAll" type="button">
            Expand all
        </button>

        <button id="collapseAll" type="button">
            Collapse all
        </button>

        <button id="saveAll" type="button">
            Save edits
        </button>

        <button id="exportEdits" type="button">
            Export edits
        </button>

        <button id="importEdits" type="button">
            Import edits
        </button>

        <button id="saveHtml" type="button">
            Save edited HTML
        </button>

        <input
            id="importFile"
            class="file-input"
            type="file"
            accept="application/json"
        >

    </div>
</div>


<main>
{''.join(sections)}
</main>


<div id="savedNotice" class="saved-notice">
    Saved.
</div>


<script>
const ORIGINAL_ANSWERS = {initial_answers_json};
const QUESTION_NAMES = {question_names_json};

const STORAGE_KEY =
    "ai-engineering-interview-answer-edits-v1";

let answers = loadSavedAnswers();


function loadSavedAnswers() {{
    let saved = {{}};

    try {{
        const raw = localStorage.getItem(STORAGE_KEY);

        if (raw) {{
            saved = JSON.parse(raw);
        }}
    }}
    catch (error) {{
        console.error(error);
    }}

    return {{
        ...ORIGINAL_ANSWERS,
        ...saved
    }};
}}


function persist() {{
    localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify(answers)
    );

    showSaved();
}}


function showSaved() {{
    const notice =
        document.getElementById("savedNotice");

    notice.style.display = "block";

    clearTimeout(showSaved.timer);

    showSaved.timer = setTimeout(() => {{
        notice.style.display = "none";
    }}, 1200);
}}


function initializeCards() {{
    document
        .querySelectorAll(".question-card")
        .forEach(card => {{

            const id = card.dataset.questionId;

            const display =
                card.querySelector("[data-answer-display]");

            const editor =
                card.querySelector("[data-answer-editor]");

            display.textContent =
                answers[id] || "";

            editor.value =
                answers[id] || "";
        }});
}}


document.addEventListener("click", event => {{

    const card =
        event.target.closest(".question-card");

    if (!card) {{
        return;
    }}

    const id = card.dataset.questionId;

    const area =
        card.querySelector(".answer-area");

    const display =
        card.querySelector("[data-answer-display]");

    const editor =
        card.querySelector("[data-answer-editor]");

    const edit =
        card.querySelector(".edit-answer");

    const save =
        card.querySelector(".save-answer");

    const cancel =
        card.querySelector(".cancel-edit");


    if (
        event.target.classList.contains(
            "toggle-answer"
        )
    ) {{
        const opening = area.hidden;

        area.hidden = !area.hidden;

        event.target.textContent =
            opening
                ? "Hide answer"
                : "Show answer";

        event.target.setAttribute(
            "aria-expanded",
            String(opening)
        );

        return;
    }}


    if (
        event.target.classList.contains(
            "edit-answer"
        )
    ) {{
        editor.value = answers[id] || "";

        display.hidden = true;
        editor.hidden = false;

        edit.hidden = true;
        save.hidden = false;
        cancel.hidden = false;

        editor.focus();

        return;
    }}


    if (
        event.target.classList.contains(
            "save-answer"
        )
    ) {{
        answers[id] = editor.value;

        display.textContent = answers[id];

        display.hidden = false;
        editor.hidden = true;

        edit.hidden = false;
        save.hidden = true;
        cancel.hidden = true;

        persist();

        return;
    }}


    if (
        event.target.classList.contains(
            "cancel-edit"
        )
    ) {{
        editor.value = answers[id] || "";

        display.hidden = false;
        editor.hidden = true;

        edit.hidden = false;
        save.hidden = true;
        cancel.hidden = true;
    }}
}});


document
    .getElementById("saveAll")
    .addEventListener("click", persist);


document
    .getElementById("expandAll")
    .addEventListener("click", () => {{

        document
            .querySelectorAll(".question-card")
            .forEach(card => {{

                const area =
                    card.querySelector(".answer-area");

                const toggle =
                    card.querySelector(".toggle-answer");

                area.hidden = false;
                toggle.textContent = "Hide answer";

                toggle.setAttribute(
                    "aria-expanded",
                    "true"
                );
            }});
    }});


document
    .getElementById("collapseAll")
    .addEventListener("click", () => {{

        document
            .querySelectorAll(".question-card")
            .forEach(card => {{

                const area =
                    card.querySelector(".answer-area");

                const toggle =
                    card.querySelector(".toggle-answer");

                area.hidden = true;
                toggle.textContent = "Show answer";

                toggle.setAttribute(
                    "aria-expanded",
                    "false"
                );
            }});
    }});


document
    .getElementById("search")
    .addEventListener("input", event => {{

        const query =
            event.target.value
                .trim()
                .toLowerCase();

        document
            .querySelectorAll(".question-card")
            .forEach(card => {{

                const id =
                    card.dataset.questionId;

                const haystack =
                    (
                        QUESTION_NAMES[id]
                        + "\\n"
                        + (answers[id] || "")
                    ).toLowerCase();

                card.classList.toggle(
                    "search-hidden",
                    query
                    && !haystack.includes(query)
                );
            }});

        document
            .querySelectorAll(".topic")
            .forEach(section => {{

                const visible =
                    section.querySelector(
                        ".question-card:not(.search-hidden)"
                    );

                section.hidden = !visible;
            }});
    }});


function downloadBlob(content, filename, type) {{
    const blob = new Blob(
        [content],
        {{type}}
    );

    const objectUrl =
        URL.createObjectURL(blob);

    const anchor =
        document.createElement("a");

    anchor.href = objectUrl;
    anchor.download = filename;

    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();

    URL.revokeObjectURL(objectUrl);
}}


document
    .getElementById("exportEdits")
    .addEventListener("click", () => {{

        downloadBlob(
            JSON.stringify(
                answers,
                null,
                2
            ),
            "ai_interview_answer_edits.json",
            "application/json"
        );
    }});


document
    .getElementById("importEdits")
    .addEventListener("click", () => {{
        document
            .getElementById("importFile")
            .click();
    }});


document
    .getElementById("importFile")
    .addEventListener(
        "change",
        async event => {{

            const file =
                event.target.files[0];

            if (!file) {{
                return;
            }}

            const imported =
                JSON.parse(await file.text());

            answers = {{
                ...answers,
                ...imported
            }};

            persist();
            initializeCards();

            event.target.value = "";
        }
    );


document
    .getElementById("saveHtml")
    .addEventListener("click", () => {{

        persist();

        /*
         * Embed current edits into a copied standalone HTML.
         * This makes the manually edited version portable,
         * rather than depending only on localStorage.
         */

        const clone =
            document.documentElement.cloneNode(true);

        clone
            .querySelectorAll(".answer-editor")
            .forEach(editor => {{
                const card =
                    editor.closest(".question-card");

                const id =
                    card.dataset.questionId;

                editor.textContent =
                    answers[id] || "";
            }});

        clone
            .querySelectorAll(
                "[data-answer-display]"
            )
            .forEach(display => {{
                const card =
                    display.closest(".question-card");

                const id =
                    card.dataset.questionId;

                display.textContent =
                    answers[id] || "";
            }});

        let serialized =
            "<!doctype html>\\n"
            + clone.outerHTML;

        /*
         * Add an embedded seed containing the current edits
         * by replacing ORIGINAL_ANSWERS in the copied file.
         */

        const replacement =
            "const ORIGINAL_ANSWERS = "
            + JSON.stringify(answers)
            + ";";

        serialized =
            serialized.replace(
                /const ORIGINAL_ANSWERS = \\{{[\\s\\S]*?\\}};/,
                replacement
            );

        downloadBlob(
            serialized,
            "ai_engineering_interview_questions_edited.html",
            "text/html"
        );
    }});


initializeCards();
</script>

</body>
</html>
"""

    # This is intentionally not an f-string. CSS and JavaScript contain
    # many braces, so normalize the doubled braces first, then inject data.
    document = document.replace("{{", "{")
    document = document.replace("}}", "}")

    document = document.replace(
        "{len(questions)}",
        str(len(questions)),
    )
    document = document.replace(
        "{''.join(sections)}",
        "".join(sections),
    )
    document = document.replace(
        "{initial_answers_json}",
        initial_answers_json,
    )
    document = document.replace(
        "{question_names_json}",
        question_names_json,
    )

    return document


# ============================================================
# SECURITY / NO-URL VERIFICATION
# ============================================================

FORBIDDEN_HTML_TEXT = [
    re.compile(r"https?://", re.I),
    re.compile(r"\bwww\.", re.I),
]


def verify_output(document: str):
    if not isinstance(document, str):
        raise TypeError(
            "build_html() did not return a string. "
            f"Got: {type(document).__name__}"
        )

    for regex in FORBIDDEN_HTML_TEXT:
        match = regex.search(document)

        if match:
            left = max(0, match.start() - 100)
            right = min(len(document), match.end() + 100)
            context = document[left:right]

            raise RuntimeError(
                "\nURL-FREE VERIFICATION FAILED.\n"
                f"Matched: {regex.pattern}\n"
                f"Context:\n{context}"
            )

    soup = BeautifulSoup(document, "html.parser")

    forbidden_attributes = {
        "href",
        "src",
        "action",
        "formaction",
        "poster",
        "data",
    }

    for tag in soup.find_all(True):
        if tag.name and tag.name.casefold() == "a":
            raise RuntimeError(
                "\nURL-FREE VERIFICATION FAILED.\n"
                "The generated document contains an <a> element."
            )

        for attribute in forbidden_attributes:
            if tag.has_attr(attribute):
                raise RuntimeError(
                    "\nURL-FREE VERIFICATION FAILED.\n"
                    f"Element <{tag.name}> contains forbidden "
                    f"attribute {attribute!r}."
                )



# ============================================================
# MAIN
# ============================================================

def main():
    clone_or_update()

    questions = parse_readme()

    print()
    print(
        f"[parse] Found {len(questions)} questions"
    )

    supplied = sum(
        bool(q.answer_url or q.answer)
        for q in questions
    )

    print(
        f"[parse] {supplied} have supplied answer material"
    )

    print()

    # Persistent cache:
    # each unique answer source is saved immediately after extraction.
    cache = load_disk_cache()

    if cache:
        print(
            f"[cache] Loaded {len(cache)} previously scraped "
            f"source(s) from {CACHE_FILE}"
        )

    for i, q in enumerate(
        questions,
        start=1,
    ):
        print(
            f"[{i:03d}/{len(questions):03d}] ",
            end="",
        )

        if q.answer_url and q.answer_url in cache:
            cached = cache[q.answer_url]

            q.answer = remove_urls(cached.get("answer", ""))
            q.source_type = cached.get("source_type", "unknown")
            q.extraction_status = cached.get("status", "failed")

            print(
                f"disk cache     | "
                f"{q.question[:65]}"
            )

            continue

        if not q.answer_url:
            extract_answer(q)

            if q.answer:
                print(
                    f"{q.source_type:14s} | "
                    f"{q.question[:65]}"
                )
            else:
                print(
                    f"no source      | "
                    f"{q.question[:65]}"
                )

            continue

        extract_answer(q)

        cache[q.answer_url] = {
            "answer": remove_urls(q.answer),
            "source_type": q.source_type,
            "status": q.extraction_status,
        }

        save_disk_cache(cache)

    document = build_html(questions)

    # This is important because URLs used during scraping
    # must never migrate into the final output.
    verify_output(document)

    OUTPUT_HTML.write_text(
        document,
        encoding="utf-8",
    )

    ok = sum(
        q.extraction_status == "ok"
        for q in questions
    )

    failed = sum(
        q.extraction_status == "failed"
        for q in questions
    )

    missing = sum(
        q.extraction_status in {
            "missing",
            "no-source",
        }
        for q in questions
    )

    print()
    print("=" * 70)
    print("BUILD COMPLETE")
    print("=" * 70)
    print(f"Questions                  : {len(questions)}")
    print(f"Answers extracted          : {ok}")
    print(f"Extraction failures        : {failed}")
    print(f"No source answer in README : {missing}")
    print(f"Unique external sources    : {len(cache)}")
    print()
    print("URL-free HTML verification : PASSED")
    print()
    print(
        "HTML:",
        OUTPUT_HTML.resolve(),
    )


if __name__ == "__main__":
    main()