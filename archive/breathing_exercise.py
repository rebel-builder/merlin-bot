#!/usr/bin/env python3
"""
Merlin — Breathing Exercise Module

Guides Ezra through a 3-round box-adjacent breathing sequence when in RED energy.

Triggerable via:
  - Brain server tool: [TOOL: breathing]
  - Verbal trigger: "Merlin, breathing" / "let's breathe"
  - Energy detection: when update_energy("red") fires

Two runtime modes:
  1. Brain server mode (standalone TTS): uses do_tts() from merlin_brain_server
     and afplay for playback. Works when imported inside brain server.
  2. Event bus mode: emits "speak" events via Merlin's Voice module.
     Works when running inside the full Merlin v2 stack (main.py).

Wire into brain server:
  - Import run_breathing_exercise at top of merlin_brain_server.py
  - Add `elif call == "breathing":` branch in execute_tool()
  - Add [TOOL: breathing] to the system prompt tool list
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
import time
import threading
import re
from pathlib import Path

log = logging.getLogger("merlin.breathing")

# ── Sequence definition ─────────────────────────────────────────────────────
#
# Each step: (text_to_speak, pause_seconds_after_speaking)
# pause_seconds_after_speaking = time to hold silence AFTER the audio finishes.
# The TTS itself takes ~0.5-1s to generate + playback duration — pauses are
# the explicit hold/breath windows the user experiences.

INTRO = "Let's breathe. Three rounds."

ROUNDS = [
    ("Stand up. Hands above your head.", 3),
    ("Deep breath in.", 4),
    ("Hold.", 4),
    ("Lower your hands slowly. Breathe out.", 6),
]

OUTRO = "Good. You can sit back down."

NUM_ROUNDS = 3


# ── TTS helpers ─────────────────────────────────────────────────────────────

def _clean_text(text: str) -> str:
    """Strip markdown and non-ASCII that crash Kokoro."""
    text = " ".join(text.replace("\n", " ").split()).strip()
    text = re.sub(r'[*_~`#]', '', text)
    text = re.sub(r'[^\x00-\x7F]', '', text)
    return text.strip()


def _generate_tts_brain_server(text: str, tts_model=None):
    """
    Generate TTS using the Kokoro model already loaded in brain server context.
    Returns WAV bytes or None.

    tts_model: the global tts_model from merlin_brain_server (may be None on first call,
               will be lazily loaded).
    """
    try:
        import numpy as np

        if tts_model is None:
            from mlx_audio.tts.generate import load_model
            tts_model = load_model("prince-canuma/Kokoro-82M")
            log.info("[breathing] Kokoro loaded")

        clean = _clean_text(text)
        if not clean:
            return None, tts_model

        chunks = list(tts_model.generate(text=clean, voice="am_fenrir"))
        if not chunks:
            return None, tts_model

        audio = np.concatenate([
            np.array(c.audio, dtype=np.float32) for c in chunks
        ])
        pcm = (audio * 32767).clip(-32768, 32767).astype(np.int16).tobytes()
        sr = chunks[0].sample_rate

        result = subprocess.run(
            ["ffmpeg", "-y", "-f", "s16le", "-ar", str(sr), "-ac", "1",
             "-i", "pipe:0", "-ar", "48000", "-f", "wav", "pipe:1"],
            input=pcm, capture_output=True, timeout=15,
        )

        wav_bytes = result.stdout if result.returncode == 0 else None
        return wav_bytes, tts_model

    except Exception as e:
        log.error(f"[breathing] TTS error: {e}")
        return None, tts_model


def _play_wav_bytes(wav_bytes: bytes) -> None:
    """Write WAV to a temp file and play via afplay (blocks until done)."""
    if not wav_bytes:
        return
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(wav_bytes)
            tmp_path = f.name

        subprocess.run(["afplay", tmp_path], capture_output=True, timeout=30)
    except Exception as e:
        log.error(f"[breathing] Playback error: {e}")
    finally:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            pass


def _speak_and_wait(text: str, pause: float, tts_model=None):
    """
    Speak one instruction, then hold for `pause` seconds.
    Returns updated tts_model (may be newly loaded).
    """
    print(f"[breathing] Speaking: '{text}' (pause {pause}s)")
    wav, tts_model = _generate_tts_brain_server(text, tts_model)
    _play_wav_bytes(wav)
    if pause > 0:
        time.sleep(pause)
    return tts_model


# ── Core sequence ────────────────────────────────────────────────────────────

def run_breathing_exercise(tts_model=None, bus=None) -> str:
    """
    Run the full guided breathing sequence. Blocking call — takes ~1.5 min.

    Args:
        tts_model: Kokoro model instance if already loaded (brain server mode).
                   Pass None to auto-load. Returned model can be reused.
        bus: EventBus instance for event-bus mode. If provided, uses bus.emit("speak")
             instead of direct TTS calls.

    Returns:
        Status string for brain server tool result.
    """
    print("[breathing] Starting breathing exercise")

    if bus is not None:
        # Event-bus mode: delegate to Voice module
        return _run_via_event_bus(bus)
    else:
        # Brain server mode: direct TTS + afplay
        return _run_direct(tts_model)


def _run_direct(tts_model=None) -> str:
    """Brain server mode: generate TTS directly and play via afplay."""
    # Intro
    tts_model = _speak_and_wait(INTRO, pause=1.5, tts_model=tts_model)

    for round_num in range(1, NUM_ROUNDS + 1):
        print(f"[breathing] Round {round_num}/{NUM_ROUNDS}")
        if NUM_ROUNDS > 1:
            # Brief round marker as a short pause, no extra speech (keeps it clean)
            time.sleep(0.5)

        for text, pause in ROUNDS:
            tts_model = _speak_and_wait(text, pause=pause, tts_model=tts_model)

        # Inter-round gap (except after last round)
        if round_num < NUM_ROUNDS:
            time.sleep(1.0)

    # Outro
    tts_model = _speak_and_wait(OUTRO, pause=0, tts_model=tts_model)
    print("[breathing] Exercise complete")
    return "Breathing exercise complete. Three rounds done."


def _run_via_event_bus(bus) -> str:
    """
    Event-bus mode: emit speak events and use Voice._speak_thread's lock
    to ensure sequential playback.

    Voice._speak_thread holds self._lock, so back-to-back emit("speak") calls
    will queue naturally because each speak thread acquires the same lock.
    We use threading.Event to detect when each utterance finishes before
    sleeping for the pause window.
    """
    done = threading.Event()

    def on_finished():
        done.set()

    def speak_and_wait(text: str, pause: float):
        done.clear()
        bus.on("speaking_finished", on_finished)
        bus.emit("speak", text=text)
        # Wait for speaking_finished event (with generous timeout)
        done.wait(timeout=15)
        bus.off("speaking_finished", on_finished)
        if pause > 0:
            time.sleep(pause)

    print("[breathing] Event-bus mode: starting")

    speak_and_wait(INTRO, pause=1.5)

    for round_num in range(1, NUM_ROUNDS + 1):
        print(f"[breathing] Round {round_num}/{NUM_ROUNDS}")
        if NUM_ROUNDS > 1:
            time.sleep(0.5)

        for text, pause in ROUNDS:
            speak_and_wait(text, pause=pause)

        if round_num < NUM_ROUNDS:
            time.sleep(1.0)

    speak_and_wait(OUTRO, pause=0)
    print("[breathing] Exercise complete")
    return "Breathing exercise complete."


# ── Brain server integration helpers ────────────────────────────────────────

# Trigger phrases that should invoke the breathing exercise (checked in /think handler)
BREATHING_TRIGGERS = [
    "breathing",
    "let's breathe",
    "lets breathe",
    "help me breathe",
    "breathing exercise",
    "breathe with me",
    "calm me down",
    "i need to breathe",
]


def is_breathing_trigger(text: str) -> bool:
    """Return True if user's spoken text should trigger the breathing exercise."""
    lowered = text.lower().strip()
    return any(trigger in lowered for trigger in BREATHING_TRIGGERS)


