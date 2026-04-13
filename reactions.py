#!/usr/bin/env python3
"""
Merlin Reactions Layer — runs on Pi 5, uses local Ollama LLM.

Three-tier system:
  1. Reflexes — instant, rule-based, no LLM (<100ms)
  2. Behavior chains — reflex fires, then LLM scripts the follow-up sequence
  3. Context reactions — LLM-classified, picks from a fixed palette (~3s)

The LLM never generates speech. It directs sequences of pre-built
micro-behaviors (sounds + camera movements) that chain together
to create unique, organic-feeling reactions every time.
"""

import json
import random
import socket
import struct
import subprocess
import threading
import time
from datetime import datetime

import requests

# ── Config ──────────────────────────────────────────────────

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen3.5:0.8b"

SPEAKER_DEVICE = "plughw:1,0"
SOUNDS_DIR = "/home/pi/RBOS/merlin/sounds"
STARTLE_PORT = 8902  # send startle to tracker
TRACKER_HOST = "127.0.0.1"

# Audio monitoring
LOUD_NOISE_THRESHOLD = 3000   # RMS value — adjust based on testing
STARTLE_COOLDOWN = 8.0        # seconds between startle reflexes
REACTION_COOLDOWN = 5.0       # seconds between any context reactions

# Adaptive volume — Merlin gets louder in noisy rooms, quieter in silence.
# ALSA mixer: card 1 (USB speaker), control "PCM", range 0–240.
ALSA_CARD = "1"
ALSA_CONTROL = "PCM"
VOLUME_MIN = 25               # ~10% — whisper in dead silence
VOLUME_MAX = 200              # ~83% — loud but not clipping
VOLUME_UPDATE_INTERVAL = 5.0  # seconds between volume adjustments
RMS_WINDOW_SIZE = 30          # rolling window of RMS samples (~10s at 3 samples/sec)

# ── Reaction Palette ────────────────────────────────────────
# Each reaction = sound(s) to play + optional camera action

REACTIONS = {
    "startled": {
        "sounds": ["alert"],
        "camera": "startle",  # triggers tracker's do_startle()
        "description": "Quick snap + alert sound. For sudden unexpected stimuli.",
    },
    "curious": {
        "sounds": ["curious"],
        "camera": None,
        "description": "Interested hum. Something unusual noticed.",
    },
    "happy": {
        "sounds": ["happy"],
        "camera": None,
        "description": "Pleased tone. Something good recognized.",
    },
    "greeting": {
        "sounds": ["greeting"],
        "camera": None,
        "description": "Warm hello. Person arrived.",
    },
    "farewell": {
        "sounds": ["goodbye"],
        "camera": None,
        "description": "Gentle bye. Person leaving.",
    },
    "acknowledge": {
        "sounds": ["ack"],
        "camera": None,
        "description": "Small sound. Noted something, minimal response.",
    },
    "alert": {
        "sounds": ["alert"],
        "camera": None,
        "description": "Attention sound. Something needs awareness.",
    },
    "idle_hum": {
        "sounds": None,  # picks random musical note
        "camera": None,
        "description": "Quiet musical note. Ambient life signal.",
    },
    "none": {
        "sounds": None,
        "camera": None,
        "description": "No reaction. Most events should map here.",
    },
}

# Musical notes for idle hum — single notes, random selection
IDLE_NOTES = [
    "n1_C_warm", "n1_D_warm", "n1_E_warm", "n1_F_warm", "n1_G_warm",
    "n1_C_soft", "n1_D_soft", "n1_E_soft", "n1_F_soft", "n1_G_soft",
]

# Musical phrases for sequences
RISING_PHRASES = ["n2_up_CE_tight", "n2_up_CG_tight", "n3_up_CEG"]
FALLING_PHRASES = ["n2_dn_EC_tight", "n2_dn_GC_tight", "n3_dn_GEC"]
ARC_PHRASES = ["n3_arc_CED", "n3_arc_CGE", "n3_arc_EGF"]
LONG_PHRASES = ["n4_CDEF", "n4_CEFG", "n5_CDEFG", "n5_CGEFD"]

# ── Micro-Behaviors (chain building blocks) ─────────────────
# Each is a small program: ~1-3 seconds of physical behavior.
# The LLM chains these together after a reflex fires.

