# Merlin Sound Design

*One source. Five notes. One character.*

---

## Fiction

Merlin is a small, warm, wooden creature on Ezra's desk. His sounds come from his body — the camera speaker. Never from Mac speakers, never from Bluetooth. If sound doesn't come from where his eyes are, the fiction breaks.

## Source

All sounds derive from one instrument: **wood clave**. A short, dry, percussive wooden strike with inharmonic overtones and fast exponential decay. No reverb. No sustain. No metal. Every sound Merlin makes shares this timbral DNA.

This follows the Gabaldon principle (Anki Vector): one organic source, processed many ways, creates the sense of one character better than many sources ever could.

## The Five Notes

```
C5 (523 Hz) — grounded, warm, home
D5 (587 Hz) — neutral, thinking, processing
E5 (659 Hz) — middle, acknowledgment
F5 (698 Hz) — transitional, curious
G5 (784 Hz) — bright, alert, attentive
```

A perfect fifth spans C to G. This interval is sacred.

## The Sacred Fifth

**C → G (ascending fifth) = Merlin opens his ears.**
**G → C (descending fifth) = Merlin closes his ears.**

These bookend every conversation. You only hear the fifth when Merlin's attentiveness changes state. Everything inside a conversation uses D, E, F — the notes between.

The fifth is the symbol of his presence. It is not used for anything else.

## Timing

Notes are fast. Merlin is small and quick.

| Parameter | Value | Feel |
|-----------|-------|------|
| Note duration | 40–55ms | Percussive tap |
| Gap between notes | 25–30ms | Second note enters as first decays to ~9% |
| Decay speed | 44–48 | Fast exponential, wooden |
| Full open/close | ~130ms total | One quick gesture, not two events |

The gap is tuned so the second note attacks at the tail of the first note's decay. This creates a seamless handoff — one gesture, not two separate taps.

## Sound Categories

Sounds are organized by note count. Lower counts = more important moments. Higher counts = ambient life.

| Notes | Use | Volume | Character |
|-------|-----|--------|-----------|
| 1 | Wake, responding, single acknowledgments | Normal | Punctuation |
| 2 | Open (C-G), close (G-C), ready (E-F), stopping | Normal | Bookends and transitions |
| 3 | Thinking (F-F-F), greeting, goodbye, expressive reactions | Normal | Phrases |
| 4 | Idle chatter | Quiet | Little musical fidgets |
| 5 | Idle flourishes | Very quiet | Tiny melodies, alive on desk |

## Locked Conversation Flow

```
"Hey Merlin"     → C-G (open)
  You speak      → F-F-F (thinking) → Merlin speaks → E-F (ready, "your turn")
  You speak      → F-F-F (thinking) → Merlin speaks → E-F (ready)
  ...
"Back to work"   → G-C (close)
```

## Variation (Vector Principle)

Repetition kills the magic. At runtime, Merlin selects from a **semi-random pool** of sound alternatives, weighted by current state and stimulation level. The generator produces combinatorial variations from the five notes — hundreds of sounds, all from the same family.

For any given sound event (e.g., "idle chirp"), there should be 10+ variations. The same sound should never play twice in a row.

## Volume Hierarchy

| Context | Level |
|---------|-------|
| Open/close (fifth) | Full — this is a state change |
| Thinking, reactions | Normal — conversation punctuation |
| Idle chatter | Quiet — background aliveness |
| Idle flourishes | Very quiet — almost subliminal |

## Clave Parameters

The `clave_strike()` function is the atom. All sounds are built from it.

```
freq:         Note frequency (523–784 Hz for C5–G5)
brightness:   0–1, upper harmonic content (0.25–0.55 typical)
volume:       Amplitude (0.05–0.37 typical)
duration:     Note length in seconds (0.035–0.075 typical)
decay_speed:  Exponential decay rate (32–55 typical, higher = shorter)
click_amount: Transient attack intensity (0.2–0.4 typical)
```

Harmonic structure uses inharmonic partials at ratios 1.0, 2.7, 5.4, 8.1 — real wood, not tuned metal. Each partial decays faster than the one below it.

## Generation

```bash
# Default palette (seed 0)
python3 generate_clave.py

# Different variations (same family, different phrases)
python3 generate_clave.py --seed 42
python3 generate_clave.py --seed 99
```

Each seed produces a different set of 4- and 5-note idle phrases while keeping the named presets (open, close, thinking, etc.) identical.

## Conversation State → Sound

```
IDLE
  └→ wake word detected → play OPEN (C-G) → CONVERSATION

CONVERSATION
  ├→ speech detected → [thinking sound] → process → respond
  ├→ dismissal phrase → play CLOSE (G-C) → IDLE
  ├→ hush phrase → play CLOSE (G-C) → HUSHED
  └→ silence timeout → play CLOSE (G-C) → IDLE

HUSHED
  ├→ wake word → play OPEN (C-G) → CONVERSATION
  └→ hush timeout → IDLE (silent)
```

## Design Principles (from Vector research)

1. **Fiction first.** Define the character. All sound flows from who the robot is.
2. **One source.** Shared timbral DNA creates one character.
3. **Sound from the body.** External playback breaks the illusion.
4. **Variation over perfection.** 10+ variations per event. Repetition kills the magic.
5. **Pre-recorded beats procedural.** Authored variations with semi-random selection sound more alive than real-time synthesis.
6. **Stimulation dial.** Active Merlin = chirpier. Idle Merlin = quieter.
7. **Test emotional clarity.** What's obvious to the designer isn't obvious to others.
8. **Frequency reality.** Small speakers live in 200–2000 Hz. Design there.

---

*"Tiny, no reverb, wooden, not harsh or metallic. All from the same family, like a voice."*
*— Ezra, April 7, 2026*
