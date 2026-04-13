#!/usr/bin/env python3
"""
Generate Merlin's sound palette from a single source: wood clave.

Design principles (Vector/Gabaldon):
  - One organic source, processed many ways → shared timbral DNA
  - Semi-random selection at runtime → repetition kills the magic
  - Pre-recorded variations > procedural → more character

Merlin's fiction: a small, warm, wooden creature.
Five notes: C5, D5, E5, F5, G5 — like a tiny wooden xylophone.
All dry, no reverb, not harsh or metallic.

Categories by note count:
  1-note: single tinks (wake, responding, simple acknowledgments)
  2-note: pairs (listening, stopping, short reactions)
  3-note: triplets (thinking, greeting, expressive moments)
  4-note: idle chatter (little musical phrases)
  5-note: idle flourishes (tiny melodies)

Usage:
  python3 generate_clave.py              # generate default set
  python3 generate_clave.py --seed 42    # different variations
  python3 generate_clave.py --seed 99    # yet more variations
"""

import argparse
import numpy as np
import wave
from itertools import combinations, permutations
from pathlib import Path

SAMPLE_RATE = 48000
OUT_DIR = Path(__file__).parent

# ── The five notes ──────────────────────────────────────────
# C5 through G5 — warm, clear range for a small speaker.

NOTES = {
    "C": 523.25,
    "D": 587.33,
    "E": 659.25,
    "F": 698.46,
    "G": 783.99,
}
NOTE_NAMES = list(NOTES.keys())
NOTE_FREQS = list(NOTES.values())


# ── Core: the wood clave strike ─────────────────────────────

def clave_strike(freq=900, brightness=0.5, volume=0.3, duration=0.05,
                 click_amount=0.3, decay_speed=46):
    """One wood clave hit. The atom of Merlin's voice.

    Locked timing: ~50ms duration, ~46 decay speed.
    Second note enters as first decays to ~9% amplitude.
    See SOUND_DESIGN.md for principles.
    """
    n = int(SAMPLE_RATE * duration)
    t = np.linspace(0, duration, n)

    # Exponential decay — the key to "wooden"
    env = np.exp(-decay_speed * t)

    # Transient click (broadband noise burst, first 1.5ms)
    click_len = int(SAMPLE_RATE * 0.0015)
    click = np.zeros(n)
    click[:click_len] = np.random.randn(click_len) * click_amount
    click[:click_len] *= np.exp(-np.linspace(0, 10, click_len))

    # Inharmonic partials (real wood ratios, not integer)
    h1 = np.sin(2 * np.pi * freq * t)
    h2 = 0.5 * np.sin(2 * np.pi * freq * 2.7 * t)
    h3 = brightness * 0.25 * np.sin(2 * np.pi * freq * 5.4 * t)
    h4 = brightness * 0.1 * np.sin(2 * np.pi * freq * 8.1 * t)

    body = (h1 * env +
            h2 * np.exp(-decay_speed * 1.8 * t) +
            h3 * np.exp(-decay_speed * 3.0 * t) +
            h4 * np.exp(-decay_speed * 5.0 * t))

    signal = (click + body) * volume
    return np.tanh(signal * 1.5) / 1.5


def silence(duration):
    return np.zeros(int(SAMPLE_RATE * duration))


def save_wav(filename, samples):
    """Save float32 samples as 16-bit WAV."""
    peak = np.max(np.abs(samples))
    if peak > 0.95:
        samples = samples * (0.95 / peak)
    pcm = (samples * 32767).clip(-32768, 32767).astype(np.int16)
    path = OUT_DIR / filename
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm.tobytes())
    ms = len(pcm) / SAMPLE_RATE * 1000
    return ms


# ── Phrase builder ──────────────────────────────────────────

