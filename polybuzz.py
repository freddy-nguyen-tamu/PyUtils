import os
import re
import json
import time
import uuid
import asyncio
import requests
import edge_tts

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

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.1-8b-instruct:free")
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

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
    voice = str(raw.get("voice") or "Soft-spoken girl").strip()[:80]
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
        "voice": voice,
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


def append_history(character_id, role, content, heart_whisper="", meta=None):
    item = {
        "role": role,
        "content": content,
        "heart_whisper": heart_whisper or "",
        "meta": meta or {},
        "created_at": now_iso(),
    }

    append_jsonl(history_path(character_id), item)


def provider_settings():
    if CHAT_PROVIDER == "openrouter":
        if not OPENROUTER_API_KEY:
            raise RuntimeError("OPENROUTER_API_KEY is missing.")

        return {
            "url": OPENROUTER_API_URL,
            "model": OPENROUTER_MODEL,
            "headers": {
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "http://localhost:5000",
                "X-Title": "Local Character Chat",
            },
        }

    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is missing.")

    return {
        "url": GROQ_API_URL,
        "model": GROQ_MODEL,
        "headers": {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
        },
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


def chat_completion(messages, temperature=0.85, max_tokens=600, json_mode=False, retries=6):
    settings = provider_settings()

    payload = {
        "model": settings["model"],
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    backoff = 1.0

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
- Heart whisper is the character's private inner thinking about the male user.
- Heart whisper must use he, him, or his when referring to the user.
- Heart whisper must never be written from the user's point of view.
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
  "heart_whisper": "short fictional private inner thought about him, using he/him/his",
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
        {"role": "system", "content": "Convert the text into valid JSON only."},
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


def fix_heart_whisper(obj):
    heart = str(obj.get("heart_whisper") or "").strip()

    if not heart:
        heart = "He is harder to read than I expected, and that makes me want to know him more."

    bad_user_pov_markers = [
        "i wonder what she",
        "i wonder what he thinks of me",
        "i should tell her",
        "i should tell him",
        "my heart",
        "my feelings",
        "she makes me",
        "he makes me feel like a user",
    ]

    lower = heart.lower()

    if any(x in lower for x in bad_user_pov_markers):
        heart = "He is harder to read than I expected, and that makes me want to know him more."

    if not re.search(r"\b(he|him|his)\b", heart, flags=re.I):
        heart = heart.rstrip(".") + ", but he probably should not know that yet."

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


@app.route("/")
def index():
    return Response(INDEX_HTML, mimetype="text/html")


@app.route("/api/config")
def api_config():
    return jsonify({
        "provider": CHAT_PROVIDER,
        "model": OPENROUTER_MODEL if CHAT_PROVIDER == "openrouter" else GROQ_MODEL,
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
    return jsonify(read_jsonl(history_path(character_id), limit=limit))


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
    messages = build_messages(character, memory, user_message)

    try:
        raw_reply = chat_completion(
            messages,
            temperature=0.9,
            max_tokens=650,
            json_mode=True,
        )

        obj = repair_character_json(raw_reply)

        visible = format_assistant_visible(obj)
        heart_whisper = fix_heart_whisper(obj)
        memory_update = obj.get("memory_update") or []

        add_recent_message(memory, "user", user_message)
        add_recent_message(memory, "assistant", visible, heart_whisper=heart_whisper)

        append_history(character_id, "user", user_message)
        append_history(character_id, "assistant", visible, heart_whisper=heart_whisper)

        if isinstance(memory_update, list):
            facts = memory.get("facts", [])

            for fact in memory_update:
                fact = str(fact or "").strip()

                if fact:
                    facts.append(fact)

            memory["facts"] = dedupe_facts(facts)

        memory = maybe_summarize_memory(character, memory)
        save_memory(character_id, memory)

        return jsonify({
            "reply": visible,
            "action": obj.get("action", ""),
            "speech": obj.get("speech", ""),
            "heart_whisper": heart_whisper,
            "memory_update": memory_update,
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/tts", methods=["POST"])
def api_tts():
    try:
        raw = request.get_json(force=True)
        text = clean_tts_text(raw.get("text") or "")
        voice_style = str(raw.get("voice") or "Soft-spoken girl")

        if not text:
            return jsonify({"error": "No text to speak."}), 400

        audio_data = asyncio.run(make_edge_tts_mp3(text, voice_style))

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
  <title>Local Character Chat</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #0f0f10;
      --sidebar: #151518;
      --panel: #1d1d21;
      --panel2: #29292e;
      --text: #eeeeee;
      --muted: #a7a7ad;
      --accent: #8b5cf6;
      --border: #37373d;
      --bubble-user: #3b2d69;
      --bubble-bot: #202024;
    }

    * {
      box-sizing: border-box;
    }

    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: Arial, sans-serif;
      height: 100vh;
      overflow: hidden;
      display: grid;
      grid-template-columns: 320px 390px 1fr;
      transition: grid-template-columns 0.18s ease;
    }

    body.hide-chats {
      grid-template-columns: 0 390px 1fr;
    }

    body.hide-editor {
      grid-template-columns: 320px 0 1fr;
    }

    body.hide-chats.hide-editor,
    body.focus-mode {
      grid-template-columns: 0 0 1fr;
    }

    .messenger,
    aside {
      overflow: hidden;
      min-width: 0;
      transition: opacity 0.18s ease;
    }

    body.hide-chats .messenger,
    body.focus-mode .messenger {
      opacity: 0;
      pointer-events: none;
      border-right: 0;
    }

    body.hide-editor aside,
    body.focus-mode aside {
      opacity: 0;
      pointer-events: none;
      border-right: 0;
    }

    .messenger {
      background: #111114;
      border-right: 1px solid var(--border);
      display: grid;
      grid-template-rows: auto 1fr auto;
      overflow: hidden;
    }

    .messenger-head {
      padding: 18px;
      border-bottom: 1px solid var(--border);
    }

    .messenger-head h2 {
      margin: 0;
      font-size: 22px;
    }

    #characterList {
      overflow: auto;
      padding: 10px;
      display: grid;
      gap: 8px;
      align-content: start;
    }

    .char-card {
      display: grid;
      grid-template-columns: 48px 1fr;
      gap: 10px;
      background: transparent;
      border: 1px solid transparent;
      border-radius: 14px;
      padding: 9px;
      cursor: pointer;
    }

    .char-card:hover {
      background: #1a1a1f;
    }

    .char-card.active {
      background: #221b35;
      border-color: var(--accent);
    }

    .char-avatar {
      width: 48px;
      height: 48px;
      border-radius: 999px;
      object-fit: cover;
      background: #333;
    }

    .char-name {
      font-weight: bold;
      margin-top: 3px;
    }

    .char-meta {
      color: var(--muted);
      font-size: 12px;
      margin-top: 3px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .messenger-foot {
      padding: 12px;
      border-top: 1px solid var(--border);
    }

    aside {
      border-right: 1px solid var(--border);
      overflow-y: auto;
      padding: 18px;
      background: var(--sidebar);
    }

    main {
      display: grid;
      grid-template-rows: auto 1fr auto;
      height: 100vh;
      overflow: hidden;
      min-width: 0;
    }

    h1, h2, h3 {
      margin: 0 0 12px;
    }

    label {
      display: block;
      margin: 12px 0 5px;
      color: var(--muted);
      font-size: 13px;
    }

    input, textarea, select, button {
      width: 100%;
      padding: 10px;
      border-radius: 10px;
      border: 1px solid var(--border);
      background: var(--panel2);
      color: var(--text);
      font-size: 14px;
    }

    textarea {
      min-height: 70px;
      resize: vertical;
    }

    button {
      cursor: pointer;
      background: #2d2545;
      border-color: #4c3b78;
      margin-top: 10px;
      font-weight: bold;
    }

    button:hover {
      border-color: var(--accent);
    }

    .secondary-button {
      background: #232329;
      border-color: #42424a;
    }

    .danger-button {
      background: #3a1d25;
      border-color: #6d2636;
    }

    .tag-grid {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin: 7px 0;
    }

    .tag {
      width: auto;
      padding: 8px 11px;
      margin: 0;
      border-radius: 9px;
      background: #303034;
      border: 1px solid #444;
      color: #ccc;
      font-size: 13px;
      font-weight: normal;
    }

    .tag.selected {
      background: #3b2d69;
      border-color: var(--accent);
      color: white;
    }

    header {
      padding: 12px 18px;
      border-bottom: 1px solid var(--border);
      background: #151518;
      display: grid;
      grid-template-columns: auto 52px 1fr auto;
      align-items: center;
      gap: 13px;
    }

    .top-controls {
      display: flex;
      gap: 7px;
      align-items: center;
    }

    .top-controls button {
      width: auto;
      margin: 0;
      padding: 8px 10px;
      font-size: 12px;
      white-space: nowrap;
    }

    #avatar {
      width: 52px;
      height: 52px;
      border-radius: 999px;
      background: #333;
      object-fit: cover;
    }

    #chatTitle {
      font-size: 20px;
      font-weight: bold;
    }

    #chatSub {
      color: var(--muted);
      font-size: 13px;
      margin-top: 3px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    #messages {
      overflow: auto;
      padding: 20px;
      display: flex;
      flex-direction: column;
      gap: 13px;
      scroll-behavior: smooth;
    }

    .msg {
      max-width: min(75%, 850px);
      padding: 12px 14px;
      border-radius: 15px;
      white-space: pre-wrap;
      line-height: 1.45;
      color: var(--text);            /* <-- FIX: explicitly set text color */
    }

    body.focus-mode .msg {
      max-width: min(68%, 950px);
    }

    .user {
      align-self: flex-end;
      background: var(--bubble-user);
    }

    .bot {
      align-self: flex-start;
      background: var(--bubble-bot);
      border: 1px solid var(--border);
    }

    .system-note {
      align-self: center;
      max-width: 70%;
      padding: 8px 12px;
      border-radius: 999px;
      background: #1b1b20;
      color: var(--muted);
      font-size: 12px;
      text-align: center;
    }

    .actions {
      display: flex;
      gap: 8px;
      margin-top: 9px;
    }

    .actions button {
      width: auto;
      padding: 7px 10px;
      font-size: 12px;
      margin: 0;
    }

    .heart-whisper {
      margin-top: 10px;
      padding: 9px 10px;
      border-left: 2px solid var(--accent);
      background: rgba(139, 92, 246, 0.10);
      color: #d8ccff;
      border-radius: 8px;
      font-size: 12px;
      line-height: 1.45;
      font-style: italic;
    }

    .heart-label {
      display: block;
      margin-bottom: 3px;
      color: #bfaaff;
      font-size: 11px;
      font-style: normal;
      font-weight: bold;
    }

    footer {
      border-top: 1px solid var(--border);
      padding: 14px;
      display: grid;
      grid-template-columns: 1fr 110px;
      gap: 10px;
      background: #151518;
    }

    #messageInput {
      min-height: 46px;
      max-height: 130px;
    }

    .small {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.35;
    }

    .divider {
      height: 1px;
      background: var(--border);
      margin: 18px 0;
    }

    .pill {
      display: inline-block;
      font-size: 11px;
      background: #33333a;
      color: #bbb;
      padding: 3px 6px;
      border-radius: 6px;
      margin-top: 4px;
    }

    @media (max-width: 950px) {
      body {
        grid-template-columns: 0 0 1fr;
      }

      .messenger,
      aside {
        opacity: 0;
        pointer-events: none;
      }

      body.mobile-show-chats {
        grid-template-columns: 320px 0 1fr;
      }

      body.mobile-show-editor {
        grid-template-columns: 0 390px 1fr;
      }

      body.mobile-show-chats .messenger,
      body.mobile-show-editor aside {
        opacity: 1;
        pointer-events: auto;
      }
    }


    /* FINAL LAYOUT FIX:
       Prevent hidden panels from stretching the chat thousands of pixels wide.
       This is the permanent version of the console fix that made user messages fit. */
    html,
    body {
      width: 100vw !important;
      max-width: 100vw !important;
      min-width: 0 !important;
      overflow: hidden !important;
    }

    body {
      display: grid !important;
      grid-template-columns: minmax(0, 320px) minmax(0, 390px) minmax(0, 1fr) !important;
    }

    body.focus-mode {
      grid-template-columns: minmax(0, 1fr) !important;
    }

    body.focus-mode > .messenger,
    body.focus-mode > aside {
      display: none !important;
      width: 0 !important;
      min-width: 0 !important;
      max-width: 0 !important;
      opacity: 0 !important;
      pointer-events: none !important;
      border-right: 0 !important;
    }

    body.hide-chats:not(.hide-editor):not(.focus-mode) {
      grid-template-columns: minmax(0, 390px) minmax(0, 1fr) !important;
    }

    body.hide-chats:not(.focus-mode) > .messenger {
      display: none !important;
      width: 0 !important;
      min-width: 0 !important;
      max-width: 0 !important;
      opacity: 0 !important;
      pointer-events: none !important;
      border-right: 0 !important;
    }

    body.hide-editor:not(.hide-chats):not(.focus-mode) {
      grid-template-columns: minmax(0, 320px) minmax(0, 1fr) !important;
    }

    body.hide-editor:not(.focus-mode) > aside {
      display: none !important;
      width: 0 !important;
      min-width: 0 !important;
      max-width: 0 !important;
      opacity: 0 !important;
      pointer-events: none !important;
      border-right: 0 !important;
    }

    body.hide-chats.hide-editor:not(.focus-mode) {
      grid-template-columns: minmax(0, 1fr) !important;
    }

    body.hide-chats.hide-editor > .messenger,
    body.hide-chats.hide-editor > aside {
      display: none !important;
      width: 0 !important;
      min-width: 0 !important;
      max-width: 0 !important;
      opacity: 0 !important;
      pointer-events: none !important;
      border-right: 0 !important;
    }

    main {
      width: 100% !important;
      max-width: 100vw !important;
      min-width: 0 !important;
      overflow: hidden !important;
      contain: inline-size !important;
    }

    header,
    footer,
    #messages {
      width: 100% !important;
      max-width: 100% !important;
      min-width: 0 !important;
    }

    header {
      grid-template-columns: auto 52px minmax(0, 1fr) auto !important;
    }

    footer {
      grid-template-columns: minmax(0, 1fr) 110px !important;
    }

    #messages {
      overflow-x: hidden !important;
      box-sizing: border-box !important;
      align-items: stretch !important;
    }

    .msg {
      box-sizing: border-box !important;
      max-width: min(75%, 850px) !important;
      min-width: 0 !important;
      overflow-wrap: anywhere !important;
      word-break: break-word !important;
      color: var(--text) !important;
      -webkit-text-fill-color: var(--text) !important;
    }

    body.focus-mode .msg {
      max-width: min(75%, 850px) !important;
    }

    .msg.user {
      align-self: flex-end !important;
      margin-left: auto !important;
      margin-right: 0 !important;
      transform: none !important;
      background: var(--bubble-user) !important;
    }

    .msg.bot {
      align-self: flex-start !important;
      margin-left: 0 !important;
      margin-right: auto !important;
      transform: none !important;
      background: var(--bubble-bot) !important;
      border: 1px solid var(--border) !important;
    }

    @media (max-width: 950px) {
      body:not(.mobile-show-chats):not(.mobile-show-editor):not(.focus-mode) {
        grid-template-columns: minmax(0, 1fr) !important;
      }

      body:not(.mobile-show-chats):not(.mobile-show-editor):not(.focus-mode) > .messenger,
      body:not(.mobile-show-chats):not(.mobile-show-editor):not(.focus-mode) > aside {
        display: none !important;
      }

      body.mobile-show-chats:not(.focus-mode) {
        grid-template-columns: minmax(0, 320px) minmax(0, 1fr) !important;
      }

      body.mobile-show-chats:not(.focus-mode) > .messenger {
        display: grid !important;
        opacity: 1 !important;
        pointer-events: auto !important;
        width: auto !important;
        min-width: 0 !important;
        max-width: none !important;
      }

      body.mobile-show-chats:not(.focus-mode) > aside {
        display: none !important;
      }

      body.mobile-show-editor:not(.focus-mode) {
        grid-template-columns: minmax(0, 390px) minmax(0, 1fr) !important;
      }

      body.mobile-show-editor:not(.focus-mode) > aside {
        display: block !important;
        opacity: 1 !important;
        pointer-events: auto !important;
        width: auto !important;
        min-width: 0 !important;
        max-width: none !important;
      }

      body.mobile-show-editor:not(.focus-mode) > .messenger {
        display: none !important;
      }
    }

  </style>
