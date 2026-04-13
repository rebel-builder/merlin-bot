#!/usr/bin/env python3
"""Merlin Gestures — PTZ body language via Amcrest HTTP API."""

import time
import random
import threading
import requests
from requests.auth import HTTPDigestAuth

from config import CAMERA_AUTH, CAMERA_PTZ_BASE

AUTH = CAMERA_AUTH
BASE = CAMERA_PTZ_BASE


def _ptz(code, speed=3, duration=0.15):
    """Move camera in a direction for a duration, then stop.
    code: Up, Down, Left, Right, LeftUp, RightUp, LeftDown, RightDown
    speed: 1-8 (camera actually accepts 1-255 but 1-8 is practical)
    """
    try:
        requests.get(
            f"{BASE}?action=start&channel=0&code={code}&arg1=0&arg2={speed}&arg3=0",
            auth=AUTH, timeout=2,
        )
        time.sleep(duration)
        requests.get(
            f"{BASE}?action=stop&channel=0&code={code}&arg1=0&arg2=0&arg3=0",
            auth=AUTH, timeout=2,
        )
    except Exception as e:
        print(f"[gestures] PTZ error: {e}")


def go_home():
    """Return to home position (preset 1)."""
    try:
        requests.get(
            f"{BASE}?action=start&channel=0&code=GotoPreset&arg1=0&arg2=1&arg3=0",
            auth=AUTH, timeout=2,
        )
    except Exception as e:
        print(f"[gestures] Home error: {e}")


# ── Acknowledgment gestures ────────────────────────────────────────

def nod():
    """Nod — quick tilt up then return home. (Up works, Down doesn't on this firmware.)"""
    _ptz("Up", speed=3, duration=0.15)
    go_home()


def shake():
    """Subtle head shake — left, right, home."""
    _ptz("Left", speed=2, duration=0.10)
    time.sleep(0.1)
    _ptz("Right", speed=2, duration=0.20)
    time.sleep(0.1)
    go_home()


def double_nod():
    """Two quick nods — emphatic yes."""
    for _ in range(2):
        _ptz("Up", speed=3, duration=0.10)
        time.sleep(0.08)
        go_home()
        time.sleep(0.15)


# ── Attention gestures ─────────────────────────────────────────────

def perk_up():
    """Quick tilt up — alert, noticed something."""
    _ptz("Up", speed=3, duration=0.10)
    time.sleep(0.3)
    go_home()


# ── PID Face Tracker (Wiener, 1948) ────────────────────────────────
# Same math as WWII anti-aircraft fire control.
# P = correct proportional to error
# I = accumulate persistent offset
# D = anticipate movement direction

class PIDTracker:
    """Norbert Wiener's gift to Merlin."""

    def __init__(self, kp=0.8, ki=0.05, kd=0.3, deadzone=0.12):
        self.kp = kp          # proportional gain
        self.ki = ki          # integral gain
        self.kd = kd          # derivative gain
        self.deadzone = deadzone
        self.integral = 0.0
        self.prev_error = 0.0
        self.prev_time = time.time()
        self.cooldown = 0.5   # min seconds between PTZ commands
        self.last_cmd_time = 0

    def update(self, face_x, frame_w):
        """Calculate PTZ correction from face position. Returns (direction, speed, duration) or None."""
        now = time.time()
        dt = now - self.prev_time
        if dt < 0.01:
            dt = 0.01
        self.prev_time = now

        # Error: how far off-center (-1 to 1)
        error = (face_x - frame_w / 2) / (frame_w / 2)

        # Dead zone — don't correct tiny offsets
        if abs(error) < self.deadzone:
            self.integral = 0  # reset integral when centered
            self.prev_error = error
            return None

        # PID terms
        p = self.kp * error
        self.integral += error * dt
        self.integral = max(-2.0, min(2.0, self.integral))  # clamp windup
        i = self.ki * self.integral
        d = self.kd * (error - self.prev_error) / dt
        self.prev_error = error

        # Combined output
        output = p + i + d

        # Convert to PTZ command
        direction = "Right" if output > 0 else "Left"
        magnitude = abs(output)

        # Map magnitude to speed (1-3) and duration (0.05-0.15)
        speed = max(1, min(3, int(magnitude * 2)))
        duration = max(0.05, min(0.15, magnitude * 0.1))

        return (direction, speed, duration)


_pid = PIDTracker()

def track_face(face_x, frame_w):
    """PID-controlled face tracking. Smooth, predictive, no overshoot."""
    now = time.time()
    if (now - _pid.last_cmd_time) < _pid.cooldown:
        # Still update PID state for derivative term, just don't send command
        _pid.update(face_x, frame_w)
        return

    result = _pid.update(face_x, frame_w)
    if result:
        direction, speed, duration = result
        _pid.last_cmd_time = now
        _ptz(direction, speed=speed, duration=duration)


# ── Idle behaviors ─────────────────────────────────────────────────

def idle_wander():
    """Random small movement — looking around, curious. Always returns home."""
    moves = [
        ("Left", 1, 0.12),
        ("Right", 1, 0.12),
        ("Up", 1, 0.08),
        ("LeftUp", 1, 0.10),
        ("RightUp", 1, 0.10),
    ]
    code, speed, dur = random.choice(moves)
    _ptz(code, speed=speed, duration=dur)
    time.sleep(random.uniform(1.5, 4.0))
    go_home()


def look_up_think():
    """Look up briefly — as if thinking."""
    _ptz("Up", speed=1, duration=0.10)
    time.sleep(random.uniform(1.0, 2.0))
    go_home()


def idle_loop(stop_event):
    """Run idle behaviors in a background thread until stopped."""
    behaviors = [idle_wander, look_up_think, idle_wander, idle_wander]
    while not stop_event.is_set():
        stop_event.wait(random.uniform(15, 40))
        if stop_event.is_set():
            break
        random.choice(behaviors)()


# ── Gesture runner (non-blocking) ──────────────────────────────────

def run_gesture(gesture_fn, *args):
    """Run a gesture in a background thread so it doesn't block."""
    t = threading.Thread(target=gesture_fn, args=args, daemon=True)
    t.start()
    return t


if __name__ == "__main__":
    print("Testing gestures...")
    print("Nod...")
    nod()
    time.sleep(1)
    print("Shake...")
    shake()
    time.sleep(1)
    print("Perk up...")
    perk_up()
    time.sleep(1)
    print("Look up think...")
    look_up_think()
    print("Done!")
