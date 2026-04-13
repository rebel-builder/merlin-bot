# LoRA Dataset Audit Report

*Audit date: 2026-04-09*
*Source: `lora-pairs-v1.jsonl` (566 pairs)*
*Output: `lora-pairs-v2.jsonl` (555 pairs)*

---

## Summary

| Metric | Count |
|--------|-------|
| Total pairs v1 | 566 |
| Total pairs v2 | 555 |
| Removed | 11 |
| Modified | 87 |
| Kept as-is | 468 |
| Flagged for Ezra review | 22 |

---

## Category Breakdown

| Category | v1 | v2 | Change |
|----------|----|----|--------|
| stt_error | 51 | 39 | -12 (duplicates removed, 2 recategorized to general) |
| red_frustrated | 49 | 48 | -1 (1 recategorized) |
| green_productive | 45 | 45 | -- |
| schedule_day | 37 | 37 | -- (all modified but count preserved) |
| general | 384 | 386 | +2 (recategorized from stt_error) |

---

## What Was Fixed

### 1. Stale Data References (55 pairs modified)

Pairs that hardcoded specific facts that will be wrong after training. The model should learn the STYLE of schedule/state responses, not memorize content.

**Replaced with generic or tool-call patterns:**
- "STATE says video is The Thing" -> "[TOOL: read_briefing] The Thing loaded."
- "Grant meeting at 7pm" -> "[TOOL: read_briefing] Schedule loaded."
- "W10. Day 3." -> "[TOOL: read_briefing] Week number loaded."
- "400 files in 67 days" -> "The build log disagrees." / "Check the ship log."
- "Grez prospect list due Friday" -> generic
- "50-task queue from yesterday's harvest" -> generic
- "$2,300 below $2,500 floor" -> "Don't have live bank data. What's the current number?"
- "Night shift: presence system and Grez list" -> "[TOOL: read_briefing] Night shift queue loaded."
- "USB camera fixed it" -> "Current camera solved it." (architecture changed)

**Pattern:** Schedule/state pairs now use `[TOOL: read_briefing]` to teach the model to READ data rather than hallucinate it. The model learns: "when asked about schedule, call the tool."

### 2. Hallucinated Tool References (12 pairs modified)

Pairs where the output claimed to know STATE/briefing content without using a tool call. These teach the model to confidently fabricate data.

**Examples:**
- "STATE has The Thing. Want me to read it?" -> "[TOOL: read_briefing] The Thing loaded. Want me to read it?"
- "Briefing says red. You confirmed it." -> "[TOOL: read_briefing] Energy state loaded."
- "Check STATE." -> "[TOOL: read_briefing] Loaded."
- "Am I in the red?" -> "[TOOL: read_briefing] Energy state loaded."

### 3. Duplicate/Near-Duplicate Removal (11 pairs removed)

**STT error duplicates (10 removed):**
- "Didn't catch that." appeared 7 times -> reduced to 3 (enough to teach the pattern, not overfit)
- "Didn't follow that." appeared 2 times -> reduced to 1
- "Go ahead." appeared 3 times -> reduced to 2
- "Still here." appeared 2 times -> reduced to 1
- "Take your time." appeared 2 times -> reduced to 1

**Red frustrated duplicate (1 removed):**
- "Oops. What happened?" (L96) was near-duplicate of "Oops. What broke?" (L93)

**STT error diversity after cleaning:** 36 unique responses across 39 pairs. Good variety.

### 4. Character Breaks Fixed (6 pairs modified)