</head>

<body>
  <section class="messenger">
    <div class="messenger-head">
      <h2>Chats</h2>
      <div class="small" id="configText">Loading model...</div>
    </div>

    <div id="characterList"></div>

    <div class="messenger-foot">
      <button onclick="newCharacter()">+ New Character</button>
    </div>
  </section>

  <aside>
    <h2 id="editorTitle">Create Character</h2>

    <label>Name</label>
    <input id="name" placeholder="Flirting Lady" />

    <label>Gender</label>
    <select id="gender">
      <option>Female</option>
      <option>Male</option>
      <option>Non-binary</option>
      <option>Unknown</option>
    </select>

    <label>Profile picture URL or base64 data URL</label>
    <input id="profile_picture" placeholder="https://... or data:image/png;base64,..." />

    <label>Intro, 1-400 letters</label>
    <textarea id="intro" maxlength="400" placeholder="{char} is a valiant knight who is silent and reserved."></textarea>

    <label>Greeting, 1-1000 letters</label>
    <textarea id="greeting" maxlength="1000" placeholder="*She glances at him quietly.* I did not expect you here."></textarea>

    <label>Permanent core memory, never summarized</label>
    <textarea id="core_memory" maxlength="2500" placeholder="Important permanent facts about this character, relationship rules, setting, backstory, speaking style, or things she must never forget."></textarea>

    <label>Character Origin</label>
    <div class="tag-grid" data-group="character_origin">
      <button class="tag">OC</button><button class="tag">Fan-made</button><button class="tag">Anime</button>
      <button class="tag">Game</button><button class="tag">Movies&TV</button><button class="tag">Text Game</button>
      <button class="tag">Celebrity</button>
    </div>

    <label>Identity</label>
    <div class="tag-grid" data-group="identity">
      <button class="tag">Badboy</button><button class="tag">Billionaire</button><button class="tag">Criminal</button>
      <button class="tag">Femboy</button><button class="tag">Friend</button><button class="tag">Killer</button>
      <button class="tag">Mafia</button><button class="tag">Non-human</button><button class="tag">Student</button>
      <button class="tag">Teacher</button><button class="tag">Tomboy</button><button class="tag">Vampire</button>
      <button class="tag">Werewolf</button><button class="tag">YouTuber</button>
    </div>

    <label>Personality</label>
    <div class="tag-grid" data-group="personality">
      <button class="tag">Caring</button><button class="tag">Clingy</button><button class="tag">Clumsy</button>
      <button class="tag">Cold</button><button class="tag">Crazy</button><button class="tag">Cunning</button>
      <button class="tag">Cute</button><button class="tag">Dominant</button><button class="tag">Elegant</button>
      <button class="tag">Flirtatious</button><button class="tag">Funny</button><button class="tag">Gentle</button>
      <button class="tag">Highly-intelligent</button><button class="tag">Jealous</button><button class="tag">Loyal</button>
      <button class="tag">Manipulative</button><button class="tag">Mature</button>
    </div>

    <label>Story</label>
    <div class="tag-grid" data-group="story">
      <button class="tag">Adventure</button><button class="tag">BL</button><button class="tag">Comedy</button>
      <button class="tag">Crime</button><button class="tag">Enemies to Lovers</button><button class="tag">Fantasy</button>
      <button class="tag">First Love</button><button class="tag">Forbidden Love</button><button class="tag">Friends to Lovers</button>
      <button class="tag">GL</button><button class="tag">Harem</button><button class="tag">Horror</button>
      <button class="tag">Omegaverse</button><button class="tag">Paranormal</button><button class="tag">Romance</button>
      <button class="tag">RPG</button><button class="tag">Sci-Fi</button><button class="tag">Slice of Life</button>
    </div>

    <label>Dialogue style example, one back-and-forth</label>
    <textarea id="dialogue_style" placeholder="User: Are you angry at me?
