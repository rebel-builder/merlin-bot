#!/usr/bin/env python3
"""
Merlin Vision Collector — runs on Pi alongside tracker.

Captures a frame every N seconds, sends to LM Studio for state classification,
logs the result. Over time this builds a labeled dataset for fine-tuning.

Also saves raw frames to disk for manual review and relabeling.

Usage: python3 vision_collector.py
"""

import base64
import cv2
import json
import os
import signal
import time
from datetime import datetime
from pathlib import Path

import requests

# ── Config ──────────────────────────────────────────────────

CAMERA_INDEX = int(os.getenv("MERLIN_CAMERA_INDEX", "0"))
BRAIN_HOST = os.getenv("MERLIN_BRAIN_HOST", "100.123.211.1")
LLM_URL = f"http://{BRAIN_HOST}:1234/v1/chat/completions"
VISION_MODEL = os.getenv("MERLIN_VISION_MODEL", "gemma-4-e4b-it")

# Capture interval
INTERVAL_NORMAL = 15     # seconds between captures when present
INTERVAL_ABSENT = 60     # seconds between captures when absent
INTERVAL_ACTIVE = 5      # seconds when state just changed

# Storage
FRAME_DIR = Path("/home/pi/RBOS/merlin/vision-data/frames")
LOG_PATH = Path("/home/pi/RBOS/merlin/vision-data/state-log.jsonl")
FRAME_DIR.mkdir(parents=True, exist_ok=True)
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

# Keep raw frames for training data
SAVE_FRAMES = True
MAX_FRAMES = 5000  # delete oldest after this many

VISION_PROMPT = """You are a desk camera observing Ezra. Report ONLY these 6 things, one word each:

1. PRESENT: yes/no (is a person visible?)
2. POSTURE: upright/leaning-back/hunched/head-in-hands/standing
3. FOCUS: screen/phone/away/camera/eating/talking
4. ENERGY: high/neutral/low (from body language only)
5. HANDS: keyboard/mouse/phone/face/lap/gesture/food
6. NOTABLE: one short detail or "nothing"

Format exactly: PRESENT:yes POSTURE:upright FOCUS:screen ENERGY:neutral HANDS:keyboard NOTABLE:nothing"""

running = True
def shutdown(sig, frame):
    global running
    running = False
signal.signal(signal.SIGINT, shutdown)
signal.signal(signal.SIGTERM, shutdown)


def capture_frame(cap):
    """Capture a frame and return as JPEG bytes + numpy array."""
    ret, frame = cap.read()
    if not ret:
        return None, None
    _, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return jpeg.tobytes(), frame


def classify_frame(jpeg_bytes):
    """Send frame to LM Studio for state classification."""
    img_b64 = base64.b64encode(jpeg_bytes).decode()

    try:
        resp = requests.post(LLM_URL, json={
            "model": VISION_MODEL,
            "messages": [
                {"role": "system", "content": VISION_PROMPT},
                {"role": "user", "content": [
                    {"type": "text", "text": "Check now."},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}}
                ]}
            ],
            "max_tokens": 40,
            "temperature": 0.1,
        }, timeout=30)

        if resp.status_code == 200:
            text = resp.json()["choices"][0]["message"]["content"].strip()
            return parse_state(text), text
        else:
            return None, f"error:{resp.status_code}"
    except Exception as e:
        return None, f"error:{e}"


def parse_state(text):
    """Parse structured state output into dict."""
    state = {}
    for part in text.split():
        if ":" in part:
            key, val = part.split(":", 1)
            state[key.upper()] = val.lower()
    return state


def save_frame(jpeg_bytes, state, timestamp):
    """Save frame to disk with state in filename for easy browsing."""
    if not SAVE_FRAMES:
        return

    present = state.get("PRESENT", "unk")
    posture = state.get("POSTURE", "unk")
    energy = state.get("ENERGY", "unk")
    focus = state.get("FOCUS", "unk")

    filename = f"{timestamp}_{present}_{posture}_{energy}_{focus}.jpg"
    filepath = FRAME_DIR / filename

    with open(filepath, "wb") as f:
        f.write(jpeg_bytes)

    # Cleanup old frames
    frames = sorted(FRAME_DIR.glob("*.jpg"))
    if len(frames) > MAX_FRAMES:
        for old in frames[:len(frames) - MAX_FRAMES]:
            old.unlink()


def log_state(state, raw_text, timestamp):
    """Append state to JSONL log."""
    entry = {
        "ts": timestamp,
        "state": state,
        "raw": raw_text,
    }
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")


def main():
    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        print(f"[vision] ERROR: Cannot open camera {CAMERA_INDEX}")
        return

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    print(f"[vision] Vision collector running")
    print(f"[vision] Model: {VISION_MODEL} at {LLM_URL}")
    print(f"[vision] Frames: {FRAME_DIR}")
    print(f"[vision] Log: {LOG_PATH}")

    last_state = {}
    interval = INTERVAL_NORMAL
    captures = 0

    while running:
        jpeg, frame = capture_frame(cap)
        if jpeg is None:
            time.sleep(1)
            continue

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        state, raw = classify_frame(jpeg)

        if state:
            captures += 1

            # Adjust interval based on presence
            if state.get("PRESENT") == "no":
                interval = INTERVAL_ABSENT
            elif state != last_state:
                interval = INTERVAL_ACTIVE  # state changed, capture more
                print(f"[vision] State change: {raw}")
            else:
                interval = INTERVAL_NORMAL

            save_frame(jpeg, state, timestamp)
            log_state(state, raw, timestamp)

            if captures % 10 == 0 or state != last_state:
                print(f"[vision] #{captures} {raw}")

            last_state = state
        else:
            print(f"[vision] Classification failed: {raw}")

        time.sleep(interval)

    cap.release()
    print(f"[vision] Done. {captures} frames captured.")


if __name__ == "__main__":
    main()
