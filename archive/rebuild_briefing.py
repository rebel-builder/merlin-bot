#!/usr/bin/env python3
"""
merlin/rebuild_briefing.py
Nightly briefing regenerator for Merlin.

Reads core/STATE.md, extracts key fields, and writes merlin/briefing.md.
Run via night shift cron or manually: python3 merlin/rebuild_briefing.py

Output: merlin/briefing.md (50 lines max, Merlin voice format)
"""

import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────

RBOS = Path(__file__).parent.parent
STATE_FILE = RBOS / "core" / "STATE.md"
BRIEFING_FILE = RBOS / "merlin" / "briefing.md"

# ── Helpers ───────────────────────────────────────────────────────────────────

def read_state() -> str:
    if not STATE_FILE.exists():
        sys.exit(f"[rebuild_briefing] ERROR: {STATE_FILE} not found")
    return STATE_FILE.read_text(encoding="utf-8")


def extract(pattern: str, text: str, default: str = "unknown") -> str:
    """Return first capture group from pattern, or default."""
    m = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
    return m.group(1).strip() if m else default


def extract_big_three(text: str) -> list[str]:
    """Pull Big Three lines from STATE.md."""
    m = re.search(r"\*\*Big Three:\*\*(.*?)(?:\n---|\n##)", text, re.DOTALL)
    if not m:
        return []
    block = m.group(1)
    items = re.findall(r"^\d+\.\s+(.+)$", block, re.MULTILINE)
    return items[:3]


def extract_last_active(text: str) -> str:
    m = re.search(r"\*\*Last Active:\s*([^\*]+)\*\*", text)
    return m.group(1).strip() if m else "unknown"


def extract_energy(text: str) -> str:
    m = re.search(r"\*\*Energy:\*\*\s*(.+)", text)
    return m.group(1).strip() if m else "unknown"


def extract_the_thing(text: str) -> str:
    m = re.search(r"\*\*The Thing:\*\*\s*(.+)", text)
    return m.group(1).strip() if m else "TBD at desk"


def extract_oath(text: str) -> str:
    m = re.search(r"Month\s+(\d+)\s+of\s+42", text)
    if m:
        return f"Month {m.group(1)} of 42"
    return "Month ? of 42"


def extract_sprint_week(text: str) -> str:
    m = re.search(r"Sprint\s+(W\d+)", text)
    return m.group(1) if m else "W?"


def extract_section(heading: str, text: str) -> str:
    """Extract content under a markdown heading."""
    pattern = rf"## {re.escape(heading)}\n(.*?)(?=\n## |\Z)"
    m = re.search(pattern, text, re.DOTALL)
    return m.group(1).strip() if m else ""


def count_lines(s: str) -> int:
    return len(s.splitlines())


# ── Builder ───────────────────────────────────────────────────────────────────

def build_briefing(state: str) -> str:
    today = datetime.now()
    day_name = today.strftime("%A")
    date_str = today.strftime("%Y-%m-%d")

    # Sprint day (W10 started Apr 6; adjust as needed by reading sprint header)
    sprint_week = extract_sprint_week(state)
    # Attempt to infer sprint day from sprint start date in STATE.md
    sprint_day = "?"
    m = re.search(r"Apr\s+(\d+)\s+[–\-]\s+Apr\s+\d+", state)
    if m:
        sprint_start_day = int(m.group(1))
        sprint_start = today.replace(month=4, day=sprint_start_day)
        delta = (today.date() - sprint_start.date()).days + 1
        if 1 <= delta <= 7:
            sprint_day = str(delta)

    energy = extract_energy(state)
    the_thing = extract_the_thing(state)
    oath = extract_oath(state)
    big_three = extract_big_three(state)
    last_active = extract_last_active(state)

    # Big Three formatted lines
    big_three_lines = []
    for i, item in enumerate(big_three, 1):
        big_three_lines.append(f"{i}. {item}")

    big_three_block = "\n".join(big_three_lines) if big_three_lines else "(see STATE.md)"

    # Open loops — pull from STATE.md Night shift or RBOS Upgrades sections
    # Keep it simple: surface a few known open loops from the state text
    open_loops = []

    # Scan for common open loop markers
    loop_patterns = [
        (r"(Grez prospect list.*?)(?:\n|$)", "Grez"),
        (r"(GGUF conversion.*?blocked.*?)(?:\n|$)", "GGUF"),
        (r"(false wake.*?)(?:\n|$)", "Wake word"),
        (r"(Income must flow by.*?)(?:\n|$)", "Finance"),
        (r"(sprint review.*?Sunday.*?)(?:\n|$)", "Sprint review"),
    ]
    for pattern, label in loop_patterns:
        m = re.search(pattern, state, re.IGNORECASE)
        if m:
            snippet = m.group(1).strip()[:80]
            open_loops.append(f"- {label}: {snippet}")

    open_loops_block = "\n".join(open_loops[:5]) if open_loops else "- (check STATE.md)"

    briefing = f"""date: {date_str}
day: {day_name}
sprint: {sprint_week} Day {sprint_day}
energy: {energy}
oath: {oath}

---

## Big Three — Status

{big_three_block}

---

## Last Session

Last active: {last_active}
The Thing was: {the_thing}

---

## Suggested Thing ({day_name})

Check queue/NIGHT_SHIFT_QUEUE.yaml for overnight research results.
Then: confirm energy state before choosing.

Build-in-public momentum: daily video > any single technical task.
Technical unlock: check GGUF night shift research before assuming blocked.

---

## Open Loops

{open_loops_block}

---

Ask Ezra: energy state? What's pulling at you this morning?

*Updated by rebuild_briefing.py — {date_str}*
"""

    return briefing.strip()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"[rebuild_briefing] Reading {STATE_FILE}")
    state = read_state()

    print("[rebuild_briefing] Building briefing...")
    briefing = build_briefing(state)

    line_count = count_lines(briefing)
    print(f"[rebuild_briefing] Generated {line_count} lines")

    if line_count > 55:
        print(f"[rebuild_briefing] WARNING: {line_count} lines exceeds 50-line target")

    BRIEFING_FILE.write_text(briefing + "\n", encoding="utf-8")
    print(f"[rebuild_briefing] Wrote {BRIEFING_FILE}")


if __name__ == "__main__":
    main()
