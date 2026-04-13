#!/usr/bin/env python3
"""
Merlin Vision Collector — Standalone version.

Grabs a frame from the PIXY camera periodically (when tracker isn't using it),
sends to LM Studio on Nate's Mac for state classification,
logs results + saves frames for fine-tuning dataset.

Alternative: runs as HTTP endpoint that the tracker calls to share frames.

Usage: python3 vision_collector_standalone.py
  or: python3 vision_collector_standalone.py --http (runs as frame receiver)
"""

import base64
import json
import os
import signal
import sys
import time
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

import requests

# ── Config ──────────────────────────────────────────────────

BRAIN_HOST = os.getenv("MERLIN_BRAIN_HOST", "100.123.211.1")
LLM_URL = f"http://{BRAIN_HOST}:1234/v1/chat/completions"
VISION_MODEL = os.getenv("MERLIN_VISION_MODEL", "gemma-4-e4b-it")

INTERVAL = 15  # seconds between classifications
FRAME_DIR = Path("/home/pi/RBOS/merlin/vision-data/frames")
LOG_PATH = Path("/home/pi/RBOS/merlin/vision-data/state-log.jsonl")
FRAME_DIR.mkdir(parents=True, exist_ok=True)
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

MAX_FRAMES = 5000

VISION_PROMPT = """You are a desk camera observing Ezra. Report ONLY these 6 things, one word each:

1. PRESENT: yes/no
2. POSTURE: upright/leaning-back/hunched/head-in-hands/standing
3. FOCUS: screen/phone/away/camera/eating/talking
4. ENERGY: high/neutral/low
5. HANDS: keyboard/mouse/phone/face/lap/gesture/food
6. NOTABLE: one short detail or "nothing"

Format: PRESENT:yes POSTURE:upright FOCUS:screen ENERGY:neutral HANDS:keyboard NOTABLE:nothing"""

running = True
def shutdown(sig, frame):
    global running
    running = False
signal.signal(signal.SIGINT, shutdown)
signal.signal(signal.SIGTERM, shutdown)

# Latest frame (shared between HTTP receiver and classifier)
_latest_frame = None
_latest_frame_time = 0


def classify_jpeg(jpeg_bytes):
    """Send JPEG to LM Studio for classification."""
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
            return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"error:{e}"
    return None


def parse_state(text):
    state = {}
    if text:
        for part in text.split():
            if ":" in part:
                k, v = part.split(":", 1)
                state[k.upper()] = v.lower()
    return state


def save_and_log(jpeg_bytes, state, raw, timestamp):
    # Save frame
    p = state.get("PRESENT", "unk")
    posture = state.get("POSTURE", "unk")
    energy = state.get("ENERGY", "unk")
    focus = state.get("FOCUS", "unk")
    filename = f"{timestamp}_{p}_{posture}_{energy}_{focus}.jpg"
    with open(FRAME_DIR / filename, "wb") as f:
        f.write(jpeg_bytes)

    # Log
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps({"ts": timestamp, "state": state, "raw": raw}) + "\n")

    # Cleanup
    frames = sorted(FRAME_DIR.glob("*.jpg"))
    for old in frames[:max(0, len(frames) - MAX_FRAMES)]:
        old.unlink()


# ── HTTP Frame Receiver ──────────────────────────────────────
# The tracker can POST frames here instead of us opening the camera

class FrameHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        global _latest_frame, _latest_frame_time
        if self.path == "/frame":
            length = int(self.headers.get("Content-Length", 0))
            _latest_frame = self.rfile.read(length)
            _latest_frame_time = time.time()
            self.send_response(200)
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        if self.path == "/state":
            # Return latest state
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"state": _latest_state, "ts": _latest_ts}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass

_latest_state = {}
_latest_ts = ""


def run_http_mode():
    """Run as HTTP server receiving frames from tracker."""
    global _latest_state, _latest_ts, _latest_frame, _latest_frame_time

    import threading

    server = HTTPServer(("0.0.0.0", 8901), FrameHandler)
    http_thread = threading.Thread(target=server.serve_forever, daemon=True)
    http_thread.start()
    print(f"[vision] HTTP mode on :8901 — tracker POSTs frames to /frame")
    print(f"[vision] GET /state for latest classification")

    last_classified = 0
    captures = 0

    while running:
        if _latest_frame and (time.time() - last_classified) > INTERVAL:
            jpeg = _latest_frame
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

            raw = classify_jpeg(jpeg)
            state = parse_state(raw)

            if state:
                captures += 1
                _latest_state = state
                _latest_ts = timestamp
                save_and_log(jpeg, state, raw, timestamp)
                print(f"[vision] #{captures} {raw}")

            last_classified = time.time()

        time.sleep(1)

    server.shutdown()
    print(f"[vision] Done. {captures} classifications.")


def run_camera_mode():
    """Run with direct camera access (when tracker isn't running)."""
    import cv2

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("[vision] Camera busy (tracker running?). Use --http mode instead.")
        return

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    print("[vision] Camera mode — direct capture")

    captures = 0
    while running:
        ret, frame = cap.read()
        if not ret:
            time.sleep(1)
            continue

        _, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        jpeg_bytes = jpeg.tobytes()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        raw = classify_jpeg(jpeg_bytes)
        state = parse_state(raw)

        if state:
            captures += 1
            save_and_log(jpeg_bytes, state, raw, timestamp)
            print(f"[vision] #{captures} {raw}")

        time.sleep(INTERVAL)

    cap.release()
    print(f"[vision] Done. {captures} classifications.")


if __name__ == "__main__":
    if "--http" in sys.argv:
        run_http_mode()
    else:
        run_camera_mode()
