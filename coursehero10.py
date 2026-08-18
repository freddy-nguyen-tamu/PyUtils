import os
import time

import groq_dynamic

# ---------- Config ----------
GROQ_API_KEYS = groq_dynamic.load_groq_keys()

if not GROQ_API_KEYS:
    raise SystemExit(
        "No Groq API keys found. Set GROQ_API_KEY and optionally "
        "GROQ_API_KEY1 through GROQ_API_KEY9 in the environment."
    )

print(
    f"Loaded {len(GROQ_API_KEYS)} Groq API key(s): "
    + ", ".join(name for name, _ in GROQ_API_KEYS)
)


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
def ask_groq_auto_switch(prompt: str) -> tuple[str, str, str]:
    """
    Uses Groq model discovery, then tries every loaded model/key combination.
    """
    data = groq_dynamic.groq_chat_try_all_models(
        [
            {"role": "system", "content": SYSTEM_MESSAGE},
            {"role": "user", "content": prompt},
        ],
        max_tokens=MAX_TOKENS,
        temperature=TEMPERATURE,
        timeout=TIMEOUT,
    )
    meta = data.get("_groq", {})
    essay = data["choices"][0]["message"]["content"].strip()
    return essay, meta.get("model", "auto"), meta.get("key_name", "unknown")


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