MICRO_BEHAVIORS = {
    "look_around": {
        "sounds": None,
        "camera": "slow_pan",   # slow camera sweep
        "pause": 1.5,
        "description": "Slow camera pan to scan the area.",
    },
    "look_toward_sound": {
        "sounds": None,
        "camera": "startle",    # quick look
        "pause": 0.5,
        "description": "Quick camera snap toward sound source.",
    },
    "settle": {
        "sounds": None,  # picks falling phrase
        "camera": "center",     # return to neutral
        "pause": 1.0,
        "description": "Return to center. Calm down.",
        "sound_pool": FALLING_PHRASES,
    },
    "stay_alert": {
        "sounds": ["alert"],
        "camera": None,         # hold position
        "pause": 2.0,
        "description": "Hold position, alert sound. Watching.",
    },
    "curious_tilt": {
        "sounds": ["curious"],
        "camera": "tilt_up",    # slight head tilt
        "pause": 1.0,
        "description": "Slight camera tilt, curious sound.",
    },
    "nervous_fidget": {
        "sounds": None,
        "camera": "fidget",     # small random movements
        "pause": 1.5,
        "description": "Small random camera jitters. Uneasy.",
        "sound_pool": ["n2_tap_CC", "n2_tap_DD", "n2_tap_EE"],
    },
    "relax": {
        "sounds": None,
        "camera": "center",
        "pause": 1.0,
        "description": "Ease back to center. All clear.",
        "sound_pool": IDLE_NOTES,
    },
    "perk_up": {
        "sounds": None,
        "camera": "tilt_up",
        "pause": 0.8,
        "description": "Quick lift. Noticed something interesting.",
        "sound_pool": RISING_PHRASES,
    },
    "warm_hum": {
        "sounds": None,
        "camera": None,
        "pause": 1.0,
        "description": "Warm musical phrase. Content.",
        "sound_pool": ARC_PHRASES,
    },
    "done": {
        "sounds": None,
        "camera": None,
        "pause": 0,
        "description": "Chain ends. No more actions.",
    },
}

MAX_CHAIN_LENGTH = 4  # prevent infinite loops

# ── LLM System Prompts ─────────────────────────────────────

CLASSIFY_PROMPT = """You are the reaction classifier for Merlin, a desk companion.

Given a sensor event, pick ONE reaction from this list:
  startled — sudden unexpected stimulus
  curious — something unusual, worth noticing
  happy — something positive recognized
  greeting — person just arrived
  farewell — person leaving
  acknowledge — minimal, noted
  alert — needs attention
  idle_hum — ambient life signal
  none — no reaction needed (DEFAULT for most events)

RULES:
- Respond with ONLY the reaction name. One word. No explanation.
- Default to "none" unless the event genuinely warrants a reaction.
- Merlin is calm and doesn't overreact. Most events = "none".
- Only "startled" for truly sudden, unexpected stimuli.
- "curious" for unusual patterns, not routine activity."""

CHAIN_PROMPT = """You direct Merlin's physical behavior after a reaction.

Pick what he does NEXT from this list:
  look_around — slow scan
  settle — return to center, calm
  stay_alert — hold, keep watching
  curious_tilt — head tilt
  nervous_fidget — jittery
  relax — ease back, all clear
  perk_up — quick lift
  warm_hum — content sound
  done — stop

RULES:
- ONE word only.
- NEVER repeat the last behavior. Always pick something DIFFERENT.
- Chains end with settle, relax, or done. Pick one of these soon.
- After startled: look_around or stay_alert, then settle or relax.
- After greeting: perk_up or warm_hum, then done."""


# ── State ───────────────────────────────────────────────────

class ReactionsState:
    def __init__(self):
        self.last_startle_time = 0
        self.last_reaction_time = 0
        self.face_present = False
        self.face_arrived_time = 0
        self.last_loud_noise_time = 0
        self.llm_available = False
        self.lock = threading.Lock()
        # Adaptive volume
        self.rms_samples = []          # rolling window of recent RMS values
        self.last_volume_update = 0
        self.current_volume = 60       # start at ~25%

_state = ReactionsState()


# ── Adaptive Volume ─────────────────────────────────────────

