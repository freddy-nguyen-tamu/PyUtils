import asyncio
import json
import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

from playwright.async_api import (
    async_playwright,
    Page,
    Request,
    Response,
    Error as PWError,
)

# =============================================================================
# EDIT THESE TWO LISTS ONLY (keep same length)
# =============================================================================
URLS = [
    # A + B + C are all meant to run on the PROFILE page.
    "https://www.instagram.com/pyoapple/",
    "https://www.instagram.com/pyoapple/",
    "https://www.instagram.com/pyoapple/",
]

PROBES_TO_URL = [
    # -------------------------
    # A) Fix web_profile_info test (stop 400)
    # -------------------------
    r"""
(async () => {
  const username = "pyoapple";
  const url = `https://i.instagram.com/api/v1/users/web_profile_info/?username=${encodeURIComponent(username)}`;

  const csrftoken = (document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/)||[])[1] || "";
  const xIgAppId = "936619743392459"; // from your appIdGuessFromScripts

  const r = await fetch(url, {
    credentials: "include",
    headers: {
      "x-ig-app-id": xIgAppId,
      "x-csrftoken": csrftoken,
      "x-requested-with": "XMLHttpRequest"
    }
  });

  console.log("status:", r.status);
  const txt = await r.text();
  console.log("body_prefix:", txt.slice(0, 200));
})();
""",
    # -------------------------
    # B) Install hooks to log real GraphQL requests (doc_id + variables)
    # -------------------------
    r"""
(() => {
  if (window.__ig_probe_installed__) {
    console.log("probe already installed");
    return;
  }
  window.__ig_probe_installed__ = true;

  const redact = (s) => {
    if (!s) return s;
    return s.length > 1200 ? (s.slice(0,1200) + "…<truncated>") : s;
  };

  const parseBody = async (body) => {
    if (!body) return null;
    if (typeof body === "string") return redact(body);
    if (body instanceof URLSearchParams) return redact(body.toString());
    if (body instanceof FormData) {
      const o = {};
      for (const [k,v] of body.entries()) o[k] = String(v);
      return o;
    }
    try { return String(body); } catch { return null; }
  };

  // Hook fetch
  const origFetch = window.fetch;
  window.fetch = async (...args) => {
    const [input, init] = args;
    const url = typeof input === "string" ? input : input.url;
    const method = init?.method || "GET";
    const body = await parseBody(init?.body);

    if (url.includes("/graphql/query")) {
      console.log("[IG graphql/fetch]", { url, method, headers: init?.headers, body });
    }
    return origFetch(...args);
  };

  // Hook XHR
  const origOpen = XMLHttpRequest.prototype.open;
  const origSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function(method, url, ...rest) {
    this.__ig_url__ = url;
    this.__ig_method__ = method;
    return origOpen.call(this, method, url, ...rest);
  };
  XMLHttpRequest.prototype.send = function(body) {
    if (String(this.__ig_url__ || "").includes("/graphql/query")) {
      console.log("[IG graphql/xhr]", {
        url: this.__ig_url__,
        method: this.__ig_method__,
        body: (typeof body === "string" ? redact(body) : body)
      });
    }
    return origSend.call(this, body);
  };

  console.log("IG probe installed. Now scroll profile and open a post.");
})();
""",
    # -------------------------
    # C) Better DOM fallback: anchor count + sample hrefs
    # -------------------------
    r"""
(() => {
  const anchors = [...document.querySelectorAll('a[href*="/p/"], a[href*="/reel/"]')];
  console.log("anchors_found:", anchors.length);
  console.log(anchors.slice(0,10).map(a => a.href));
})();
""",
]

# =============================================================================
# Settings (tuned to be faster + only pause once)
# =============================================================================
OUTPUT_TXT = "probe_transcript.txt"
USER_DATA_DIR = os.path.abspath("./pw_user_data")
HEADLESS = False

# Faster waits
NAV_WAIT_MS = 800
AFTER_SNIPPET_WAIT_MS = 800
MAX_JSON_EVENTS_PER_URL = 250

# Only pause once at the start (for login / manual setup)
PAUSE_ONCE_AT_START = True

# "Settle" is often the biggest slowdown. Keep it short and non-blocking.
SETTLE_TIMEOUT_MS = 4_000


# =============================================================================
# Helpers
# =============================================================================
def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def indent_block(s: str, prefix: str = "    ") -> str:
    return "\n".join(prefix + line for line in s.splitlines())


def safe_json(obj: Any, max_len: int = 1800) -> str:
    try:
        txt = json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        txt = str(obj)
    return txt if len(txt) <= max_len else txt[:max_len] + "\n... [truncated] ..."


def header_names_only(headers: Dict[str, str]) -> List[str]:
    return sorted({k.lower() for k in headers.keys()})


class Transcript:
    def __init__(self, filepath: str):
        self.filepath = filepath
        self.lines: List[str] = []

    def add(self, s: str = "") -> None:
        self.lines.append(s)

    def section(self, title: str) -> None:
        self.add("")
        self.add("=" * 80)
        self.add(title)
        self.add("=" * 80)

    def flush(self) -> None:
        with open(self.filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(self.lines) + "\n")


