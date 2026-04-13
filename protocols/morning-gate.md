# Morning Gate Protocol

*Designed April 9, 2026. Future module: `merlin/morning.py`.*

---

## The Core Idea

Not a routine. Not a sequence. Not a schedule.

**Three things done by 9am.** That's it.

- Medicated
- Eaten something
- Groomed

The morning is chaotic and non-linear by design. Some days meds at 7:45. Some days 8:30. Some days food first. Some days shower first. Doesn't matter. What matters is that by 9am, those three gates are closed.

Merlin's job is not to orchestrate the morning. Merlin watches the clock and the gates. When gates are running late, he says something. Like a roommate, not a drill sergeant.

---

## The Three Gates

| Gate | ID | Confirmed By | Auto-Detect? |
|------|----|-------------|--------------|
| Medicated | `gate_meds` | Voice ("took my meds", "medicated") | No — voice confirm only |
| Eaten something | `gate_food` | Voice ("ate", "had breakfast", "just ate something") | No — voice confirm only |
| Groomed | `gate_groomed` | Voice ("showered", "ready", "I'm up") | Partial — face detection change |

**Design principle:** Don't require perfect phrases. The LLM interprets intent. "I'm good" after a checkin counts. "Already did it" counts. The gate just needs to close.

---

## The Window: 7am – 9am

| Time | Phase | State |
|------|-------|-------|
| Before 7am | Sleep window | No gate logic. Silent. |
| 7:00am | Window opens | Gates initialize as open (incomplete). |
| 7:00–7:30am | Early window | Presence-only. No prompting. Let him move. |
| 7:30am | First check | Gentle — only if Merlin sees Ezra (face/person detected) |
| 8:15am | Mid check | Firmer — any open gates get named |
| 8:45am | Urgent | All open gates named. One shot. |
| 9:00am | Gate closes | Log result. Transition to day mode. Done. |

---

## Escalation Tiers

### Tier 1 — 7:30am (Gentle)
Trigger only if Ezra is at desk AND at least one gate is open.

One gate open: say nothing. He's moving, let him move.
Two gates open: one soft prompt.
All three open: one soft prompt.

Examples (King Rhoam voice — under 30 words, direct, not warm):
- "Morning. Meds and food still open."
- "Still early. Meds when you're ready."
- "Two gates left. No rush yet."

If Ezra confirms a gate during or after: acknowledge once, close it.

### Tier 2 — 8:15am (Firmer)
Trigger if Ezra is at desk AND 1+ gates open.

Name the specific open gates. Don't soften it, but don't repeat more than once.

Examples:
- "8:15. Meds still open."
- "Food and meds. You've got 45 minutes."
- "Still need to eat. And meds."

Wait for confirmation. If he confirms, close the gate, say nothing further.

### Tier 3 — 8:45am (Urgent)
Trigger regardless of desk presence. This is the last call.

If Ezra is at desk: speak directly.
If Ezra is absent: use a push notification (future) or log it. Don't shout into an empty room.

Examples:
- "8:45. Meds. Now."
- "Fifteen minutes. Meds and food. Go."
- "Last call. What's still open?"

No lecture. One utterance. Then wait.

### 9:00am — Gate Closes
Log the state of all three gates. No follow-up prompts. The morning window is over.

Merlin does not guilt-trip after 9am. What's done is done. Move forward.

---

## Voice Confirmation Parsing

Merlin listens for natural language confirmation during the morning window. The brain.py LLM handles intent recognition — no keyword list needed.

Phrases that close `gate_meds`:
- "Took my meds" / "just took them" / "medicated" / "already did meds"

Phrases that close `gate_food`:
- "Just ate" / "had something" / "breakfast done" / "I ate" / "had coffee and a bar"

Phrases that close `gate_groomed`:
- "Showered" / "ready" / "got dressed" / "I'm up" / "groomed"

Merlin can also ask directly: "Meds done?" → "yeah" closes the gate.

**One confirmation per gate is enough.** Merlin doesn't re-ask a closed gate.

---

## State Tracking

Morning state lives in `/tmp/merlin-morning.json`, reset at midnight.

```json
{
  "date": "2026-04-09",
  "window_open": true,
  "gates": {
    "meds": {"closed": false, "closed_at": null},
    "food": {"closed": false, "closed_at": null},
    "groomed": {"closed": false, "closed_at": null}
  },
  "checks": {
    "tier1_fired": false,
    "tier2_fired": false,
    "tier3_fired": false
  },
  "result": null
}
```

At 9:00am, `result` is written:
- `"clean"` — all three closed before 9am
- `"partial"` — 1-2 closed
- `"missed"` — none closed

`result` is logged to `merlin/logs/morning-gates.csv` for weekly pattern review (future).

---

## Integration with Brain Server

Morning gate runs as a scheduled background loop inside `morning.py`. It does NOT go through the LLM for gate logic — gate timing and escalation are deterministic. The LLM is only used when speaking.

### Event Flow

