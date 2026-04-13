"""
Merlin sound effects — synthesized tones, no external files needed.
Plays through the default audio output (BT speaker).

Replace these with MP3 files later if you want custom sounds.
"""

import numpy as np
import sounddevice as sd
from config import SPEAKER_DEVICE

SAMPLE_RATE = 24000


def _play(samples):
    """Play audio samples without blocking other operations."""
    try:
        sd.play(samples.astype(np.float32), samplerate=SAMPLE_RATE, device=SPEAKER_DEVICE)
        sd.wait()
    except Exception as e:
        print(f"[sounds] Playback error: {e}")


def listening():
    """Soft ascending two-tone chime — Merlin heard you."""
    duration = 0.25
    t = np.linspace(0, duration, int(SAMPLE_RATE * duration), endpoint=False)

    # Two quick ascending notes
    tone1 = np.sin(2 * np.pi * 600 * t[:len(t)//2]) * 0.15
    tone2 = np.sin(2 * np.pi * 900 * t[len(t)//2:]) * 0.15

    # Fade in/out to avoid clicks
    fade = int(SAMPLE_RATE * 0.02)
    tone1[:fade] *= np.linspace(0, 1, fade)
    tone1[-fade:] *= np.linspace(1, 0, fade)
    tone2[:fade] *= np.linspace(0, 1, fade)
    tone2[-fade:] *= np.linspace(1, 0, fade)

    _play(np.concatenate([tone1, tone2]))


def thinking():
    """Gentle low pulse — Merlin is thinking."""
    duration = 0.6
    t = np.linspace(0, duration, int(SAMPLE_RATE * duration), endpoint=False)

    # Soft low hum with gentle wobble
    wobble = 1 + 0.02 * np.sin(2 * np.pi * 4 * t)
    tone = np.sin(2 * np.pi * 220 * t * wobble) * 0.1

    # Smooth envelope (fade in, sustain, fade out)
    env = np.ones_like(t)
    fade_in = int(SAMPLE_RATE * 0.1)
    fade_out = int(SAMPLE_RATE * 0.2)
    env[:fade_in] = np.linspace(0, 1, fade_in)
    env[-fade_out:] = np.linspace(1, 0, fade_out)

    _play(tone * env)


def acknowledged():
    """Single soft tone — Merlin acknowledged a command (mute, nevermind, etc)."""
    duration = 0.15
    t = np.linspace(0, duration, int(SAMPLE_RATE * duration), endpoint=False)

    tone = np.sin(2 * np.pi * 500 * t) * 0.1
    fade = int(SAMPLE_RATE * 0.02)
    tone[:fade] *= np.linspace(0, 1, fade)
    tone[-fade:] *= np.linspace(1, 0, fade)

    _play(tone)


def greeting():
    """Warm three-note rising chime — Merlin sees you."""
    duration = 0.45
    t = np.linspace(0, duration, int(SAMPLE_RATE * duration), endpoint=False)
    third = len(t) // 3

    tone1 = np.sin(2 * np.pi * 440 * t[:third]) * 0.12
    tone2 = np.sin(2 * np.pi * 554 * t[third:2*third]) * 0.12
    tone3 = np.sin(2 * np.pi * 659 * t[2*third:]) * 0.12

    fade = int(SAMPLE_RATE * 0.015)
    for tone in [tone1, tone2, tone3]:
        tone[:fade] *= np.linspace(0, 1, fade)
        tone[-fade:] *= np.linspace(1, 0, fade)

    _play(np.concatenate([tone1, tone2, tone3]))