def _rms_to_volume(avg_rms):
    """Map ambient noise level to speaker volume.

    Quiet room → quiet Merlin. Noisy room → louder Merlin.
    Uses a curve that rises steeply at first then flattens —
    so even moderate noise makes Merlin audible, but he never
    blasts in a very loud room.

    RMS ranges (approximate, needs real-world tuning):
      < 100   dead silence (night, empty room)
      100-300 typical quiet desk
      300-800 conversation nearby, music
      800+    loud activity, TV, multiple voices
    """
    if avg_rms < 50:
        return VOLUME_MIN
    if avg_rms > 2000:
        return VOLUME_MAX

    # Square root curve: rises fast then flattens
    import math
    normalized = min(avg_rms / 2000.0, 1.0)
    volume = VOLUME_MIN + (VOLUME_MAX - VOLUME_MIN) * math.sqrt(normalized)
    return int(volume)


def _set_alsa_volume(volume):
    """Set the USB speaker volume via ALSA mixer."""
    try:
        subprocess.run(
            ["amixer", "-c", ALSA_CARD, "sset", ALSA_CONTROL, str(volume)],
            capture_output=True, timeout=3,
        )
    except Exception:
        pass


def update_volume():
    """Called periodically. Adjusts speaker volume based on rolling ambient RMS."""
    now = time.time()
    if now - _state.last_volume_update < VOLUME_UPDATE_INTERVAL:
        return

    _state.last_volume_update = now

    with _state.lock:
        if not _state.rms_samples:
            return
        avg_rms = sum(_state.rms_samples) / len(_state.rms_samples)

    new_volume = _rms_to_volume(avg_rms)

    # Only update hardware if volume changed meaningfully (±5 units)
    if abs(new_volume - _state.current_volume) >= 5:
        _state.current_volume = new_volume
        _set_alsa_volume(new_volume)
        print(f"[volume] RMS avg={avg_rms:.0f} → volume={new_volume}/{VOLUME_MAX}")


def feed_rms(rms_level):
    """Feed an RMS sample into the rolling window. Called from audio loop."""
    with _state.lock:
        _state.rms_samples.append(rms_level)
        # Trim to window size
        if len(_state.rms_samples) > RMS_WINDOW_SIZE:
            _state.rms_samples = _state.rms_samples[-RMS_WINDOW_SIZE:]


# ── Sound Playback ──────────────────────────────────────────

