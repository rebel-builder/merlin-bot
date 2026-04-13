#!/usr/bin/env python3
"""Merlin Senses — runs on Pi. Eyes, ears, mouth via the Amcrest camera."""

import cv2
import json
import time
import asyncio
import subprocess
import threading
import websockets
import requests
from requests.auth import HTTPDigestAuth
import struct
import base64
import argparse
import signal
import sys
from datetime import datetime
import gestures

# Camera config — imported from config.py (secrets in .env)
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import CAMERA_IP, CAMERA_RTSP_MAIN, CAMERA_RTSP_SUB

RTSP_MAIN = CAMERA_RTSP_MAIN
RTSP_SUB = CAMERA_RTSP_SUB

# Detection config
DETECT_INTERVAL = 0.5
PRESENCE_TIMEOUT = 10.0
MIN_FACE_SIZE = 40

# Audio config — go2rtc handles speaker output via ONVIF backchannel
GO2RTC_URL = "http://localhost:1984"
GO2RTC_RTSP = "rtsp://localhost:8554/merlin"  # go2rtc RTSP re-stream (for mic)
GO2RTC_STREAM = "merlin"

# Mic config
MIC_CHUNK_SECONDS = 4
MIC_SAMPLE_RATE = 16000
MIC_CHUNK_BYTES = MIC_SAMPLE_RATE * 2 * MIC_CHUNK_SECONDS  # 128000
MIC_RMS_THRESHOLD = 300  # voice activity threshold

# Brain connection
BRAIN_PORT = 8900

# State
last_face_time = 0
person_present = False
speaking = False
running = True
idle_stop = None
mic_proc = None  # persistent ffmpeg for mic


# ── Detection ──────────────────────────────────────────────────────

def create_detector():
    cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    detector = cv2.CascadeClassifier(cascade_path)
    if detector.empty():
        print("[senses] ERROR: Could not load face cascade")
        sys.exit(1)
    print("[senses] Face detector loaded (Haar cascade)")
    return detector


def detect_faces(frame, detector):
    small = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)
    faces = detector.detectMultiScale(
        gray, scaleFactor=1.2, minNeighbors=5,
        minSize=(MIN_FACE_SIZE, MIN_FACE_SIZE),
    )
    if len(faces) > 0:
        return [(int(x * 2), int(y * 2), int(w * 2), int(h * 2)) for (x, y, w, h) in faces]
    return []


# ── Camera Speaker (mouth) ─────────────────────────────────────────

def play_audio_on_camera(audio_bytes, fmt="mp3"):
    """Send audio to camera speaker via go2rtc ONVIF backchannel."""
    global speaking
    speaking = True

    try:
        audio_path = f"/tmp/merlin_speak.{fmt}"
        with open(audio_path, "wb") as f:
            f.write(audio_bytes)

        src = f"ffmpeg:{audio_path}#audio=pcma#input=file"
        r = requests.post(
            f"{GO2RTC_URL}/api/streams",
            params={"dst": GO2RTC_STREAM, "src": src},
            timeout=10,
        )
        print(f"[senses] go2rtc speak: {r.status_code}")

        # Wait for playback to finish
        # ElevenLabs MP3 ~48kbps → duration ≈ bytes/6000, plus pipeline delay
        duration = max(len(audio_bytes) / 6000, 1.0) + 2.0
        time.sleep(duration)

    except Exception as e:
        print(f"[senses] Speaker error: {e}")
    finally:
        speaking = False


# ── Persistent Mic Capture ─────────────────────────────────────────

