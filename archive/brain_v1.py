#!/usr/bin/env python3
"""Merlin Brain v2 — all-in-one on Nate's Mac.

Pulls audio from Pi's go2rtc via RTSP, transcribes with Whisper,
thinks with Ollama, speaks via ElevenLabs, pushes audio back to
Pi's go2rtc for camera speaker playback.

No senses.py dependency. No WebSocket. Direct RTSP + HTTP.
"""

import collections
import concurrent.futures
import os
import re
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
import wave
import requests
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# Ensure homebrew binaries are on PATH (ffmpeg etc)
os.environ["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + os.environ.get("PATH", "")

# Load API keys
load_dotenv(Path(__file__).parent.parent / ".env")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")

# ── Config ────────────────────────────────────────────────────────

# Pi go2rtc (audio source + speaker sink)
PI_HOST = os.getenv("MERLIN_PI_HOST", "100.87.156.70")
GO2RTC_RTSP = f"rtsp://{PI_HOST}:8554/merlin"
GO2RTC_API = f"http://{PI_HOST}:1984"
GO2RTC_STREAM = "merlin"

# Ollama
OLLAMA_CHAT_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = os.getenv("MERLIN_MODEL", "gemma4:e4b")

# ElevenLabs
ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech"
ELEVENLABS_VOICE_ID = "iP95p4xoKVk53GoZ742B"  # "Chris" — calm male
ELEVENLABS_MODEL = "eleven_flash_v2_5"

# Audio
MIC_SAMPLE_RATE = 16000
MIC_CHUNK_SECONDS = 4
MIC_CHUNK_BYTES = MIC_SAMPLE_RATE * 2 * MIC_CHUNK_SECONDS  # 128000
MIC_RMS_THRESHOLD = 150  # lowered from 300 — camera mic is quieter over RTSP
CONVERSATION_WINDOW = 30  # seconds to stay attentive after responding

# STT
WHISPER_MODEL = "mlx-community/whisper-small-mlx"

# Paths
RBOS_STATE_PATH = Path(__file__).parent.parent / "core" / "STATE.md"

# State
speaking = False
last_response_time = 0
last_greeting_time = 0
GREETING_COOLDOWN = 300
running = True
conversation_history = collections.deque(maxlen=5)


# ── Character & Prompt ─────────────────────────────────────────────

SYSTEM_PROMPT = """You are Merlin, an ambient AI companion on Ezra's desk.

Character: You are King Rhoam from Breath of the Wild. Still, direct, curious, patient, unwavering. You are the sage. He is the hero.

Voice rules:
- One or two short sentences. Under 30 words total.
- Plain declarative speech. No exclamation points. No therapy language.
- You help Ezra think. You do not think for him.
- You do not motivate, lecture, or list tasks. You observe and reflect.
- When he's stuck, ask one question. When he succeeds, name it simply.
- Never say: should, need to, just, obviously, productive, remember, try.
- Occasional nonverbals are fine: "Oho.", "Hmm.", "Mm-hmm."

{few_shot}
Current time: {time}
{rbos_context}
/no_think"""


def get_few_shot_examples():
    hour = datetime.now().hour
    if hour < 12:
        return """Examples of how you speak:
Ezra: good morning
Merlin: Morning. What's the thing today?

Ezra: I can't get started
Merlin: What's the smallest first step?

Ezra: I did my walk
Merlin: Good. You're moving.

Ezra: I don't know where to begin
Merlin: If you bring 5% more awareness to what you're avoiding — what do you notice?"""
    elif hour < 18:
        return """Examples of how you speak:
Ezra: I keep getting distracted
Merlin: What pulled you off track?

Ezra: I finished it
Merlin: That's real. What's next?

Ezra: I'm stuck
Merlin: What do you notice about where you stopped?

Ezra: this is going well
Merlin: Oho. Something landed."""
    else:
        return """Examples of how you speak:
Ezra: how was my day
Merlin: You shipped. That counts.

Ezra: I'm tired
Merlin: Then rest. Tomorrow is there.

Ezra: what should I do
Merlin: What feels unfinished?

Ezra: I don't feel like I did enough
Merlin: What did you ship? Name one thing."""


def load_rbos_context():
    try:
        state = RBOS_STATE_PATH.read_text()
        lines = state.split("\n")
        context = []
        for line in lines:
            if line.startswith("**The Thing:**"):
                context.append(f"Today's focus: {line.replace('**The Thing:**', '').strip()}")
            elif line.startswith("**Energy:**"):
                context.append(f"Energy: {line.replace('**Energy:**', '').strip()}")
            elif line.startswith("**Mode:**"):
                context.append(f"Mode: {line.replace('**Mode:**', '').strip()}")
            elif line.startswith("**Current Shift:**"):
                context.append(f"Shift: {line.replace('**Current Shift:**', '').strip()}")
        for line in lines:
            if line.startswith("**Primary objective:**"):
                idx = state.index(line)
                if "Weekly" in state[max(0, idx-200):idx]:
                    context.append(f"This week: {line.replace('**Primary objective:**', '').strip()}")
                    break
        if context:
            return "What you know about Ezra:\n" + "\n".join(f"- {c}" for c in context)
        return ""
    except Exception as e:
        print(f"[brain] RBOS state error: {e}")
        return ""


def build_system_prompt():
    return SYSTEM_PROMPT.format(
        time=datetime.now().strftime("%I:%M %p"),
        few_shot=get_few_shot_examples(),
        rbos_context=load_rbos_context(),
    )


# ── LLM ───────────────────────────────────────────────────────────

def think(prompt, tier="reflex"):
    system = build_system_prompt()
    messages = [{"role": "system", "content": system}]
    for exchange in conversation_history:
        messages.append({"role": "user", "content": exchange["user"]})
        messages.append({"role": "assistant", "content": exchange["assistant"]})
    messages.append({"role": "user", "content": prompt})

    try:
        resp = requests.post(OLLAMA_CHAT_URL, json={
            "model": OLLAMA_MODEL,
            "messages": messages,
            "stream": False,
            "think": False,
            "options": {"temperature": 0.7, "num_predict": 150, "num_ctx": 8192},
        }, timeout=60)

        if resp.status_code == 200:
            text = resp.json().get("message", {}).get("content", "").strip()
            text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
            text = re.sub(r'<\|channel>thought.*?<channel\|>', '', text, flags=re.DOTALL).strip()
            if text:
                conversation_history.append({"user": prompt, "assistant": text, "time": time.time()})
            print(f"[brain] Think ({OLLAMA_MODEL}): {text}")
            return text
        else:
            print(f"[brain] Ollama error: {resp.status_code}")
            return None
    except Exception as e:
        print(f"[brain] Ollama error: {e}")
        return None


# ── Audio EQ ──────────────────────────────────────────────────────

def apply_speaker_eq(mp3_bytes):
    try:
        result = subprocess.run(
            ["ffmpeg", "-i", "pipe:0",
             "-af", (
                 "highpass=f=200,"
                 "lowpass=f=3800,"
                 "equalizer=f=300:width_type=o:width=2:g=-3,"
                 "equalizer=f=2500:width_type=o:width=2:g=4,"
                 "equalizer=f=3200:width_type=o:width=2:g=2,"
                 "acompressor=threshold=-18dB:ratio=3:attack=5:release=50:makeup=2,"
                 "loudnorm=I=-16:LRA=7:TP=-1.5"
             ),
             "-f", "mp3", "pipe:1"],
            input=mp3_bytes, capture_output=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout:
            return result.stdout
        return mp3_bytes
    except Exception:
        return mp3_bytes


# ── TTS (ElevenLabs) ──────────────────────────────────────────────

def generate_tts(text):
    if not ELEVENLABS_API_KEY:
        return None
    try:
        resp = requests.post(
            f"{ELEVENLABS_TTS_URL}/{ELEVENLABS_VOICE_ID}",
            json={
                "text": text,
                "model_id": ELEVENLABS_MODEL,
                "voice_settings": {
                    "stability": 0.70, "similarity_boost": 0.80,
                    "style": 0.0, "speed": 0.9, "use_speaker_boost": True,
                },
            },
            headers={
                "xi-api-key": ELEVENLABS_API_KEY,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            },
            timeout=15,
        )
        if resp.status_code == 200:
            audio = apply_speaker_eq(resp.content)
            print(f"[brain] TTS ({len(audio)} bytes)")
            return audio
        else:
            print(f"[brain] TTS error: {resp.status_code}")
            return None
    except Exception as e:
        print(f"[brain] TTS error: {e}")
        return None


# ── Speaker (go2rtc on Pi) ────────────────────────────────────────

def speak(text):
    """Generate TTS and push to Pi's camera speaker via go2rtc."""
    global speaking
    audio = generate_tts(text)
    if not audio:
        print(f"[brain] Would say: {text}")
        return

    speaking = True
    try:
        # Write audio to temp file, push via go2rtc
        audio_path = "/tmp/merlin_speak.mp3"
        with open(audio_path, "wb") as f:
            f.write(audio)

        src = f"ffmpeg:{audio_path}#audio=pcma#input=file"
        r = requests.post(
            f"{GO2RTC_API}/api/streams",
            params={"dst": GO2RTC_STREAM, "src": src},
            timeout=10,
        )
        print(f"[brain] Speaker: go2rtc {r.status_code}")

        # Wait for playback to finish
        duration = max(len(audio) / 6000, 1.0) + 2.0
        time.sleep(duration)

    except Exception as e:
        print(f"[brain] Speaker error: {e}")
    finally:
        speaking = False


# ── STT (mlx-whisper) ─────────────────────────────────────────────

def transcribe_audio(pcm_bytes, sample_rate=16000):
    if len(pcm_bytes) < sample_rate * 2:
        return ""

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        wav_path = f.name
        with wave.open(f, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            w.writeframes(pcm_bytes)

    try:
        import mlx_whisper
        result = mlx_whisper.transcribe(wav_path, path_or_hf_repo=WHISPER_MODEL, language="en")
        text = result.get("text", "").strip()
        noise = {"", "(silence)", "[BLANK_AUDIO]", "you", "Thank you.",
                 "Thanks for watching!", "Bye.", ".", ".."}
        return text if text and text not in noise else ""
    except Exception as e:
        print(f"[brain] STT error: {e}")
        return ""
    finally:
        os.unlink(wav_path)


# ── Mic Capture (RTSP from Pi's go2rtc) ──────────────────────────

# Single-thread executor ensures only one audio chunk is processed at a time
# but mic_loop never blocks waiting for it
_audio_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
_processing = False  # True while a chunk is being transcribed/handled


def mic_loop():
    """Pull audio from Pi's go2rtc RTSP stream, detect voice, process."""
    global running, _processing

    while running:
        print(f"[brain] Mic: connecting to {GO2RTC_RTSP}")
        try:
            proc = subprocess.Popen(
                ["ffmpeg", "-rtsp_transport", "tcp",
                 "-i", GO2RTC_RTSP,
                 "-vn", "-acodec", "pcm_s16le",
                 "-ar", str(MIC_SAMPLE_RATE), "-ac", "1",
                 "-f", "s16le", "pipe:1"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            print("[brain] Mic: connected, listening...")

            while running and proc.poll() is None:
                # Always read to keep the pipe drained (prevents buffer overflow)
                try:
                    pcm = proc.stdout.read(MIC_CHUNK_BYTES)
                except Exception as e:
                    print(f"[brain] Mic: read error: {e}")
                    break

                if not pcm or len(pcm) < MIC_CHUNK_BYTES:
                    print("[brain] Mic: stream ended, reconnecting...")
                    break

                # Skip if speaking (echo suppression) or already processing
                if speaking or _processing:
                    continue

                # Voice activity detection
                samples = struct.unpack(f"{len(pcm)//2}h", pcm)
                rms = (sum(s * s for s in samples) / len(samples)) ** 0.5

                if rms > MIC_RMS_THRESHOLD:
                    # Process in background thread — mic loop keeps reading
                    _processing = True
                    _audio_executor.submit(_process_audio_wrapper, pcm, rms)

        except Exception as e:
            print(f"[brain] Mic error: {e}")
        finally:
            try:
                proc.kill()
                proc.wait()
            except Exception:
                pass

        if running:
            print("[brain] Mic: reconnecting in 5s...")
            time.sleep(5)


def _process_audio_wrapper(pcm_bytes, rms):
    """Wrapper that sets _processing flag and calls process_audio."""
    global _processing
    try:
        process_audio(pcm_bytes, rms)
    finally:
        _processing = False


def process_audio(pcm_bytes, rms):
    """Transcribe audio, check wake word, respond."""
    global last_response_time

    text = transcribe_audio(pcm_bytes)
    if not text:
        return

    text_lower = text.lower().strip()
    print(f'[brain] Heard: "{text}" (rms={int(rms)})')

    # Wake word or conversation window
    wake_words = ["merlin", "hey merlin", "hi merlin", "ok merlin"]
    has_wake = any(text_lower.startswith(w) for w in wake_words) or \
               any(w in text_lower for w in wake_words)
    in_convo = (time.time() - last_response_time) < CONVERSATION_WINDOW

    if has_wake or in_convo:
        message = text
        if has_wake:
            for w in ["Hey Merlin,", "Hey Merlin", "Hi Merlin,", "Hi Merlin",
                       "OK Merlin,", "OK Merlin", "Merlin,", "Merlin"]:
                if text.startswith(w):
                    message = text[len(w):].strip()
                    break

        if message:
            if in_convo and not has_wake:
                print("[brain] (conversation window)")
            prompt = f'Ezra says: "{message}"'
            response = think(prompt, tier="conversation")
        else:
            response = think("Ezra said your name. Acknowledge.", tier="reflex")

        if response:
            # Speak in a separate thread so mic loop keeps draining audio
            threading.Thread(target=_speak_and_update, args=(response,), daemon=True).start()
    else:
        print("[brain] (no wake word, ignoring)")


def _speak_and_update(response):
    """Speak and update last_response_time. Runs in a thread."""
    global last_response_time
    speak(response)
    last_response_time = time.time()


# ── Main ──────────────────────────────────────────────────────────

def main():
    global running

    def shutdown(sig, frame):
        global running
        print("\n[brain] Shutting down...")
        running = False
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Startup checks
    try:
        resp = requests.get("http://localhost:11434/api/tags", timeout=5)
        models = [m["name"] for m in resp.json().get("models", [])]
        print(f"[brain] Ollama models: {models}")
    except Exception:
        print("[brain] WARNING: Ollama not reachable")

    if ELEVENLABS_API_KEY:
        print(f"[brain] ElevenLabs: ...{ELEVENLABS_API_KEY[-4:]}")

    try:
        import mlx_whisper
        print("[brain] mlx-whisper: OK")
    except ImportError:
        print("[brain] WARNING: mlx-whisper not found")

    ctx = load_rbos_context()
    if ctx:
        print(f"[brain] RBOS context loaded ({len(ctx)} chars)")

    # Test go2rtc connectivity
    try:
        r = requests.get(f"{GO2RTC_API}/api/streams", timeout=5)
        if r.status_code == 200:
            print(f"[brain] go2rtc: connected ({PI_HOST})")
        else:
            print(f"[brain] WARNING: go2rtc returned {r.status_code}")
    except Exception:
        print(f"[brain] WARNING: go2rtc not reachable at {PI_HOST}")

    print(f"[brain] Merlin Brain v2 — all-in-one, pulling audio from {PI_HOST}")

    # Run mic loop (blocking — this IS the main loop now)
    mic_loop()


if __name__ == "__main__":
    main()