def run_breathing_in_background(tts_model=None, bus=None) -> str:
    """
    Kick off the breathing exercise in a background thread (non-blocking).
    Returns immediately with a status string. Used when the brain server
    wants to respond to Ezra first, then run the exercise.
    """
    thread = threading.Thread(
        target=run_breathing_exercise,
        kwargs={"tts_model": tts_model, "bus": bus},
        daemon=True,
        name="breathing_exercise",
    )
    thread.start()
    return "Starting breathing exercise now."


# ── Wiring instructions (not executed — read by devs) ───────────────────────
#
# === merlin_brain_server.py ===
#
# 1. At top of file, add import:
#    from breathing_exercise import run_breathing_exercise, BREATHING_TRIGGERS
#
# 2. In _BASE_SYSTEM_PROMPT, add to TOOLS list:
#    [TOOL: breathing] — guide Ezra through a breathing exercise (RED energy)
#
# 3. In execute_tool(), add before the `else` clause:
#    elif call == "breathing":
#        return run_breathing_exercise(tts_model=tts_model)
#
# 4. In do_think(), after the spoken_text trigger check, add RED energy detection:
#    from breathing_exercise import is_breathing_trigger
#    if is_breathing_trigger(text):
#        return "[TOOL: breathing]"  # let execute_tool handle it
#
# === main.py (event-bus stack) ===
#
# Import and register as a handler on the "breathing" event:
#    from breathing_exercise import run_breathing_in_background
#    bus.on("breathing", lambda: run_breathing_in_background(bus=bus))
#
# Trigger from energy module when RED detected:
#    if new_energy == "red":
#        bus.emit("breathing")


# ── Standalone test ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="[%(name)s] %(message)s")

    if "--test-trigger" in sys.argv:
        # Test the trigger detection
        test_phrases = [
            "Merlin, breathing",
            "let's breathe",
            "help me code",
            "I need to breathe",
            "what time is it",
            "calm me down",
        ]
        print("Trigger detection test:")
        for phrase in test_phrases:
            result = is_breathing_trigger(phrase)
            print(f"  '{phrase}' → {'TRIGGER' if result else 'no'}")
        sys.exit(0)

    # Full exercise test — runs the real TTS sequence
    print("Running breathing exercise (full sequence, ~90 seconds)...")
    print("Ctrl-C to abort.\n")
    try:
        status = run_breathing_exercise()
        print(f"\nResult: {status}")
    except KeyboardInterrupt:
        print("\nAborted.")