Character: *She looks away, cheeks faintly red.* I did not say that..."></textarea>

    <label>Voice</label>
    <select id="voice">
      <option>Flirting Lady</option>
      <option>Mature sister</option>
      <option>Sweet girl</option>
      <option>Soft-spoken girl</option>
      <option>Hoarse-voiced girl</option>
      <option>Elegant lady</option>
      <option>Mean girl</option>
      <option>Brainy girl</option>
      <option>Mysterious lady</option>
      <option>Deep male</option>
      <option>Calm male</option>
      <option>Energetic male</option>
    </select>

    <button onclick="saveCharacter()">Save Character</button>
    <button class="secondary-button" onclick="resetMemory()">Reset Active Memory</button>
    <button class="danger-button" onclick="deleteCharacter()">Delete Character</button>

    <p class="small">
      Full history loads into chat from local history. The prompt still only sends core memory, summary, facts, and recent messages.
    </p>
  </aside>

  <main>
    <header>
      <div class="top-controls">
        <button class="secondary-button" onclick="toggleChats()">☰ Chats</button>
        <button class="secondary-button" onclick="toggleEditor()">⚙ Editor</button>
        <button class="secondary-button" onclick="toggleFocus()">Focus</button>
      </div>

      <img id="avatar" />

      <div>
        <div id="chatTitle">No character selected</div>
        <div id="chatSub">Create or choose a character.</div>
      </div>

      <div class="top-controls">
        <button class="secondary-button" onclick="loadHistory()">Reload History</button>
      </div>
    </header>

    <section id="messages"></section>

    <footer>
      <textarea id="messageInput" placeholder="Type your message..."></textarea>
      <button onclick="sendMessage()">Send</button>
    </footer>
  </main>