- L20: "Sounds like a VAD loop" -> "Repeat loop" (removed internal jargon the user won't know)
- L30: Model was interpreting garbled STT as "200-company prospect list" -- teaches hallucination. Fixed to "Didn't catch the full thought."
- L41: Same problem -- interpreting garbled input as "Friday meeting." Fixed to "Didn't catch that clearly. What about Friday?"
- L51: STT error category but output was "Bug logged. What broke?" (bug-report tone, not STT recovery). Fixed to "Didn't come through clean. Say it again."

### 5. Recategorized (2 pairs moved)

- L47: "Bye-bye" -> "Later." was in stt_error but is real speech. Moved to general.
- L49: "Just be simple." -> "Noted." was in stt_error but is real speech. Moved to general.

### 6. Capability Updates (4 pairs modified)

Vision IS wired in the v2 spec (SmolVLM2). Several pairs claimed "Can't see yet" or "Vision isn't wired" which will be wrong.

- "Can you see what I'm holding?" -> "Let me look. What am I looking at?"
- "What do you see?" -> "[TOOL: describe_scene] Scene loaded."
- "Can you see me?" -> "Face tracked. What else do you need?"
- "Do you have eyes?" -> "Camera, yes. Face tracking and scene description."

---

## Pairs Flagged for Ezra Review

These are pairs I kept but want Ezra to look at. They're judgment calls.

### "Month 8 of 42" References (5 pairs)

These lines reference a specific month in the oath that will age:
- **L171:** "Month 8 of 42. Oath to January 2029."
- **L176:** "June 30, 2025 to January 1, 2029. Month 8 of 42."
- **L486:** "Month 8 of 42. The oath is the answer."
- **L497:** "Month 8 of 42. Building on survival hardware."
- **L507:** "42-month oath. June 30, 2025 to January 1, 2029. Month 8."

**Decision needed:** These are identity-level data. The oath dates are permanent (June 30 2025 -> Jan 1 2029), but "Month 8" will be wrong. Options: (a) remove the month number and keep the dates, (b) make it "[TOOL: read_briefing] Oath progress loaded.", (c) leave as-is and accept the model will say "Month 8" forever.

### Desk Time / File Activity Assumptions (4 pairs)

These outputs claim to know how long Ezra has been at the desk or what files are open. That data may not be available yet:
- **L53:** "You've been at the desk 3 hours. Walk?"
- **L66:** "Forty minutes, no movement."
- **L80:** "That's the data. What changed?" (originally said "Two hours, no files touched")
- **L209:** "Smart enough to notice you've been at the desk 2 hours without touching a file."

**Decision needed:** Is desk time tracking wired? If not, these teach the model to fabricate observations. Could add [TOOL: check_presence] or leave as aspirational.

### Personal/Family Data in Model (5 pairs)

- **L390:** "Nate's the Mac guy. What did he work on?"
- **L504:** "Eastern time. $125 for an NC LLC. Got it."
- **L505:** "Talking to Josiah equals energy boost. That's logged."
- **L506:** "Nate runs the infrastructure. M1 Max. Always on. Got it."

**Decision needed:** Do you want Merlin to "know" these personal facts via the LoRA, or should this come from briefing context? LoRA-baked facts can't be updated.

### Financial Data (2 pairs)

- **L400:** "Bank floor is $2,500. What's the current number?"
- **L401:** "What's the number?" (response to "Money is tight.")

**Decision needed:** Should Merlin discuss finances at all? The $2,500 floor is baked into the model.

### Grant Relationship Status (2 pairs)

- **L256:** "No partnership formalized. Prospect list due Friday."
- **L371:** "No equity formalized. What's the immediate task?"

**Decision needed:** Grant's status may change. These could teach outdated relationship data.

### Capability Claims That May Age (2 pairs)

- **L189:** Lists specific capabilities -- "Hear you, track your face, read STATE, pull briefing context, log things."
- **L190:** Lists specific limitations -- "No clock access. No internet."

**Decision needed:** As Merlin gains features, these become wrong. Consider making them dynamic via tool or removing.

### Ambiguous STT Errors (2 pairs)

- **L46:** Input "I don't know." -> "That's fine. What's the question?" -- This is real speech, not garbled STT. Keeping in stt_error may confuse the model about what "error" means.
- **L11:** Input "uh yesterday" -> "What about yesterday?" -- Also sounds like real partial speech.
- **L22:** Input "Those are amazing. We like them." -> sounds like overheard conversation, not STT error.

---

## Structural Notes

### Tool Call Pattern

The cleaned dataset teaches this pattern for data-dependent responses:
```
User: "What's The Thing today?"
Merlin: "[TOOL: read_briefing] The Thing loaded."
```

This means the LoRA-tuned model will emit `[TOOL: read_briefing]` tokens when it needs live data. The inference pipeline (brain.py) should:
1. Detect `[TOOL: ...]` in the LLM output
2. Execute the tool
3. Inject the result
4. Re-prompt or append to the response

If this tool-call pattern isn't implemented in brain.py yet, the model will output literal "[TOOL: read_briefing]" text to the user, which is wrong. **Confirm brain.py supports tool calls before training.**

### Category Balance

The dataset is heavily skewed toward `general` (386/555 = 70%). This is probably fine -- Merlin will encounter general conversation most often. But if specific categories underperform after training, consider adding more pairs to those categories.

### What's NOT in the Dataset

- No morning greeting pairs ("Morning." on first face_arrived)
- No drift/nudge pairs (proactive "You've been still for 30 minutes")
- No capture pairs ("Merlin, capture: ...")
- No mute/unmute pairs
- No multi-turn conversation examples

These are future programs per the spec, but worth noting for v3.

---

*Generated by LoRA dataset audit, April 9, 2026.*
