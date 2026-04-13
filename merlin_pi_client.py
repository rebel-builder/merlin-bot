#!/usr/bin/env python3
"""
Merlin Pi Client — runs on Pi, talks to Brain Server on Nate's Mac.

Conversation model:
  IDLE → wake word → play C-G → CONVERSATION
  CONVERSATION → speech → process → respond → CONVERSATION
  CONVERSATION → dismissal/hush/silence timeout → play G-C → IDLE or HUSHED
  HUSHED → wake word → play C-G → CONVERSATION
  HUSHED → timeout → IDLE (silent)

Sound bookends:
  Open:  C-G (ascending)  = "I'm here"
  Close: G-C (descending) = "Going quiet"
"""

import datetime
import os
import signal
import struct
import subprocess
import tempfile
import time
import wave

import requests

# ── Config ──────────────────────────────────────────────────

BRAIN_URL = os.getenv("MERLIN_BRAIN_URL", "http://100.123.211.1:8900")
MIC_DEVICE = "plughw:3,0"
SPEAKER_DEVICE = "plughw:1,0"
SAMPLE_RATE = 16000
SILENCE_THRESHOLD = 500
SILENCE_DURATION = 0.8
MIN_SPEECH_DURATION = 0.3

# Conversation closes after this many seconds of silence
CONVERSATION_TIMEOUT = 45

WAKE_WORDS = ["merlin", "hey merlin", "hi merlin", "ok merlin",
              "erlin", "marlin", "hey marlin", "berlin", "murlin",
              "marlon", "hey marlon", "merlan", "merlín"]

# Phrases that close conversation politely
DISMISS_PHRASES = ["back to work", "nevermind", "never mind", "that's all",
                   "thanks merlin", "thank you merlin", "ok bye",
                   "goodbye", "good night", "i'm done", "all good",
                   "that's it", "ok thanks", "alright thanks"]

# Phrases that close conversation AND mute until wake word
HUSH_PHRASES = ["be quiet", "stop listening", "shut up", "hush",
                "quiet", "stop interrupting", "go to sleep", "mute",
                "merlin stop", "merlin quiet", "merlin hush"]

HUSH_TIMEOUT = 300  # 5 minutes, then auto-resume to IDLE

# Quiet hours — no listening
QUIET_START = 23  # 11pm
QUIET_END = 7     # 7am

SOUNDS_DIR = "/home/pi/RBOS/merlin/sounds"


# ── State ───────────────────────────────────────────────────

class State:
    IDLE = "idle"
    CONVERSATION = "conversation"
    HUSHED = "hushed"


# ── Audio ───────────────────────────────────────────────────

def play_sound(name):
    """Play a short sound effect through the USB speaker."""
    path = f"{SOUNDS_DIR}/{name}.wav"
    if not os.path.exists(path):
        print(f"  [sound not found: {path}]")
        return
    try:
        subprocess.run(["aplay", "-D", SPEAKER_DEVICE, "-q", path],
                       capture_output=True, timeout=5)
    except Exception:
        pass


