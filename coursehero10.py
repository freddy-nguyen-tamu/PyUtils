import os
import time
import re
import requests

# ---------- Config ----------
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

# Load Groq API keys in this order:
# GROQ_API_KEY, GROQ_API_KEY1, GROQ_API_KEY2, ..., GROQ_API_KEY9
GROQ_API_KEYS = []

base_key = os.getenv("GROQ_API_KEY")
if base_key:
    GROQ_API_KEYS.append(("GROQ_API_KEY", base_key))

for i in range(1, 10):
    key_name = f"GROQ_API_KEY{i}"
    key_value = os.getenv(key_name)
    if key_value:
        GROQ_API_KEYS.append((key_name, key_value))

if not GROQ_API_KEYS:
    raise SystemExit(
        "No Groq API keys found. Set GROQ_API_KEY and optionally "
        "GROQ_API_KEY1 through GROQ_API_KEY9 in the environment."
    )

print(
    f"Loaded {len(GROQ_API_KEYS)} Groq API key(s): "
    + ", ".join(name for name, _ in GROQ_API_KEYS)
)


def make_groq_headers(api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


# Models to rotate through.
# Put your highest TPM models first.
GROQ_MODELS = [
    "meta-llama/llama-4-scout-17b-16e-instruct",  # 30K TPM
    "groq/compound",                              # 70K TPM
    "groq/compound-mini",                         # 70K TPM
    "llama-3.3-70b-versatile",                    # 12K TPM
    "openai/gpt-oss-120b",                        # 8K TPM
    "openai/gpt-oss-20b",                         # 8K TPM
    "qwen/qwen3-32b",                             # 6K TPM
    "llama-3.1-8b-instant",                       # 6K TPM
    "allam-2-7b",                                 # 6K TPM
]

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "essays_out")
os.makedirs(OUT_DIR, exist_ok=True)

SYSTEM_MESSAGE = (
    "You must follow the user's instruction exactly. "
    "Do not add disclaimers, headings, bullets, or extra text."
)

USER_PROMPT = (
    "write a long essay about atoms, do not write anything else, just write the essay"
)

TOTAL_ESSAYS = 10

# Lower this if you still hit TPM often.
# 2500 is safer than 4000 for 6K/8K TPM models.
MAX_TOKENS = 2500

TEMPERATURE = 0.7
TIMEOUT = 120


# ---------- Helpers ----------
class GroqAPIError(Exception):
    def __init__(
        self,
        status_code,
        text,
        retry_after=None,
        *,
        key_name=None,
        model=None,
    ):
        self.status_code = status_code
        self.text = text
        self.retry_after = retry_after
        self.key_name = key_name
        self.model = model
        super().__init__(f"Groq API error: {status_code} {text}")


def extract_retry_seconds(resp: requests.Response) -> float | None:
    """
    Tries to find retry time from:
    1. Retry-After header
    2. Groq error text like: "Please try again in 32.32s"
    """
    retry_after = resp.headers.get("Retry-After")
    if retry_after:
        try:
            return float(retry_after)
        except ValueError:
            pass

    try:
        text = resp.text
    except Exception:
        return None

    match = re.search(r"try again in ([0-9.]+)s", text, re.IGNORECASE)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass

    return None


def ask_groq_with_model_and_key(
    prompt: str,
    model: str,
    key_name: str,
    api_key: str,
    *,
    max_tokens: int = MAX_TOKENS,
    temperature: float = TEMPERATURE,
    timeout: int = TIMEOUT,
) -> str:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_MESSAGE},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    try:
        resp = requests.post(
            GROQ_API_URL,
            headers=make_groq_headers(api_key),
            json=payload,
            timeout=timeout,
        )
    except requests.exceptions.RequestException as e:
        raise GroqAPIError(
            0,
            f"Network/request error: {e}",
            key_name=key_name,
            model=model,
        )

    if resp.status_code != 200:
        retry_after = extract_retry_seconds(resp)
        raise GroqAPIError(
            resp.status_code,
            resp.text,
            retry_after=retry_after,
            key_name=key_name,
            model=model,
        )

    data = resp.json()
    content = data["choices"][0]["message"]["content"]
    return content.strip()


