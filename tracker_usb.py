#!/usr/bin/env python3
"""
Merlin Face Tracker v5 — USB Edition (EMEET PIXY).

YuNet face detection + UVC PTZ control. Everything on Nate's Mac.
No Pi, no RTSP, no ONVIF. USB camera = zero latency.

Replaces tracker.py (v3, ONVIF/RTSP) when EMEET PIXY is connected.

The PD controller, smoothing, deadband, and face detection logic are
preserved from v3. Only the I/O layer changes: OpenCV USB capture
replaces RTSP, UVCPTZController replaces ONVIF SOAP.

Run:  python3 merlin/tracker_usb.py
Stop: Ctrl+C (returns camera to home)
"""

import cv2
import csv
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────

# Camera index (0 = first camera, 1 = second — set after probe)
CAMERA_INDEX = int(os.getenv("MERLIN_CAMERA_INDEX", "0"))  # PIXY confirmed at index 0 on Ezra's Mac, index 1 on Nate's Mac
CAMERA_WIDTH = 1920
CAMERA_HEIGHT = 1080
CAMERA_FPS = 30

# YuNet model
YUNET_MODEL = os.path.join(os.path.dirname(__file__), "models", "face_detection_yunet_2023mar.onnx")

# Tracking parameters (preserved from v3 — tuned for Amcrest, retune for PIXY)
DEADBAND = 0.03
SPEED_FAST = 5.0
SPEED_FINE = 0.7
FINE_ZONE = 0.20
MIN_VELOCITY = 0.12
FACE_LOST_TIMEOUT = 8.0

# Smoothing + PD control
SMOOTH_ALPHA = 0.7
KP = 1.0
KD = 0.7
VELOCITY_RAMP = 0.8
VELOCITY_THRESHOLD = 0.03

# Axis mapping — MUST be verified empirically with PIXY
# These may need to be flipped from the Amcrest values
PAN_SIGN = 1.0
TILT_SIGN = -1.0

# PTZ scale: convert PD output (0-1 range velocity) to degrees for UVC
# Amcrest used ONVIF velocity (-1 to +1). PIXY uses absolute degrees.
# This controls how aggressively the camera moves per frame.
PTZ_SCALE_PAN = 2.0   # degrees per unit of PD output
PTZ_SCALE_TILT = 1.5  # degrees per unit of PD output

# Brain notification
BRAIN_URL = os.getenv("MERLIN_BRAIN_URL", "http://localhost:8900/event")


# ── Brain Notification ────────────────────────────────────────

_last_notified = None

def notify_brain(event_type):
    """Notify brain module of face events via HTTP."""
    global _last_notified
    if event_type == _last_notified:
        return
    _last_notified = event_type
    try:
        import requests
        requests.post(BRAIN_URL, json={"type": event_type}, timeout=1)
    except Exception:
        pass


# ── PTZ Control ───────────────────────────────────────────────

class PTZController:
    """Wraps UVC PTZ for the tracking loop.

    The tracker outputs velocity-like values (from PD controller).
    This converts them to absolute position updates for UVC.
    Tracks current position internally and applies deltas.
    """

    def __init__(self):
        self._pan = 0.0   # current pan in degrees
        self._tilt = 0.0  # current tilt in degrees
        self._ptz = None

        try:
            from ptz_uvc import UVCPTZController
            self._ptz = UVCPTZController()
            print(f"[tracker] PTZ: UVC connected")
        except Exception as e:
            print(f"[tracker] PTZ: FAILED — {e}")
            print(f"[tracker] Running in DETECTION-ONLY mode (no motor control)")

    def move(self, pan_vel, tilt_vel):
        """Apply velocity as position delta. Pan/tilt_vel are PD output values."""
        if self._ptz is None:
            return

        # Convert velocity to degree delta
        self._pan += pan_vel * PTZ_SCALE_PAN
        self._tilt += tilt_vel * PTZ_SCALE_TILT

        # Clamp to PIXY range (±155° pan, ±90° tilt)
        self._pan = max(-155.0, min(155.0, self._pan))
        self._tilt = max(-90.0, min(90.0, self._tilt))

        try:
            self._ptz.set_pantilt(self._pan, self._tilt)
        except Exception as e:
            print(f"[tracker] PTZ error: {e}")

    def stop(self):
        """No-op for absolute positioning (no momentum to stop)."""
        pass

    def home(self):
        """Return to center."""
        self._pan = 0.0
        self._tilt = 0.0
        if self._ptz:
            try:
                self._ptz.home()
            except Exception:
                pass

    def close(self):
        if self._ptz:
            self._ptz.close()


