#!/usr/bin/env python3
"""
Merlin Passive Vision — runs on Pi 5, uses Qwen3.5:0.8b (local Ollama).

Simple presence tracking: reads the tracker's snapshot every 60s, asks the
local LLM "who/what is here?", logs the result. No Mac needed.

Primary purpose: desk time tracking throughout the day.
Produces a presence log that RBOS can read for daily summaries.

Output: /tmp/merlin-presence.jsonl — one JSON line per observation.
"""

import base64
import json
import os
import time
from datetime import datetime
from pathlib import Path

import requests

# ── Config ──────────────────────────────────────────────────

OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "qwen3.5:0.8b"
SNAPSHOT_PATH = "/tmp/merlin-snapshot.jpg"
PRESENCE_LOG = "/tmp/merlin-presence.jsonl"
SCAN_INTERVAL = 60  # seconds between observations

# Prompt for simple presence classification
PRESENCE_PROMPT = """Look at this image from a desk camera. Answer in JSON only:
{"people": 0, "description": "brief scene", "activity": "what is happening"}

Rules:
- people: count of visible people (0, 1, 2, etc.)
- description: one short sentence (under 15 words)
- activity: one of: working, talking, empty, away, sleeping, playing, eating, unknown
- JSON only, no other text."""


# ── Main ────────────────────────────────────────────────────

def observe():
    """Take one observation: read snapshot, classify, log."""
    snapshot = Path(SNAPSHOT_PATH)
    if not snapshot.exists():
        return None

    # Check freshness — skip if snapshot is older than 2 minutes
    age = time.time() - snapshot.stat().st_mtime
    if age > 120:
        return None

    try:
        img_b64 = base64.b64encode(snapshot.read_bytes()).decode()

        resp = requests.post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "messages": [
                    {"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                        {"type": "text", "text": PRESENCE_PROMPT},
                    ]},
                ],
                "stream": False,
                "think": False,
                "options": {
                    "num_predict": 60,
                    "temperature": 0.2,
                },
            },
            timeout=30,
        )

        if resp.status_code != 200:
            return None

        raw = resp.json().get("message", {}).get("content", "").strip()

        # Try to parse JSON from response
        try:
            # Handle markdown code blocks
            if "```" in raw:
                raw = raw.split("```")[1].strip()
                if raw.startswith("json"):
                    raw = raw[4:].strip()
            observation = json.loads(raw)
        except json.JSONDecodeError:
            observation = {"people": -1, "description": raw[:100], "activity": "unknown"}

        # Add timestamp
        observation["timestamp"] = datetime.now().isoformat()
        observation["snapshot_age_s"] = round(age, 1)

        # Log to JSONL
        with open(PRESENCE_LOG, "a") as f:
            f.write(json.dumps(observation) + "\n")

        return observation

    except Exception as e:
        print(f"[passive_vision] Error: {e}")
        return None


def get_desk_time_today():
    """Read today's presence log and calculate total desk time."""
    log_path = Path(PRESENCE_LOG)
    if not log_path.exists():
        return 0, []

    today = datetime.now().strftime("%Y-%m-%d")
    observations = []
    desk_minutes = 0

    for line in log_path.read_text().strip().split("\n"):
        try:
            obs = json.loads(line)
            if obs["timestamp"].startswith(today):
                observations.append(obs)
        except (json.JSONDecodeError, KeyError):
            continue

    # Count observations with people > 0 as "at desk"
    at_desk = sum(1 for o in observations if o.get("people", 0) > 0)
    desk_minutes = at_desk * (SCAN_INTERVAL / 60)

    return desk_minutes, observations


def run():
    """Main loop — observe every SCAN_INTERVAL seconds."""
    print(f"[passive_vision] Starting — scan every {SCAN_INTERVAL}s")
    print(f"[passive_vision] Model: {OLLAMA_MODEL}")
    print(f"[passive_vision] Log: {PRESENCE_LOG}")

    while True:
        obs = observe()
        if obs:
            people = obs.get("people", "?")
            activity = obs.get("activity", "?")
            desc = obs.get("description", "")
            print(f"[passive_vision] {obs['timestamp']}: {people} people, {activity} — {desc}")
        else:
            print(f"[passive_vision] {datetime.now().isoformat()}: no observation (snapshot stale or missing)")

        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    run()
