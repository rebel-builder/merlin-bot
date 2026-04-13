# Merlin Face Tracker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone face-tracking process that keeps Ezra centered in the camera frame using proven ONVIF PTZ control.

**Architecture:** Frigate-style move-and-wait loop. No PID controller. Calculate face offset from frame center → send ONVIF RelativeMove in FOV space → poll GetStatus until motor is IDLE → repeat. Camera's own motor controller handles motion smoothing. Separate process from brain/senses.

**Tech Stack:** Python 3.13, OpenCV (Haar cascade, upgrade to DNN SSD later), requests (HTTP Digest auth for ONVIF), ONVIF RelativeMove FOV + GetStatus polling.

**Proven by:** Frigate NVR autotracker (production, 1500+ lines, RelativeMove + wait-for-IDLE pattern), Amcrest IP4M-1041B confirmed supporting RelativeMove FOV + MoveStatus transitions.

---

## File Structure

| File | Purpose |
|------|---------|
| `merlin/tracker.py` | Standalone tracking loop (rewrite) |
| (no other files) | Single file, no dependencies beyond what's on Pi |

## Hardware Facts (empirically verified)

- ONVIF RelativeMove FOV: **works** (tested, 200 OK, position changes)
- ONVIF GetStatus MoveStatus: **works** (reports MOVING/IDLE transitions)
- ONVIF ContinuousMove: **works** (for fallback)
- ONVIF AbsoluteMove: **works** (for privacy mode)
- ONVIF GotoPreset: **works** (for return-to-home)
- Profile token: `MediaProfile00000`
- Auth: HTTP Digest (no WS-Security needed)
- PTZ service: `http://192.168.1.26/onvif/ptz_service`
- RTSP sub stream: `rtsp://admin:pass@192.168.1.26:554/cam/realmonitor?channel=1&subtype=1`
- Home preset: `1`
- Tilt range: Y=1.055 (floor) to Y=-1.728 (ceiling)
- Face detection: Haar cascade (~20-40ms on 640x480)

## Constants (from Frigate + Roboflow research)

```python
# Dead zone: suppress moves < 5% of frame (Frigate uses 5%, Roboflow uses 14-20%)
MOVE_THRESHOLD = 0.05

# Speed for RelativeMove (1.0 = max)
MOVE_SPEED = 1.0

# Face lost timeout before returning home (Frigate default: configurable)
FACE_LOST_TIMEOUT = 5.0

# Motor poll interval
POLL_INTERVAL = 0.1  # 100ms

# Motor timeout (if camera never reports IDLE)
MOTOR_TIMEOUT = 10.0
```

---

### Task 1: ONVIF Helper Functions

**Files:**
- Create: `merlin/tracker.py` (rewrite from scratch)

- [ ] **Step 1: Write ONVIF SOAP helper + RelativeMove + GetStatus + Stop + GotoPreset + Privacy**

```python
# All ONVIF calls use HTTP Digest auth, single requests.Session for connection pooling
# RelativeMove uses TranslationSpaceFov URI
# GetStatus returns (pan, tilt, move_status) tuple
# wait_for_idle() polls GetStatus at 100ms until IDLE or timeout
```

- [ ] **Step 2: Test on Pi — verify RelativeMove(0.1, 0.0) moves camera and wait_for_idle() returns**

Run: `ssh pi "python3 -c 'from tracker import *; print(ptz_relative_move(0.1, 0)); print(ptz_wait_for_idle())'"`
Expected: Camera pans slightly right, function returns True

- [ ] **Step 3: Test GotoPreset returns camera to home**

Run: `ssh pi "python3 -c 'from tracker import *; ptz_home(); print(ptz_get_status())'"`
Expected: Camera returns to home position

---

### Task 2: Face Detection Function

- [ ] **Step 4: Write detect_face() using Haar cascade**

```python
# Returns (cx, cy) normalized 0-1 where (0.5, 0.5) is center, or None
# Uses equalizeHist for lighting robustness
# Returns largest face by area
```

- [ ] **Step 5: Test face detection on a single RTSP frame**

