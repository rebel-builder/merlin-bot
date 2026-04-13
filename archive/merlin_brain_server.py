#!/usr/bin/env python3
"""
Merlin Brain Server — runs on Nate's Mac.

HTTP API that the Pi calls:
  POST /stt     — upload WAV, get text back
  POST /think   — send text, get LLM response
  POST /tts     — send text, get MP3 audio back
  GET  /health  — status check

Replaces SCP+SSH with clean HTTP calls.
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import tempfile
import os
import subprocess
import re
import time
from datetime import datetime
from pathlib import Path

# Breathing exercise — imported lazily to avoid loading Kokoro at boot
from breathing_exercise import run_breathing_exercise, is_breathing_trigger

PORT = 8900

# ── MemPalace Integration ───────────────────────────────────

MEMPALACE_VENV_PYTHON = "/Users/ezradrake/Documents/mempalace/venv/bin/python3"

def load_wake_up_context():
    """Load MemPalace identity (L0) for system prompt.
    Checks RBOS folder first (iCloud synced), then ~/.mempalace/ fallback."""
    for path in [
        os.path.join("/Users/ezradrake/Documents/mempalace", "identity.txt"),
        os.path.expanduser("~/.mempalace/identity.txt"),
    ]:
        try:
            with open(path) as f:
                return f.read().strip()
        except FileNotFoundError:
            continue
    return ""

def search_memory(query, limit=2):
    """Search MemPalace for context relevant to the user's message.
    Returns a short context string, or empty string if nothing found."""
    try:
        result = subprocess.run(
            [MEMPALACE_VENV_PYTHON, "-c", f"""