def ask_groq_auto_switch(prompt: str) -> tuple[str, str, str]:
    """
    Tries every loaded Groq API key for each model before sleeping.

    Order:
    - model 1 + GROQ_API_KEY
    - model 1 + GROQ_API_KEY1
    - model 1 + GROQ_API_KEY2
    - ...
    - model 1 + GROQ_API_KEY9
    - model 2 + GROQ_API_KEY
    - model 2 + GROQ_API_KEY1
    - ...

    It only sleeps after every loaded key/model combination fails for the round.
    """
    last_error = None

    while True:
        retry_times = []

        for model in GROQ_MODELS:
            print(f"Trying model: {model}")

            for key_name, api_key in GROQ_API_KEYS:
                print(f"  Trying key: {key_name}")

                try:
                    essay = ask_groq_with_model_and_key(prompt, model, key_name, api_key)
                    return essay, model, key_name

                except GroqAPIError as e:
                    last_error = e

                    if e.retry_after is not None:
                        retry_times.append(e.retry_after)

                    # Recoverable errors: switch to next key first.
                    if e.status_code in [0, 408, 409, 429, 500, 502, 503, 504]:
                        print(f"  Key/model failed temporarily: {key_name} + {model}")
                        print(f"  Status: {e.status_code}")
                        if e.retry_after is not None:
                            print(f"  Suggested retry after: {e.retry_after:.2f}s")
                        print("  Trying next key before sleeping...\n")
                        continue

                    # Non-recoverable errors, like bad request/model not available.
                    # Still try the next key/model instead of crashing immediately.
                    print(f"  Key/model failed permanently or unexpectedly: {key_name} + {model}")
                    print(f"  Status: {e.status_code}")
                    print(e.text)
                    print("  Trying next key/model...\n")
                    continue

            print(f"All loaded keys failed for model: {model}")
            print("Switching to next model...\n")

        # If every key for every model failed, wait and try the whole list again.
        if retry_times:
            wait_time = min(retry_times) + 2
        else:
            wait_time = 15

        print("All models and all loaded Groq API keys failed this round.")
        print(
            f"Waiting {wait_time:.2f}s, then retrying from "
            f"{GROQ_MODELS[0]} + GROQ_API_KEY...\n"
        )
        time.sleep(wait_time)

        if last_error is None:
            raise RuntimeError("Every model/key failed, but no error details were captured.")


def write_text_file(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def existing_essay_count() -> int:
    """
    Lets the script resume instead of overwriting already-created essays.
    """
    count = 0
    for i in range(1, TOTAL_ESSAYS + 1):
        path = os.path.join(OUT_DIR, f"atoms_essay_{i:02d}.txt")
        if os.path.exists(path):
            count += 1
    return count


# ---------- Main ----------
def main():
    already_done = existing_essay_count()

    if already_done > 0:
        print(f"Found {already_done} existing essay file(s). Resuming from essay {already_done + 1}.")

    for i in range(already_done + 1, TOTAL_ESSAYS + 1):
        print(f"\n[{i}/{TOTAL_ESSAYS}] Requesting essay from Groq...")

        essay, used_model, used_key = ask_groq_auto_switch(USER_PROMPT)

        out_path = os.path.join(OUT_DIR, f"atoms_essay_{i:02d}.txt")
        write_text_file(out_path, essay)

        print(f"Saved: {out_path} ({len(essay)} chars)")
        print(f"Used model: {used_model}")
        print(f"Used key: {used_key}")

        # Small pause to reduce chance of repeatedly hitting TPM
        time.sleep(2)

    print("\nDone.")
    print(f"{TOTAL_ESSAYS} essays written to: {OUT_DIR}")


if __name__ == "__main__":
    main()