def play_sound_async(name):
    """Play sound without blocking."""
    path = f"{SOUNDS_DIR}/{name}.wav"
    try:
        subprocess.Popen(["aplay", "-D", SPEAKER_DEVICE, "-q", path],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


running = True
def shutdown(sig, frame):
    global running
    running = False
signal.signal(signal.SIGINT, shutdown)
signal.signal(signal.SIGTERM, shutdown)


_mic_proc = None

def start_mic():
    """Start the arecord subprocess."""
    global _mic_proc
    if _mic_proc is None or _mic_proc.poll() is not None:
        _mic_proc = subprocess.Popen(
            ["arecord", "-D", MIC_DEVICE, "-f", "S16_LE",
             "-r", str(SAMPLE_RATE), "-c", "1", "-t", "raw", "-q"],
            stdout=subprocess.PIPE
        )
    return _mic_proc

def stop_mic():
    """Kill the arecord subprocess completely (echo suppression)."""
    global _mic_proc
    if _mic_proc:
        _mic_proc.kill()
        _mic_proc.wait()
        _mic_proc = None


def record_utterance():
    """Record from PIXY mic until silence. Returns WAV bytes or None."""
    chunk_duration = 0.3
    chunks = []
    speech_started = False
    silence_time = 0
    total_time = 0

    proc = start_mic()
    chunk_bytes = int(SAMPLE_RATE * chunk_duration * 2)

    try:
        while running and total_time < 30:
            data = proc.stdout.read(chunk_bytes)
            if not data:
                break

            samples = struct.unpack(f"{len(data)//2}h", data)
            rms = (sum(s * s for s in samples) / len(samples)) ** 0.5

            if rms > SILENCE_THRESHOLD:
                if not speech_started:
                    speech_started = True
                    print(f"[mic] Listening... (rms={rms:.0f})")
                silence_time = 0
                chunks.append(data)
            elif speech_started:
                chunks.append(data)
                silence_time += chunk_duration
                if silence_time >= SILENCE_DURATION:
                    break

            total_time += chunk_duration
    except Exception:
        pass

    if not speech_started:
        return None

    audio_data = b"".join(chunks)
    duration = len(audio_data) / 2 / SAMPLE_RATE
    if duration < MIN_SPEECH_DURATION:
        return None

    import io
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(audio_data)
    return buf.getvalue()


# ── Brain ───────────────────────────────────────────────────

def stt(wav_bytes):
    """Send audio to brain server for transcription."""
    try:
        resp = requests.post(f"{BRAIN_URL}/stt", data=wav_bytes,
                             headers={"Content-Type": "audio/wav"}, timeout=30)
        if resp.status_code == 200:
            return resp.json().get("text", "")
    except Exception as e:
        print(f"[stt] Error: {e}")
    return ""


def think(text):
    """Send text to brain server for LLM response."""
    try:
        resp = requests.post(f"{BRAIN_URL}/think",
                             json={"text": text}, timeout=30)
        if resp.status_code == 200:
            return resp.json().get("reply", "")
    except Exception as e:
        print(f"[think] Error: {e}")
    return ""


TTS_CACHE_DIR = "/home/pi/RBOS/merlin/sounds/tts_cache"

def _check_tts_cache(text):
    """Check if we have a cached WAV for this exact response."""
    clean = text.lower().strip().rstrip('.!?').replace("'", "").replace("'", "")
    filename = clean.replace(" ", "_").replace(",", "").replace("?", "").replace("!", "")
    path = f"{TTS_CACHE_DIR}/{filename}.wav"
    if os.path.exists(path):
        return path
    return None


def speak(text):
    """Get TTS audio from brain server and play through speaker. Uses cache for common phrases."""
    try:
        # Check cache first — skip TTS entirely for known phrases
        cached = _check_tts_cache(text)
        if cached:
            print(f"[speak] CACHED: {text[:50]}")
            result = subprocess.run(
                ["mpv", "--no-video", f"--audio-device=alsa/plughw:1,0", cached],
                capture_output=True, timeout=60)
            return

        print(f"[speak] Requesting TTS for: {text[:50]}...")
        resp = requests.post(f"{BRAIN_URL}/tts",
                             json={"text": text}, timeout=30)
        if resp.status_code == 200 and resp.content:
            print(f"[speak] Got {len(resp.content)} bytes audio")
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                f.write(resp.content)
                f.flush()
                result = subprocess.run(
                    ["mpv", "--no-video", f"--audio-device=alsa/plughw:1,0", f.name],
                    capture_output=True, timeout=60)
                if result.returncode != 0:
                    print(f"[speak] mpv error: {result.stderr.decode()[:200]}")
                os.unlink(f.name)
        else:
            print(f"[speak] TTS failed: status={resp.status_code} len={len(resp.content)}")
    except Exception as e:
        print(f"[speak] Error: {e}")


# ── Phrase matching ─────────────────────────────────────────

def has_wake_word(text):
    text_lower = text.lower().strip()
    for w in WAKE_WORDS:
        if w in text_lower:
            return True
    return False

def is_dismiss(text):
    text_lower = text.lower().strip()
    for phrase in DISMISS_PHRASES:
        if phrase in text_lower:
            return True
    return False

def is_hush(text):
    text_lower = text.lower().strip()
    for phrase in HUSH_PHRASES:
        if phrase in text_lower:
            return True
    return False

def strip_wake_word(text):
    text_lower = text.lower().strip()
    for w in ["hey merlin,", "hey merlin", "hi merlin,", "hi merlin",
               "ok merlin,", "ok merlin", "merlin,", "merlin",
               "hey marlin,", "hey marlin", "marlin,", "marlin"]:
        if text_lower.startswith(w):
            return text[len(w):].strip()
    return text


# ── Main loop ───────────────────────────────────────────────

def main():
    print("=" * 50)
    print("Merlin Pi Client")
    print(f"Brain: {BRAIN_URL}")
    print(f"Mic: {MIC_DEVICE} | Speaker: {SPEAKER_DEVICE}")
    print("=" * 50)

    # Health check
    try:
        r = requests.get(f"{BRAIN_URL}/health", timeout=5)
        if r.status_code == 200:
            print("Brain server: connected")
        else:
            print("Brain server: error")
            return
    except:
        print(f"Brain server not reachable at {BRAIN_URL}")
        return

    play_sound("startup")
    print("State: IDLE — listening for 'Hey Merlin'...\n")

    state = State.IDLE
    last_reply = ""
    last_response_time = 0
    hush_time = 0
    from difflib import SequenceMatcher

    def close_conversation(next_state=State.IDLE):
        """Play close sound and transition state."""
        nonlocal state, last_reply, last_response_time
        play_sound("close")
        state = next_state
        last_reply = ""
        last_response_time = 0
        if next_state == State.HUSHED:
            print(f'  [hushed — say "Hey Merlin" to wake]')
        else:
            print("  [conversation closed]")
        print(f"State: {state.upper()}\n")

    def open_conversation():
        """Play open sound and enter conversation."""
        nonlocal state
        play_sound("open")
        state = State.CONVERSATION
        print("  [conversation opened]")
        print(f"State: {state.upper()}")

    while running:
        # ── Quiet hours ──────────────────────────────
        hour = datetime.datetime.now().hour
        if hour >= QUIET_START or hour < QUIET_END:
            stop_mic()
            time.sleep(30)
            start_mic()
            continue

        # ── Check for conversation timeout ──────────────
        if state == State.CONVERSATION and last_response_time > 0:
            if (time.time() - last_response_time) > CONVERSATION_TIMEOUT:
                print("  [silence timeout]")
                close_conversation(State.IDLE)

        # ── Check for hush timeout ──────────────────────
        if state == State.HUSHED:
            if (time.time() - hush_time) > HUSH_TIMEOUT:
                state = State.IDLE
                print("  [hush expired — back to idle]")
                print(f"State: {state.upper()}\n")

        # ── Record ──────────────────────────────────────
        wav = record_utterance()
        if wav is None:
            continue

        # ── STT ─────────────────────────────────────────
        text = stt(wav)
        if not text:
            continue

        # ── Echo filter ─────────────────────────────────
        if last_reply:
            similarity = SequenceMatcher(None, text.lower(), last_reply.lower()).ratio()
            if similarity > 0.4:
                print(f'  [echo filtered]')
                continue

        # ── State machine ───────────────────────────────

        has_wake = has_wake_word(text)

        # HUSHED: only wake word breaks through
        if state == State.HUSHED:
            if has_wake:
                open_conversation()
                continue  # wait for next utterance
            else:
                continue

        # IDLE: need wake word to start
        elif state == State.IDLE:
            if has_wake:
                open_conversation()
                # Always wait for next utterance after opening.
                # The wake word is the greeting. The request comes next.
                continue
            else:
                continue

        # CONVERSATION: check for close triggers first
        else:
            # Hush command → close + mute
            if is_hush(text):
                hush_time = time.time()
                close_conversation(State.HUSHED)
                continue

            # Dismiss command → close politely
            if is_dismiss(text):
                print(f'  You: "{text.strip()}"')
                close_conversation(State.IDLE)
                continue

            # Wake word mid-conversation is fine, just strip it
            message = strip_wake_word(text) if has_wake else text

        print(f'  You: "{message}"')

        # ── Think + Speak ───────────────────────────────
        play_sound_async("thinking")
        reply = think(message)
        if not reply:
            continue
        print(f'  Merlin: "{reply}"')

        # Kill mic during playback (echo suppression)
        stop_mic()
        speak(reply)
        play_sound("ready")  # E-F: "your turn"
        last_reply = reply
        last_response_time = time.time()
        time.sleep(0.5)
        start_mic()
        print()

    play_sound("close")
    print("\nShutdown.")


if __name__ == "__main__":
    main()