# ── Face Detection (YuNet) ────────────────────────────────────

yunet = cv2.FaceDetectorYN.create(YUNET_MODEL, "", (640, 480), 0.5, 0.3, 5000)

DETECT_SIZE = (320, 240)

def detect_face(frame):
    """Detect largest face via YuNet at reduced resolution.
    Returns (cx, cy) normalized 0-1, or None."""
    small = cv2.resize(frame, DETECT_SIZE)
    yunet.setInputSize(DETECT_SIZE)
    _, faces = yunet.detect(small)

    if faces is None or len(faces) == 0:
        return None

    best = max(range(len(faces)), key=lambda i: faces[i][14])
    f = faces[best]
    cx = (f[0] + f[2] / 2) / DETECT_SIZE[0]
    cy = (f[1] + f[3] / 2) / DETECT_SIZE[1]
    return (cx, cy)


# ── Performance Logger ────────────────────────────────────────

LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")

class TrackingLogger:
    def __init__(self):
        os.makedirs(LOG_DIR, exist_ok=True)
        date_str = datetime.now().strftime("%Y-%m-%d")
        self.path = os.path.join(LOG_DIR, f"tracking-usb-{date_str}.csv")
        self.file = open(self.path, "a", newline="")
        self.writer = csv.writer(self.file)
        if os.path.getsize(self.path) == 0:
            self.writer.writerow([
                "timestamp", "face_x", "face_y", "err_x", "err_y",
                "pan_vel", "tilt_vel", "speed_mode", "detect_ms",
            ])
        self.session_start = time.monotonic()
        self.moves = 0
        self.overshoots = 0
        self.prev_err_x = 0
        print(f"[tracker] Logging to {self.path}")

    def log(self, face_x, face_y, err_x, err_y, pan_vel, tilt_vel, speed_mode, detect_ms):
        self.writer.writerow([
            f"{time.monotonic() - self.session_start:.2f}",
            f"{face_x:.3f}", f"{face_y:.3f}",
            f"{err_x:.3f}", f"{err_y:.3f}",
            f"{pan_vel:.3f}", f"{tilt_vel:.3f}",
            speed_mode, f"{detect_ms:.1f}",
        ])
        self.moves += 1
        if self.prev_err_x * err_x < 0 and abs(err_x) > DEADBAND:
            self.overshoots += 1
        self.prev_err_x = err_x
        if self.moves % 50 == 0:
            self.file.flush()

    def summary(self):
        elapsed = time.monotonic() - self.session_start
        rate = self.moves / elapsed if elapsed > 0 else 0
        print(f"[tracker] Session: {elapsed:.0f}s, {self.moves} moves, "
              f"{rate:.1f}/s, {self.overshoots} overshoots")

    def close(self):
        self.summary()
        self.file.close()


# ── Main Tracking Loop ────────────────────────────────────────