<script>
let characters = [];
let selectedCharacter = null;
let currentAudio = null;

function isMobileLayout() {
  return window.matchMedia("(max-width: 950px)").matches;
}

function clearMobilePanels() {
  document.body.classList.remove("mobile-show-chats");
  document.body.classList.remove("mobile-show-editor");
}

function toggleChats() {
  if (isMobileLayout()) {
    document.body.classList.remove("focus-mode");
    document.body.classList.remove("mobile-show-editor");
    document.body.classList.toggle("mobile-show-chats");
    return;
  }

  document.body.classList.remove("focus-mode");
  document.body.classList.toggle("hide-chats");
}

function toggleEditor() {
  if (isMobileLayout()) {
    document.body.classList.remove("focus-mode");
    document.body.classList.remove("mobile-show-chats");
    document.body.classList.toggle("mobile-show-editor");
    return;
  }

  document.body.classList.remove("focus-mode");
  document.body.classList.toggle("hide-editor");
}

function toggleFocus() {
  clearMobilePanels();
  document.body.classList.toggle("focus-mode");
}

async function loadConfig() {
  const res = await fetch("/api/config");
  const cfg = await res.json();
  document.getElementById("configText").textContent = `${cfg.provider} · ${cfg.model}`;
}

async function playVoice(text) {
  text = stripAction(text);

  if (!text) return;

  try {
    if (currentAudio) {
      currentAudio.pause();
      currentAudio = null;
    }

    const res = await fetch("/api/tts", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        text,
        voice: selectedCharacter?.voice || "Soft-spoken girl"
      })
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      alert(err.error || "TTS failed.");
      return;
    }

    const blob = await res.blob();
    const url = URL.createObjectURL(blob);

    currentAudio = new Audio(url);
    currentAudio.onended = () => URL.revokeObjectURL(url);
    await currentAudio.play();

  } catch (err) {
    alert("Voice playback failed: " + err.message);
  }
}

