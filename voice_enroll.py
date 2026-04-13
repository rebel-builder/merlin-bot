#!/usr/bin/env python3
"""
Merlin Voice Enrollment — capture voice samples for speaker recognition.

Records 30 seconds of speech from the PIXY mic. Just talk naturally —
read something, tell a story, say whatever. One person per recording.

Usage:
    python3 voice_enroll.py ezra
    python3 voice_enroll.py nate
    python3 voice_enroll.py mel
"""

import os
import subprocess
import sys
import time

VOICES_DIR = "/home/pi/RBOS/merlin/voices"
MIC_DEVICE = "plughw:3,0"
SAMPLE_RATE = 16000
RECORD_SECONDS = 120  # 2 minutes — more audio = stronger voice signature


def enroll(name):
    person_dir = os.path.join(VOICES_DIR, name.lower())
    os.makedirs(person_dir, exist_ok=True)

    existing = len([f for f in os.listdir(person_dir) if f.endswith('.wav')])
    filename = f"{name.lower()}_{existing + 1:03d}.wav"
    filepath = os.path.join(person_dir, filename)

    print(f"\n{'='*50}")
    print(f"  Voice Enrollment: {name}")
    print(f"{'='*50}")
    print(f"\nExisting recordings: {existing}")
    print(f"Recording: {RECORD_SECONDS} seconds")
    print(f"\nJust talk naturally for {RECORD_SECONDS} seconds.")
    print("Say anything — tell a story, describe your day,")
    print("read something out loud, count to 100, whatever.")
    print(f"\nRecording starts in 3 seconds...")
    time.sleep(3)

    print(f"\n>>> RECORDING — talk now! ({RECORD_SECONDS}s)")

    try:
        result = subprocess.run(
            ["arecord", "-D", MIC_DEVICE, "-f", "S16_LE",
             "-r", str(SAMPLE_RATE), "-c", "1", "-t", "wav",
             "-d", str(RECORD_SECONDS), filepath],
            capture_output=True, timeout=RECORD_SECONDS + 5,
        )

        if result.returncode == 0 and os.path.exists(filepath):
            size = os.path.getsize(filepath)
            print(f"\n>>> DONE — saved {filename} ({size // 1024} KB)")
            print(f"    Path: {filepath}")
        else:
            print(f"\nRecording failed: {result.stderr.decode()[:200]}")
            return

    except subprocess.TimeoutExpired:
        print(f"\nRecording timed out but file may still be saved.")

    total = len([f for f in os.listdir(person_dir) if f.endswith('.wav')])
    print(f"\nTotal recordings for {name}: {total}")

    if total >= 2:
        print(f"\nReady to train! Run: python3 voice_train.py")
    else:
        print(f"\nOne more recording recommended for better accuracy.")
        print(f"Run again: python3 voice_enroll.py {name}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 voice_enroll.py <name>")
        print("  e.g.: python3 voice_enroll.py nate")
        sys.exit(1)

    enroll(sys.argv[1])