async def safe_goto(page: Page, url: str, transcript: Transcript, attempts: int = 4) -> None:
    last_err: Optional[Exception] = None
    for i in range(1, attempts + 1):
        try:
            print(f"GOTO: {url} (attempt {i}/{attempts})")
            transcript.add(f"[{now_iso()}] GOTO attempt {i}/{attempts}: {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            await page.wait_for_timeout(NAV_WAIT_MS)
            return
        except PWError as e:
            last_err = e
            msg = str(e)
            transcript.add(f"[{now_iso()}] goto error: {msg}")
            # Common IG behavior: navigation interrupts due to redirects / SPA updates.
            if "interrupted by another navigation" in msg.lower():
                await page.wait_for_timeout(700)
                continue
            await page.wait_for_timeout(700)
    raise last_err or RuntimeError("safe_goto failed without exception?")


async def settle_page_fast(page: Page, transcript: Transcript, timeout_ms: int = SETTLE_TIMEOUT_MS) -> None:
    """
    Fast-ish settle:
      - do NOT wait long for networkidle (Instagram often never goes idle)
      - just try briefly; otherwise continue
    """
    transcript.add(f"[{now_iso()}] Settling page (fast)...")
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    except Exception:
        pass
    await page.wait_for_timeout(300)
    transcript.add(f"[{now_iso()}] Settled. Current URL: {page.url}")


async def run_probe(page: Page, transcript: Transcript, label: str, code: str) -> None:
    transcript.add(f"\n[{now_iso()}] RUN PROBE: {label}")
    transcript.add("CODE:")
    transcript.add(indent_block(code.strip()))
    transcript.flush()

    try:
        await page.evaluate(code)
    except Exception as e:
        transcript.add(f"!! Probe execution error: {repr(e)}")

    await page.wait_for_timeout(AFTER_SNIPPET_WAIT_MS)
    transcript.flush()


async def attach_listeners(page: Page, transcript: Transcript, json_events: List[Dict[str, Any]]) -> None:
    page.on("console", lambda msg: transcript.add(f"[console.{msg.type}] {msg.text}"))
    page.on("pageerror", lambda err: transcript.add(f"[pageerror] {err}"))

    async def on_response(resp: Response):
        try:
            req: Request = resp.request
            if req.resource_type not in ("xhr", "fetch"):
                return

            ctype = (resp.headers.get("content-type") or "").lower()
            if "json" not in ctype:
                return

            if len(json_events) >= MAX_JSON_EVENTS_PER_URL:
                return

            json_events.append(
                {
                    "time": now_iso(),
                    "url": resp.url,
                    "method": req.method,
                    "status": resp.status,
                    "request_header_names": header_names_only(req.headers),
                    "response_header_names": header_names_only(resp.headers),
                }
            )
        except Exception as e:
            transcript.add(f"[network_listener_error] {repr(e)}")

    page.on("response", on_response)


# =============================================================================
# Main
# =============================================================================
async def main():
    if len(URLS) != len(PROBES_TO_URL):
        raise ValueError(
            f"URLS length ({len(URLS)}) must equal PROBES_TO_URL length ({len(PROBES_TO_URL)})."
        )

    transcript = Transcript(OUTPUT_TXT)
    transcript.section("Generic URL Probe Transcript (A/B/C)")
    transcript.add(f"Started: {now_iso()}")
    transcript.add(f"Headless: {HEADLESS}")
    transcript.add(f"User data dir: {USER_DATA_DIR}")
    transcript.add(f"Total URL/probe pairs: {len(URLS)}")
    transcript.add(f"Pause once at start: {PAUSE_ONCE_AT_START}")
    transcript.flush()

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            USER_DATA_DIR,
            headless=HEADLESS,
            viewport={"width": 1280, "height": 850},
        )
        page = context.pages[0] if context.pages else await context.new_page()

        json_events: List[Dict[str, Any]] = []
        await attach_listeners(page, transcript, json_events)

        # Navigate once up-front to the first URL so you can login / do manual steps.
        if PAUSE_ONCE_AT_START and URLS:
            first_url = URLS[0]
            transcript.section("INITIAL SETUP")
            transcript.add(f"[{now_iso()}] Initial open: {first_url}")
            transcript.flush()

            await safe_goto(page, first_url, transcript)
            await settle_page_fast(page, transcript)

            print("\n============================================================")
            print("Initial setup pause (ONLY ONCE).")
            print("Do any manual steps now (login, hard refresh, scroll, click, etc.).")
            print("When you're done and ready for the script to run ALL probes, press ENTER.")
            print("============================================================\n")
            input("Press ENTER to start running probes... ")
            transcript.add(f"[{now_iso()}] User finished initial setup pause.")
            transcript.flush()

        for idx, (url, probe_js) in enumerate(zip(URLS, PROBES_TO_URL), start=1):
            transcript.section(f"PAIR #{idx}")
            transcript.add(f"[{now_iso()}] OPEN URL: {url}")
            transcript.flush()

            # If it's the same URL as current, don't re-navigate (saves time)
            if page.url.rstrip("/") != url.rstrip("/"):
                await safe_goto(page, url, transcript)
                await settle_page_fast(page, transcript)
            else:
                transcript.add(f"[{now_iso()}] Skipping goto (already on URL). Current: {page.url}")
                transcript.flush()

            await run_probe(page, transcript, f"Probe for {url} (pair #{idx})", probe_js)

        transcript.section("SUMMARY")
        transcript.add(f"Finished: {now_iso()}")
        transcript.add(f"Total JSON Fetch/XHR events captured: {len(json_events)}")
        transcript.add(f"Transcript file: {OUTPUT_TXT}")
        transcript.flush()

        print("Closing browser/context...")
        try:
            await asyncio.wait_for(context.close(), timeout=10)
        except Exception as e:
            print(f"Context close timed out or errored: {e!r}")

        try:
            await asyncio.wait_for(p.stop(), timeout=5)
        except Exception as e:
            print(f"Playwright stop timed out or errored: {e!r}")

    print(f"Wrote transcript to: {OUTPUT_TXT}")
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())