function getSelectedTags() {
  const tags = {
    character_origin: [],
    identity: [],
    personality: [],
    story: []
  };

  document.querySelectorAll(".tag-grid").forEach(grid => {
    const group = grid.dataset.group;

    grid.querySelectorAll(".tag.selected").forEach(btn => {
      tags[group].push(btn.textContent.trim());
    });
  });

  return tags;
}

function setSelectedTags(tags) {
  document.querySelectorAll(".tag").forEach(btn => btn.classList.remove("selected"));

  document.querySelectorAll(".tag-grid").forEach(grid => {
    const group = grid.dataset.group;
    const values = tags?.[group] || [];

    grid.querySelectorAll(".tag").forEach(btn => {
      if (values.includes(btn.textContent.trim())) {
        btn.classList.add("selected");
      }
    });
  });
}

document.querySelectorAll(".tag").forEach(btn => {
  btn.addEventListener("click", e => {
    e.preventDefault();
    btn.classList.toggle("selected");
  });
});

async function loadCharacters() {
  const res = await fetch("/api/characters");
  characters = await res.json();

  const list = document.getElementById("characterList");
  list.innerHTML = "";

  characters.forEach(c => {
    const card = document.createElement("div");
    card.className = "char-card" + (selectedCharacter?.id === c.id ? " active" : "");
    card.onclick = () => selectCharacter(c.id);

    const avatar = c.profile_picture || "";
    const last = c._last_message || c.intro || "";

    card.innerHTML = `
      <img class="char-avatar" src="${escapeAttr(avatar)}" onerror="this.src='';">
      <div>
        <div class="char-name">${escapeHtml(c.name)}</div>
        <div class="char-meta">${escapeHtml(last)}</div>
        <span class="pill">${escapeHtml(c.voice || "Voice")}</span>
      </div>
    `;

    list.appendChild(card);
  });
}

