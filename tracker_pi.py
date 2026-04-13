#!/usr/bin/env python3
"""
Merlin Face Tracker + Idle Behavior — runs on Pi 5.

Disney animation principles applied:
  2. Anticipation — dip before turning
  5. Follow Through — overshoot then settle
  6. Slow In/Slow Out — ease into/out of movements
  7. Arc — curved paths, not straight lines
  9. Timing — speed communicates personality
  10. Exaggeration — big gestures on a tiny robot

State machine (priority order):
  TRACKING  — face detected, smooth follow
  ATTENTIVE — face just lost (<60s), hold position, wait for return
  SEARCHING — periodic re-search for face (every 2-3 min)
  IDLE      — breathing + glances (no face for >60s)
"""

import argparse
import cv2
import datetime
import math
import os
import random
import signal
import subprocess
import time
import threading

# ── Config ──────────────────────────────────────────────────

# Auto-detect PIXY camera (survives replug/reboot)
try:
    from camera_detect import detect_pixy_safe
    CAMERA_INDEX, PTZ_DEVICE = detect_pixy_safe()
except ImportError:
    CAMERA_INDEX, PTZ_DEVICE = 1, "/dev/video1"
    print("[tracker] camera_detect not found, using fallback index 1")
YUNET_MODEL = '/home/pi/RBOS/merlin/models/face_detection_yunet_2023mar.onnx'
DETECT_SIZE = (320, 240)
DEADBAND = 0.05
SMOOTH = 0.5
PAN_SIGN = -1
TILT_SIGN = -1

# Timing
TRACKING_DURATION = 60      # track face for this long, then go idle (even if face visible)
LINGER_DURATION = 15        # hold position this long after losing face
IDLE_BREATHING_DELAY = 5    # seconds after going idle before breathing starts
SEARCH_INTERVAL = 150       # re-search every 2.5 minutes
SEARCH_DURATION = 20        # look for face for 20 seconds during re-search
GLANCE_MIN_INTERVAL = 15    # minimum seconds between saccadic glances
GLANCE_MAX_INTERVAL = 40    # maximum seconds between glances
IDLE_SOUND_MIN = 8          # minimum seconds between idle sounds
IDLE_SOUND_MAX = 20         # maximum seconds between idle sounds

# Breathing (Disney Principle 10: Exaggeration)
BREATHING_AMPLITUDE = 8     # degrees — big enough to read on tiny robot
BREATHING_SPEED = 0.15      # Hz — slow and organic

# Quiet hours — no sounds, no tracking, no voice
QUIET_START = 23  # 11pm
QUIET_END = 7     # 7am

# Startle (listens on UDP for signals from other processes)
STARTLE_PORT = 8902
RECORD_PORT = 8903
STARTLE_COOLDOWN = 5        # minimum seconds between startles
STARTLE_SNAP_DEGREES = 25   # how far to snap on startle

# Sounds
SOUNDS_DIR = "/home/pi/RBOS/merlin/sounds"
SPEAKER_DEVICE = "plughw:1,0"

# Vision
VISION_URL = "http://localhost:8901/frame"
VISION_INTERVAL = 15

# ── State ───────────────────────────────────────────────────

class State:
    TRACKING = "tracking"
    ATTENTIVE = "attentive"
    SEARCHING = "searching"
    SEEKING = "seeking"        # proactive "Hey, Ezra" search — the North Star behavior
    IDLE = "idle"

# Seeking config
SEEK_INTERVAL = 999999         # disabled — Ezra finds proactive seeking annoying
SEEK_PAN_POSITIONS = [         # sweep positions in degrees (systematic room scan)
    (0, 0),                    # center
    (-40, 0),                  # left
    (-40, -20),                # left-down
    (0, -20),                  # center-down
    (40, -20),                 # right-down
    (40, 0),                   # right
    (40, 15),                  # right-up
    (0, 15),                   # center-up
    (-40, 15),                 # left-up
    (0, 0),                    # back to center
]
SEEK_HOLD_TIME = 1.5           # seconds to hold each position and check for face
SEEK_GREETINGS_FOUND = [       # clips to play when face found during seek
    "merlin_hey_ezra",
    "merlin_hey_ezra_there_you_are",
    "merlin_hey_ezra_check_in",
]
SEEK_GREETINGS_LOST = [        # clips to play when face NOT found after full sweep
    "merlin_hey_ezra_cant_find",
    "merlin_hey_ezra_been_a_while",
]

