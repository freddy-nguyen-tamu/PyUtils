#!/usr/bin/env python3
"""
Scrape NeetCode problems + solutions from a list page and generate a LaTeX file
for Overleaf.

What this version extracts per problem:
- title
- problem statement
- solution text

Run:
    pip install playwright
    playwright install chromium
    python neetcode.py --headless --limit 3 --output test.tex
    python neetcode.py --headless --output blind75_solutions.tex
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
from urllib.parse import urljoin, urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


DEFAULT_START_URL = "https://neetcode.io/practice/practice/blind75"


@dataclass
class ProblemData:
    title: str
    url: str
    problem_text: str
    solution_text: str


def debug(enabled: bool, message: str) -> None:
    if enabled:
        print(f"[debug] {message}", flush=True)


def latex_escape(text: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(ch, ch) for ch in text)


def normalize_whitespace(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00a0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def text_to_latex_paragraphs(text: str) -> str:
    text = normalize_whitespace(text)
    if not text:
        return "No text found."

    blocks = [b.strip() for b in text.split("\n\n") if b.strip()]
    out: List[str] = []

    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue

        if len(lines) == 1 and len(lines[0]) <= 80 and not lines[0].endswith("."):
            out.append(r"\paragraph{" + latex_escape(lines[0]) + "}")
        else:
            joined = " ".join(lines)
            out.append(latex_escape(joined))
            out.append("")

    return "\n".join(out).strip()


def build_tex_document(title: str, problems: List[ProblemData]) -> str:
    parts: List[str] = [
        r"\documentclass[11pt]{article}",
        r"\usepackage[margin=1in]{geometry}",
        r"\usepackage[T1]{fontenc}",
        r"\usepackage[utf8]{inputenc}",
        r"\usepackage{hyperref}",
        r"\usepackage{parskip}",
        r"\title{" + latex_escape(title) + "}",
        r"\author{}",
        r"\date{}",
        r"\begin{document}",
        r"\maketitle",
        r"\tableofcontents",
        r"\newpage",
    ]

    for idx, problem in enumerate(problems, start=1):
        parts.append(r"\section{" + latex_escape(f"{idx}. {problem.title}") + "}")
        parts.append(r"\textbf{Source: } \url{" + problem.url + "}")
        parts.append("")
        parts.append(r"\subsection*{Problem}")
        parts.append(text_to_latex_paragraphs(problem.problem_text))
        parts.append("")
        parts.append(r"\subsection*{Solution}")
        parts.append(text_to_latex_paragraphs(problem.solution_text))
        parts.append("")

    parts.append(r"\end{document}")
    return "\n".join(parts)


def safe_title_from_page(page_title: str) -> str:
    page_title = normalize_whitespace(page_title)
    if not page_title:
        return "NeetCode Solutions"
    return re.sub(r"\s+", " ", page_title)


def dismiss_noise(page, verbose: bool) -> None:
    debug(verbose, "Trying to dismiss popups/modals if present")

    possible_buttons = [
        "button:has-text('Close')",
        "button:has-text('close')",
        "button:has-text('Got it')",
        "button:has-text('Continue')",
        "button:has-text('Skip')",
        "button[aria-label='Close']",
    ]

    for selector in possible_buttons:
        try:
            matches = page.locator(selector)
            count = min(matches.count(), 3)
            for i in range(count):
                try:
                    matches.nth(i).click(timeout=500)
                    debug(verbose, f"Dismissed popup using selector: {selector}")
                    page.wait_for_timeout(200)
                except Exception:
                    pass
        except Exception:
            pass


def maybe_expand_all(page, verbose: bool) -> None:
    debug(verbose, "Trying to expand the problem list")

    expand_selectors = [
        "text=Expand",
        "span:has-text('Expand')",
    ]

    for selector in expand_selectors:
        try:
            loc = page.locator(selector).first
            if loc.is_visible(timeout=1500):
                debug(verbose, f"Clicking expand control: {selector}")
                loc.click(timeout=3000)
                page.wait_for_timeout(1200)
                break
        except Exception:
            pass

    accordion_selectors = [
        "button.accordion",
        "button:has-text('Arrays & Hashing')",
        "button:has-text('Two Pointers')",
        "button:has-text('Sliding Window')",
        "button:has-text('Stack')",
        "button:has-text('Binary Search')",
        "button:has-text('Linked List')",
        "button:has-text('Trees')",
        "button:has-text('Heap / Priority Queue')",
        "button:has-text('Backtracking')",
        "button:has-text('Tries')",
        "button:has-text('Graphs')",
        "button:has-text('Advanced Graphs')",
    ]

    for selector in accordion_selectors:
        try:
            locs = page.locator(selector)
            count = min(locs.count(), 50)
            if count > 0:
                debug(verbose, f"Found {count} accordion candidates for selector: {selector}")
            for i in range(count):
                try:
                    locs.nth(i).click(timeout=1000)
                    page.wait_for_timeout(150)
                except Exception:
                    pass
        except Exception:
            pass

    page.wait_for_timeout(1500)


def extract_problem_links(page, base_url: str, verbose: bool) -> List[str]:
    debug(verbose, "Extracting candidate problem links from the list page")

    maybe_expand_all(page, verbose)

    candidates_raw = page.eval_on_selector_all(
        "*",
        """
        els => {
            const out = [];
            for (const el of els) {
                const attrs = [
                    el.getAttribute('href'),
                    el.getAttribute('routerLink'),
                    el.getAttribute('routerlink'),
                    el.getAttribute('ng-reflect-router-link'),
                    el.getAttribute('data-href'),
                ];
                for (const v of attrs) {
                    if (v) out.push(v);
                }
            }
            return out;
        }
        """
    )

    debug(verbose, f"Collected {len(candidates_raw)} raw route-like attributes")

    html_text = page.content()
    html_matches = re.findall(
        r'/(?:problems|problem)/[a-z0-9-]+(?:[/?"][^\\s"\']*)?',
        html_text,
    )
    debug(verbose, f"Regex fallback found {len(html_matches)} problem-like paths in HTML")

    all_raw = list(candidates_raw) + html_matches

    good: List[str] = []
    seen: set[str] = set()

    for raw in all_raw:
        if not raw:
            continue

        full = urljoin(base_url, raw.strip())
        parsed = urlparse(full)

        if parsed.netloc and parsed.netloc != "neetcode.io":
            continue

        if not re.search(r"^/problems/[a-z0-9-]+(?:/.*)?$", parsed.path):
            continue

        bad_parts = ["/submissions", "/notes", "/editorial"]
        if any(part in parsed.path for part in bad_parts):
            continue

        normalized = f"https://neetcode.io{parsed.path}"
        if parsed.query:
            normalized += f"?{parsed.query}"

        if normalized not in seen:
            seen.add(normalized)
            good.append(normalized)

    debug(verbose, f"Filtered down to {len(good)} likely problem URLs")
    if good[:10]:
        debug(verbose, "First few problem URLs:")
        for url in good[:10]:
            debug(verbose, f"  {url}")

    return good


def extract_problem_title(page, verbose: bool) -> str:
    selectors = [
        "h1",
        "h2",
        "[class*='title']",
    ]

    for selector in selectors:
        try:
            loc = page.locator(selector).first
            txt = normalize_whitespace(loc.inner_text(timeout=1000))
            if txt and len(txt) < 120:
                debug(verbose, f"Problem title from selector {selector}: {txt}")
                return txt
        except Exception:
            pass

    title = safe_title_from_page(page.title())
    title = re.sub(r"\s*-\s*NeetCode.*$", "", title).strip()
    return title or "Untitled Problem"


def click_solution_tab(page, verbose: bool) -> bool:
    debug(verbose, "Looking for the Solution tab/button")

    solution_selectors = [
        "text=Solution",
        "span:has-text('Solution')",
        "[role='tab']:has-text('Solution')",
        "button:has-text('Solution')",
        "a:has-text('Solution')",
    ]

    for selector in solution_selectors:
        try:
            loc = page.locator(selector).first
            if loc.is_visible(timeout=1500):
                debug(verbose, f"Clicking solution control with selector: {selector}")
                loc.click(timeout=3000)
                page.wait_for_timeout(1200)
                return True
        except Exception:
            continue

    debug(verbose, "Could not click Solution tab with known selectors")
    return False


def extract_problem_text(page, title: str, verbose: bool) -> str:
    """
    Extract problem statement BEFORE opening the solution tab.
    We try to find the main problem area and strip obvious non-problem UI text.
    """
    debug(verbose, "Trying to extract problem statement")

    candidate_selectors = [
        "main",
        "article",
        "section",
        "[class*='problem']",
        "[class*='content']",
        "body",
    ]

    best_text = ""

    for selector in candidate_selectors:
        try:
            texts = page.eval_on_selector_all(
                selector,
                """
                els => els.map(el => (el.innerText || '').trim()).filter(Boolean)
                """
            )
        except Exception:
            continue

        for text in texts:
            cleaned = normalize_whitespace(text)
            if not cleaned:
                continue

            # Prefer content that mentions examples / constraints / input-output style wording
            score = 0
            for needle in [
                "Example",
                "Constraints",
                "Input",
                "Output",
                "Given",
                "Return",
            ]:
                if needle.lower() in cleaned.lower():
                    score += 1

            # Prefer content containing the title too
            if title and title.lower() in cleaned.lower():
                score += 2

            # Reject clearly gigantic page-level dumps unless they still look promising
            if len(cleaned) > 20000 and score < 2:
                continue

            if score >= 2 and len(cleaned) > len(best_text):
                best_text = cleaned
            elif not best_text and len(cleaned) > 300:
                best_text = cleaned

        if best_text:
            debug(verbose, f"Current best problem text length after selector {selector}: {len(best_text)} chars")

    if not best_text:
        return "No problem text could be extracted."

    # Trim obvious site chrome / navigation noise
    lines = [line.strip() for line in best_text.splitlines()]
    filtered: List[str] = []

    noise_patterns = [
        r"^Back$",
        r"^Hints?$",
        r"^Submissions$",
        r"^Solution$",
        r"^Editorial$",
        r"^Code$",
        r"^Run$",
        r"^Submit$",
        r"^Python$",
        r"^Java$",
        r"^C\+\+$",
        r"^JavaScript$",
        r"^Sign in$",
        r"^Get Pro$",
        r"^Practice$",
        r"^Roadmap$",
        r"^Courses$",
        r"^Pro$",
        r"^Menu$",
    ]

    for line in lines:
        if not line:
            filtered.append("")
            continue

        is_noise = any(re.fullmatch(pat, line, flags=re.IGNORECASE) for pat in noise_patterns)
        if is_noise:
            continue

        filtered.append(line)

    cleaned = normalize_whitespace("\n".join(filtered))

    # If solution-related headings appear later in the block, cut there.
    split_markers = [
        "\nSolution\n",
        "\nEditorial\n",
        "\nSubmissions\n",
    ]
    for marker in split_markers:
        idx = cleaned.find(marker)
        if idx != -1:
            cleaned = cleaned[:idx].strip()

    return cleaned or "No problem text could be extracted."


def extract_solution_text(page, verbose: bool) -> str:
    debug(verbose, "Trying to extract solution content")

    candidate_selectors = [
        "app-solution-tab",
        "[class*='solution']",
        "section",
        "article",
        ".content",
        "main",
        "body",
    ]

    best_text = ""

    for selector in candidate_selectors:
        try:
            texts = page.eval_on_selector_all(
                selector,
                """
                els => els.map(el => (el.innerText || '').trim()).filter(Boolean)
                """
            )
        except Exception:
            continue

        for text in texts:
            cleaned = normalize_whitespace(text)
            if not cleaned:
                continue

            score = 0
            for needle in [
                "Intuition",
                "Algorithm",
                "Time Complexity",
                "Space Complexity",
                "Approach",
                "Solution",
            ]:
                if needle.lower() in cleaned.lower():
                    score += 1

            if len(cleaned) > len(best_text) and (score >= 1 or len(cleaned) > 500):
                best_text = cleaned

        if best_text:
            debug(
                verbose,
                f"Current best solution text length after selector {selector}: {len(best_text)} chars",
            )

    if not best_text:
        return "No solution text could be extracted."

    lines = [line.strip() for line in best_text.splitlines()]
    filtered: List[str] = []

    noise_patterns = [
        r"^Back$",
        r"^Hints?$",
        r"^Submissions$",
        r"^Editorial$",
        r"^Code$",
        r"^Run$",
        r"^Submit$",
        r"^Python$",
        r"^Java$",
        r"^C\+\+$",
        r"^JavaScript$",
        r"^Sign in$",
        r"^Get Pro$",
        r"^Practice$",
        r"^Roadmap$",
        r"^Courses$",
        r"^Pro$",
        r"^Menu$",
    ]

    for line in lines:
        if not line:
            filtered.append("")
            continue

        is_noise = any(re.fullmatch(pat, line, flags=re.IGNORECASE) for pat in noise_patterns)
        if is_noise:
            continue

        filtered.append(line)

    return normalize_whitespace("\n".join(filtered)) or "No solution text could be extracted."


def scrape_problem(page, url: str, verbose: bool) -> Optional[ProblemData]:
    debug(verbose, f"Opening problem page: {url}")

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1500)
    except PlaywrightTimeoutError:
        debug(verbose, f"Timeout loading problem page: {url}")
        return None

    dismiss_noise(page, verbose)

    debug(verbose, f"Raw page title: {page.title()}")
    title = extract_problem_title(page, verbose)

    # Extract problem statement BEFORE clicking solution
    problem_text = extract_problem_text(page, title, verbose)
    debug(verbose, f"Problem text length: {len(problem_text)}")

    clicked = click_solution_tab(page, verbose)
    if not clicked:
        debug(verbose, "Solution tab was not clicked; continuing anyway")

    dismiss_noise(page, verbose)
    page.wait_for_timeout(800)

    solution_text = extract_solution_text(page, verbose)
    debug(verbose, f"Solution text length: {len(solution_text)}")

    title = title.strip() or "Untitled Problem"

    debug(verbose, f"Final normalized title: {title}")

    return ProblemData(
        title=title,
        url=url,
        problem_text=problem_text or "No problem text could be extracted.",
        solution_text=solution_text or "No solution text could be extracted.",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scrape NeetCode problems and solutions into a LaTeX file for Overleaf."
    )
    parser.add_argument(
        "--start-url",
        default=DEFAULT_START_URL,
        help="NeetCode list page URL (default: Blind 75)",
    )
    parser.add_argument(
        "--output",
        default="neetcode_solutions.tex",
        help="Output .tex filename",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit number of problems to scrape (0 = all)",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browser headlessly",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Reduce debug logging",
    )
    args = parser.parse_args()

    verbose = not args.quiet

    debug(verbose, "Starting scraper")
    debug(verbose, f"Start URL: {args.start_url}")
    debug(verbose, f"Output file: {args.output}")
    debug(verbose, f"Limit: {args.limit if args.limit else 'ALL'}")
    debug(verbose, f"Headless: {args.headless}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        context = browser.new_context()
        page = context.new_page()

        debug(verbose, "Opening list page")
        try:
            page.goto(args.start_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2500)
        except PlaywrightTimeoutError:
            debug(verbose, "Timeout while loading start URL")
            browser.close()
            return 1

        dismiss_noise(page, verbose)
        page.wait_for_timeout(1500)
        maybe_expand_all(page, verbose)

        page_title = safe_title_from_page(page.title())
        debug(verbose, f"List page title: {page_title}")

        problem_links = extract_problem_links(page, args.start_url, verbose)

        if not problem_links:
            debug(verbose, "No problem links found. Exiting.")
            browser.close()
            return 2

        if args.limit > 0:
            problem_links = problem_links[: args.limit]
            debug(verbose, f"Trimmed problem list to {len(problem_links)} URLs")

        problems: List[ProblemData] = []

        for idx, problem_url in enumerate(problem_links, start=1):
            debug(verbose, f"--- [{idx}/{len(problem_links)}] {problem_url} ---")
            item = scrape_problem(page, problem_url, verbose)
            if item is not None:
                problems.append(item)
                debug(verbose, f"Saved: {item.title}")
            else:
                debug(verbose, f"Skipped failed problem URL: {problem_url}")

        browser.close()

    if not problems:
        debug(verbose, "No problem data collected. Nothing to write.")
        return 3

    doc_title = f"{page_title} Problems and Solutions"
    tex = build_tex_document(doc_title, problems)

    output_path = Path(args.output)
    output_path.write_text(tex, encoding="utf-8")

    debug(verbose, f"Wrote LaTeX output to: {output_path.resolve()}")
    debug(verbose, f"Total problems written: {len(problems)}")
    print(f"\nDone. Wrote {len(problems)} problems to {output_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())