function newCharacter() {
  selectedCharacter = null;

  document.getElementById("editorTitle").textContent = "Create Character";
  document.getElementById("name").value = "";
  document.getElementById("gender").value = "Female";
  document.getElementById("profile_picture").value = "";
  document.getElementById("intro").value = "";
  document.getElementById("greeting").value = "";
  document.getElementById("core_memory").value = "";
  document.getElementById("dialogue_style").value = "";
  document.getElementById("voice").value = "Soft-spoken girl";

  setSelectedTags({});

  document.getElementById("chatTitle").textContent = "No character selected";
  document.getElementById("chatSub").textContent = "Create or choose a character.";
  document.getElementById("avatar").src = "";
  document.getElementById("messages").innerHTML = "";

  document.body.classList.remove("focus-mode");
  document.body.classList.remove("hide-editor");
  clearMobilePanels();

  loadCharacters();
}

async function selectCharacter(id) {
  const res = await fetch("/api/characters/" + encodeURIComponent(id));
  selectedCharacter = await res.json();

  document.getElementById("editorTitle").textContent = "Update Character";
  document.getElementById("name").value = selectedCharacter.name || "";
  document.getElementById("gender").value = selectedCharacter.gender || "Unknown";
  document.getElementById("profile_picture").value = selectedCharacter.profile_picture || "";
  document.getElementById("intro").value = selectedCharacter.intro || "";
  document.getElementById("greeting").value = selectedCharacter.greeting || "";
  document.getElementById("core_memory").value = selectedCharacter.core_memory || "";
  document.getElementById("dialogue_style").value = selectedCharacter.dialogue_style || "";
  document.getElementById("voice").value = selectedCharacter.voice || "Soft-spoken girl";

  setSelectedTags(selectedCharacter.tags || {});

  document.getElementById("chatTitle").textContent = selectedCharacter.name;
  document.getElementById("chatSub").textContent = selectedCharacter.intro;
  document.getElementById("avatar").src = selectedCharacter.profile_picture || "";

  clearMobilePanels();

  await loadCharacters();
  await loadHistory();
}