running = True
def stop(s, f):
    global running
    running = False
signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, stop)

state = State.SEARCHING  # start searching so first face triggers tracking
pan = 0
tilt = 0
sx, sy = 0.5, 0.5
last_face_pan = 0           # where we last saw the face
last_face_tilt = 0
tracking_start = 0          # when we started tracking this round
lost_at = None
idle_start = 0              # when we entered idle
last_ptz = 0
last_log = 0
last_glance = 0
last_idle_sound = 0
next_idle_sound = 10
last_search = 0
search_start = 0
last_startle = 0
last_seek = 0
last_vision_post = 0
frames = 0
startle_pending = False
_record_pending = None

# ── Hardware ────────────────────────────────────────────────

def set_ptz(p, t):
    """Send pan/tilt command to PIXY. Units: arcseconds (3600 = 1 degree)."""
    p = max(-540000, min(540000, round(p / 3600) * 3600))
    t = max(-324000, min(324000, round(t / 3600) * 3600))
    subprocess.run(
        ['v4l2-ctl', '-d', PTZ_DEVICE,
         f'--set-ctrl=pan_absolute={p}', f'--set-ctrl=tilt_absolute={t}'],
        capture_output=True, timeout=2)
    return p, t


def play_sound(name):
    """Play a sound file through USB speaker (non-blocking)."""
    path = f"{SOUNDS_DIR}/{name}.wav"
    if os.path.exists(path):
        subprocess.Popen(
            ["aplay", "-D", SPEAKER_DEVICE, "-q", path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def get_idle_sound():
    """Pick a random 4 or 5-note idle sound."""
    sounds = [f for f in os.listdir(SOUNDS_DIR)
              if f.startswith(("n4_", "n5_")) and f.endswith(".wav")]
    if sounds:
        return random.choice(sounds).replace(".wav", "")
    return None

# ── Startle listener (UDP) ──────────────────────────────────

def startle_listener():
    """Listen for startle signals from other processes (e.g., pi client)."""
    global startle_pending
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", STARTLE_PORT))
    sock.settimeout(1.0)
    while running:
        try:
            data, _ = sock.recvfrom(256)
            msg = data.decode().strip()
            if msg == "startle":
                startle_pending = True
        except Exception:
            pass
    sock.close()

threading.Thread(target=startle_listener, daemon=True, name="startle").start()

# ── Record control listener (UDP) ─────────────────────────

def record_control_listener():
    """Listen for record start/stop commands from brain server."""
    global _record_pending
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", RECORD_PORT))
    sock.settimeout(1.0)
    while running:
        try:
            data, addr = sock.recvfrom(256)
            msg = data.decode().strip()
            if msg in ("record_start", "record_stop"):
                _record_pending = msg
                print(f"[tracker] Record command: {msg}")
        except Exception:
            pass
    sock.close()

threading.Thread(target=record_control_listener, daemon=True, name="record_ctrl").start()

# ── Vision posting ──────────────────────────────────────────

def post_frame_to_vision(jpeg_bytes):
    """Send frame to vision collector in background."""
    try:
        import requests
        requests.post(VISION_URL, data=jpeg_bytes, timeout=2)
    except Exception:
        pass

# ── Movement helpers ────────────────────────────────────────

def ease_to(target_pan, target_tilt, steps=8, pause=0.04):
    """
    Ease into a position (Disney Principle 6: Slow In/Slow Out).
    Uses cubic easing for natural acceleration/deceleration.
    """
    global pan, tilt
    start_pan, start_tilt = pan, tilt
    for i in range(1, steps + 1):
        # Cubic ease in-out
        t = i / steps
        if t < 0.5:
            ease = 4 * t * t * t
        else:
            ease = 1 - pow(-2 * t + 2, 3) / 2
        p = start_pan + (target_pan - start_pan) * ease
        t_val = start_tilt + (target_tilt - start_tilt) * ease
        pan, tilt = set_ptz(p, t_val)
        time.sleep(pause)


def play_sound_blocking(name):
    """Play a sound and wait for it to finish."""
    path = f"{SOUNDS_DIR}/{name}.wav"
    if os.path.exists(path):
        subprocess.run(
            ["aplay", "-D", SPEAKER_DEVICE, "-q", path],
            capture_output=True, timeout=15)


def do_seek(cap, yunet):
    """
    The North Star behavior: "Hey, Ezra."

    Systematically scan the room looking for a face.
    If found → lock on, say "Hey, Ezra. There you are."
    If not found → say "Hey, Ezra? I can't find you."

    Returns True if face was found, False if not.
    """
    global pan, tilt, state, tracking_start, last_seek, sx, sy
    last_seek = time.monotonic()

    print("[tracker] SEEKING — looking for Ezra...")

    for pos_pan_deg, pos_tilt_deg in SEEK_PAN_POSITIONS:
        if not running:
            return False

        target_pan = int(pos_pan_deg * 3600)
        target_tilt = int(pos_tilt_deg * 3600)

        # Ease to position (Disney Principle 6)
        ease_to(target_pan, target_tilt, steps=6, pause=0.03)
        pan, tilt = target_pan, target_tilt

        # Hold and check for face
        check_start = time.monotonic()
        while (time.monotonic() - check_start) < SEEK_HOLD_TIME:
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.01)
                continue

            small = cv2.resize(frame, DETECT_SIZE)
            yunet.setInputSize(DETECT_SIZE)
            _, faces = yunet.detect(small)

            if faces is not None and len(faces) > 0:
                # Found a face!
                f_idx = max(range(len(faces)), key=lambda i: faces[i][14])
                cx = (faces[f_idx][0] + faces[f_idx][2] / 2) / DETECT_SIZE[0]
                cy = (faces[f_idx][1] + faces[f_idx][3] / 2) / DETECT_SIZE[1]
                sx, sy = cx, cy

                print(f"[tracker] FOUND EZRA at ({pos_pan_deg}°, {pos_tilt_deg}°)!")

                # Play greeting
                greeting = random.choice(SEEK_GREETINGS_FOUND)
                play_sound_blocking(greeting)
                print(f"[tracker] Said: {greeting}")

                # Transition to tracking
                state = State.TRACKING
                tracking_start = time.monotonic()
                return True

            time.sleep(0.05)

        print(f"[tracker] seek: ({pos_pan_deg}°, {pos_tilt_deg}°) — no face")

    # Full sweep done, no face found
    print("[tracker] SEEK COMPLETE — Ezra not found")
    ease_to(0, 0, steps=8, pause=0.03)
    pan, tilt = 0, 0

    # Call out
    callout = random.choice(SEEK_GREETINGS_LOST)
    play_sound_blocking(callout)
    print(f"[tracker] Called out: {callout}")

    return False


def do_startle():
    """
    Startle reflex. Disney Principles:
      2. Anticipation — none (startles are sudden)
      5. Follow Through — overshoot then settle back
      10. Exaggeration — snap hard
    """
    global pan, tilt, last_startle
    now = time.monotonic()
    if now - last_startle < STARTLE_COOLDOWN:
        return
    last_startle = now

    # Snap opposite of current heading (looks surprised)
    snap_pan = -pan if abs(pan) > 3600 else STARTLE_SNAP_DEGREES * 3600 * random.choice([-1, 1])
    snap_tilt = -abs(tilt) - 10 * 3600  # tilt up (startled jump)

    print(f'[tracker] STARTLE! snap to ({snap_pan/3600:.0f}°, {snap_tilt/3600:.0f}°)')
    # 1. Hard snap (fast, no easing)
    pan, tilt = set_ptz(snap_pan, snap_tilt)
    play_sound("wake")
    time.sleep(0.3)

    # 2. Quick search (dart left-right)
    for offset in [15 * 3600, -30 * 3600, 15 * 3600]:
        pan, tilt = set_ptz(pan + offset, tilt)
        time.sleep(0.15)

    # 3. Settle back to home (eased — Principle 6)
    time.sleep(0.3)
    ease_to(0, 0, steps=8, pause=0.03)


def do_glance():
    """
    Saccadic glance. Disney Principles:
      2. Anticipation — tiny dip before snap
      6. Slow In/Slow Out — fast snap, slow return
      7. Arc — curved path back
      8. Secondary Action — sound plays with glance
    """
    global pan, tilt, last_glance
    last_glance = time.monotonic()

    # Pick a random point — big range, especially vertical (Principle 10: Exaggeration)
    target_pan = random.randint(-40, 40) * 3600
    target_tilt = random.randint(-25, 25) * 3600

    # Anticipation: tiny dip opposite direction (Principle 2)
    anti_pan = pan - (target_pan - pan) * 0.08
    anti_tilt = tilt - (target_tilt - tilt) * 0.08
    set_ptz(anti_pan, anti_tilt)
    time.sleep(0.06)

    # Fast snap to target (Principle 9: fast = curious)
    pan, tilt = set_ptz(target_pan, target_tilt)
    print(f'[tracker] glance → ({target_pan/3600:.0f}°, {target_tilt/3600:.0f}°)')

    # Play a sound most of the time (Secondary Action, Principle 8)
    if random.random() < 0.7:
        sound = get_idle_sound()
        if sound:
            play_sound(sound)

    # Hold and look
    time.sleep(random.uniform(1.5, 3.0))

    # Slow return to center (eased — Principle 6)
    ease_to(0, 0, steps=10, pause=0.05)


# ── Arguments ──────────────────────────────────────────────

_parser = argparse.ArgumentParser(description='Merlin Face Tracker')
_parser.add_argument('--record', type=str, nargs='?', const='~/merlin-pov.mp4',
                     help='Record POV footage (optionally specify output path)')
_parser.add_argument('--record-duration', type=int, default=0,
                     help='Auto-stop recording after N seconds (0 = manual stop)')
_args = _parser.parse_args()

_record_file = None
if _args.record:
    _record_file = os.path.expanduser(_args.record)
    if not _record_file.endswith('.mp4'):
        _record_file += '.mp4'

# ── Main loop ───────────────────────────────────────────────

yunet = cv2.FaceDetectorYN.create(YUNET_MODEL, '', DETECT_SIZE, 0.5, 0.3, 5000)
cap = cv2.VideoCapture(CAMERA_INDEX)

if _record_file:
    # 1080p MJPEG for recording quality
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
    cap.set(cv2.CAP_PROP_FPS, 30)
else:
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

# Recording setup
_writer = None
_audio_proc = None
_record_start = None
_video_tmp = None
_audio_tmp = None
_rec_frames = 0

if _record_file:
    actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    _video_tmp = _record_file.replace('.mp4', '_tmp.avi')
    _audio_tmp = _record_file.replace('.mp4', '_tmp.wav')

    _writer = cv2.VideoWriter(_video_tmp, cv2.VideoWriter_fourcc(*'MJPG'),
                               30, (actual_w, actual_h))

    # Start audio recording from PIXY mic
    try:
        _audio_proc = subprocess.Popen(
            ['arecord', '-D', 'plughw:3,0', '-f', 'S16_LE', '-r', '48000', '-c', '1', _audio_tmp],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f'[tracker] Audio recording failed to start: {e}')
        _audio_proc = None

    _record_start = time.monotonic()
    print(f'[tracker] REC {actual_w}x{actual_h} + audio -> {_record_file}')

# Snapshot: save latest frame for remote verification
SNAPSHOT_PATH = "/tmp/merlin-snapshot.jpg"
SNAPSHOT_INTERVAL = 2  # seconds
_last_snapshot = 0

def save_snapshot(frame):
    """Save current frame to disk for remote viewing."""
    global _last_snapshot
    now = time.monotonic()
    if now - _last_snapshot > SNAPSHOT_INTERVAL:
        cv2.imwrite(SNAPSHOT_PATH, frame)
        _last_snapshot = now

print('[tracker] Merlin tracker online. "Hey, Ezra" behavior loaded.')
print(f'[tracker] Track: {TRACKING_DURATION}s | Linger: {LINGER_DURATION}s | Seek every: {SEEK_INTERVAL}s | Breathing: {BREATHING_AMPLITUDE}°')
if _record_file:
    dur = f'{_args.record_duration}s' if _args.record_duration else 'manual stop'
    print(f'[tracker] Recording active ({dur}). Ctrl+C or SIGTERM to stop.')

try:
    while running:
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.01)
            continue
        frames += 1
        now = time.monotonic()

        # ── Recording ───────────────────────────────
        # Handle voice-command record start
        if _record_pending == "record_start" and not _writer:
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            _record_file = f"/home/pi/merlin-pov-{ts}.mp4"
            _video_tmp = _record_file.replace('.mp4', '_tmp.avi')
            _audio_tmp = None
            actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            _writer = cv2.VideoWriter(_video_tmp, cv2.VideoWriter_fourcc(*'MJPG'),
                                       30, (actual_w, actual_h))
            _record_start = time.monotonic()
            _rec_frames = 0
            play_sound("open")
            print(f'[tracker] REC START {actual_w}x{actual_h} → {_record_file}')
            _record_pending = None
        # Handle voice-command record stop
        elif _record_pending == "record_stop" and _writer:
            _writer.release()
            _writer = None
            play_sound("close")
            print(f'[tracker] REC STOP ({_rec_frames} frames). Encoding...')
            result = subprocess.run(
                ['ffmpeg', '-y', '-i', _video_tmp,
                 '-c:v', 'libx264', '-preset', 'fast', '-crf', '23', _record_file],
                capture_output=True, timeout=300)
            if result.returncode == 0:
                size_mb = os.path.getsize(_record_file) / (1024 * 1024)
                os.remove(_video_tmp)
                print(f'[tracker] REC SAVED: {_record_file} ({size_mb:.1f}MB)')
            else:
                print(f'[tracker] REC merge failed. Raw: {_video_tmp}')
            _record_pending = None
        else:
            _record_pending = None

        # Write frames while recording
        if _writer:
            _writer.write(frame)
            _rec_frames += 1
            if _args.record_duration > 0 and (now - _record_start) > _args.record_duration:
                print(f'[tracker] Recording duration {_args.record_duration}s reached')
                running = False
                continue
            if _rec_frames % 300 == 0:
                elapsed = int(now - _record_start)
                print(f'[tracker] REC {elapsed}s ({_rec_frames} frames)')

        # ── Quiet hours check ────────────────────────
        hour = datetime.datetime.now().hour
        if hour >= QUIET_START or hour < QUIET_END:
            # Sleep mode — no tracking, no sounds, just breathe very slowly
            if state != State.IDLE or now - last_log > 60:
                if state != State.IDLE:
                    set_ptz(0, 0)
                    pan, tilt = 0, 0
                    state = State.IDLE
                    idle_start = now
                print(f'[tracker] QUIET HOURS ({hour}:00) — sleeping')
                last_log = now
            time.sleep(1)
            continue

        # ── Save snapshot for remote verification ──
        save_snapshot(frame)

        # ── Face detection ──────────────────────────
        small = cv2.resize(frame, DETECT_SIZE)
        yunet.setInputSize(DETECT_SIZE)
        _, faces = yunet.detect(small)
        face_found = faces is not None and len(faces) > 0

        # ── Startle check (any state) ───────────────
        if startle_pending:
            startle_pending = False
            do_startle()
            continue

        # ── FACE FOUND ──────────────────────────────
        if face_found:
            f = max(range(len(faces)), key=lambda i: faces[i][14])
            cx = (faces[f][0] + faces[f][2] / 2) / DETECT_SIZE[0]
            cy = (faces[f][1] + faces[f][3] / 2) / DETECT_SIZE[1]
            lost_at = None

            # Start tracking from any non-tracking state (but not during seek — seek handles itself)
            if state == State.SEEKING:
                pass  # do_seek handles face detection internally
            elif state != State.TRACKING:
                was_idle = state == State.IDLE
                time_in_idle = (now - idle_start) if idle_start else 0
                state = State.TRACKING
                tracking_start = now
                sx, sy = cx, cy

                # Only chime if we've been idle for 2+ minutes (real absence, not just the 60s cycle)
                if was_idle and time_in_idle > 120:
                    print(f'[tracker] Face found after {int(time_in_idle)}s absence — welcome back')
                    play_sound("spotted")
                else:
                    print(f'[tracker] Face found — tracking (silent re-engage)')

            if state == State.TRACKING:
                # Check if it's time to stop
                if (now - tracking_start) > TRACKING_DURATION:
                    # Done looking at you for now. Go idle.
                    state = State.IDLE
                    idle_start = now
                    last_search = now
                    print(f'[tracker] Tracked {TRACKING_DURATION}s — going idle')
                    ease_to(0, 0, steps=8, pause=0.03)
                    pan, tilt = 0, 0
                else:
                    # Still tracking — smooth follow
                    sx = SMOOTH * cx + (1 - SMOOTH) * sx
                    sy = SMOOTH * cy + (1 - SMOOTH) * sy
                    ex = sx - 0.5
                    ey = sy - 0.5

                    if now - last_ptz > 0.1:
                        moved = False
                        if abs(ex) > DEADBAND:
                            step = int(3600 + abs(ex) * 46800)
                            pan += PAN_SIGN * step if ex > 0 else -PAN_SIGN * step
                            moved = True
                        if abs(ey) > DEADBAND:
                            step = int(3600 + abs(ey) * 46800)
                            tilt += TILT_SIGN * step if ey > 0 else -TILT_SIGN * step
                            moved = True
                        if moved:
                            pan, tilt = set_ptz(pan, tilt)
                            last_ptz = now

                    last_face_pan = pan
                    last_face_tilt = tilt

                    if now - last_log > 2.0:
                        elapsed = int(now - tracking_start)
                        print(f'[tracker] TRACKING ({elapsed}/{TRACKING_DURATION}s) ptz=({pan/3600:.0f}°,{tilt/3600:.0f}°)')
                        last_log = now

            # Post frame to vision
            if now - last_vision_post > VISION_INTERVAL:
                _, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                threading.Thread(target=post_frame_to_vision,
                                 args=(jpeg.tobytes(),), daemon=True).start()
                last_vision_post = now

        # ── NO FACE ─────────────────────────────────
        else:
            # If we were tracking and lost the face, linger
            if state == State.TRACKING:
                if lost_at is None:
                    lost_at = now
                if (now - lost_at) > LINGER_DURATION:
                    state = State.IDLE
                    idle_start = now
                    last_search = now
                    print(f'[tracker] Lingered {LINGER_DURATION}s — going idle')
                    ease_to(0, 0, steps=8, pause=0.03)
                    pan, tilt = 0, 0
                elif state == State.TRACKING and now - last_log > 2.0:
                    remaining = int(LINGER_DURATION - (now - lost_at))
                    print(f'[tracker] LINGER — waiting {remaining}s')
                    last_log = now

            # If searching and no face found, let search timeout handle it
            elif state == State.SEARCHING:
                if (now - search_start) > SEARCH_DURATION:
                    state = State.IDLE
                    idle_start = now
                    last_search = now
                    last_glance = now
                    print('[tracker] Search timeout — back to idle')
                    ease_to(0, 0, steps=8, pause=0.03)
                    pan, tilt = 0, 0

            # IDLE behaviors
            if state == State.IDLE:
                idle_duration = now - idle_start if idle_start else 0

                # "Hey, Ezra" — proactive face search (the North Star)
                if (now - last_seek) > SEEK_INTERVAL:
                    state = State.SEEKING
                    found = do_seek(cap, yunet)
                    if not found:
                        state = State.IDLE
                        idle_start = time.monotonic()
                    # else: do_seek already set state to TRACKING
                    continue  # skip rest of loop, re-enter main loop

                # Breathing
                elif idle_duration > IDLE_BREATHING_DELAY:
                    t = now
                    p1 = math.sin(2 * math.pi * BREATHING_SPEED * t)
                    p2 = 0.4 * math.sin(2 * math.pi * BREATHING_SPEED * 1.7 * t)
                    t1 = math.sin(2 * math.pi * BREATHING_SPEED * 0.6 * t)
                    t2 = 0.3 * math.sin(2 * math.pi * BREATHING_SPEED * 2.1 * t)

                    breath_pan = int(BREATHING_AMPLITUDE * 3600 * (p1 + p2))
                    breath_tilt = int(BREATHING_AMPLITUDE * 0.6 * 3600 * (t1 + t2))
                    set_ptz(breath_pan, breath_tilt)

                    if now - last_log > 3.0:
                        print(f'[tracker] IDLE breathing pan={breath_pan/3600:.1f}° tilt={breath_tilt/3600:.1f}°')
                        last_log = now

                # Idle sounds — quiet tinks while breathing
                if (now - last_idle_sound) > next_idle_sound:
                    sound = get_idle_sound()
                    if sound:
                        play_sound(sound)
                        print(f'[tracker] ♪ {sound}')
                    last_idle_sound = now
                    next_idle_sound = random.uniform(IDLE_SOUND_MIN, IDLE_SOUND_MAX)

                # Saccadic glances
                if idle_duration > IDLE_BREATHING_DELAY and (now - last_glance) > random.uniform(GLANCE_MIN_INTERVAL, GLANCE_MAX_INTERVAL):
                    do_glance()

        time.sleep(0.01)