def play_sound(name):
    """Play a WAV file through the Pi speaker. Non-blocking."""
    path = f"{SOUNDS_DIR}/{name}.wav"
    try:
        subprocess.Popen(
            ["aplay", "-D", SPEAKER_DEVICE, "-q", path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def send_startle():
    """Send startle signal to tracker via UDP."""
    send_camera_command("startle")


def send_camera_command(command):
    """Send a command to tracker via UDP. Commands: startle, center, tilt_up, slow_pan, fidget."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(command.encode(), (TRACKER_HOST, STARTLE_PORT))
        sock.close()
    except Exception:
        pass


# ── Tier 1: Reflexes (instant, no LLM) ─────────────────────

def reflex_loud_noise(rms_level):
    """Instant startle on loud noise. Then LLM scripts the follow-up."""
    now = time.time()
    with _state.lock:
        if now - _state.last_startle_time < STARTLE_COOLDOWN:
            return False
        if rms_level > LOUD_NOISE_THRESHOLD:
            _state.last_startle_time = now
            _state.last_reaction_time = now
            print(f"[reactions] REFLEX: startle (rms={rms_level:.0f})")
            send_startle()
            play_sound("alert")
            # Chain: LLM decides what Merlin does after being startled
            event = f"Loud noise. RMS={rms_level:.0f}. Merlin was startled."
            threading.Thread(
                target=run_behavior_chain,
                args=(event, "startled"),
                daemon=True, name="chain_startle",
            ).start()
            return True
    return False


def reflex_face_arrived():
    """Instant greeting when face first detected after absence."""
    now = time.time()
    with _state.lock:
        if _state.face_present:
            return False
        _state.face_present = True
        _state.face_arrived_time = now
        if now - _state.last_reaction_time < REACTION_COOLDOWN:
            return False
        _state.last_reaction_time = now
    print("[reactions] REFLEX: greeting (face arrived)")
    play_sound("greeting")
    # Chain: what does Merlin do after greeting?
    threading.Thread(
        target=run_behavior_chain,
        args=("Person sat down at the desk. Merlin greeted them.", "greeting"),
        daemon=True, name="chain_greet",
    ).start()
    return True


def reflex_face_lost():
    """Mark face as gone. Quiet farewell if they were here a while."""
    now = time.time()
    with _state.lock:
        was_present = _state.face_present
        duration = now - _state.face_arrived_time if was_present else 0
        _state.face_present = False
        if not was_present or duration < 30:
            return False  # too brief to react
        if now - _state.last_reaction_time < REACTION_COOLDOWN:
            return False
        _state.last_reaction_time = now
    print(f"[reactions] REFLEX: farewell (was here {duration:.0f}s)")
    play_sound("goodbye")
    return True


# ── Tier 2: Context Reactions (LLM-classified) ─────────────

def _llm_pick(system_prompt, user_prompt, valid_set, temperature=0.5):
    """Send a classification request to Ollama. Returns a key from valid_set or None."""
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "system": system_prompt,
                "prompt": user_prompt,
                "stream": False,
                "think": False,
                "options": {
                    "num_predict": 5,
                    "temperature": temperature,
                },
            },
            timeout=30,
        )
        if resp.status_code == 200:
            raw = resp.json().get("response", "").strip().lower()
            pick = raw.split()[0].rstrip(".,!?") if raw else None
            if pick in valid_set:
                return pick
            # Try underscore variants (LLM might output "look around" instead of "look_around")
            pick_under = raw.replace(" ", "_").split("\n")[0].rstrip(".,!?")
            if pick_under in valid_set:
                return pick_under
            print(f"[reactions] LLM returned unknown: {raw!r}")
    except Exception as e:
        print(f"[reactions] LLM error: {e}")
    return None


def classify_event(event_description):
    """Send event to local Ollama, get reaction name back."""
    return _llm_pick(CLASSIFY_PROMPT, event_description, REACTIONS) or "none"


def execute_reaction(reaction_name):
    """Execute a named reaction — play sounds, trigger camera."""
    if reaction_name == "none":
        return

    now = time.time()
    with _state.lock:
        if now - _state.last_reaction_time < REACTION_COOLDOWN:
            return
        _state.last_reaction_time = now

    reaction = REACTIONS.get(reaction_name, REACTIONS["none"])
    print(f"[reactions] EXECUTE: {reaction_name}")

    # Sound
    sounds = reaction["sounds"]
    if sounds:
        play_sound(random.choice(sounds))
    elif reaction_name == "idle_hum":
        play_sound(random.choice(IDLE_NOTES))

    # Camera
    if reaction.get("camera") == "startle":
        send_startle()


def execute_micro(behavior_name):
    """Execute a single micro-behavior from the chain palette."""
    if behavior_name == "done":
        return

    behavior = MICRO_BEHAVIORS.get(behavior_name)
    if not behavior:
        return

    print(f"[reactions]   chain → {behavior_name}")

    # Sound
    pool = behavior.get("sound_pool")
    if behavior["sounds"]:
        play_sound(random.choice(behavior["sounds"]))
    elif pool:
        play_sound(random.choice(pool))

    # Camera
    cam = behavior.get("camera")
    if cam == "startle":
        send_startle()
    elif cam == "center":
        send_camera_command("center")
    elif cam == "tilt_up":
        send_camera_command("tilt_up")
    elif cam == "slow_pan":
        send_camera_command("slow_pan")
    elif cam == "fidget":
        send_camera_command("fidget")

    # Pause between chain steps
    pause = behavior.get("pause", 1.0)
    if pause > 0:
        time.sleep(pause)


def run_behavior_chain(trigger_event, initial_reaction):
    """After a reflex fires, LLM scripts the follow-up sequence.

    Example chain after startle:
      startle (instant) → look_around → curious_tilt → settle
    """
    if not _state.llm_available:
        return

    chain = [initial_reaction]

    for step in range(MAX_CHAIN_LENGTH):
        chain_str = " → ".join(chain)
        prompt = f"Event: {trigger_event}\nSequence so far: {chain_str}\nDo NOT repeat '{chain[-1]}'. What next?"
        pick = _llm_pick(CHAIN_PROMPT, prompt, MICRO_BEHAVIORS, temperature=0.8)

        # Prevent direct repeats even if LLM fails
        if pick == chain[-1]:
            pick = "done"

        if not pick or pick == "done":
            print(f"[reactions]   chain ends (step {step + 1})")
            break

        execute_micro(pick)
        chain.append(pick)

    print(f"[reactions] CHAIN COMPLETE: {' → '.join(chain)}")


def context_react(event_description):
    """Classify an event with the LLM and execute the reaction.
    Runs in a background thread so it doesn't block the caller."""
    def _run():
        reaction = classify_event(event_description)
        if reaction != "none":
            execute_reaction(reaction)
            # After the initial reaction, run a behavior chain
            run_behavior_chain(event_description, reaction)

    threading.Thread(target=_run, daemon=True, name="ctx_react").start()


# ── Public API ──────────────────────────────────────────────

def on_audio_rms(rms_level):
    """Called with each audio chunk's RMS level. Feeds volume + checks startle."""
    feed_rms(rms_level)
    update_volume()
    reflex_loud_noise(rms_level)


def on_face_event(event_type):
    """Called when tracker reports face_arrived or face_lost."""
    if event_type == "face_arrived":
        reflex_face_arrived()
    elif event_type == "face_lost":
        reflex_face_lost()


def on_ambient_event(description):
    """Called for ambiguous events that need LLM classification.
    Examples:
      "Unusual sound pattern — rhythmic tapping, not speech"
      "Very quiet for 20 minutes, then sudden activity"
      "Multiple voices detected"
    """
    context_react(description)


# ── Health Check ────────────────────────────────────────────

def check_ollama():
    """Check if Ollama is running and model is available."""
    try:
        resp = requests.get("http://localhost:11434/api/tags", timeout=3)
        if resp.status_code == 200:
            models = [m["name"] for m in resp.json().get("models", [])]
            _state.llm_available = OLLAMA_MODEL in models
            return _state.llm_available
    except Exception:
        pass
    _state.llm_available = False
    return False


# ── Standalone test mode ────────────────────────────────────

if __name__ == "__main__":
    print("Merlin Reactions — Test Mode")
    print(f"Ollama: {OLLAMA_URL}")
    print(f"Model: {OLLAMA_MODEL}")

    if check_ollama():
        print("LLM: connected\n")
    else:
        print("LLM: NOT available — context reactions disabled\n")

    # Test adaptive volume
    print("--- Testing adaptive volume ---")
    test_levels = [50, 150, 400, 1000, 2500]
    for rms in test_levels:
        vol = _rms_to_volume(rms)
        bar = "=" * (vol // 5)
        print(f"  RMS {rms:>5} → volume {vol:>3}/{VOLUME_MAX}  {bar}")
    print()

    # Test reflexes
    print("--- Testing reflexes ---")
    print(f"Loud noise (rms=5000): ", end="")
    reflex_loud_noise(5000)
    time.sleep(1)

    print(f"Face arrived: ", end="")
    reflex_face_arrived()
    time.sleep(1)

    print(f"Face lost: ", end="")
    _state.face_arrived_time = time.time() - 60  # pretend they were here 60s
    reflex_face_lost()
    time.sleep(1)

    # Test LLM classification
    if _state.llm_available:
        print("\n--- Testing LLM classification ---")
        test_events = [
            "Sudden loud bang from nearby. RMS jumped from 200 to 8000.",
            "Quiet rhythmic tapping sound. Like fingers on desk.",
            "Bird singing outside the window.",
            "Someone sneezed.",
            "Complete silence for 30 minutes.",
        ]
        for event in test_events:
            print(f"\nEvent: {event}")
            reaction = classify_event(event)
            print(f"  → {reaction}")
            time.sleep(1)

        # Test behavior chains
        print("\n--- Testing behavior chains ---")
        print("\nChain after startle:")
        run_behavior_chain("Loud bang on desk. Merlin was startled.", "startled")

        print("\nChain after greeting:")
        run_behavior_chain("Person sat down. Morning. Merlin greeted them.", "greeting")

    print("\nDone.")