def main():
    global running
    running = True

    def shutdown(sig, frame):
        global running
        running = False

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Open USB camera (no RTSP, no buffer drain thread needed)
    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        print(f"[tracker] ERROR: Cannot open camera at index {CAMERA_INDEX}")
        print(f"[tracker] Try: MERLIN_CAMERA_INDEX=1 python3 tracker_usb.py")
        return

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_HEIGHT)
    cap.set(cv2.CAP_PROP_FPS, CAMERA_FPS)

    actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print(f"[tracker] USB camera opened: {actual_w}x{actual_h} at index {CAMERA_INDEX}")

    # Initialize PTZ
    ptz = PTZController()

    print(f"[tracker] YuNet + UVC PTZ tracker (USB)")
    print(f"[tracker] Deadband={DEADBAND}, fast={SPEED_FAST}, fine={SPEED_FINE}")
    print("[tracker] Running.")

    logger = TrackingLogger()
    face_lost_since = None
    is_tracking = False
    is_moving = False
    last_log = 0

    # Smoothing state
    smooth_x = 0.5
    smooth_y = 0.5
    prev_err_x = 0.0
    prev_err_y = 0.0
    current_pan_vel = 0.0
    current_tilt_vel = 0.0
    _last_sent_pan = 0.0
    _last_sent_tilt = 0.0

    try:
        while running:
            # USB capture is synchronous — always returns latest frame
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.01)
                continue

            t_detect = time.monotonic()
            face = detect_face(frame)
            detect_ms = (time.monotonic() - t_detect) * 1000

            if face is not None:
                raw_x, raw_y = face
                face_lost_since = None

                if not is_tracking:
                    smooth_x = raw_x
                    smooth_y = raw_y
                    prev_err_x = raw_x - 0.5
                    prev_err_y = raw_y - 0.5
                    print(f"[tracker] Face acquired ({raw_x:.2f}, {raw_y:.2f})")
                    is_tracking = True
                    notify_brain("face_arrived")

                # 1. Exponential smoothing
                smooth_x = SMOOTH_ALPHA * raw_x + (1 - SMOOTH_ALPHA) * smooth_x
                smooth_y = SMOOTH_ALPHA * raw_y + (1 - SMOOTH_ALPHA) * smooth_y

                # 2. Error from center
                err_x = smooth_x - 0.5
                err_y = smooth_y - 0.5

                # 3. Dead zone
                if abs(err_x) < DEADBAND and abs(err_y) < DEADBAND:
                    if is_moving:
                        ptz.stop()
                        is_moving = False
                        current_pan_vel = 0.0
                        current_tilt_vel = 0.0
                    prev_err_x = err_x
                    prev_err_y = err_y
                else:
                    # 4. PD controller
                    d_err_x = err_x - prev_err_x
                    d_err_y = err_y - prev_err_y

                    dist = max(abs(err_x), abs(err_y))
                    speed = SPEED_FINE if dist < FINE_ZONE else SPEED_FAST

                    target_pan = PAN_SIGN * (KP * err_x * speed + KD * d_err_x * speed)
                    target_tilt = TILT_SIGN * (KP * err_y * speed + KD * d_err_y * speed)

                    # 5. Velocity ramping
                    pan_delta = target_pan - current_pan_vel
                    tilt_delta = target_tilt - current_tilt_vel

                    if abs(pan_delta) > VELOCITY_RAMP:
                        pan_delta = VELOCITY_RAMP if pan_delta > 0 else -VELOCITY_RAMP
                    if abs(tilt_delta) > VELOCITY_RAMP:
                        tilt_delta = VELOCITY_RAMP if tilt_delta > 0 else -VELOCITY_RAMP

                    current_pan_vel += pan_delta
                    current_tilt_vel += tilt_delta

                    pan_vel = current_pan_vel
                    tilt_vel = current_tilt_vel
                    if 0 < abs(pan_vel) < MIN_VELOCITY:
                        pan_vel = MIN_VELOCITY if pan_vel > 0 else -MIN_VELOCITY
                    if 0 < abs(tilt_vel) < MIN_VELOCITY:
                        tilt_vel = MIN_VELOCITY if tilt_vel > 0 else -MIN_VELOCITY

                    pan_vel = max(-0.8, min(0.8, pan_vel))
                    tilt_vel = max(-0.8, min(0.8, tilt_vel))

                    if abs(err_x) < DEADBAND:
                        pan_vel = 0.0
                    if abs(err_y) < DEADBAND:
                        tilt_vel = 0.0

                    pan_changed = abs(pan_vel - _last_sent_pan) > VELOCITY_THRESHOLD
                    tilt_changed = abs(tilt_vel - _last_sent_tilt) > VELOCITY_THRESHOLD
                    if pan_changed or tilt_changed or not is_moving:
                        ptz.move(pan_vel, tilt_vel)
                        _last_sent_pan = pan_vel
                        _last_sent_tilt = tilt_vel
                    is_moving = True

                    speed_mode = "fine" if dist < FINE_ZONE else "fast"
                    logger.log(smooth_x, smooth_y, err_x, err_y,
                              pan_vel, tilt_vel, speed_mode, detect_ms)

                    prev_err_x = err_x
                    prev_err_y = err_y

                now = time.monotonic()
                if now - last_log > 2.0:
                    print(f"[tracker] face=({smooth_x:.2f},{smooth_y:.2f}) "
                          f"err=({err_x:+.2f},{err_y:+.2f}) "
                          f"vel=({current_pan_vel:+.2f},{current_tilt_vel:+.2f}) "
                          f"{'fine' if max(abs(err_x),abs(err_y)) < FINE_ZONE else 'fast'} "
                          f"detect={detect_ms:.0f}ms")
                    last_log = now

            else:
                if is_tracking:
                    if face_lost_since is None:
                        face_lost_since = time.monotonic()
                        ptz.stop()
                        is_moving = False
                        current_pan_vel = 0.0
                        current_tilt_vel = 0.0

                    if time.monotonic() - face_lost_since > FACE_LOST_TIMEOUT:
                        print("[tracker] Face lost → home")
                        ptz.home()
                        is_tracking = False
                        face_lost_since = None
                        notify_brain("face_lost")

            time.sleep(0.01)

    finally:
        print("[tracker] Shutting down...")
        ptz.stop()
        ptz.home()
        ptz.close()
        logger.close()
        cap.release()


if __name__ == "__main__":
    main()