Run: `ssh pi "python3 -c 'from tracker import *; print(detect_face_from_stream())'"`
Expected: Tuple like (0.55, 0.42) if Ezra is visible, None if not

---

### Task 3: Main Tracking Loop (Frigate pattern)

- [ ] **Step 6: Write the main loop**

```
while running:
    face = detect_face(frame)
    if face:
        offset_x = (face_x / frame_w - 0.5) * 2   # normalized [-1, 1]
        offset_y = (0.5 - face_y / frame_h) * 2    # inverted for camera coords

        if abs(offset_x) > MOVE_THRESHOLD or abs(offset_y) > MOVE_THRESHOLD:
            # Suppress small moves (Frigate pattern)
            if abs(offset_x) < MOVE_THRESHOLD: offset_x = 0
            if abs(offset_y) < MOVE_THRESHOLD: offset_y = 0

            ptz_relative_move(offset_x, offset_y)
            ptz_wait_for_idle()  # BLOCK until motor stops

        face_lost_since = None
    else:
        if face_lost_since and (now - face_lost_since) > FACE_LOST_TIMEOUT:
            ptz_home()
            ptz_wait_for_idle()
```

Key design decisions:
- **wait_for_idle() blocks** — no new move until motor finishes. This prevents oscillation.
- **No PID** — offset goes directly to RelativeMove. Camera motor handles smoothing.
- **Suppress small axes** — if only pan is significant, send pan-only (zero tilt). Prevents diagonal jitter.

- [ ] **Step 7: Add frame buffer flushing**

Before each detection, grab and discard 2-3 frames to get the latest. Stale frames cause the tracker to correct for where the face WAS, not where it IS.

- [ ] **Step 8: Add signal handling and cleanup**

Ctrl+C → ptz_stop() → ptz_home() → exit cleanly.

---

### Task 4: Deploy and Test

- [ ] **Step 9: Push to Pi, kill old processes, run standalone**

```bash
scp merlin/tracker.py pi:/home/pi/RBOS/merlin/
ssh pi "pkill -9 -f tracker; pkill -9 -f senses"
ssh pi "python3 -B -u /home/pi/RBOS/merlin/tracker.py"
```

- [ ] **Step 10: Verify via logs — check face position, move commands, IDLE waits**

Read tracker log. Expected pattern:
```
Face acquired (0.65, 0.40)
RelativeMove(0.30, 0.00) → waiting...
IDLE after 0.4s at (0.52, 0.40)
RelativeMove(0.04, 0.00) → waiting...
IDLE after 0.2s at (0.50, 0.41)
(centered, no move)
```

- [ ] **Step 11: Ezra watches camera behavior and reports**

- [ ] **Step 12: Commit**

```bash
git add merlin/tracker.py
git commit -m "Face tracker v2: Frigate-style RelativeMove + wait-for-IDLE"
```

---

### Task 5 (if needed): Tuning

Only if Task 4 testing reveals issues:

- [ ] **Step 13: Adjust MOVE_THRESHOLD** if camera jitters (increase) or doesn't track small movements (decrease)
- [ ] **Step 14: Adjust offset scaling** if camera overshoots (multiply offset by 0.5) or undershoots (multiply by 1.5)
- [ ] **Step 15: Add velocity prediction** if tracking lags behind fast head movements (Frigate's advanced feature — predict where face will be when motor finishes)

---

## Why This Will Work (Unlike Previous Attempts)

| Previous Problem | How This Plan Fixes It |
|-----------------|----------------------|
| PID gains: blind trial and error | No PID. Direct offset → RelativeMove. |
| ContinuousMove overshoots | RelativeMove is self-stopping. Camera handles motion profile. |
| Oscillation (move → overshoot → correct → overshoot) | wait_for_idle() blocks until motor stops. One move at a time. |
| Stale frames causing wrong corrections | Frame buffer flushing before each detection. |
| Multiple concerns in one process | Standalone tracker.py, single purpose. |

## Organon Concepts

- [[Goal-Directed Action]]
- [[Proof]]
- [[Theory-Practice Dichotomy]]
- [[Stack on Existing Behaviors]]
- [[Productiveness]]