finally:
    # Stop recording and merge
    if _writer:
        _writer.release()
        print(f'[tracker] Video closed ({_rec_frames} frames)')
    if _audio_proc:
        _audio_proc.terminate()
        _audio_proc.wait()
        print('[tracker] Audio closed')
    if _record_file and _video_tmp and os.path.exists(_video_tmp):
        print('[tracker] Merging video + audio (H.264)...')
        merge_cmd = ['ffmpeg', '-y', '-i', _video_tmp]
        if _audio_tmp and os.path.exists(_audio_tmp):
            merge_cmd += ['-i', _audio_tmp]
        merge_cmd += ['-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
                      '-c:a', 'aac', '-b:a', '128k', _record_file]
        result = subprocess.run(merge_cmd, capture_output=True, timeout=300)
        if result.returncode == 0:
            size_mb = os.path.getsize(_record_file) / (1024 * 1024)
            print(f'[tracker] Recording saved: {_record_file} ({size_mb:.1f}MB)')
            os.remove(_video_tmp)
            if _audio_tmp and os.path.exists(_audio_tmp):
                os.remove(_audio_tmp)
        else:
            print(f'[tracker] Merge failed. Raw files: {_video_tmp} {_audio_tmp}')
            print(result.stderr.decode()[-500:] if result.stderr else '')

    set_ptz(0, 0)
    cap.release()
    print(f'[tracker] Done. {frames} frames.')
