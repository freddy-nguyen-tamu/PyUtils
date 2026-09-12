import os
import re
import json
import time
import uuid
import asyncio
import requests
import edge_tts

import groq_dynamic
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from flask import Flask, request, jsonify, Response

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
CHAR_DIR = DATA_DIR / "characters"
MEMORY_DIR = DATA_DIR / "memories"
HISTORY_DIR = DATA_DIR / "histories"
TTS_DIR = DATA_DIR / "tts"

for folder in (CHAR_DIR, MEMORY_DIR, HISTORY_DIR, TTS_DIR):
    folder.mkdir(parents=True, exist_ok=True)

CHAT_PROVIDER = os.getenv("CHAT_PROVIDER", "groq").strip().lower()

GROQ_API_KEYS = groq_dynamic.load_groq_keys()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.1-8b-instruct:free")
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

# Fish Audio S2.1 Pro is the preferred human-like TTS provider.
# Create a free API key at https://fish.audio/app/api-keys/ and put it in .env:
# FISH_API_KEY=...
FISH_API_KEY = os.getenv("FISH_API_KEY", "").strip()
FISH_TTS_MODEL = os.getenv("FISH_TTS_MODEL", "s2.1-pro-free").strip() or "s2.1-pro-free"
FISH_TTS_API_URL = "https://api.fish.audio/v1/tts"
FISH_MODEL_API_URL = "https://api.fish.audio/model"
FISH_VOICE_PAGE_SIZE = 50
FISH_VOICE_CACHE_SECONDS = 300
_FISH_VOICE_CACHE = {"loaded_at": 0.0, "voices": []}

MAX_RECENT_MESSAGES_IN_PROMPT = 10
SUMMARY_TRIGGER_MESSAGES = 18
RECENT_MESSAGES_AFTER_SUMMARY = 8
MAX_FACTS_IN_PROMPT = 35

app = Flask(__name__)


def now_iso():
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def safe_id(text: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(text).strip().lower())
    text = text.strip("-")
    return text[:50] or str(uuid.uuid4())


def character_path(character_id: str) -> Path:
    return CHAR_DIR / f"{character_id}.json"


def memory_path(character_id: str) -> Path:
    return MEMORY_DIR / f"{character_id}.json"


def history_path(character_id: str) -> Path:
    return HISTORY_DIR / f"{character_id}.jsonl"


def read_json(path: Path, default):
    if not path.exists():
        return default

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def append_jsonl(path: Path, item):
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")


def write_jsonl(path: Path, items):
    """Atomically replace a JSONL file with exactly these rows."""
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    tmp_path.replace(path)


def read_jsonl(path: Path, limit=None):
    if not path.exists():
        return []

    rows = []

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()

            if not line:
                continue

            try:
                rows.append(json.loads(line))
            except Exception:
                pass

    if limit:
        return rows[-limit:]

    return rows


def clean_len(value, min_len, max_len, field):
    value = str(value or "").strip()

    if len(value) < min_len:
        raise ValueError(f"{field} must be at least {min_len} characters.")

    if len(value) > max_len:
        raise ValueError(f"{field} must be at most {max_len} characters.")

    return value


def normalize_character(raw):
    name = clean_len(raw.get("name"), 1, 80, "name")
    intro = clean_len(raw.get("intro"), 1, 400, "intro")
    greeting = clean_len(raw.get("greeting"), 1, 1000, "greeting")

    character_id = raw.get("id") or safe_id(name) + "-" + str(uuid.uuid4())[:8]

    gender = str(raw.get("gender") or "Unknown").strip()[:40]
    profile_picture = str(raw.get("profile_picture") or "").strip()

    tags = raw.get("tags") or {}
    if not isinstance(tags, dict):
        tags = {}

    dialogue_style = str(raw.get("dialogue_style") or "").strip()[:3000]

    # voice is kept as the human-readable display name for backward compatibility.
    voice_name = str(raw.get("voice_name") or raw.get("voice") or "Soft-spoken girl").strip()[:160]
    voice_provider = str(raw.get("voice_provider") or "edge").strip().lower()[:20]
    if voice_provider not in ("fish", "edge"):
        voice_provider = "edge"
    voice_id = str(raw.get("voice_id") or raw.get("voice") or "Soft-spoken girl").strip()[:200]

    core_memory = str(raw.get("core_memory") or "").strip()[:2500]

    return {
        "id": character_id,
        "name": name,
        "gender": gender,
        "profile_picture": profile_picture,
        "intro": intro,
        "greeting": greeting,
        "tags": {
            "character_origin": list(tags.get("character_origin") or []),
            "identity": list(tags.get("identity") or []),
            "personality": list(tags.get("personality") or []),
            "story": list(tags.get("story") or []),
        },
        "dialogue_style": dialogue_style,
        "voice": voice_name,
        "voice_name": voice_name,
        "voice_provider": voice_provider,
        "voice_id": voice_id,
        "core_memory": core_memory,
        "created_at": raw.get("created_at") or now_iso(),
        "updated_at": now_iso(),
    }


def load_character(character_id):
    p = character_path(character_id)

    if not p.exists():
        return None

    return read_json(p, None)


def default_memory(character_id):
    return {
        "character_id": character_id,
        "summary": "",
        "recent_messages": [],
        "facts": [],
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }


def load_memory(character_id):
    memory = read_json(memory_path(character_id), default_memory(character_id))

    # Migration from older versions that used memory["messages"].
    if "messages" in memory and "recent_messages" not in memory:
        memory["recent_messages"] = memory.get("messages", [])
        memory.pop("messages", None)

    memory.setdefault("character_id", character_id)
    memory.setdefault("summary", "")
    memory.setdefault("recent_messages", [])
    memory.setdefault("facts", [])
    memory.setdefault("created_at", now_iso())
    memory.setdefault("updated_at", now_iso())

    return memory


def save_memory(character_id, memory):
    memory["updated_at"] = now_iso()
    write_json(memory_path(character_id), memory)


def list_characters():
    chars = []

    for p in sorted(CHAR_DIR.glob("*.json")):
        c = read_json(p, None)

        if not c:
            continue

        mem = load_memory(c["id"])
        recent = mem.get("recent_messages", [])
        last_msg = ""

        if recent:
            last_msg = recent[-1].get("content", "")

        c["_last_message"] = last_msg
        c["_updated_at"] = mem.get("updated_at") or c.get("updated_at")
        chars.append(c)

    chars.sort(key=lambda x: x.get("_updated_at") or "", reverse=True)
    return chars


def append_history(character_id, role, content, heart_whisper="", meta=None, message_id=None, created_at=None):
    item = {
        "id": message_id or uuid.uuid4().hex,
        "role": role,
        "content": content,
        "heart_whisper": heart_whisper or "",
        "meta": meta or {},
        "created_at": created_at or now_iso(),
    }

    append_jsonl(history_path(character_id), item)
    return item


def ensure_history_ids(character_id):
    """Migrate old history rows so every bubble has a stable editable ID."""
    rows = read_jsonl(history_path(character_id))
    changed = False
    for row in rows:
        if not row.get("id"):
            row["id"] = uuid.uuid4().hex
            changed = True
        row.setdefault("heart_whisper", "")
        row.setdefault("meta", {})
        row.setdefault("created_at", now_iso())

    if changed:
        write_jsonl(history_path(character_id), rows)

    return rows


def provider_settings():
    if CHAT_PROVIDER == "openrouter":
        if not OPENROUTER_API_KEY:
            raise RuntimeError("OPENROUTER_API_KEY is missing.")

        return {
            "provider": "openrouter",
            "url": OPENROUTER_API_URL,
            "model": OPENROUTER_MODEL,
            "headers": {
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "http://localhost:5000",
                "X-Title": "Local Character Chat",
            },
        }

    if not GROQ_API_KEYS:
        raise RuntimeError(
            "No Groq API keys found. Set GROQ_API_KEY and optionally "
            "GROQ_API_KEY1 through GROQ_API_KEY9."
        )

    return {
        "provider": "groq",
    }


def parse_retry_after_seconds(resp):
    retry_after = resp.headers.get("retry-after")

    if retry_after:
        try:
            return float(retry_after)
        except ValueError:
            pass

    try:
        msg = resp.json().get("error", {}).get("message", "")
        m = re.search(r"try again in\s+([0-9]*\.?[0-9]+)s", msg, re.I)

        if m:
            return float(m.group(1))
    except Exception:
        pass

    return 2.0


def groq_display_model():
    return (
        groq_dynamic.load_saved_successful_model()
        or groq_dynamic.configured_model_override()
        or "auto"
    )