def build_phrase(note_indices, gap=0.027, volume=0.25, brightness=0.4,
                 duration=0.05, decay_speed=46, accent_last=False,
                 rallentando=False):
    """Build a multi-note phrase from note indices (0-4 = C-G).

    Args:
        note_indices: list of ints, 0=C, 1=D, 2=E, 3=F, 4=G
        gap: silence between notes (seconds)
        volume: base volume
        brightness: harmonic content
        duration: note duration
        accent_last: make last note slightly louder/brighter
        rallentando: gradually slow down (increase gaps)
    """
    parts = []
    n_notes = len(note_indices)

    for i, idx in enumerate(note_indices):
        freq = NOTE_FREQS[idx]
        v = volume
        b = brightness
        d = duration
        g = gap

        # Accent last note
        if accent_last and i == n_notes - 1:
            v *= 1.3
            b = min(b * 1.3, 0.8)
            d *= 1.2

        # Rallentando: gaps get wider
        if rallentando and i > 0:
            g = gap * (1 + 0.3 * i)

        if i > 0:
            parts.append(silence(g))

        parts.append(clave_strike(
            freq=freq, brightness=b, volume=v,
            duration=d, decay_speed=decay_speed))

    return np.concatenate(parts)


# ── Generation ──────────────────────────────────────────────