async function loadHistory() {
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

    if (!Array.isArray(history) || history.length === 0) {
      addSystemNote("No previous messages yet. This conversation will continue here.");
      return;
    }

    for (const msg of history) {
      addMessage(
        msg.role === "user" ? "user" : "bot",
        msg.content || "",
        msg.heart_whisper || "",
        false
      );
    }

    requestAnimationFrame(() => {
      box.scrollTop = box.scrollHeight;
    });

  } catch (err) {
    box.innerHTML = "";
    addSystemNote("Could not load history: " + err.message);
  }
}

async function saveCharacter() {
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
    voice: document.getElementById("voice").value,
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
}

async function resetMemory() {
  if (!selectedCharacter) {
    alert("Select a character first.");
    return;
  }

  if (!confirm("Reset active prompt memory? Full visible history will remain.")) {
    return;
  }

  await fetch(`/api/characters/${selectedCharacter.id}/memory`, {method: "DELETE"});
  await loadHistory();
}

async function deleteCharacter() {
  if (!selectedCharacter) {
    alert("Select a character first.");
    return;
  }

  if (!confirm(`Delete ${selectedCharacter.name}, including memory and local history?`)) {
    return;
  }

  await fetch(`/api/characters/${selectedCharacter.id}`, {method: "DELETE"});
  newCharacter();
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

function addMessage(type, text, heartWhisper="", shouldScroll=true) {
  const box = document.getElementById("messages");
  const div = document.createElement("div");

  div.className = `msg ${type}`;

  const content = document.createElement("div");
  content.textContent = text;
  div.appendChild(content);

  if (type === "bot") {
    if (heartWhisper && heartWhisper.trim()) {
      const whisper = document.createElement("div");
      whisper.className = "heart-whisper";

      const label = document.createElement("span");
      label.className = "heart-label";
      label.textContent = "Heart whisper";

      const body = document.createElement("div");
      body.textContent = heartWhisper.trim();

      whisper.appendChild(label);
      whisper.appendChild(body);
      div.appendChild(whisper);
    }

    const actions = document.createElement("div");
    actions.className = "actions";

    const voiceBtn = document.createElement("button");
    voiceBtn.textContent = "Voice";
    voiceBtn.onclick = () => playVoice(text);

    actions.appendChild(voiceBtn);
    div.appendChild(actions);
  }

  box.appendChild(div);

  if (shouldScroll) {
    box.scrollTop = box.scrollHeight;
  }
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

  addMessage("user", text);
  addMessage("bot", "*thinking quietly* ...");

  const res = await fetch(`/api/chat/${selectedCharacter.id}`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({message: text})
  });

  const data = await res.json();

  const botMessages = document.querySelectorAll(".msg.bot");
  const lastBot = botMessages[botMessages.length - 1];

  if (lastBot) {
    lastBot.remove();
  }

  if (!res.ok) {
    addMessage("bot", "*the system flickers* " + (data.error || "Something went wrong."));
    return;
  }

  addMessage("bot", data.reply, data.heart_whisper);
  await loadCharacters();
}

function escapeHtml(s) {
  return String(s || "").replace(/[&<>"']/g, m => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;"
  }[m]));
}

function escapeAttr(s) {
  return String(s || "").replace(/[&<>"']/g, m => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;"
  }[m]));
}

document.getElementById("messageInput").addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

window.addEventListener("keydown", e => {
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
});

loadConfig();
loadCharacters();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    print(f"Provider: {CHAT_PROVIDER}")
    print(f"Model: {OPENROUTER_MODEL if CHAT_PROVIDER == 'openrouter' else GROQ_MODEL}")
    print("Open: http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=True)