```
morning.py (background timer loop)
    │
    ├── 7:30am → check gates → if needed: emit speak(tier1_line)
    ├── 8:15am → check gates → if needed: emit speak(tier2_line)
    ├── 8:45am → check gates → emit speak(tier3_line)
    └── 9:00am → log result → emit morning_complete(result)

speech(text) event from audio_pipeline
    │
    ├── morning.py.on_voice(text) ← intercepts if window is open
    │       └── parse for gate confirmation
    │           └── if confirmed: close gate, emit speak(ack)
    │           └── if not gate-related: pass through to brain.py
    └── brain.py (normal conversation)
```

morning.py intercepts speech events first (as specified in brain.py event priority chain). Gate confirmations are consumed here. General conversation passes through.

### Ack Lines (after gate closes)

Short, King Rhoam style. One acknowledgment, then silence.

- Meds confirmed: "Good."
- Food confirmed: "Noted."
- Groomed confirmed: "Ready."
- All three closed early: "All three. You're set."

No praise. No "Great job!" He's not a dog. He's a person who did the basic things.

---

## Presence Gating

Merlin does not prompt into an empty room.

- **Tier 1 (7:30am):** Only fires if Ezra is at desk (person detected via HOG).
- **Tier 2 (8:15am):** Only fires if Ezra is at desk.
- **Tier 3 (8:45am):** Fires regardless. This is the last call. If absent: log only. If present: speak.

When Ezra arrives at desk after an absence during the morning window, Merlin does NOT replay missed tiers. He checks current gate state and time, and uses the appropriate tier for current time. No catch-up lectures.

---

## First Sight Behavior

When Merlin first sees Ezra in the morning (face_arrived after overnight absence):

Standard greeting from brain.py: "Morning."

Then — if 7:30am or later AND any gates open — **immediate gate check** instead of waiting for the next scheduled tier. Don't wait 20 minutes to tell him what's open.

Example:
- 8:10am, Ezra sits down, face detected
- Merlin: "Morning. Meds and food still open. 50 minutes."

Conversational, not alarming. State the facts. Let him respond.

---

## What This Replaces

The old ritual system failed because:
1. It required a fixed sequence that ADHD brains can't reliably run
2. It created dread — missing a step felt like failing the whole ritual
3. It was time-bound to a specific clock time, not a window
4. Any deviation broke the chain

The gate system wins because:
1. **Order doesn't matter.** Shower at 7:15 or 8:50 — same gate, same result.
2. **Partial credit is real.** Two gates closed = real progress, not failure.
3. **The window is wide.** 7–9am is 2 hours. A flexible 2-hour window beats a rigid 8:30am ritual.
4. **Merlin tracks, Ezra does.** No self-report required. Just say it when you do it.
5. **Missing the window isn't shame.** It's logged. Patterns emerge. Adjustments happen later.

---

## Edge Cases

**Ezra explicitly says he's not doing one:**
- "I'm skipping breakfast today" → close `gate_food` as `skipped` (not `closed`). Logged differently. No repeat prompt.
- "Not taking meds today" → close `gate_meds` as `skipped`. No argument. His call.

**Ezra already confirmed a gate yesterday:**
- Gates reset at midnight. Every morning starts fresh. No carry-over.

**Weekend / no-schedule day:**
- Gate window still runs (7–9am). Behavior identical.
- Future: config flag to widen window to 10am on weekends.

**Ezra mutes Merlin during morning:**
- morning.py queues any tier prompts. On unmute: if still within window and still relevant, deliver once. If past 9am, discard.

**Merlin restarts mid-morning:**
- morning.py reads `/tmp/merlin-morning.json` on startup. Resumes from saved gate state. No re-prompting for already-closed gates.

---

## Build Notes (for morning.py)

This module plugs into the existing event bus. No changes to audio_pipeline, voice, or brain are required beyond the event priority chain already specified in `merlin-v2-spec.md` (morning.py gets voice events first).

**New events this module emits:**

```
gate_closed(gate_id, method)    — a gate was confirmed
gate_check(tier)                — a scheduled check fired
morning_complete(result)        — 9am, window closed
```

**New events this module consumes:**

```
speech(text)                    — intercepts for gate confirmation
face_arrived()                  — triggers first-sight gate summary
```

**Timer mechanism:** simple Python threading.Timer, not a cron job. morning.py sets timers at startup for 7:30, 8:15, 8:45, and 9:00am based on current time. If Merlin starts after a tier has passed, that tier is skipped — no retroactive firing.

---

## Future Additions (not building now)

- **Gate 4: Water** — "Had water?" Optional gate. Low friction.
- **Weekend window expansion** — 10am deadline on Sat/Sun via config
- **Walk gate** — "Walked?" as an afternoon gate (different protocol)
- **Morning log CSV** — weekly pattern view, shows which gates get skipped most
- **PIR sensor trigger** — motion in kitchen → assume food gate candidate, ask softly

---

*Gates, not schedules. Done by when, not done at when. That's the whole idea.*
