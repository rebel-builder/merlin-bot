#!/usr/bin/env python3
"""Generate Merlin's sound palette. All procedural, tuned for tiny USB speaker."""

import numpy as np
import struct
import wave
from pathlib import Path

SAMPLE_RATE = 48000
OUT_DIR = Path(__file__).parent

def save_wav(filename, samples):
    """Save float32 samples as 16-bit WAV."""
    pcm = (samples * 32767).clip(-32768, 32767).astype(np.int16)
    path = OUT_DIR / filename
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm.tobytes())
    print(f"  {filename} ({len(pcm)/SAMPLE_RATE*1000:.0f}ms, {path.stat().st_size}B)")

def envelope(length, attack=0.01, decay=0.05):
    """ADSR-ish envelope."""
    t = np.linspace(0, 1, length)
    att = int(attack * length)
    dec = int(decay * length)
    env = np.ones(length)
    env[:att] = np.linspace(0, 1, att)
    env[-dec:] = np.linspace(1, 0, dec)
    return env

def chirp(f_start, f_end, duration, volume=0.3):
    """Frequency sweep."""
    n = int(SAMPLE_RATE * duration)
    t = np.linspace(0, duration, n)
    freq = np.linspace(f_start, f_end, n)
    phase = 2 * np.pi * np.cumsum(freq) / SAMPLE_RATE
    return volume * np.sin(phase) * envelope(n)

def tone(freq, duration, volume=0.3):
    """Pure sine tone."""
    n = int(SAMPLE_RATE * duration)
    t = np.linspace(0, duration, n)
    return volume * np.sin(2 * np.pi * freq * t) * envelope(n)

def silence(duration):
    return np.zeros(int(SAMPLE_RATE * duration))


print("Generating Merlin sounds...")

# 1. WAKE — "I heard you" acknowledgment (ascending two-tone)
s = np.concatenate([tone(800, 0.08), silence(0.03), tone(1200, 0.1)])
save_wav("wake.wav", s)

# 2. LISTENING — "I'm processing" (soft repeating pulse)
pulses = []
for i in range(3):
    pulses.append(tone(600 + i*100, 0.06, volume=0.15))
    pulses.append(silence(0.08))
save_wav("listening.wav", np.concatenate(pulses))

# 3. ACKNOWLEDGE — "Got it" (single warm beep)
save_wav("ack.wav", tone(900, 0.12, volume=0.25))

# 4. HAPPY — "Something good happened" (ascending chirp)
save_wav("happy.wav", chirp(600, 1400, 0.2, volume=0.3))

# 5. CURIOUS — "Hmm interesting" (wobble)
n = int(SAMPLE_RATE * 0.4)
t = np.linspace(0, 0.4, n)
wobble = 0.2 * np.sin(2 * np.pi * (500 + 80*np.sin(2*np.pi*6*t)) * t) * envelope(n)
save_wav("curious.wav", wobble)

# 6. ALERT — "Hey, check this" (two quick ascending pings)
s = np.concatenate([tone(800, 0.06), silence(0.04), tone(1100, 0.08)])
save_wav("alert.wav", s)

# 7. SAD — "That's rough" (descending tone)
save_wav("sad.wav", chirp(800, 400, 0.35, volume=0.2))

# 8. THINKING — "Processing..." (rapid soft alternating)
bits = []
for i in range(6):
    f = 700 if i % 2 == 0 else 900
    bits.append(tone(f, 0.05, volume=0.12))
    bits.append(silence(0.03))
save_wav("thinking.wav", np.concatenate(bits))

# 9. GREETING — "Morning!" (warm ascending three-tone)
s = np.concatenate([
    tone(500, 0.1, volume=0.2),
    silence(0.03),
    tone(700, 0.1, volume=0.25),
    silence(0.03),
    tone(1000, 0.15, volume=0.3),
])
save_wav("greeting.wav", s)

# 10. GOODBYE — "See ya" (descending two-tone, gentle)
s = np.concatenate([tone(900, 0.12, volume=0.2), silence(0.04), tone(600, 0.15, volume=0.15)])
save_wav("goodbye.wav", s)

# 11. ERROR — "Oops" (low buzz)
save_wav("error.wav", tone(300, 0.2, volume=0.2))

# 12. NUDGE — "Hey, still there?" (single soft ping)
save_wav("nudge.wav", tone(700, 0.15, volume=0.15))

# 13. STARTUP — "Booting up" (ascending sweep with echo)
startup = chirp(300, 1500, 0.5, volume=0.25)
echo = np.concatenate([silence(0.1), chirp(300, 1500, 0.5, volume=0.1)])
combined = np.zeros(max(len(startup), len(echo)))
combined[:len(startup)] += startup
combined[:len(echo)] += echo
save_wav("startup.wav", combined)

print(f"\nDone. {len(list(OUT_DIR.glob('*.wav')))} sounds generated in {OUT_DIR}")