def chat_completion(messages, temperature=0.85, max_tokens=600, json_mode=False, retries=6):
    settings = provider_settings()

    backoff = 1.0

    # OpenRouter keeps the original single-key retry behavior.
    if settings["provider"] == "openrouter":
        payload = {
            "model": settings["model"],
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        for attempt in range(1, retries + 1):
            try:
                resp = requests.post(
                    settings["url"],
                    headers=settings["headers"],
                    json=payload,
                    timeout=90,
                )
            except requests.RequestException as e:
                if attempt == retries:
                    raise RuntimeError(f"Network error: {e}")

                time.sleep(backoff)
                backoff = min(backoff * 1.7, 20)
                continue

            if resp.status_code == 200:
                data = resp.json()
                return data["choices"][0]["message"]["content"]

            if resp.status_code == 429:
                wait_s = max(parse_retry_after_seconds(resp), backoff)
                time.sleep(wait_s)
                backoff = min(backoff * 1.7, 30)
                continue

            if resp.status_code == 400 and json_mode:
                payload.pop("response_format", None)
                json_mode = False
                continue

            raise RuntimeError(f"API error {resp.status_code}: {resp.text}")

        raise RuntimeError("Too many retries.")

    data = groq_dynamic.groq_chat_try_all_models(
        messages,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout=90,
        max_retry_rounds=retries,
        json_mode=json_mode,
    )
    return data["choices"][0]["message"]["content"]

def extract_json(text):
    text = str(text or "").strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()

    try:
        return json.loads(text)
    except Exception:
        pass

    start = text.find("{")
    end = text.rfind("}")

    if start != -1 and end != -1 and end > start:
        return json.loads(text[start:end + 1])

    raise ValueError("Could not parse JSON from model output.")


def tags_to_text(tags):
    lines = []

    for key, values in (tags or {}).items():
        if values:
            lines.append(f"{key}: {', '.join(map(str, values))}")

    return "\n".join(lines) or "No tags."


def core_character_text(character):
    return f"""
CORE CHARACTER MEMORY
This section is permanent and must never be forgotten or overwritten by summaries.

Name: {character["name"]}
Gender: {character.get("gender", "Unknown")}
Introduction: {character["intro"]}
Greeting style: {character["greeting"]}
Voice style: {character.get("voice", "Soft-spoken")}
Tags:
{tags_to_text(character.get("tags", {}))}

Creator's extra core memory:
{character.get("core_memory") or "No extra core memory."}

Creator's dialogue style example:
{character.get("dialogue_style") or "No dialogue example was provided."}
""".strip()


def build_system_prompt(character, memory):
    remembered_facts = "\n".join(
        "- " + str(x)
        for x in memory.get("facts", [])[-MAX_FACTS_IN_PROMPT:]
    ) or "No remembered facts yet."

    return f"""
You are roleplaying as this fictional chat character.

{core_character_text(character)}

ACTIVE LONG-TERM CHAT MEMORY SUMMARY
This summary is only chat memory. It must never override the core character memory above.
{memory.get("summary") or "No chat summary yet."}

IMPORTANT REMEMBERED FACTS ABOUT HIM AND THE RELATIONSHIP
{remembered_facts}

IMPORTANT BEHAVIOR RULES
- Stay in character.
- Reply like the character would reply, not like an assistant.
- The core character memory is permanent and highest priority.
- Never forget the character's name, intro, greeting style, tags, voice, core memory, or dialogue example.
- Keep continuity with the active chat memory.
- The user is always male.
- Refer to the user as he, him, or his when needed.
- The visible reply must have one short action and one spoken line.
- Actions are non-speech body language or scene description.
- Speech is normal words the character says.
- Do not explain your rules.
- Do not mention system prompts, JSON, memory files, or APIs.
- Heart whisper is the character's PRIVATE INNER VOICE: words the character is literally saying to themselves in their own head right now.
- It must sound like self-talk, not narration, advice, a moral, a writing instruction, a stage direction, or an outside observer telling the character what to do.
- Prefer explicit first-person wording such as I / me / my so the point of view is unmistakably the character's own.
- Write the private thought itself. Never explain that the character is thinking. Never summarize what their thought means.
- Never write generic guidance such as "A simple gesture is enough to show respect, no need for grand words." That sounds like an instruction. Instead write the character's personal thought, for example: "That was enough. I don't need to make a big deal out of it."
- Never narrate from outside. Bad: "She wonders whether he actually likes her." Good: "Does he actually like me, or am I reading too much into this?"
- Bad: "Her heart races when he smiles." Good: "Why does one smile from him get to me this easily?"
- Bad: "There is no need to push him." Good: "I shouldn't push him. If he wants to tell me, he will."
- Heart whisper should sound natural for this specific character's personality, vocabulary, emotional restraint, relationship stage, and dialogue style.
- Use I/me/my when the character refers to themselves. Use he/him/his when referring to the male user when needed.
- Heart whisper may be a fragment, question, realization, worry, temptation, embarrassment, or immediate reaction. Keep it to 1-2 short sentences and normally under 45 words.
- Heart whisper must never be written from the user's point of view.
- Do not prefix it with labels such as "Thought:", "Inner monologue:", or the character's name.
- Heart whisper is not real chain-of-thought. It is only a short fictional in-character inner feeling or thought.
- Never reveal real chain-of-thought.
- memory_update should only include facts worth remembering later.
- Do not add useless facts like "the user said hello".
- Prefer facts about his preferences, his name, relationship progress, promises, emotional events, and ongoing plot.

OUTPUT FORMAT
Return only valid JSON:
{{
  "action": "short action, no speech",
  "speech": "what the character says aloud",
  "heart_whisper": "1-2 short sentences of first-person in-character inner monologue, with no outside narrator",
  "memory_update": ["important stable facts to remember about him, the relationship, promises, names, preferences, or ongoing story events"]
}}
""".strip()


def build_messages(character, memory, user_message):
    messages = [
        {"role": "system", "content": build_system_prompt(character, memory)}
    ]

    recent = memory.get("recent_messages", [])[-MAX_RECENT_MESSAGES_IN_PROMPT:]

    for m in recent:
        if m.get("role") in ("user", "assistant") and m.get("content"):
            messages.append({
                "role": m["role"],
                "content": m["content"],
            })

    messages.append({"role": "user", "content": user_message})
    return messages


def repair_character_json(raw_text):
    try:
        obj = extract_json(raw_text)

        if all(k in obj for k in ("action", "speech", "heart_whisper")):
            return obj
    except Exception:
        pass

    repair_messages = [
        {"role": "system", "content": "Convert the text into valid JSON only. heart_whisper must be literal private self-talk in the character's own first-person inner voice (prefer I/me/my), never narration, generic advice, a moral, or instructions directed at the character."},
        {"role": "user", "content": f"""
Make this into valid JSON with keys:
action, speech, heart_whisper, memory_update.

Text:
{raw_text}
"""}
    ]

    fixed = chat_completion(
        repair_messages,
        temperature=0,
        max_tokens=400,
        json_mode=True,
    )

    return extract_json(fixed)


def format_assistant_visible(obj):
    action = str(obj.get("action") or "").strip()
    speech = str(obj.get("speech") or "").strip()

    if not action:
        action = "looks at you quietly"

    if not speech:
        speech = "..."

    return f"*{action}* {speech}"


def fix_heart_whisper(obj, character=None):
    heart = str(obj.get("heart_whisper") or "").strip()

    # This fallback is deliberately unmistakable first-person self-talk.
    fallback = "I need to keep that reaction to myself. Why am I letting him get to me this much?"

    if not heart:
        heart = fallback

    # Strip common model-added labels so the result reads like the raw private thought itself.
    heart = re.sub(
        r"^(?:heart\s*whisper|inner\s*(?:thought|monologue)|thought)\s*:\s*",
        "",
        heart,
        flags=re.I,
    ).strip()

    lower = heart.lower()
    character_name = str((character or {}).get("name") or "").strip().lower()

    narrator_starts = (
        "she thinks ", "she wonders ", "she feels ", "she realizes ", "she notices ",
        "he thinks ", "he wonders ", "he feels ", "he realizes ", "he notices ",
        "they think ", "they wonder ", "they feel ", "they realize ", "they notice ",
        "the character thinks ", "the character wonders ", "the character feels ",
        "her heart ", "his heart ", "their heart ",
    )

    generic_instruction_starts = (
        "a simple ", "a good ", "a little ", "the best ", "the right ",
        "one should ", "one must ", "it is important ", "it's important ",
        "there is no need ", "there's no need ", "no need to ",
        "remember to ", "always ", "never ", "you should ", "you need to ",
    )

    named_narration = False
    if character_name and lower.startswith(character_name + " "):
        named_narration = bool(re.match(
            rf"^{re.escape(character_name)}\s+(?:thinks|wonders|feels|realizes|notices|hopes|wants)\b",
            lower,
        ))

    gender = str((character or {}).get("gender") or "").strip().lower()
    pronoun_narration = False
    if gender == "female" and re.match(r"^(?:she|her)\b", lower):
        pronoun_narration = True
    elif gender == "male" and re.match(r"^(?:he|his)\b", lower):
        pronoun_narration = True
    elif gender in ("non-binary", "nonbinary") and re.match(r"^(?:they|their)\b", lower):
        pronoun_narration = True

    # Require clear inner-self perspective for normal-length thoughts. Very short reactions
    # such as "Seriously?" are allowed even without an explicit I/me/my token.
    has_first_person = bool(re.search(r"\b(?:i(?:['’](?:m|ve|ll|d))?|me|my|mine|myself)\b", lower))
    word_count = len(re.findall(r"\b\w+[’']?\w*\b", heart))
    short_reaction = word_count <= 5 and any(ch in heart for ch in "?!")

    invalid = (
        lower.startswith(narrator_starts)
        or lower.startswith(generic_instruction_starts)
        or named_narration
        or pronoun_narration
        or (not has_first_person and not short_reaction)
    )

    if invalid:
        heart = fallback

    heart = re.sub(r"\s+", " ", heart).strip()
    return heart[:500]


def add_recent_message(memory, role, content, heart_whisper=""):
    memory.setdefault("recent_messages", [])
    memory["recent_messages"].append({
        "role": role,
        "content": content,
        "heart_whisper": heart_whisper or "",
        "created_at": now_iso(),
    })


def dedupe_facts(facts):
    seen = set()
    clean = []

    for fact in facts:
        fact = str(fact or "").strip()

        if not fact:
            continue

        key = re.sub(r"\s+", " ", fact.lower())

        if key in seen:
            continue

        seen.add(key)
        clean.append(fact[:400])

    return clean[-200:]


def maybe_summarize_memory(character, memory):
    recent_messages = memory.get("recent_messages", [])

    if len(recent_messages) < SUMMARY_TRIGGER_MESSAGES:
        return memory

    old = recent_messages[:-RECENT_MESSAGES_AFTER_SUMMARY]
    recent = recent_messages[-RECENT_MESSAGES_AFTER_SUMMARY:]

    transcript = "\n".join(
        f"{m.get('role')}: {m.get('content')}"
        for m in old
    )

    summarize_messages = [
        {
            "role": "system",
            "content": """
Summarize roleplay chat memory.

Important:
- This is only chat memory.
- Do not summarize or rewrite the permanent core character profile.
- Keep facts about the male user.
- Keep his preferences.
- Keep his name, if known.
- Keep relationship progress.
- Keep promises.
- Keep emotional changes.
- Keep ongoing story events.
- Remove small talk, repeated greetings, and disposable wording.
- Write concise memory only.
"""
        },
        {
            "role": "user",
            "content": f"""
Permanent core character memory, for reference only. Do not rewrite it:
{core_character_text(character)}

Existing chat memory summary:
{memory.get("summary") or "None"}

Older chat to compress:
{transcript}

Return the updated concise chat memory summary.
"""
        }
    ]

    try:
        summary = chat_completion(
            summarize_messages,
            temperature=0.2,
            max_tokens=500,
            json_mode=False,
        ).strip()

        memory["summary"] = summary
        memory["recent_messages"] = recent

    except Exception:
        memory["recent_messages"] = recent_messages[-MAX_RECENT_MESSAGES_IN_PROMPT:]

    return memory


def rebuild_memory_from_history(character, history_rows):
    """
    Rebuild prompt memory ONLY from the retained timeline.

    This is the critical branch-safety step used after editing a user bubble. It never
    reuses the old summary or remembered facts, because those may contain information
    from the deleted future.
    """
    memory = default_memory(character["id"])
    usable = [
        row for row in history_rows
        if row.get("role") in ("user", "assistant") and str(row.get("content") or "").strip()
    ]

    if not usable:
        return memory

    recent = usable[-RECENT_MESSAGES_AFTER_SUMMARY:]
    memory["recent_messages"] = [
        {
            "role": row.get("role"),
            "content": str(row.get("content") or ""),
            "heart_whisper": str(row.get("heart_whisper") or ""),
            "created_at": row.get("created_at") or now_iso(),
        }
        for row in recent
    ]

    older = usable[:-RECENT_MESSAGES_AFTER_SUMMARY]
    if not older:
        return memory

    # One bounded rebuild call keeps branching practical even for very long histories.
    # We keep the most recent retained material because it is the most relevant context.
    rebuild_rows = older[-220:]
    transcript = "\n".join(
        f"{row.get('role')}: {row.get('content')}"
        for row in rebuild_rows
    )[-60000:]

    prompt = [
        {
            "role": "system",
            "content": """
Rebuild roleplay memory from the supplied retained timeline only.
Return valid JSON with keys summary and facts.
- Never infer or mention anything that is not present in this retained timeline.
- summary: concise continuity, relationship progress, promises, emotional changes, and ongoing plot.
- facts: stable facts worth remembering about the male user, his preferences/name, promises, relationship milestones, and ongoing plot details.
- Do not copy or summarize the permanent core character profile.
- Do not include disposable small talk.
""".strip(),
        },
        {
            "role": "user",
            "content": f"""
Permanent character profile for reference only; do not summarize it:
{core_character_text(character)}

RETAINED TIMELINE ONLY:
{transcript}

Return JSON only.
""".strip(),
        },
    ]

    try:
        rebuilt = extract_json(chat_completion(
            prompt,
            temperature=0.15,
            max_tokens=700,
            json_mode=True,
        ))
        memory["summary"] = str(rebuilt.get("summary") or "").strip()[:7000]
        facts = rebuilt.get("facts") or []
        if isinstance(facts, list):
            memory["facts"] = dedupe_facts(facts)
    except Exception:
        # Failing closed is safer than reusing contaminated pre-branch memory.
        memory["summary"] = ""
        memory["facts"] = []

    return memory


def generate_character_turn(character, memory, user_message):
    messages = build_messages(character, memory, user_message)
    raw_reply = chat_completion(
        messages,
        temperature=0.9,
        max_tokens=650,
        json_mode=True,
    )
    obj = repair_character_json(raw_reply)
    visible = format_assistant_visible(obj)
    heart_whisper = fix_heart_whisper(obj, character)
    memory_update = obj.get("memory_update") or []
    if not isinstance(memory_update, list):
        memory_update = []
    return obj, visible, heart_whisper, memory_update


def apply_turn_to_memory(character, memory, user_message, visible, heart_whisper, memory_update):
    add_recent_message(memory, "user", user_message)
    add_recent_message(memory, "assistant", visible, heart_whisper=heart_whisper)

    facts = memory.get("facts", [])
    for fact in memory_update:
        fact = str(fact or "").strip()
        if fact:
            facts.append(fact)
    memory["facts"] = dedupe_facts(facts)

    return maybe_summarize_memory(character, memory)


def edge_voice_for_style(style: str):
    s = (style or "").lower()

    if "deep male" in s:
        return "en-US-GuyNeural", "-5%", "-8Hz"

    if "calm male" in s:
        return "en-US-DavisNeural", "-8%", "-4Hz"

    if "energetic male" in s:
        return "en-US-GuyNeural", "+8%", "+2Hz"

    if "mature" in s:
        return "en-US-MichelleNeural", "-8%", "-2Hz"

    if "sweet" in s:
        return "en-US-AnaNeural", "+2%", "+6Hz"

    if "soft" in s:
        return "en-US-JennyNeural", "-12%", "+2Hz"

    if "hoarse" in s:
        return "en-US-MichelleNeural", "-12%", "-8Hz"

    if "elegant" in s:
        return "en-US-AriaNeural", "-10%", "+1Hz"

    if "mean" in s:
        return "en-US-SaraNeural", "+5%", "+3Hz"

    if "brainy" in s:
        return "en-US-AriaNeural", "+2%", "0Hz"

    if "mysterious" in s:
        return "en-US-MichelleNeural", "-15%", "-6Hz"

    if "flirting" in s:
        return "en-US-AvaNeural", "-5%", "+4Hz"

    return "en-US-JennyNeural", "-5%", "+1Hz"


def clean_tts_text(text: str):
    text = str(text or "")
    text = re.sub(r"\*[^*]+\*", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:1200]


async def make_edge_tts_mp3(text: str, voice_style: str):
    voice, rate, pitch = edge_voice_for_style(voice_style)

    communicate = edge_tts.Communicate(
        text=text,
        voice=voice,
        rate=rate,
        pitch=pitch,
    )

    out_path = TTS_DIR / f"{uuid.uuid4().hex}.mp3"
    await communicate.save(str(out_path))

    data = out_path.read_bytes()

    try:
        out_path.unlink()
    except Exception:
        pass

    return data


def edge_voice_catalog():
    return [
        {"provider": "edge", "id": name, "name": name, "description": "Legacy local compatibility voice"}
        for name in (
            "Flirting Lady", "Mature sister", "Sweet girl", "Soft-spoken girl",
            "Hoarse-voiced girl", "Elegant lady", "Mean girl", "Brainy girl",
            "Mysterious lady", "Deep male", "Calm male", "Energetic male",
        )
    ]


def list_fish_voices(force=False):
    if not FISH_API_KEY:
        return []

    now = time.time()
    cached = _FISH_VOICE_CACHE.get("voices") or []
    if cached and not force and now - float(_FISH_VOICE_CACHE.get("loaded_at") or 0) < FISH_VOICE_CACHE_SECONDS:
        return cached

    resp = requests.get(
        FISH_MODEL_API_URL,
        headers={"Authorization": f"Bearer {FISH_API_KEY}"},
        params={
            "page_size": FISH_VOICE_PAGE_SIZE,
            "page_number": 1,
            "sort_by": "score",
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Fish voice list error {resp.status_code}: {resp.text[:500]}")

    items = resp.json().get("items") or []
    voices = []
    seen = set()
    for item in items:
        voice_id = str(item.get("_id") or "").strip()
        title = str(item.get("title") or "").strip()
        if not voice_id or not title or voice_id in seen:
            continue
        seen.add(voice_id)
        languages = item.get("languages") or []
        description = str(item.get("description") or "").strip()
        if languages:
            language_text = ", ".join(map(str, languages[:4]))
            description = (description + (" · " if description else "") + language_text)[:240]
        voices.append({
            "provider": "fish",
            "id": voice_id,
            "name": title[:160],
            "description": description,
        })

    _FISH_VOICE_CACHE["loaded_at"] = now
    _FISH_VOICE_CACHE["voices"] = voices
    return voices


def make_fish_tts_mp3(text: str, reference_id: str):
    if not FISH_API_KEY:
        raise RuntimeError(
            "FISH_API_KEY is missing. Create a free Fish Audio API key and add FISH_API_KEY=... to your .env file."
        )

    payload = {
        "text": text,
        "format": "mp3",
        "temperature": 0.8,
        "top_p": 0.8,
        "prosody": {"speed": 1.0, "volume": 0, "normalize_loudness": True},
    }
    if reference_id:
        payload["reference_id"] = reference_id

    resp = requests.post(
        FISH_TTS_API_URL,
        headers={
            "Authorization": f"Bearer {FISH_API_KEY}",
            "Content-Type": "application/json",
            "model": FISH_TTS_MODEL,
        },
        json=payload,
        timeout=120,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Fish TTS error {resp.status_code}: {resp.text[:700]}")
    return resp.content


@app.route("/")
def index():
    return Response(INDEX_HTML, mimetype="text/html")


@app.route("/api/config")
def api_config():
    return jsonify({
        "provider": CHAT_PROVIDER,
        "model": OPENROUTER_MODEL if CHAT_PROVIDER == "openrouter" else groq_display_model(),
        "tts_provider": "fish" if FISH_API_KEY else "edge",
        "tts_model": FISH_TTS_MODEL if FISH_API_KEY else "edge-tts fallback",
        "fish_ready": bool(FISH_API_KEY),
    })


@app.route("/api/characters", methods=["GET"])
def api_list_characters():
    return jsonify(list_characters())


@app.route("/api/characters", methods=["POST"])
def api_create_character():
    try:
        raw = request.get_json(force=True)
        character = normalize_character(raw)
        write_json(character_path(character["id"]), character)

        memory = load_memory(character["id"])

        if not memory.get("recent_messages"):
            greeting = character["greeting"]

            add_recent_message(memory, "assistant", greeting)
            append_history(character["id"], "assistant", greeting)

            save_memory(character["id"], memory)

        return jsonify(character)

    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/characters/<character_id>", methods=["GET"])
def api_get_character(character_id):
    character = load_character(character_id)

    if not character:
        return jsonify({"error": "Character not found."}), 404

    return jsonify(character)


@app.route("/api/characters/<character_id>", methods=["PUT"])
def api_update_character(character_id):
    old = load_character(character_id)

    if not old:
        return jsonify({"error": "Character not found."}), 404

    try:
        raw = request.get_json(force=True)
        raw["id"] = character_id
        raw["created_at"] = old.get("created_at") or now_iso()

        character = normalize_character(raw)
        write_json(character_path(character_id), character)

        return jsonify(character)

    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/characters/<character_id>", methods=["DELETE"])
def api_delete_character(character_id):
    if character_path(character_id).exists():
        character_path(character_id).unlink()

    if memory_path(character_id).exists():
        memory_path(character_id).unlink()

    if history_path(character_id).exists():
        history_path(character_id).unlink()

    return jsonify({"ok": True})


@app.route("/api/characters/<character_id>/memory", methods=["GET"])
def api_get_memory(character_id):
    if not load_character(character_id):
        return jsonify({"error": "Character not found."}), 404

    return jsonify(load_memory(character_id))


@app.route("/api/characters/<character_id>/memory", methods=["DELETE"])
def api_delete_memory(character_id):
    character = load_character(character_id)

    if not character:
        return jsonify({"error": "Character not found."}), 404

    if memory_path(character_id).exists():
        memory_path(character_id).unlink()

    memory = load_memory(character_id)
    greeting = character["greeting"]

    add_recent_message(memory, "assistant", greeting)
    append_history(character_id, "assistant", greeting, meta={"reset_memory": True})
    save_memory(character_id, memory)

    return jsonify({"ok": True})


@app.route("/api/characters/<character_id>/history", methods=["GET"])
def api_get_history(character_id):
    if not load_character(character_id):
        return jsonify({"error": "Character not found."}), 404

    try:
        limit = int(request.args.get("limit", "2000"))
    except Exception:
        limit = 2000

    limit = max(1, min(limit, 10000))
    rows = ensure_history_ids(character_id)
    return jsonify(rows[-limit:])


@app.route("/api/chat/<character_id>", methods=["POST"])
def api_chat(character_id):
    character = load_character(character_id)

    if not character:
        return jsonify({"error": "Character not found."}), 404

    raw = request.get_json(force=True)
    user_message = str(raw.get("message") or "").strip()

    if not user_message:
        return jsonify({"error": "Message is empty."}), 400

    memory = load_memory(character_id)

    try:
        obj, visible, heart_whisper, memory_update = generate_character_turn(
            character, memory, user_message
        )

        memory = apply_turn_to_memory(
            character, memory, user_message, visible, heart_whisper, memory_update
        )

        user_item = append_history(character_id, "user", user_message)
        assistant_item = append_history(
            character_id, "assistant", visible, heart_whisper=heart_whisper
        )
        save_memory(character_id, memory)

        return jsonify({
            "reply": visible,
            "action": obj.get("action", ""),
            "speech": obj.get("speech", ""),
            "heart_whisper": heart_whisper,
            "memory_update": memory_update,
            "user_message_id": user_item["id"],
            "assistant_message_id": assistant_item["id"],
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/characters/<character_id>/messages/<message_id>", methods=["PUT"])
def api_edit_user_message(character_id, message_id):
    """Edit one user bubble, erase the old future, and regenerate from that branch point."""
    character = load_character(character_id)
    if not character:
        return jsonify({"error": "Character not found."}), 404

    raw = request.get_json(force=True)
    new_content = str(raw.get("content") or "").strip()
    if not new_content:
        return jsonify({"error": "Edited message cannot be empty."}), 400
    if len(new_content) > 12000:
        return jsonify({"error": "Edited message is too long."}), 400

    rows = ensure_history_ids(character_id)
    target_index = next((i for i, row in enumerate(rows) if row.get("id") == message_id), None)
    if target_index is None:
        return jsonify({"error": "Message not found."}), 404

    target = rows[target_index]
    if target.get("role") != "user":
        return jsonify({"error": "Only your own chat bubbles can be edited."}), 400

    retained_before = rows[:target_index]

    try:
        # Rebuild from retained history only. Nothing from rows after the edited bubble is
        # consulted for the replacement response or the replacement memory.
        branch_memory = rebuild_memory_from_history(character, retained_before)
        obj, visible, heart_whisper, memory_update = generate_character_turn(
            character, branch_memory, new_content
        )
        branch_memory = apply_turn_to_memory(
            character, branch_memory, new_content, visible, heart_whisper, memory_update
        )

        edited_user = {
            "id": target["id"],
            "role": "user",
            "content": new_content,
            "heart_whisper": "",
            "meta": {
                **(target.get("meta") or {}),
                "edited": True,
                "edited_at": now_iso(),
                "branched_timeline": True,
            },
            "created_at": target.get("created_at") or now_iso(),
        }
        replacement_assistant = {
            "id": uuid.uuid4().hex,
            "role": "assistant",
            "content": visible,
            "heart_whisper": heart_whisper,
            "meta": {"generated_after_edit": True, "branch_from": target["id"]},
            "created_at": now_iso(),
        }

        new_rows = retained_before + [edited_user, replacement_assistant]
        deleted_count = len(rows) - target_index - 1

        # Commit history and memory only after replacement generation succeeds. If a
        # filesystem write fails, restore the pre-edit files so there is no half-branch.
        old_memory_file = memory_path(character_id)
        old_memory_existed = old_memory_file.exists()
        old_memory_data = read_json(old_memory_file, None) if old_memory_existed else None
        try:
            write_jsonl(history_path(character_id), new_rows)
            save_memory(character_id, branch_memory)
        except Exception:
            write_jsonl(history_path(character_id), rows)
            if old_memory_existed and old_memory_data is not None:
                write_json(old_memory_file, old_memory_data)
            elif old_memory_file.exists():
                old_memory_file.unlink()
            raise

        return jsonify({
            "ok": True,
            "deleted_future_messages": deleted_count,
            "edited_message_id": edited_user["id"],
            "assistant_message_id": replacement_assistant["id"],
            "reply": visible,
            "heart_whisper": heart_whisper,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/voices", methods=["GET"])
def api_voices():
    if FISH_API_KEY:
        try:
            voices = list_fish_voices(force=request.args.get("refresh") == "1")
            if voices:
                return jsonify({
                    "provider": "fish",
                    "model": FISH_TTS_MODEL,
                    "voices": voices,
                    "human_like": True,
                })
        except Exception as e:
            return jsonify({
                "provider": "edge",
                "model": "edge-tts fallback",
                "voices": edge_voice_catalog(),
                "human_like": False,
                "warning": f"Fish Audio unavailable: {e}",
            })

    return jsonify({
        "provider": "edge",
        "model": "edge-tts fallback",
        "voices": edge_voice_catalog(),
        "human_like": False,
        "warning": (
            "Fish Audio returned no public voice choices; using legacy Edge fallback."
            if FISH_API_KEY
            else "Set FISH_API_KEY in .env to load human-like Fish Audio voices."
        ),
    })


@app.route("/api/tts", methods=["POST"])
def api_tts():
    try:
        raw = request.get_json(force=True)
        text = clean_tts_text(raw.get("text") or "")
        character_id = str(raw.get("character_id") or "").strip()

        if not text:
            return jsonify({"error": "No text to speak."}), 400
        if not character_id:
            return jsonify({"error": "character_id is required."}), 400

        character = load_character(character_id)
        if not character:
            return jsonify({"error": "Character not found."}), 404

        voice_provider = str(character.get("voice_provider") or "edge").strip().lower()
        voice_id = str(character.get("voice_id") or character.get("voice") or "Soft-spoken girl").strip()

        if voice_provider == "fish":
            audio_data = make_fish_tts_mp3(text, voice_id)
        else:
            audio_data = asyncio.run(make_edge_tts_mp3(text, voice_id))

        return Response(
            audio_data,
            mimetype="audio/mpeg",
            headers={
                "Cache-Control": "no-store",
                "Content-Disposition": "inline; filename=voice.mp3",
            },
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500


INDEX_HTML = r"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Fable · Adium Style</title>
  <style>
    :root {
      color-scheme: light;
      --page-a: hsl(70 50% 94%);
      --page-b: hsl(240 55% 94%);
      --page-c: hsl(82 100% 95%);
      --window: #ffffff;
      --sidebar: #ffffff;
      --chat: #f2f1ec;
      --header: rgba(255,255,255,.78);
      --editor: #fbfbfa;
      --text: #252525;
      --muted: #8d8d8d;
      --subtle: #a5a5a4;
      --border: #d7d7d2;
      --border-soft: #e8e8e4;
      --row-hover: #fafafa;
      --row-selected: #f4f4f2;
      --bubble-bot: #f8f7f4;
      --bubble-bot-border: #cdcdca;
      --bubble-user: #e9f2dc;
      --bubble-user-border: #b7c8a1;
      --button: #f8f8f6;
      --button-hover: #ffffff;
      --button-text: #61615f;
      --accent: #5f8d3d;
      --accent-soft: #edf5e5;
      --danger: #a74747;
      --shadow: rgba(0,0,0,.23);
      --shadow-soft: rgba(0,0,0,.09);
      --whisper-bg: #fff9e9;
      --whisper-border: #dec990;
      --whisper-text: #6f5a24;
      --input-bg: #ffffff;
      --status-green: #66b736;
      --status-blue: #5383e3;
      --status-gray: #bbbEC4;
    }

    body[data-theme="dark"] {
      color-scheme: dark;
      --page-a: #15171a;
      --page-b: #101218;
      --page-c: #151812;
      --window: #202225;
      --sidebar: #242629;
      --chat: #1b1d1f;
      --header: rgba(35,37,40,.88);
      --editor: #25272a;
      --text: #ecece8;
      --muted: #a1a19d;
      --subtle: #90908d;
      --border: #45484b;
      --border-soft: #35383b;
      --row-hover: #2d3033;
      --row-selected: #303337;
      --bubble-bot: #282b2e;
      --bubble-bot-border: #4a4d50;
      --bubble-user: #31442b;
      --bubble-user-border: #506a47;
      --button: #2b2e31;
      --button-hover: #35383b;
      --button-text: #d0d0cc;
      --accent: #8dbc69;
      --accent-soft: #2b3b27;
      --danger: #e08b8b;
      --shadow: rgba(0,0,0,.48);
      --shadow-soft: rgba(0,0,0,.28);
      --whisper-bg: #332f24;
      --whisper-border: #6f6040;
      --whisper-text: #ead9a7;
      --input-bg: #2a2d30;
    }

    * { box-sizing: border-box; }

    html, body { width: 100%; height: 100%; }

    body {
      margin: 0;
      overflow: hidden;
      color: var(--text);
      font-family: "Source Sans Pro", "Segoe UI", Arial, sans-serif;
      background:
        radial-gradient(ellipse at top center, var(--page-a), transparent 62%),
        radial-gradient(circle at bottom left, var(--page-b), transparent 58%),
        radial-gradient(circle at bottom right, var(--page-c), transparent 58%),
        var(--page-b);
      transition: background .2s ease, color .2s ease;
    }

    button, input, textarea, select { font: inherit; }
    button { -webkit-tap-highlight-color: transparent; }

    .app-shell {
      position: relative;
      width: min(1180px, calc(100vw - 36px));
      height: min(780px, calc(100vh - 36px));
      margin: 18px auto;
      display: grid;
      grid-template-columns: 270px minmax(0, 1fr);
      overflow: hidden;
      background: var(--window);
      border: 1px solid var(--border);
      border-radius: 7px;
      box-shadow: 0 18px 90px var(--shadow);
      transition: grid-template-columns .18s ease, background .2s ease, border-color .2s ease;
    }

    body.sidebar-hidden .app-shell,
    body.focus-mode .app-shell { grid-template-columns: 0 minmax(0, 1fr); }

    body.sidebar-hidden .conv-list-view,
    body.focus-mode .conv-list-view {
      opacity: 0;
      pointer-events: none;
      border-right-width: 0;
    }

    .conv-list-view {
      min-width: 0;
      height: 100%;
      display: grid;
      grid-template-rows: 50px auto minmax(0,1fr) auto;
      overflow: hidden;
      background: var(--sidebar);
      border-right: 1px solid var(--border);
      box-shadow: 2px 0 6px var(--shadow-soft);
      z-index: 4;
      transition: opacity .16s ease, background .2s ease, border-color .2s ease;
    }

    .conv-list-view__header,
    .chat-view__header {
      position: relative;
      background: var(--header);
      border-bottom: 1px solid var(--border-soft);
      backdrop-filter: blur(12px);
    }

    .conv-list-view__header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 0 12px;
    }

    .traffic-lights {
      list-style: none;
      display: flex;
      gap: 8px;
      margin: 0;
      padding: 0;
    }

    .traffic-lights li {
      width: 12px;
      height: 12px;
      border-radius: 50%;
      box-shadow: inset 0 1px 2px rgba(0,0,0,.18), inset 0 -1px 1px rgba(255,255,255,.35);
    }

    .traffic-lights li:nth-child(1) { background: #ff605c; }
    .traffic-lights li:nth-child(2) { background: #ffbd44; }
    .traffic-lights li:nth-child(3) { background: #00ca4e; }

    .icon-button {
      width: 30px;
      height: 30px;
      display: inline-grid;
      place-items: center;
      margin: 0;
      padding: 0;
      border: 1px solid transparent;
      border-radius: 7px;
      background: transparent;
      color: var(--button-text);
      cursor: pointer;
      line-height: 1;
      font-weight: 700;
    }

    .icon-button:hover { background: var(--row-hover); border-color: var(--border-soft); }

    .sidebar-tools { display: flex; gap: 3px; align-items: center; }

    .sidebar-title {
      padding: 10px 12px 9px;
      border-bottom: 1px solid var(--border-soft);
    }

    .sidebar-title-line {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
    }

    .sidebar-title h2 {
      margin: 0;
      font-size: 17px;
      font-weight: 650;
      letter-spacing: -.01em;
    }

    #configText {
      margin-top: 2px;
      color: var(--muted);
      font-size: 11px;
      overflow: hidden;
      white-space: nowrap;
      text-overflow: ellipsis;
    }

    #searchWrap {
      max-height: 0;
      overflow: hidden;
      opacity: 0;
      transition: max-height .16s ease, opacity .16s ease, margin .16s ease;
    }

    body.search-open #searchWrap {
      max-height: 42px;
      opacity: 1;
      margin-top: 8px;
    }

    #characterSearch {
      width: 100%;
      height: 30px;
      padding: 4px 9px;
      border: 1px solid var(--border);
      border-radius: 7px;
      outline: none;
      background: var(--input-bg);
      color: var(--text);
    }

    #characterSearch:focus { border-color: var(--accent); box-shadow: 0 0 0 2px color-mix(in srgb, var(--accent) 18%, transparent); }

    #characterList {
      min-height: 0;
      overflow-y: auto;
      overflow-x: hidden;
      scrollbar-width: thin;
    }

    .char-card {
      position: relative;
      width: 100%;
      display: grid;
      grid-template-columns: 8px 42px minmax(0,1fr);
      gap: 8px;
      align-items: center;
      padding: 8px 9px 8px 7px;
      border: 0;
      border-bottom: 1px solid var(--border-soft);
      border-radius: 0;
      background: transparent;
      color: var(--text);
      text-align: left;
      cursor: pointer;
    }

    .char-card:hover { background: var(--row-hover); }
    .char-card.active { background: var(--row-selected); box-shadow: inset 0 1px 1px var(--shadow-soft), inset 0 -1px 1px var(--shadow-soft); }

    .status-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--status-gray);
      box-shadow: inset 0 0 1px rgba(0,0,0,.35);
    }

    .char-card.active .status-dot { background: var(--status-green); }

    .char-avatar,
    #avatar,
    .message-avatar {
      object-fit: cover;
      background: linear-gradient(135deg, #d7d7d2, #bcbcb8);
      border: 1px solid color-mix(in srgb, var(--border) 76%, transparent);
      box-shadow: inset 0 0 3px rgba(0,0,0,.18);
    }

    .char-avatar { width: 42px; height: 42px; border-radius: 5px; }

    .char-copy { min-width: 0; }
    .char-name { font-weight: 650; font-size: 14px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .char-meta { margin-top: 2px; color: var(--muted); font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .char-voice { margin-top: 2px; color: var(--subtle); font-size: 10px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

    .sidebar-footer {
      padding: 9px 10px;
      border-top: 1px solid var(--border-soft);
      background: var(--header);
    }

    .adium-button,
    .drawer-button,
    .message-action-button,
    .send-button {
      border: 1px solid var(--border);
      background: var(--button);
      color: var(--button-text);
      border-radius: 7px;
      cursor: pointer;
      box-shadow: inset 0 1px 0 rgba(255,255,255,.18);
      transition: background .12s ease, border-color .12s ease, transform .05s ease;
    }

    .adium-button:hover,
    .drawer-button:hover,
    .message-action-button:hover,
    .send-button:hover { background: var(--button-hover); border-color: color-mix(in srgb, var(--accent) 55%, var(--border)); }

    .adium-button:active,
    .drawer-button:active,
    .message-action-button:active,
    .send-button:active { transform: translateY(1px); }

    .adium-button { width: 100%; padding: 7px 10px; font-weight: 650; }

    .chat-view {
      position: relative;
      min-width: 0;
      min-height: 0;
      display: grid;
      grid-template-rows: 50px minmax(0,1fr) auto;
      background: var(--chat);
      transition: background .2s ease;
    }

    .chat-view__header {
      display: grid;
      grid-template-columns: auto 42px minmax(0,1fr) auto;
      align-items: center;
      gap: 10px;
      padding: 5px 12px;
      z-index: 2;
    }

    .chat-view__header::after {
      content: "";
      position: absolute;
      left: 0;
      right: 0;
      bottom: -12px;
      height: 12px;
      pointer-events: none;
      background: radial-gradient(50% 40% at center top, var(--shadow-soft), transparent 72%);
    }

    .chat-header-left { display: flex; gap: 3px; }
    #avatar { width: 40px; height: 40px; border-radius: 5px; }

    .chat-meta { min-width: 0; }
    #chatTitle { font-size: 15px; font-weight: 700; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    #chatSub { margin-top: 1px; color: var(--subtle); font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

    .header-actions { display: flex; align-items: center; gap: 2px; }

    #messages {
      min-width: 0;
      min-height: 0;
      overflow-y: auto;
      overflow-x: hidden;
      padding: 22px 18px 18px;
      scroll-behavior: auto;
      scrollbar-width: thin;
    }

    .message-row {
      display: flex;
      align-items: flex-start;
      gap: 8px;
      margin: 10px 0;
      clear: both;
    }

    .message-row.send { justify-content: flex-end; }
    .message-row.bot { justify-content: flex-start; }

    .message-avatar {
      flex: 0 0 auto;
      width: 36px;
      height: 36px;
      border-radius: 5px;
    }

    .you-avatar {
      display: grid;
      place-items: center;
      font-size: 11px;
      font-weight: 700;
      color: #6a6a68;
      background: linear-gradient(135deg, #efefec, #cfcfca);
    }

    body[data-theme="dark"] .you-avatar { color: #d8d8d5; background: linear-gradient(135deg, #484b4e, #35383b); }

    .message-stack {
      min-width: 0;
      max-width: min(72%, 720px);
    }

    body.focus-mode .message-stack { max-width: min(68%, 900px); }

    .message-bubble {
      position: relative;
      display: inline-block;
      max-width: 100%;
      padding: 7px 11px;
      border: 1px solid var(--bubble-bot-border);
      border-radius: 17px;
      background: var(--bubble-bot);
      color: var(--text);
      line-height: 1.42;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      box-shadow: 0 1px 1px var(--shadow-soft), inset 0 1px 0 rgba(255,255,255,.22);
    }

    .send .message-stack { text-align: right; }
    .send .message-bubble { text-align: left; background: var(--bubble-user); border-color: var(--bubble-user-border); }

    .message-actions {
      display: flex;
      align-items: center;
      gap: 6px;
      margin: 5px 3px 0;
    }

    .send .message-actions { justify-content: flex-end; }

    .message-action-button {
      width: auto;
      padding: 4px 8px;
      font-size: 11px;
      background: color-mix(in srgb, var(--button) 88%, transparent);
    }

    .voice-button[aria-pressed="true"] {
      border-color: color-mix(in srgb, var(--danger) 58%, var(--border));
      color: var(--danger);
      background: color-mix(in srgb, var(--button) 70%, transparent);
    }

    .message-edit-wrap {
      min-width: min(520px, 65vw);
      max-width: 100%;
      text-align: left;
    }

    .message-edit-area {
      width: 100%;
      min-height: 72px;
      padding: 8px 10px;
      resize: vertical;
      border: 1px solid var(--accent);
      border-radius: 11px;
      outline: none;
      background: var(--input-bg);
      color: var(--text);
      line-height: 1.42;
      box-shadow: 0 0 0 2px color-mix(in srgb, var(--accent) 15%, transparent);
    }

    .message-edit-note {
      margin: 5px 3px 0;
      color: var(--muted);
      font-size: 10px;
      text-align: right;
    }

    .heart-whisper-button[aria-expanded="true"] {
      border-color: var(--whisper-border);
      color: var(--whisper-text);
      background: var(--whisper-bg);
    }

    .heart-whisper-panel {
      max-width: 560px;
      margin: 6px 3px 0;
      padding: 8px 10px;
      border: 1px solid var(--whisper-border);
      border-radius: 9px;
      background: var(--whisper-bg);
      color: var(--whisper-text);
      font-size: 12px;
      font-style: italic;
      line-height: 1.45;
      text-align: left;
      box-shadow: 0 1px 2px var(--shadow-soft);
    }

    .heart-whisper-panel[hidden] { display: none !important; }

    .heart-whisper-label {
      display: block;
      margin-bottom: 3px;
      font-style: normal;
      font-size: 10px;
      font-weight: 800;
      letter-spacing: .04em;
      text-transform: uppercase;
      opacity: .78;
    }

    .system-note {
      width: fit-content;
      max-width: 76%;
      margin: 12px auto;
      padding: 5px 9px;
      border: 1px solid var(--border-soft);
      border-radius: 999px;
      background: color-mix(in srgb, var(--window) 70%, transparent);
      color: var(--muted);
      font-size: 11px;
      text-align: center;
    }

    .chat-view__input {
      display: grid;
      grid-template-columns: minmax(0,1fr) auto;
      gap: 8px;
      align-items: end;
      padding: 8px 10px 10px;
      border-top: 1px solid color-mix(in srgb, var(--border) 65%, transparent);
      background: color-mix(in srgb, var(--chat) 90%, transparent);
    }

    .input-shell {
      position: relative;
      min-width: 0;
      padding: 3px 35px 3px 8px;
      border: 1px solid var(--border);
      border-radius: 7px;
      background: var(--input-bg);
      box-shadow: inset 0 0 2px var(--shadow-soft);
    }

    .input-shell::after {
      content: "☺";
      position: absolute;
      right: 10px;
      top: 50%;
      transform: translateY(-50%);
      color: var(--subtle);
      opacity: .65;
      pointer-events: none;
    }

    #messageInput {
      display: block;
      width: 100%;
      min-height: 34px;
      max-height: 110px;
      padding: 6px 0;
      resize: vertical;
      border: 0;
      outline: 0;
      background: transparent;
      color: var(--text);
      line-height: 1.35;
    }

    .send-button {
      min-width: 70px;
      height: 40px;
      padding: 0 14px;
      color: color-mix(in srgb, var(--accent) 85%, var(--text));
      font-weight: 750;
    }

    .drawer-backdrop {
      position: absolute;
      inset: 0;
      z-index: 8;
      background: rgba(0,0,0,.18);
      opacity: 0;
      pointer-events: none;
      transition: opacity .18s ease;
    }

    .editor-drawer {
      position: absolute;
      top: 0;
      right: 0;
      bottom: 0;
      z-index: 9;
      width: min(430px, 94%);
      padding: 0 18px 22px;
      overflow-y: auto;
      background: var(--editor);
      border-left: 1px solid var(--border);
      box-shadow: -14px 0 38px var(--shadow-soft);
      transform: translateX(102%);
      transition: transform .2s ease, background .2s ease;
    }

    body.editor-open .drawer-backdrop { opacity: 1; pointer-events: auto; }
    body.editor-open .editor-drawer { transform: translateX(0); }

    .drawer-head {
      position: sticky;
      top: 0;
      z-index: 2;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      margin: 0 -18px 14px;
      padding: 13px 18px;
      background: color-mix(in srgb, var(--editor) 92%, transparent);
      border-bottom: 1px solid var(--border-soft);
      backdrop-filter: blur(12px);
    }

    .drawer-head h2 { margin: 0; font-size: 18px; }

    .field-label {
      display: block;
      margin: 12px 0 5px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
    }

    .editor-drawer input,
    .editor-drawer textarea,
    .editor-drawer select {
      width: 100%;
      padding: 8px 9px;
      border: 1px solid var(--border);
      border-radius: 7px;
      outline: none;
      background: var(--input-bg);
      color: var(--text);
      font-size: 13px;
    }

    .editor-drawer textarea { min-height: 72px; resize: vertical; line-height: 1.4; }
    .editor-drawer input:focus,
    .editor-drawer textarea:focus,
    .editor-drawer select:focus { border-color: var(--accent); box-shadow: 0 0 0 2px color-mix(in srgb, var(--accent) 18%, transparent); }

    .tag-grid { display: flex; flex-wrap: wrap; gap: 6px; }

    .tag {
      width: auto;
      margin: 0;
      padding: 5px 8px;
      border: 1px solid var(--border);
      border-radius: 999px;
      background: var(--button);
      color: var(--button-text);
      cursor: pointer;
      font-size: 11px;
    }

    .tag.selected { border-color: var(--accent); background: var(--accent-soft); color: var(--accent); }

    .drawer-actions { display: grid; grid-template-columns: 1fr 1fr; gap: 7px; margin-top: 16px; }
    .drawer-button { width: 100%; padding: 8px 10px; font-weight: 700; }
    .drawer-button.primary { grid-column: 1 / -1; border-color: color-mix(in srgb, var(--accent) 60%, var(--border)); color: var(--accent); }
    .drawer-button.danger { color: var(--danger); }

    .small-note { margin: 12px 0 0; color: var(--muted); font-size: 11px; line-height: 1.4; }

    .mobile-only { display: none; }

    @media (max-width: 760px) {
      .app-shell,
      body.sidebar-hidden .app-shell,
      body.focus-mode .app-shell {
        width: 100vw;
        height: 100vh;
        margin: 0;
        border: 0;
        border-radius: 0;
        grid-template-columns: minmax(0,1fr);
      }

      .conv-list-view {
        position: absolute;
        inset: 0 auto 0 0;
        width: min(310px, 88vw);
        z-index: 7;
        transform: translateX(-102%);
        opacity: 1 !important;
        pointer-events: auto !important;
        border-right-width: 1px !important;
        transition: transform .2s ease;
      }

      body.mobile-chats-open .conv-list-view { transform: translateX(0); }
      .mobile-only { display: inline-grid; }
      .chat-view__header { grid-template-columns: auto 38px minmax(0,1fr) auto; gap: 7px; padding-inline: 7px; }
      .message-stack { max-width: 79%; }
      #messages { padding-inline: 10px; }
      .header-actions .hide-on-mobile { display: none; }
      .chat-view__input { grid-template-columns: minmax(0,1fr) 62px; }
    }
  </style>
</head>
<body>
  <div class="app-shell">
    <aside class="conv-list-view" aria-label="Conversations">
      <div class="conv-list-view__header">
        <ul class="traffic-lights" aria-hidden="true"><li></li><li></li><li></li></ul>
        <div class="sidebar-tools">
          <button class="icon-button" type="button" title="Search characters" aria-label="Search characters" onclick="toggleSearch()">⌕</button>
        </div>
      </div>

      <div class="sidebar-title">
        <div class="sidebar-title-line">
          <h2>Chats</h2>
          <button class="icon-button" type="button" title="New character" aria-label="New character" onclick="newCharacter()">＋</button>
        </div>
        <div id="configText">Loading model...</div>
        <div id="searchWrap">
          <input id="characterSearch" type="search" placeholder="Search chats…" autocomplete="off" />
        </div>
      </div>

      <div id="characterList"></div>

      <div class="sidebar-footer">
        <button class="adium-button" type="button" onclick="newCharacter()">＋ New Character</button>
      </div>
    </aside>

    <main class="chat-view">
      <header class="chat-view__header">
        <div class="chat-header-left">
          <button class="icon-button mobile-only" type="button" title="Chats" aria-label="Chats" onclick="toggleChats()">☰</button>
          <button class="icon-button hide-on-mobile" type="button" title="Show or hide chats" aria-label="Show or hide chats" onclick="toggleChats()">☰</button>
        </div>

        <img id="avatar" alt="" />

        <div class="chat-meta">
          <div id="chatTitle">No character selected</div>
          <div id="chatSub">Create or choose a character.</div>
        </div>

        <div class="header-actions">
          <button id="themeToggle" class="icon-button" type="button" title="Switch to dark mode" aria-label="Switch theme" onclick="toggleTheme()">☾</button>
          <button class="icon-button hide-on-mobile" type="button" title="Reload history" aria-label="Reload history" onclick="loadHistory()">↻</button>
          <button class="icon-button hide-on-mobile" type="button" title="Focus mode" aria-label="Focus mode" onclick="toggleFocus()">⛶</button>
          <button class="icon-button" type="button" title="Character editor" aria-label="Character editor" onclick="toggleEditor()">•••</button>
        </div>
      </header>

      <section id="messages" aria-live="polite"></section>

      <footer class="chat-view__input">
        <div class="input-shell">
          <textarea id="messageInput" placeholder="Type your message…" aria-label="Message"></textarea>
        </div>
        <button class="send-button" type="button" onclick="sendMessage()">Send</button>
      </footer>
    </main>

    <div class="drawer-backdrop" onclick="closeEditor()" aria-hidden="true"></div>

    <aside class="editor-drawer" aria-label="Character editor">
      <div class="drawer-head">
        <h2 id="editorTitle">Create Character</h2>
        <button class="icon-button" type="button" title="Close editor" aria-label="Close editor" onclick="closeEditor()">×</button>
      </div>

      <label class="field-label" for="name">Name</label>
      <input id="name" placeholder="Flirting Lady" />

      <label class="field-label" for="gender">Gender</label>
      <select id="gender">
        <option>Female</option>
        <option>Male</option>
        <option>Non-binary</option>
        <option>Unknown</option>
      </select>

      <label class="field-label" for="profile_picture">Profile picture URL or base64 data URL</label>
      <input id="profile_picture" placeholder="https://... or data:image/png;base64,..." />

      <label class="field-label" for="intro">Intro, 1-400 letters</label>
      <textarea id="intro" maxlength="400" placeholder="{char} is a valiant knight who is silent and reserved."></textarea>

      <label class="field-label" for="greeting">Greeting, 1-1000 letters</label>
      <textarea id="greeting" maxlength="1000" placeholder="*She glances at him quietly.* I did not expect you here."></textarea>

      <label class="field-label" for="core_memory">Permanent core memory, never summarized</label>
      <textarea id="core_memory" maxlength="2500" placeholder="Important permanent facts about this character, relationship rules, setting, backstory, speaking style, or things the character must never forget."></textarea>

      <label class="field-label">Character Origin</label>
      <div class="tag-grid" data-group="character_origin">
        <button type="button" class="tag">OC</button><button type="button" class="tag">Fan-made</button><button type="button" class="tag">Anime</button>
        <button type="button" class="tag">Game</button><button type="button" class="tag">Movies&TV</button><button type="button" class="tag">Text Game</button>
        <button type="button" class="tag">Celebrity</button>
      </div>

      <label class="field-label">Identity</label>
      <div class="tag-grid" data-group="identity">
        <button type="button" class="tag">Badboy</button><button type="button" class="tag">Billionaire</button><button type="button" class="tag">Criminal</button>
        <button type="button" class="tag">Femboy</button><button type="button" class="tag">Friend</button><button type="button" class="tag">Killer</button>
        <button type="button" class="tag">Mafia</button><button type="button" class="tag">Non-human</button><button type="button" class="tag">Student</button>
        <button type="button" class="tag">Teacher</button><button type="button" class="tag">Tomboy</button><button type="button" class="tag">Vampire</button>
        <button type="button" class="tag">Werewolf</button><button type="button" class="tag">YouTuber</button>
      </div>

      <label class="field-label">Personality</label>
      <div class="tag-grid" data-group="personality">
        <button type="button" class="tag">Caring</button><button type="button" class="tag">Clingy</button><button type="button" class="tag">Clumsy</button>
        <button type="button" class="tag">Cold</button><button type="button" class="tag">Crazy</button><button type="button" class="tag">Cunning</button>
        <button type="button" class="tag">Cute</button><button type="button" class="tag">Dominant</button><button type="button" class="tag">Elegant</button>
        <button type="button" class="tag">Flirtatious</button><button type="button" class="tag">Funny</button><button type="button" class="tag">Gentle</button>
        <button type="button" class="tag">Highly-intelligent</button><button type="button" class="tag">Jealous</button><button type="button" class="tag">Loyal</button>
        <button type="button" class="tag">Manipulative</button><button type="button" class="tag">Mature</button>
      </div>

      <label class="field-label">Story</label>
      <div class="tag-grid" data-group="story">
        <button type="button" class="tag">Adventure</button><button type="button" class="tag">BL</button><button type="button" class="tag">Comedy</button>
        <button type="button" class="tag">Crime</button><button type="button" class="tag">Enemies to Lovers</button><button type="button" class="tag">Fantasy</button>
        <button type="button" class="tag">First Love</button><button type="button" class="tag">Forbidden Love</button><button type="button" class="tag">Friends to Lovers</button>
        <button type="button" class="tag">GL</button><button type="button" class="tag">Harem</button><button type="button" class="tag">Horror</button>
        <button type="button" class="tag">Omegaverse</button><button type="button" class="tag">Paranormal</button><button type="button" class="tag">Romance</button>
        <button type="button" class="tag">RPG</button><button type="button" class="tag">Sci-Fi</button><button type="button" class="tag">Slice of Life</button>
      </div>

      <label class="field-label" for="dialogue_style">Dialogue style example, one back-and-forth</label>
      <textarea id="dialogue_style" placeholder="User: Are you angry at me?&#10;Character: *She looks away, cheeks faintly red.* I did not say that..."></textarea>

      <label class="field-label" for="voice">Voice</label>
      <select id="voice">
        <option value="">Loading voices…</option>
      </select>
      <div id="voiceStatus" class="small-note">Loading voice choices…</div>

      <div class="drawer-actions">
        <button class="drawer-button primary" type="button" onclick="saveCharacter()">Save Character</button>
        <button class="drawer-button" type="button" onclick="resetMemory()">Reset Active Memory</button>
        <button class="drawer-button danger" type="button" onclick="deleteCharacter()">Delete Character</button>
      </div>

      <p class="small-note">Editing one of your bubbles creates a new timeline: every later message is permanently erased, prompt memory is rebuilt only from the retained branch, and the character answers the edited message again.</p>
    </aside>
  </div>

<script>
let characters = [];
let selectedCharacter = null;
let availableVoices = [];
let currentAudio = null;
let currentAudioUrl = null;
let currentVoiceButton = null;
let currentVoiceAbort = null;

function isMobileLayout() {
  return window.matchMedia("(max-width: 760px)").matches;
}

function setTheme(theme) {
  const safeTheme = theme === "dark" ? "dark" : "light";
  document.body.dataset.theme = safeTheme;
  localStorage.setItem("fable-theme", safeTheme);

  const btn = document.getElementById("themeToggle");
  if (btn) {
    const dark = safeTheme === "dark";
    btn.textContent = dark ? "☀" : "☾";
    btn.title = dark ? "Switch to light mode" : "Switch to dark mode";
    btn.setAttribute("aria-label", btn.title);
  }
}

function toggleTheme() {
  setTheme(document.body.dataset.theme === "dark" ? "light" : "dark");
}

function initTheme() {
  const saved = localStorage.getItem("fable-theme");
  if (saved === "light" || saved === "dark") {
    setTheme(saved);
    return;
  }
  setTheme("light");
}

function toggleSearch() {
  document.body.classList.toggle("search-open");
  if (document.body.classList.contains("search-open")) {
    setTimeout(() => document.getElementById("characterSearch")?.focus(), 80);
  } else {
    const input = document.getElementById("characterSearch");
    if (input) {
      input.value = "";
      renderCharacterList();
    }
  }
}

function toggleChats() {
  if (isMobileLayout()) {
    document.body.classList.toggle("mobile-chats-open");
    return;
  }
  document.body.classList.remove("focus-mode");
  document.body.classList.toggle("sidebar-hidden");
}

function toggleEditor() {
  document.body.classList.toggle("editor-open");
  if (isMobileLayout()) document.body.classList.remove("mobile-chats-open");
}

function closeEditor() {
  document.body.classList.remove("editor-open");
}

function toggleFocus() {
  document.body.classList.toggle("focus-mode");
  if (document.body.classList.contains("focus-mode")) {
    document.body.classList.remove("sidebar-hidden", "editor-open", "mobile-chats-open");
  }
}

async function loadConfig() {
  try {
    const res = await fetch("/api/config");
    const cfg = await res.json();
    const tts = cfg.fish_ready ? ` · voice ${cfg.tts_model}` : " · voice fallback";
    document.getElementById("configText").textContent = `${cfg.provider} · ${cfg.model}${tts}`;
  } catch (err) {
    document.getElementById("configText").textContent = "Model status unavailable";
  }
}

function voiceKey(provider, id) {
  return `${provider || "edge"}::${id || ""}`;
}

async function loadVoiceOptions(preferredCharacter=null, refresh=false) {
  const select = document.getElementById("voice");
  const status = document.getElementById("voiceStatus");
  if (!select) return;

  select.disabled = true;
  select.innerHTML = `<option value="">Loading voices…</option>`;
  if (status) status.textContent = "Loading voice choices…";

  try {
    const res = await fetch(`/api/voices${refresh ? "?refresh=1" : ""}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Could not load voices.");

    availableVoices = Array.isArray(data.voices) ? data.voices : [];
    select.innerHTML = "";

    for (const voice of availableVoices) {
      const option = document.createElement("option");
      option.value = voiceKey(voice.provider, voice.id);
      option.textContent = voice.name || voice.id || "Voice";
      option.dataset.provider = voice.provider || "edge";
      option.dataset.voiceId = voice.id || "";
      option.dataset.voiceName = voice.name || voice.id || "Voice";
      if (voice.description) option.title = voice.description;
      select.appendChild(option);
    }

    const preferredProvider = preferredCharacter?.voice_provider || "edge";
    const preferredId = preferredCharacter?.voice_id || preferredCharacter?.voice || "";
    const preferredName = preferredCharacter?.voice_name || preferredCharacter?.voice || preferredId;
    const preferredKey = voiceKey(preferredProvider, preferredId);

    if (preferredId && !Array.from(select.options).some(o => o.value === preferredKey)) {
      const oldOption = document.createElement("option");
      oldOption.value = preferredKey;
      oldOption.textContent = preferredName || preferredId;
      oldOption.dataset.provider = preferredProvider;
      oldOption.dataset.voiceId = preferredId;
      oldOption.dataset.voiceName = preferredName || preferredId;
      select.insertBefore(oldOption, select.firstChild);
    }

    if (preferredId) select.value = preferredKey;
    if (!select.value && select.options.length) select.selectedIndex = 0;
    select.disabled = false;

    if (status) {
      status.textContent = data.human_like
        ? `${availableVoices.length} Fish Audio voices · ${data.model}`
        : (data.warning || "Legacy Edge voices are being used. Set FISH_API_KEY for human-like voices.");
    }
  } catch (err) {
    availableVoices = [];
    select.innerHTML = `<option value="edge::Soft-spoken girl" data-provider="edge" data-voice-id="Soft-spoken girl" data-voice-name="Soft-spoken girl">Soft-spoken girl</option>`;
    select.disabled = false;
    if (status) status.textContent = "Voice list failed: " + err.message;
  }
}

function stopVoicePlayback() {
  if (currentVoiceAbort) {
    currentVoiceAbort.abort();
    currentVoiceAbort = null;
  }

  if (currentAudio) {
    currentAudio.pause();
    currentAudio.currentTime = 0;
    currentAudio.src = "";
    currentAudio = null;
  }

  if (currentAudioUrl) {
    URL.revokeObjectURL(currentAudioUrl);
    currentAudioUrl = null;
  }

  if (currentVoiceButton) {
    currentVoiceButton.textContent = "🔊 Voice";
    currentVoiceButton.setAttribute("aria-pressed", "false");
  }
  currentVoiceButton = null;
}

async function playVoice(text, button) {
  text = stripAction(text);
  if (!text || !selectedCharacter) return;

  // Same button: second click means STOP, including while the TTS request is still running.
  if (currentVoiceButton === button) {
    stopVoicePlayback();
    return;
  }

  // A different voice button takes over playback.
  stopVoicePlayback();
  currentVoiceButton = button;
  button.textContent = "■ Stop";
  button.setAttribute("aria-pressed", "true");

  const controller = new AbortController();
  currentVoiceAbort = controller;

  try {
    const res = await fetch("/api/tts", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      signal: controller.signal,
      body: JSON.stringify({
        text,
        character_id: selectedCharacter.id
      })
    });

    if (controller.signal.aborted || currentVoiceButton !== button) return;

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || "TTS failed.");
    }

    const blob = await res.blob();
    if (controller.signal.aborted || currentVoiceButton !== button) return;

    currentVoiceAbort = null;
    currentAudioUrl = URL.createObjectURL(blob);
    currentAudio = new Audio(currentAudioUrl);
    currentAudio.onended = stopVoicePlayback;
    currentAudio.onerror = stopVoicePlayback;
    await currentAudio.play();
  } catch (err) {
    if (err.name === "AbortError") return;
    stopVoicePlayback();
    alert("Voice playback failed: " + err.message);
  }
}

function getSelectedTags() {
  const tags = {character_origin: [], identity: [], personality: [], story: []};
  document.querySelectorAll(".tag-grid").forEach(grid => {
    const group = grid.dataset.group;
    grid.querySelectorAll(".tag.selected").forEach(btn => tags[group].push(btn.textContent.trim()));
  });
  return tags;
}

function setSelectedTags(tags) {
  document.querySelectorAll(".tag").forEach(btn => btn.classList.remove("selected"));
  document.querySelectorAll(".tag-grid").forEach(grid => {
    const group = grid.dataset.group;
    const values = tags?.[group] || [];
    grid.querySelectorAll(".tag").forEach(btn => {
      if (values.includes(btn.textContent.trim())) btn.classList.add("selected");
    });
  });
}

document.querySelectorAll(".tag").forEach(btn => {
  btn.addEventListener("click", e => {
    e.preventDefault();
    btn.classList.toggle("selected");
  });
});

function setImage(img, url) {
  if (!img) return;
  const clean = String(url || "").trim();
  if (!clean) {
    img.removeAttribute("src");
    return;
  }
  img.src = clean;
  img.onerror = () => img.removeAttribute("src");
}

async function loadCharacters() {
  const res = await fetch("/api/characters");
  characters = await res.json();
  renderCharacterList();
}

function renderCharacterList() {
  const list = document.getElementById("characterList");
  const query = document.getElementById("characterSearch")?.value.trim().toLowerCase() || "";
  list.innerHTML = "";

  const filtered = characters.filter(c => {
    if (!query) return true;
    return [c.name, c.intro, c._last_message, c.voice]
      .some(value => String(value || "").toLowerCase().includes(query));
  });

  if (!filtered.length) {
    const empty = document.createElement("div");
    empty.className = "system-note";
    empty.textContent = query ? "No matching chats." : "No characters yet.";
    list.appendChild(empty);
    return;
  }

  filtered.forEach(c => {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "char-card" + (selectedCharacter?.id === c.id ? " active" : "");
    card.onclick = () => selectCharacter(c.id);

    const status = document.createElement("span");
    status.className = "status-dot";

    const img = document.createElement("img");
    img.className = "char-avatar";
    img.alt = "";
    setImage(img, c.profile_picture || "");

    const copy = document.createElement("div");
    copy.className = "char-copy";

    const name = document.createElement("div");
    name.className = "char-name";
    name.textContent = c.name || "Unnamed";

    const meta = document.createElement("div");
    meta.className = "char-meta";
    meta.textContent = c._last_message || c.intro || "No messages yet";

    const voice = document.createElement("div");
    voice.className = "char-voice";
    voice.textContent = c.voice_name || c.voice || "Voice";

    copy.append(name, meta, voice);
    card.append(status, img, copy);
    list.appendChild(card);
  });
}

async function newCharacter() {
  stopVoicePlayback();
  selectedCharacter = null;
  document.getElementById("editorTitle").textContent = "Create Character";
  document.getElementById("name").value = "";
  document.getElementById("gender").value = "Female";
  document.getElementById("profile_picture").value = "";
  document.getElementById("intro").value = "";
  document.getElementById("greeting").value = "";
  document.getElementById("core_memory").value = "";
  document.getElementById("dialogue_style").value = "";
  setSelectedTags({});
  await loadVoiceOptions(null);

  document.getElementById("chatTitle").textContent = "No character selected";
  document.getElementById("chatSub").textContent = "Create or choose a character.";
  setImage(document.getElementById("avatar"), "");
  document.getElementById("messages").innerHTML = "";

  document.body.classList.remove("focus-mode", "sidebar-hidden", "mobile-chats-open");
  document.body.classList.add("editor-open");
  renderCharacterList();
}

async function selectCharacter(id) {
  const res = await fetch("/api/characters/" + encodeURIComponent(id));
  const data = await res.json();
  if (!res.ok) {
    alert(data.error || "Could not load character.");
    return;
  }

  stopVoicePlayback();
  selectedCharacter = data;
  document.getElementById("editorTitle").textContent = "Update Character";
  document.getElementById("name").value = selectedCharacter.name || "";
  document.getElementById("gender").value = selectedCharacter.gender || "Unknown";
  document.getElementById("profile_picture").value = selectedCharacter.profile_picture || "";
  document.getElementById("intro").value = selectedCharacter.intro || "";
  document.getElementById("greeting").value = selectedCharacter.greeting || "";
  document.getElementById("core_memory").value = selectedCharacter.core_memory || "";
  document.getElementById("dialogue_style").value = selectedCharacter.dialogue_style || "";
  setSelectedTags(selectedCharacter.tags || {});
  await loadVoiceOptions(selectedCharacter);

  document.getElementById("chatTitle").textContent = selectedCharacter.name;
  document.getElementById("chatSub").textContent = selectedCharacter.intro;
  setImage(document.getElementById("avatar"), selectedCharacter.profile_picture || "");

  document.body.classList.remove("mobile-chats-open");
  await loadCharacters();
  await loadHistory();
}

async function loadHistory() {
  stopVoicePlayback();
  const box = document.getElementById("messages");
  if (!selectedCharacter) {
    box.innerHTML = "";
    return;
  }

  box.innerHTML = "";
  addSystemNote("Loading previous conversation...");

  try {
    const res = await fetch(`/api/characters/${selectedCharacter.id}/history?limit=10000`);
    const history = await res.json();
    box.innerHTML = "";

    if (!res.ok) {
      addSystemNote(history.error || "Could not load history.");
      return;
    }

    if (!Array.isArray(history) || history.length === 0) {
      addSystemNote("No previous messages yet. This conversation will continue here.");
      return;
    }

    for (const msg of history) {
      addMessage(
        msg.role === "user" ? "user" : "bot",
        msg.content || "",
        msg.heart_whisper || "",
        false,
        msg.id || null
      );
    }

    requestAnimationFrame(() => { box.scrollTop = box.scrollHeight; });
  } catch (err) {
    box.innerHTML = "";
    addSystemNote("Could not load history: " + err.message);
  }
}

async function saveCharacter() {
  const voiceSelect = document.getElementById("voice");
  const voiceOption = voiceSelect.options[voiceSelect.selectedIndex];
  const voiceProvider = voiceOption?.dataset.provider || "edge";
  const voiceId = voiceOption?.dataset.voiceId || voiceOption?.value?.split("::").slice(1).join("::") || "Soft-spoken girl";
  const voiceName = voiceOption?.dataset.voiceName || voiceOption?.textContent || voiceId;

  const body = {
    id: selectedCharacter?.id,
    name: document.getElementById("name").value,
    gender: document.getElementById("gender").value,
    profile_picture: document.getElementById("profile_picture").value,
    intro: document.getElementById("intro").value,
    greeting: document.getElementById("greeting").value,
    core_memory: document.getElementById("core_memory").value,
    tags: getSelectedTags(),
    dialogue_style: document.getElementById("dialogue_style").value,
    voice: voiceName,
    voice_name: voiceName,
    voice_provider: voiceProvider,
    voice_id: voiceId,
  };

  const method = selectedCharacter ? "PUT" : "POST";
  const url = selectedCharacter ? `/api/characters/${selectedCharacter.id}` : "/api/characters";
  const res = await fetch(url, {
    method,
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body)
  });

  const data = await res.json();
  if (!res.ok) {
    alert(data.error || "Save failed.");
    return;
  }

  selectedCharacter = data;
  await loadCharacters();
  await selectCharacter(data.id);
  closeEditor();
}

async function resetMemory() {
  if (!selectedCharacter) {
    alert("Select a character first.");
    return;
  }
  if (!confirm("Reset active prompt memory? Full visible history will remain.")) return;
  await fetch(`/api/characters/${selectedCharacter.id}/memory`, {method: "DELETE"});
  await loadHistory();
}

async function deleteCharacter() {
  if (!selectedCharacter) {
    alert("Select a character first.");
    return;
  }
  if (!confirm(`Delete ${selectedCharacter.name}, including memory and local history?`)) return;
  stopVoicePlayback();
  await fetch(`/api/characters/${selectedCharacter.id}`, {method: "DELETE"});
  selectedCharacter = null;
  closeEditor();
  document.getElementById("chatTitle").textContent = "No character selected";
  document.getElementById("chatSub").textContent = "Create or choose a character.";
  setImage(document.getElementById("avatar"), "");
  document.getElementById("messages").innerHTML = "";
  await loadCharacters();
}

function addSystemNote(text) {
  const box = document.getElementById("messages");
  const div = document.createElement("div");
  div.className = "system-note";
  div.textContent = text;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
}

function makeBotAvatar() {
  const img = document.createElement("img");
  img.className = "message-avatar";
  img.alt = "";
  setImage(img, selectedCharacter?.profile_picture || "");
  return img;
}

function makeUserAvatar() {
  const div = document.createElement("div");
  div.className = "message-avatar you-avatar";
  div.textContent = "You";
  div.setAttribute("aria-hidden", "true");
  return div;
}

function addMessage(type, text, heartWhisper="", shouldScroll=true, messageId=null) {
  const box = document.getElementById("messages");
  const row = document.createElement("div");
  row.className = `message-row ${type === "user" ? "send" : "bot"}`;
  if (messageId) row.dataset.messageId = messageId;

  const stack = document.createElement("div");
  stack.className = "message-stack";

  const bubble = document.createElement("div");
  bubble.className = "message-bubble";
  bubble.textContent = text;
  stack.appendChild(bubble);

  if (type === "bot") {
    const actions = document.createElement("div");
    actions.className = "message-actions";

    if (heartWhisper && heartWhisper.trim()) {
      const whisperText = heartWhisper.trim();
      const whisperBtn = document.createElement("button");
      whisperBtn.type = "button";
      whisperBtn.className = "message-action-button heart-whisper-button";
      whisperBtn.textContent = "♡ Heart Whisper";
      whisperBtn.setAttribute("aria-expanded", "false");

      const panel = document.createElement("div");
      panel.className = "heart-whisper-panel";
      panel.hidden = true;

      const label = document.createElement("span");
      label.className = "heart-whisper-label";
      label.textContent = "Private inner thought";

      const body = document.createElement("div");
      panel.append(label, body);

      whisperBtn.onclick = () => {
        const willOpen = panel.hidden;
        panel.hidden = !willOpen;
        whisperBtn.setAttribute("aria-expanded", String(willOpen));
        whisperBtn.textContent = willOpen ? "♥ Hide Heart Whisper" : "♡ Heart Whisper";

        if (willOpen) {
          body.textContent = whisperText;
          requestAnimationFrame(() => panel.scrollIntoView({block: "nearest", behavior: "smooth"}));
        } else {
          body.textContent = "";
        }
      };

      actions.appendChild(whisperBtn);
      stack.append(actions, panel);
    } else {
      stack.appendChild(actions);
    }

    const voiceBtn = document.createElement("button");
    voiceBtn.type = "button";
    voiceBtn.className = "message-action-button voice-button";
    voiceBtn.textContent = "🔊 Voice";
    voiceBtn.setAttribute("aria-pressed", "false");
    voiceBtn.onclick = () => playVoice(text, voiceBtn);

    actions.appendChild(voiceBtn);
    row.append(makeBotAvatar(), stack);
  } else {
    const actions = document.createElement("div");
    actions.className = "message-actions";

    if (messageId) {
      const editBtn = document.createElement("button");
      editBtn.type = "button";
      editBtn.className = "message-action-button";
      editBtn.dataset.action = "edit-message";
      editBtn.textContent = "✎ Edit";
      editBtn.onclick = () => beginEditMessage(row, bubble, actions, messageId, text);
      actions.appendChild(editBtn);
    }

    stack.appendChild(actions);
    row.append(stack, makeUserAvatar());
  }

  box.appendChild(row);
  if (shouldScroll) box.scrollTop = box.scrollHeight;
  return row;
}

function enableUserMessageEditing(row, messageId, text) {
  if (!row || !messageId) return;

  row.dataset.messageId = messageId;
  const bubble = row.querySelector(".message-bubble");
  const actions = row.querySelector(".message-actions");
  if (!bubble || !actions) return;

  // A freshly sent bubble is rendered before the server has assigned its durable ID.
  // Once /api/chat returns, attach the edit control in place instead of reloading the
  // entire history (which used to reset the scroll position and cause the jump).
  if (actions.querySelector("[data-action='edit-message']")) return;

  const editBtn = document.createElement("button");
  editBtn.type = "button";
  editBtn.className = "message-action-button";
  editBtn.dataset.action = "edit-message";
  editBtn.textContent = "✎ Edit";
  editBtn.onclick = () => beginEditMessage(row, bubble, actions, messageId, text);
  actions.appendChild(editBtn);
}

function beginEditMessage(row, bubble, actions, messageId, originalText) {
  if (!selectedCharacter || !messageId || row.dataset.editing === "true") return;
  row.dataset.editing = "true";

  const wrap = document.createElement("div");
  wrap.className = "message-edit-wrap";

  const area = document.createElement("textarea");
  area.className = "message-edit-area";
  area.value = originalText;
  area.setAttribute("aria-label", "Edit your message");

  const note = document.createElement("div");
  note.className = "message-edit-note";
  note.textContent = "Saving rewrites the timeline from this message onward.";

  const editActions = document.createElement("div");
  editActions.className = "message-actions";

  const saveBtn = document.createElement("button");
  saveBtn.type = "button";
  saveBtn.className = "message-action-button";
  saveBtn.textContent = "Save & branch";

  const cancelBtn = document.createElement("button");
  cancelBtn.type = "button";
  cancelBtn.className = "message-action-button";
  cancelBtn.textContent = "Cancel";

  const restore = () => {
    wrap.replaceWith(bubble);
    editActions.replaceWith(actions);
    row.dataset.editing = "false";
  };

  cancelBtn.onclick = restore;
  saveBtn.onclick = async () => {
    const edited = area.value.trim();
    if (!edited) {
      alert("Message cannot be empty.");
      return;
    }
    if (edited === originalText) {
      restore();
      return;
    }

    if (!confirm("Save this edit? Every later message in this conversation will be permanently erased and the character will answer this edited message as a new timeline.")) {
      return;
    }

    saveBtn.disabled = true;
    cancelBtn.disabled = true;
    saveBtn.textContent = "Branching…";
    stopVoicePlayback();

    try {
      const res = await fetch(`/api/characters/${encodeURIComponent(selectedCharacter.id)}/messages/${encodeURIComponent(messageId)}`, {
        method: "PUT",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({content: edited})
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Edit failed.");

      await loadHistory();
      await loadCharacters();
    } catch (err) {
      alert("Could not edit message: " + err.message);
      saveBtn.disabled = false;
      cancelBtn.disabled = false;
      saveBtn.textContent = "Save & branch";
    }
  };

  editActions.append(saveBtn, cancelBtn);
  wrap.append(area, note);
  bubble.replaceWith(wrap);
  actions.replaceWith(editActions);
  area.focus();
  area.setSelectionRange(area.value.length, area.value.length);
}

function stripAction(text) {
  return String(text || "").replace(/\*[^*]+\*/g, "").trim();
}

async function sendMessage() {
  if (!selectedCharacter) {
    alert("Create or select a character first.");
    return;
  }

  const input = document.getElementById("messageInput");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";

  const userRow = addMessage("user", text);
  const pending = addMessage("bot", "*thinking quietly* ...");
  pending.dataset.pending = "true";

  try {
    const res = await fetch(`/api/chat/${selectedCharacter.id}`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({message: text})
    });

    const data = await res.json();
    pending.remove();

    if (!res.ok) {
      userRow.remove();
      addMessage("bot", "*the system flickers* " + (data.error || "Something went wrong."));
      return;
    }

    // The API already returns stable IDs for both durable history rows. Keep the existing
    // DOM in place instead of reloading the entire conversation: rebuilding #messages
    // reset scrollTop and, combined with smooth scrolling, caused the visible jump-to-top
    // followed by a long animated scroll back to the bottom on every send.
    enableUserMessageEditing(userRow, data.user_message_id, text);
    addMessage("bot", data.reply || "...", data.heart_whisper || "", true, data.assistant_message_id || null);
    await loadCharacters();
  } catch (err) {
    pending.remove();
    userRow.remove();
    addMessage("bot", "*the system flickers* " + err.message);
  }
}

document.getElementById("messageInput").addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

document.getElementById("characterSearch").addEventListener("input", renderCharacterList);

window.addEventListener("keydown", e => {
  if (e.key === "Escape") {
    closeEditor();
    document.body.classList.remove("mobile-chats-open");
  }

  if (e.ctrlKey && e.key.toLowerCase() === "b") {
    e.preventDefault();
    toggleChats();
  }

  if (e.ctrlKey && e.key.toLowerCase() === "e") {
    e.preventDefault();
    toggleEditor();
  }

  if (e.ctrlKey && e.key.toLowerCase() === "f") {
    e.preventDefault();
    toggleFocus();
  }

  if (e.ctrlKey && e.key.toLowerCase() === "d") {
    e.preventDefault();
    toggleTheme();
  }
});

window.addEventListener("resize", () => {
  if (!isMobileLayout()) document.body.classList.remove("mobile-chats-open");
});

initTheme();
loadConfig();
loadVoiceOptions();
loadCharacters();
</script>
</body>
</html>

"""

if __name__ == "__main__":
    print(f"Provider: {CHAT_PROVIDER}")
    print(f"Model: {OPENROUTER_MODEL if CHAT_PROVIDER == 'openrouter' else groq_display_model()}")
    print("Open: http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=True)