def generate_all(seed=0):
    rng = np.random.RandomState(seed)
    # Seed numpy's global RNG too (for click noise in clave_strike)
    np.random.seed(seed)

    counts = {"1-note": 0, "2-note": 0, "3-note": 0, "4-note": 0, "5-note": 0}

    # ── 1-NOTE: single tinks ────────────────────────────────
    print("  1-note (single tinks):")

    # One clean strike per note, 3 variations each (bright/warm/soft)
    styles = [
        ("bright", dict(brightness=0.6, volume=0.30, duration=0.045, decay_speed=48)),
        ("warm",   dict(brightness=0.3, volume=0.28, duration=0.06, decay_speed=38)),
        ("soft",   dict(brightness=0.2, volume=0.15, duration=0.05, decay_speed=50)),
    ]
    for ni, name in enumerate(NOTE_NAMES):
        for style_name, params in styles:
            fname = f"n1_{name}_{style_name}.wav"
            ms = save_wav(fname, clave_strike(freq=NOTE_FREQS[ni], **params))
            counts["1-note"] += 1

    print(f"    {counts['1-note']} sounds")

    # ── 2-NOTE: pairs ───────────────────────────────────────
    print("  2-note (pairs):")

    # All ascending pairs
    for i, j in combinations(range(5), 2):
        for gap in [0.022, 0.04]:
            tag = "tight" if gap == 0.022 else "loose"
            fname = f"n2_up_{NOTE_NAMES[i]}{NOTE_NAMES[j]}_{tag}.wav"
            ms = save_wav(fname, build_phrase([i, j], gap=gap, volume=0.25))
            counts["2-note"] += 1

    # All descending pairs
    for i, j in combinations(range(5), 2):
        fname = f"n2_dn_{NOTE_NAMES[j]}{NOTE_NAMES[i]}_tight.wav"
        ms = save_wav(fname, build_phrase([j, i], gap=0.022, volume=0.22))
        counts["2-note"] += 1

    # Same-note double taps (like a knock)
    for ni, name in enumerate(NOTE_NAMES):
        fname = f"n2_tap_{name}{name}.wav"
        ms = save_wav(fname, build_phrase([ni, ni], gap=0.018, volume=0.20,
                                          brightness=0.3))
        counts["2-note"] += 1

    print(f"    {counts['2-note']} sounds")

    # ── 3-NOTE: triplets ────────────────────────────────────
    print("  3-note (triplets):")

    # Ascending runs (C-D-E, D-E-F, E-F-G, C-E-G, etc.)
    three_asc = [[0,1,2], [1,2,3], [2,3,4], [0,2,4], [0,1,4], [0,3,4]]
    for seq in three_asc:
        names = "".join(NOTE_NAMES[i] for i in seq)
        fname = f"n3_up_{names}.wav"
        ms = save_wav(fname, build_phrase(seq, gap=0.027, volume=0.24,
                                          accent_last=True))
        counts["3-note"] += 1

    # Descending runs
    three_desc = [[2,1,0], [3,2,1], [4,3,2], [4,2,0], [4,1,0]]
    for seq in three_desc:
        names = "".join(NOTE_NAMES[i] for i in seq)
        fname = f"n3_dn_{names}.wav"
        ms = save_wav(fname, build_phrase(seq, gap=0.027, volume=0.22))
        counts["3-note"] += 1

    # Arch shapes (up-down, down-up)
    three_arch = [[0,2,1], [0,4,2], [1,3,2], [2,4,3], [4,2,3], [3,0,2], [2,0,1]]
    for seq in three_arch:
        names = "".join(NOTE_NAMES[i] for i in seq)
        fname = f"n3_arc_{names}.wav"
        ms = save_wav(fname, build_phrase(seq, gap=0.03, volume=0.22,
                                          brightness=0.35))
        counts["3-note"] += 1

    # Rhythmic (same note, musical rhythm)
    for ni in [0, 2, 4]:
        fname = f"n3_rhy_{NOTE_NAMES[ni]}.wav"
        parts = [
            clave_strike(freq=NOTE_FREQS[ni], brightness=0.3, volume=0.20, duration=0.04),
            silence(0.025),
            clave_strike(freq=NOTE_FREQS[ni], brightness=0.3, volume=0.18, duration=0.04),
            silence(0.04),
            clave_strike(freq=NOTE_FREQS[ni], brightness=0.35, volume=0.22, duration=0.045),
        ]
        ms = save_wav(fname, np.concatenate(parts))
        counts["3-note"] += 1

    print(f"    {counts['3-note']} sounds")

    # ── 4-NOTE: idle chatter ────────────────────────────────
    print("  4-note (idle chatter):")

    # Musical 4-note phrases — select a variety
    four_seqs = [
        [0,1,2,3], [1,2,3,4], [0,2,3,4],  # runs
        [0,2,4,2], [4,2,0,2], [0,4,2,3],  # arches
        [2,0,1,3], [4,3,1,0], [1,0,2,4],  # mixed
        [0,1,0,2], [2,3,2,4], [4,3,4,2],  # oscillating
        [0,2,1,3], [1,3,2,4], [3,1,0,2],  # zigzag
    ]

    for seq in four_seqs:
        names = "".join(NOTE_NAMES[i] for i in seq)
        # Vary the character
        gap = rng.uniform(0.04, 0.09)
        vol = rng.uniform(0.08, 0.14)
        bright = rng.uniform(0.2, 0.4)
        fname = f"n4_{names}.wav"
        ms = save_wav(fname, build_phrase(seq, gap=gap, volume=vol,
                                          brightness=bright, rallentando=True))
        counts["4-note"] += 1

    print(f"    {counts['4-note']} sounds")

    # ── 5-NOTE: idle flourishes ─────────────────────────────
    print("  5-note (idle flourishes):")

    five_seqs = [
        [0,1,2,3,4],  # straight up
        [4,3,2,1,0],  # straight down
        [0,2,4,3,1],  # up-and-back
        [2,0,1,3,4],  # dip then rise
        [4,2,0,1,3],  # fall then climb
        [0,4,2,3,1],  # leap and wander
        [1,3,4,2,0],  # rise and settle
        [0,1,3,4,2],  # skip up, land middle
        [4,3,1,0,2],  # descend then lift
        [2,4,3,1,0],  # peak then cascade
    ]

    for seq in five_seqs:
        names = "".join(NOTE_NAMES[i] for i in seq)
        gap = rng.uniform(0.05, 0.10)
        vol = rng.uniform(0.07, 0.12)
        bright = rng.uniform(0.25, 0.4)
        decay = rng.uniform(24, 34)
        fname = f"n5_{names}.wav"
        ms = save_wav(fname, build_phrase(seq, gap=gap, volume=vol,
                                          brightness=bright, decay_speed=decay,
                                          rallentando=True))
        counts["5-note"] += 1

    print(f"    {counts['5-note']} sounds")

    # ── NAMED PRESETS (main reactions) ──────────────────────
    print("\n  Named presets (main reactions):")

    # OPEN — C up to G (sacred fifth, attentiveness ON)
    save_wav("open.wav", np.concatenate([
        clave_strike(freq=NOTES["C"], brightness=0.48, volume=0.31, duration=0.05, decay_speed=48),
        silence(0.025),
        clave_strike(freq=NOTES["G"], brightness=0.58, volume=0.36, duration=0.055, decay_speed=45),
    ]))

    # CLOSE — G down to C (sacred fifth, attentiveness OFF)
    save_wav("close.wav", np.concatenate([
        clave_strike(freq=NOTES["G"], brightness=0.42, volume=0.26, duration=0.05, decay_speed=48),
        silence(0.028),
        clave_strike(freq=NOTES["C"], brightness=0.28, volume=0.19, duration=0.06, decay_speed=36),
    ]))

    # THINKING — F-F-F rhythmic (D was too negative, F is brighter)
    save_wav("thinking.wav", np.concatenate([
        clave_strike(freq=NOTES["F"], brightness=0.32, volume=0.21, duration=0.04, decay_speed=48),
        silence(0.03),
        clave_strike(freq=NOTES["F"], brightness=0.3, volume=0.19, duration=0.04, decay_speed=48),
        silence(0.03),
        clave_strike(freq=NOTES["F"], brightness=0.36, volume=0.23, duration=0.045, decay_speed=44),
    ]))

    # WAKE — single bright G (catches attention)
    save_wav("wake.wav", clave_strike(freq=NOTES["G"], brightness=0.55,
             volume=0.32, duration=0.05, decay_speed=46))

    # LISTENING — D up to F (inner notes, receptive)
    save_wav("listening.wav", build_phrase([1, 3], gap=0.025, volume=0.27,
             brightness=0.45))

    # RESPONDING — warm D (grounded, about to speak)
    save_wav("responding.wav", clave_strike(freq=NOTES["D"], brightness=0.3,
             volume=0.30, duration=0.06, decay_speed=38))

    # STOPPING — F down to D (inner notes, settling)
    save_wav("stopping.wav", build_phrase([3, 1], gap=0.03, volume=0.20,
             brightness=0.3))

    # GREETING — D-E-F (ascending inner notes, warm hello)
    save_wav("greeting.wav", build_phrase([1, 2, 3], gap=0.027, volume=0.28,
             brightness=0.45, accent_last=True))

    # GOODBYE — F-E-D (descending inner notes, gentle)
    save_wav("goodbye.wav", build_phrase([3, 2, 1], gap=0.03, volume=0.22,
             brightness=0.3, rallentando=True))

    # ERROR — low C4+D4 flam (dissonant minor second, 8ms grace note delay)
    err_c = clave_strike(freq=NOTES["C"] / 2, brightness=0.25, volume=0.22,
                         duration=0.07, decay_speed=30)
    err_d = clave_strike(freq=NOTES["D"] / 2, brightness=0.2, volume=0.18,
                         duration=0.07, decay_speed=32)
    flam_delay = int(SAMPLE_RATE * 0.008)
    err_mix = np.zeros(max(len(err_c), len(err_d) + flam_delay))
    err_mix[:len(err_c)] += err_c
    err_mix[flam_delay:flam_delay + len(err_d)] += err_d
    save_wav("error.wav", err_mix)

    # READY — E-F ("your turn" — plays after Merlin speaks, before listening resumes)
    save_wav("ready.wav", np.concatenate([
        clave_strike(freq=NOTES["E"], brightness=0.35, volume=0.22, duration=0.045, decay_speed=48),
        silence(0.025),
        clave_strike(freq=NOTES["F"], brightness=0.38, volume=0.24, duration=0.05, decay_speed=46),
    ]))

    # STARTUP — C-D-E-F-G ascending (full scale, waking up — only time all 5 play in named set)
    save_wav("startup.wav", build_phrase([0,1,2,3,4], gap=0.027, volume=0.22,
             brightness=0.4, accent_last=True))

    total = counts["1-note"] + counts["2-note"] + counts["3-note"] + counts["4-note"] + counts["5-note"]

    print(f"""
Summary:
  1-note:  {counts['1-note']:3d} sounds (tinks)
  2-note:  {counts['2-note']:3d} sounds (pairs)
  3-note:  {counts['3-note']:3d} sounds (triplets)
  4-note:  {counts['4-note']:3d} sounds (idle chatter)
  5-note:  {counts['5-note']:3d} sounds (idle flourishes)
  named:     9 sounds (main reactions)
  ─────────────────
  total:   {total + 9:3d} sounds

  All from one source: wood clave, 5 notes (C D E F G).
  Run with --seed N for different variations.
""")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    print(f"=== Merlin Sound Palette: Wood Clave (seed={args.seed}) ===\n")
    generate_all(seed=args.seed)