from mempalace.searcher import search_memories
from mempalace.config import MempalaceConfig
config = MempalaceConfig()
result = search_memories("{query.replace('"', '')}", palace_path=config.palace_path, n_results={limit})
hits = result.get("results", [])
for h in hits[:{limit}]:
    if h.get("similarity", 0) > 0.35:
        text = h["text"][:300].replace(chr(10), " ")
        print(f"[{{h['source_file']}}] {{text}}")
"""],
            capture_output=True, text=True, timeout=10
        )
        context = result.stdout.strip()
        if context:
            return context
    except Exception as e:
        print(f"[memory] Search error: {e}")
    return ""

# Load identity at boot
WAKE_UP_CONTEXT = load_wake_up_context()
if WAKE_UP_CONTEXT:
    print(f"[memory] Identity loaded ({len(WAKE_UP_CONTEXT)} chars)")
else:
    print("[memory] No identity.txt found — running without memory context")

# ── Conversation Logger ──────────────────────────────────────

LOG_DIR = Path("/Users/ezradrake/Documents/RBOS/merlin/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)

def get_daily_log():
    return LOG_DIR / f"conversations-{datetime.now().strftime('%Y-%m-%d')}.jsonl"

def log_exchange(user_text, merlin_reply, intent=None, latency=None, stt_latency=None, tts_latency=None):
    """Append a conversation exchange to today's JSONL log."""
    entry = {
        "ts": datetime.now().isoformat(),
        "user": user_text,
        "merlin": merlin_reply,
        "model": LLM_MODEL,
        "latency_llm": round(latency, 2) if latency else None,
        "latency_stt": round(stt_latency, 2) if stt_latency else None,
        "latency_tts": round(tts_latency, 2) if tts_latency else None,
        "words": len(merlin_reply.split()) if merlin_reply else 0,
    }
    try:
        with open(get_daily_log(), "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        print(f"[log] Error: {e}")
LLM_URL = "http://localhost:1234/v1/chat/completions"
LLM_MODEL = "google/gemma-4-26b-a4b"

_BASE_SYSTEM_PROMPT = """You are Merlin, Ezra's desktop pal. Under 15 words. Plain text only.

TOOLS — output on its own line when needed:
[TOOL: read_briefing] — The Thing, energy, schedule, what shipped
[TOOL: get_time] — current date and time
[TOOL: add_reminder(time, text)] — set a reminder
[TOOL: capture(text)] — log a note
[TOOL: update_energy(level)] — update energy (green/yellow/red)
[TOOL: look] — see through your camera
[TOOL: start_recording] — start recording POV video
[TOOL: stop_recording] — stop recording
[TOOL: breathing] — guide Ezra through a breathing exercise (use when RED energy or he asks to breathe)

IMPORTANT: When you need data, you MUST output [TOOL: name] on its own line. Never guess facts — call the tool. Examples:
- "What should I work on?" → [TOOL: read_briefing]
- "What do you see?" → [TOOL: look]
- "Remember this" → [TOOL: capture(the thing to remember)]
- "Start recording" → [TOOL: start_recording]

After a tool call, wait for the result, then respond using that information. Keep it short.
"""

# ── Briefing file ──────────────────────────────────────────
BRIEFING_PATH = Path(__file__).parent / "briefing.md"

def read_briefing():
    """Read the full briefing file."""
    try:
        return BRIEFING_PATH.read_text().strip()
    except Exception:
        return "(no briefing file found)"

def read_reminders():
    """Read just the reminders section."""
    text = read_briefing()
    if "## Reminders" in text:
        section = text.split("## Reminders")[1]
        if "##" in section:
            section = section.split("##")[0]
        return section.strip() or "(no reminders)"
    return "(no reminders)"

def add_reminder(time_str, text):
    """Append a reminder to the briefing file."""
    try:
        content = BRIEFING_PATH.read_text()
        reminder_line = f"- {time_str} — {text}\n"
        if "## Reminders" in content:
            content = content.replace("## Reminders\n", f"## Reminders\n{reminder_line}", 1)
        BRIEFING_PATH.write_text(content)
        return f"Reminder added: {time_str} — {text}"
    except Exception as e:
        return f"Error: {e}"

def capture_note(text):
    """Append a capture to the briefing file."""
    try:
        from datetime import datetime
        content = BRIEFING_PATH.read_text()
        timestamp = datetime.now().strftime("%I:%M%p").lower()
        capture_line = f"- [{timestamp}] {text}\n"
        if "## Captures" in content:
            content = content.rstrip() + "\n" + capture_line
        BRIEFING_PATH.write_text(content)
        return f"Captured: {text}"
    except Exception as e:
        return f"Error: {e}"

def update_energy(level):
    """Update the energy level in the briefing file."""
    level = level.strip().capitalize()
    if level.lower() not in ("green", "yellow", "red"):
        return f"Unknown energy level: {level}"
    try:
        content = BRIEFING_PATH.read_text()
        import re as _re
        content = _re.sub(r'Energy: \w+', f'Energy: {level}', content)
        BRIEFING_PATH.write_text(content)
        return f"Energy updated to {level}"
    except Exception as e:
        return f"Error: {e}"

def grab_snapshot():
    """Fetch the latest camera frame from the Pi."""
    import subprocess, base64
    try:
        # SCP snapshot from Pi
        subprocess.run(
            ["scp", "-o", "ConnectTimeout=3",
             "pi@100.87.156.70:/tmp/merlin-snapshot.jpg",
             "/tmp/merlin-latest-snapshot.jpg"],
            capture_output=True, timeout=5)
        with open("/tmp/merlin-latest-snapshot.jpg", "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        print(f"[vision] Snapshot error: {e}")
        return None


def do_vision(user_text, image_b64):
    """Send image + text to the vision-capable LLM."""
    import requests
    try:
        messages = [
            {"role": "system", "content": "You are Merlin. These are YOUR eyes. Say 'I see' not 'the camera sees.' One or two short plain sentences. Specific and concrete. No poetry."},
            {"role": "user", "content": [
                {"type": "text", "text": user_text or "What do you see right now?"},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}
            ]}
        ]
        resp = requests.post(LLM_URL, json={
            "model": LLM_MODEL,
            "messages": messages,
            "temperature": 0.5,
            "max_tokens": 60,
        }, timeout=30)
        if resp.status_code == 200:
            reply = resp.json()["choices"][0]["message"]["content"].strip()
            reply = re.sub(r'[*_~`#]', '', reply)
            reply = re.sub(r'[^\x00-\x7F]', ' ', reply)
            return reply
        else:
            return f"Vision error: {resp.status_code}"
    except Exception as e:
        return f"Vision error: {e}"


PI_IP = "100.87.156.70"
RECORD_PORT = 8903

def send_record_command(command):
    """Send record start/stop command to Pi tracker via UDP."""
    import socket
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(command.encode(), (PI_IP, RECORD_PORT))
        sock.close()
        return True
    except Exception as e:
        print(f"[record] UDP send error: {e}")
        return False


def execute_tool(tool_call):
    """Parse and execute a [TOOL: ...] call. Returns result string."""
    from datetime import datetime
    call = tool_call.strip()

    if call == "start_recording":
        if send_record_command("record_start"):
            return "Recording started. Your camera is now capturing video."
        return "Failed to start recording. Tracker may not be running."
    elif call == "stop_recording":
        if send_record_command("record_stop"):
            return "Recording stopped and saved."
        return "Failed to stop recording."
    elif call == "look":
        print("[vision] Grabbing snapshot...")
        image_b64 = grab_snapshot()
        if image_b64:
            description = do_vision("Describe what you see.", image_b64)
            print(f"[vision] Sees: {description}")
            return description
        return "Camera unavailable. Can't see right now."
    elif call == "read_briefing":
        return read_briefing()
    elif call == "get_time":
        return datetime.now().strftime("It is %A, %B %d, %Y at %I:%M %p")
    elif call == "read_reminders":
        return read_reminders()
    elif call.startswith("add_reminder("):
        args = call[len("add_reminder("):-1]
        parts = args.split(",", 1)
        if len(parts) == 2:
            return add_reminder(parts[0].strip(), parts[1].strip())
        return "Error: need time and text, e.g. add_reminder(6pm, eat dinner)"
    elif call.startswith("capture("):
        text = call[len("capture("):-1].strip()
        return capture_note(text)
    elif call.startswith("update_energy("):
        level = call[len("update_energy("):-1].strip()
        result = update_energy(level)
        # Auto-trigger breathing exercise on RED energy
        if level.lower() == "red":
            import threading
            threading.Thread(
                target=run_breathing_exercise,
                kwargs={"tts_model": tts_model},
                daemon=True,
                name="breathing_auto",
            ).start()
        return result
    elif call == "breathing":
        # Runs blocking in brain server context — TTS handles sequencing
        import threading
        threading.Thread(
            target=run_breathing_exercise,
            kwargs={"tts_model": tts_model},
            daemon=True,
            name="breathing_exercise",
        ).start()
        return "Starting breathing exercise."
    else:
        return f"Unknown tool: {call}"

# ── System prompt assembly ─────────────────────────────────

def build_system_prompt():
    """Build system prompt with briefing injected."""
    prompt = _BASE_SYSTEM_PROMPT
    if WAKE_UP_CONTEXT:
        prompt += f"\n\nWHO EZRA IS:\n{WAKE_UP_CONTEXT}"
    # Inject current briefing
    briefing = read_briefing()
    prompt += f"\n\nCURRENT BRIEFING (read at startup):\n{briefing}"
    return prompt

SYSTEM_PROMPT = build_system_prompt()
print(f"[briefing] Loaded ({len(read_briefing())} chars)")

conversation_history = []
tts_model = None


def do_stt(wav_path):
    """Transcribe WAV file using mlx-whisper."""
    try:
        import mlx_whisper
        result = mlx_whisper.transcribe(
            wav_path,
            path_or_hf_repo="mlx-community/whisper-small-mlx",
            language="en"
        )
        text = result.get("text", "").strip()
        noise = {"", "(silence)", "[BLANK_AUDIO]", "you", "Thank you.",
                 "Thanks for watching!", "Bye.", ".", ".."}
        return text if text not in noise else ""
    except Exception as e:
        print(f"[stt] Error: {e}")
        return ""


MAX_SPOKEN_WORDS = 50  # soft ceiling — only truncate truly runaway responses

def _truncate_for_voice(text, max_words=MAX_SPOKEN_WORDS):
    """Only truncate if response is truly runaway. Let the model talk."""
    words = text.split()
    if len(words) <= max_words:
        return text
    # Try to cut at a sentence boundary
    for i in range(min(max_words, len(words)) - 1, 0, -1):
        if words[i].endswith(('.', '?', '!')):
            return ' '.join(words[:i + 1])
    return ' '.join(words[:max_words]) + '.'


def _llm_call(messages, max_tokens=150):
    """Raw LLM API call. Returns cleaned text."""
    import requests
    try:
        resp = requests.post(LLM_URL, json={
            "model": LLM_MODEL,
            "messages": messages,
            "temperature": 0.5,
            "max_tokens": max_tokens,
            "repeat_penalty": 1.3,
            "frequency_penalty": 0.5,
        }, timeout=30)
        if resp.status_code == 200:
            reply = resp.json()["choices"][0]["message"]["content"].strip()
            reply = re.sub(r'<think>.*?</think>', '', reply, flags=re.DOTALL).strip()
            reply = re.sub(r'<\|channel>.*?<channel\|>', '', reply, flags=re.DOTALL).strip()
            reply = re.sub(r'[*_~`#]', '', reply)
            reply = re.sub(r'[^\x00-\x7F]', ' ', reply)
            reply = re.sub(r'\s+', ' ', reply).strip()
            return reply
        else:
            print(f"[llm] Error: {resp.status_code}")
            return None
    except Exception as e:
        print(f"[llm] Error: {e}")
        return None


def do_think(text):
    """Send text to LLM, handle tool calls, get final response."""
    global conversation_history

    # Short-circuit: verbal breathing triggers bypass LLM routing
    if is_breathing_trigger(text):
        import threading
        threading.Thread(
            target=run_breathing_exercise,
            kwargs={"tts_model": tts_model},
            daemon=True,
            name="breathing_verbal",
        ).start()
        reply = "Let's breathe."
        conversation_history.append({"role": "user", "content": text})
        conversation_history.append({"role": "assistant", "content": reply})
        return reply

    conversation_history.append({"role": "user", "content": text})
    if len(conversation_history) > 20:
        conversation_history = conversation_history[-20:]

    system = build_system_prompt()
    messages = [{"role": "system", "content": system}] + conversation_history

    # First LLM call
    reply = _llm_call(messages, max_tokens=150)
    if not reply:
        return None

    # Check for tool calls in the response (flexible format matching)
    tool_match = re.search(r'\[(?:TOOL:\s*|used tool:\s*)(.+?)\]', reply, re.IGNORECASE)
    if not tool_match:
        # Also catch model outputting tool names without brackets
        tool_match = re.search(r'(look|read_?briefing|get_?time|read_?reminders|add_?reminder\(.+?\)|capture\(.+?\)|update_?energy\(.+?\))', reply, re.IGNORECASE)

    if tool_match:
        tool_call = tool_match.group(1).strip()
        # Normalize: remove spaces/underscores inconsistency
        tool_call = tool_call.replace("readbriefing", "read_briefing")
        tool_call = tool_call.replace("gettime", "get_time")
        tool_call = tool_call.replace("readreminders", "read_reminders")
        tool_call = tool_call.replace("addreminder", "add_reminder")
        tool_call = tool_call.replace("updateenergy", "update_energy")

        print(f"[tool] Executing: {tool_call}")
        tool_result = execute_tool(tool_call)
        print(f"[tool] Result: {tool_result[:100]}...")

        # Strip ALL tool metadata from reply
        spoken_part = re.sub(r'\[(?:TOOL:\s*|used tool:\s*).+?\]', '', reply, flags=re.IGNORECASE).strip()

        # Feed tool result back to LLM for a natural response
        conversation_history.append({"role": "assistant", "content": "(used a tool internally)"})
        conversation_history.append({"role": "user", "content": f"[Tool result: {tool_result}]\nRespond naturally. Do NOT mention tools, brackets, or internal systems. Just answer Ezra."})

        messages = [{"role": "system", "content": system}] + conversation_history
        reply = _llm_call(messages, max_tokens=150)
        if not reply:
            reply = spoken_part if spoken_part else "Got it."

    # Final cleanup: strip any remaining tool metadata that might leak
    reply = re.sub(r'\[(?:TOOL|used tool|tool)[:\s].+?\]', '', reply, flags=re.IGNORECASE).strip()

    reply = _truncate_for_voice(reply)
    conversation_history.append({"role": "assistant", "content": reply})
    return reply


def do_tts(text):
    """Generate TTS audio, return WAV bytes."""
    global tts_model
    try:
        import numpy as np

        if tts_model is None:
            from mlx_audio.tts.generate import load_model
            tts_model = load_model("prince-canuma/Kokoro-82M")
            print("[tts] Kokoro model loaded")

        # Strip emoji, markdown, and special characters that crash Kokoro
        clean_text = " ".join(text.replace("\n", " ").split()).strip()
        clean_text = re.sub(r'[*_~`#]', '', clean_text)  # markdown
        clean_text = re.sub(r'[^\x00-\x7F]', '', clean_text)  # non-ASCII (emoji)
        clean_text = re.sub(r'\s+', ' ', clean_text).strip()
        if not clean_text:
            return None

        chunks = list(tts_model.generate(text=clean_text, voice="am_fenrir"))
        if not chunks:
            return None

        audio = np.concatenate([np.array(c.audio, dtype=np.float32) for c in chunks])
        pcm = (audio * 32767).clip(-32768, 32767).astype(np.int16).tobytes()
        sr = chunks[0].sample_rate

        result = subprocess.run(
            ["ffmpeg", "-y", "-f", "s16le", "-ar", str(sr), "-ac", "1",
             "-i", "pipe:0", "-ar", "48000", "-f", "wav", "pipe:1"],
            input=pcm, capture_output=True, timeout=15
        )

        if result.returncode == 0:
            return result.stdout
        return None

    except Exception as e:
        print(f"[tts] Error: {e}")
        return None


class MerlinHandler(BaseHTTPRequestHandler):

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))

        if self.path == "/stt":
            audio_data = self.rfile.read(length)
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                f.write(audio_data)
                wav_path = f.name

            print(f"[stt] Transcribing {length} bytes...")
            t0 = time.time()
            text = do_stt(wav_path)
            elapsed = time.time() - t0
            os.unlink(wav_path)

            print(f'[stt] "{text}" ({elapsed:.1f}s)')
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"text": text}).encode())

        elif self.path == "/think":
            body = json.loads(self.rfile.read(length))
            text = body.get("text", "")

            print(f'[heard] "{text}"')
            t0 = time.time()
            reply = do_think(text)
            elapsed = time.time() - t0

            print(f'[merlin] "{reply}" ({elapsed:.1f}s)')

            # Log the exchange
            stt_latency = body.get("stt_latency")
            log_exchange(text, reply, latency=elapsed, stt_latency=stt_latency)

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"reply": reply}).encode())

        elif self.path == "/tts":
            body = json.loads(self.rfile.read(length))
            text = body.get("text", "")

            print(f'[tts] Generating: "{text}"')
            t0 = time.time()
            audio = do_tts(text)
            elapsed = time.time() - t0

            if audio:
                print(f"[tts] Generated {len(audio)} bytes ({elapsed:.1f}s)")
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Content-Length", str(len(audio)))
                self.end_headers()
                self.wfile.write(audio)
            else:
                self.send_response(500)
                self.end_headers()

        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok"}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # quiet


def main():
    print("=" * 50)
    print(f"Merlin Brain Server on :{PORT}")
    print(f"LLM: {LLM_URL} ({LLM_MODEL})")
    print("Endpoints: /stt /think /tts /health")
    print("=" * 50)

    server = HTTPServer(("0.0.0.0", PORT), MerlinHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutdown.")
        server.server_close()


if __name__ == "__main__":
    main()