async def start_mic_process():
    """Start a persistent ffmpeg process reading from go2rtc RTSP re-stream.
    Returns the process. Audio comes from stdout as raw s16le PCM."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-rtsp_transport", "tcp",
        "-i", GO2RTC_RTSP,
        "-vn", "-acodec", "pcm_s16le", "-ar", str(MIC_SAMPLE_RATE), "-ac", "1",
        "-f", "s16le", "pipe:1",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,  # capture stderr for debugging
    )
    print(f"[senses] Mic process started (PID {proc.pid}) — persistent RTSP re-stream")
    return proc


async def kill_mic_process(proc):
    """Clean shutdown of mic ffmpeg process."""
    if proc and proc.returncode is None:
        proc.kill()
        await proc.wait()


# ── Event helpers ──────────────────────────────────────────────────

def make_event(event_type, data=None):
    return {
        "type": event_type,
        "timestamp": datetime.now().isoformat(),
        "source": "senses",
        "data": data or {},
    }


# ── Main loops ─────────────────────────────────────────────────────

async def perception_loop(brain_uri, detector):
    """Main loop: detect faces, capture audio, send events, handle commands."""
    global last_face_time, person_present, running, idle_stop, mic_proc

    print(f"[senses] Connecting to brain at {brain_uri}")

    while running:
        try:
            async with websockets.connect(brain_uri, ping_interval=20) as ws:
                print("[senses] Connected to brain")
                await ws.send(json.dumps(make_event("senses_online", {
                    "capabilities": ["face_detection", "camera_ptz", "camera_mic", "camera_speaker"],
                    "camera": CAMERA_IP,
                })))

                # Listen for commands from brain
                async def listen_commands():
                    try:
                        async for msg in ws:
                            cmd = json.loads(msg)
                            await handle_command(cmd)
                    except websockets.ConnectionClosed:
                        pass

                cmd_task = asyncio.create_task(listen_commands())

                async def forward_mic_audio():
                    """Read audio chunks from persistent ffmpeg, detect voice, forward to brain.
                    Auto-restarts ffmpeg if stream drops."""
                    global mic_proc
                    while running:
                        # Start/restart mic process if needed
                        if mic_proc is None or mic_proc.returncode is not None:
                            await kill_mic_process(mic_proc)
                            mic_proc = await start_mic_process()
                            await asyncio.sleep(1)  # let ffmpeg connect

                        # Skip audio while speaking (echo suppression)
                        if speaking:
                            try:
                                await asyncio.wait_for(
                                    mic_proc.stdout.read(MIC_CHUNK_BYTES), timeout=0.5
                                )
                            except (asyncio.TimeoutError, Exception):
                                pass
                            await asyncio.sleep(0.2)
                            continue

                        # Read one chunk (4 seconds of audio)
                        try:
                            pcm = await asyncio.wait_for(
                                mic_proc.stdout.readexactly(MIC_CHUNK_BYTES),
                                timeout=15,
                            )
                        except asyncio.IncompleteReadError:
                            stderr_data = b""
                            if mic_proc.stderr:
                                try:
                                    stderr_data = await asyncio.wait_for(mic_proc.stderr.read(500), timeout=1)
                                except Exception:
                                    pass
                            print(f"[senses] Mic stream ended: {stderr_data.decode(errors='ignore')[-200:]}")
                            await kill_mic_process(mic_proc)
                            mic_proc = None
                            await asyncio.sleep(2)
                            continue
                        except asyncio.TimeoutError:
                            print("[senses] Mic read timeout, restarting ffmpeg...")
                            await kill_mic_process(mic_proc)
                            mic_proc = None
                            await asyncio.sleep(2)
                            continue
                        except Exception as e:
                            print(f"[senses] Mic error: {e}")
                            await kill_mic_process(mic_proc)
                            mic_proc = None
                            await asyncio.sleep(2)
                            continue

                        # Voice activity detection
                        samples = struct.unpack(f"{len(pcm)//2}h", pcm)
                        rms = (sum(s * s for s in samples) / len(samples)) ** 0.5

                        if rms > MIC_RMS_THRESHOLD:
                            max_amp = max(abs(s) for s in samples)
                            event = make_event("audio_chunk", {
                                "audio_b64": base64.b64encode(pcm).decode(),
                                "sample_rate": MIC_SAMPLE_RATE,
                                "channels": 1,
                                "duration": MIC_CHUNK_SECONDS,
                                "rms": int(rms),
                                "max_amplitude": max_amp,
                            })
                            print(f"[senses] Voice detected (rms={int(rms)}, max={max_amp}), sending to brain")
                            try:
                                await ws.send(json.dumps(event))
                            except websockets.ConnectionClosed:
                                break

                mic_task = asyncio.create_task(forward_mic_audio())

                # No video/face detection here — tracker.py handles that.
                # Senses is audio-only: mic capture + brain communication.
                print("[senses] Audio-only mode (tracker.py handles vision + PTZ)")

                try:
                    while running:
                        await asyncio.sleep(1)

                finally:
                    cmd_task.cancel()
                    mic_task.cancel()
                    await kill_mic_process(mic_proc)

        except (ConnectionRefusedError, OSError, websockets.ConnectionClosed) as e:
            print(f"[senses] Brain connection lost ({e}), retrying in 5s...")
            await kill_mic_process(mic_proc)
            await asyncio.sleep(5)


async def handle_command(cmd):
    """Handle commands from the brain."""
    cmd_type = cmd.get("type")
    data = cmd.get("data", {})

    if cmd_type == "speak_audio":
        audio_bytes = base64.b64decode(data.get("audio_b64", ""))
        fmt = data.get("format", "mp3")
        print(f"[senses] Speaking ({len(audio_bytes)} bytes, {fmt})")
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, play_audio_on_camera, audio_bytes)

    elif cmd_type == "ptz_move":
        print(f"[senses] PTZ: {data}")


def signal_handler(sig, frame):
    global running
    print("\n[senses] Shutting down...")
    running = False


def main():
    parser = argparse.ArgumentParser(description="Merlin Senses — Pi perception layer")
    parser.add_argument("--brain", required=True, help="Brain host (Mac IP or Tailscale IP)")
    parser.add_argument("--port", type=int, default=BRAIN_PORT, help="Brain websocket port")
    args = parser.parse_args()

    brain_uri = f"ws://{args.brain}:{args.port}"

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    detector = create_detector()
    asyncio.run(perception_loop(brain_uri, detector))


if __name__ == "__main__":
    main()
