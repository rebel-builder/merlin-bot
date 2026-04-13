#!/usr/bin/env python3
"""Test the Amcrest camera speaker."""

import subprocess
import requests
import time
from requests.auth import HTTPDigestAuth

from config import CAMERA_IP, CAMERA_USER, CAMERA_PASS, CAMERA_AUTH

AUTH = CAMERA_AUTH
BASE = f"http://{CAMERA_IP}"

# Generate a 2-second test tone
print("Generating test tone...")
subprocess.run(
    ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
     "-f", "alaw", "-ar", "8000", "-ac", "1", "/tmp/test.al"],
    capture_output=True,
)

with open("/tmp/test.al", "rb") as f:
    audio = f.read()
print(f"Test tone: {len(audio)} bytes")

# Try multiple approaches

# Approach 1: Standard postAudio with file data
print("\n=== Approach 1: POST with file data ===")
r = requests.post(
    f"{BASE}/cgi-bin/audio.cgi?action=postAudio&httptype=singlepart&channel=1",
    data=audio,
    auth=AUTH,
    headers={"Content-Type": "Audio/G.711A"},
    timeout=10,
)
print(f"Result: {r.status_code} {r.text[:100]}")

# Approach 2: Try channel=0
print("\n=== Approach 2: channel=0 ===")
r = requests.post(
    f"{BASE}/cgi-bin/audio.cgi?action=postAudio&httptype=singlepart&channel=0",
    data=audio,
    auth=AUTH,
    headers={"Content-Type": "Audio/G.711A"},
    timeout=10,
)
print(f"Result: {r.status_code} {r.text[:100]}")

# Approach 3: Rate-limited streaming
print("\n=== Approach 3: Rate-limited streaming ===")
def gen():
    chunk_size = 320
    for i in range(0, len(audio), chunk_size):
        yield audio[i:i+chunk_size]
        time.sleep(0.04)

try:
    r = requests.post(
        f"{BASE}/cgi-bin/audio.cgi?action=postAudio&httptype=singlepart&channel=1",
        data=gen(),
        auth=AUTH,
        headers={"Content-Type": "Audio/G.711A", "Transfer-Encoding": "chunked"},
        timeout=10,
    )
    print(f"Result: {r.status_code} {r.text[:100]}")
except requests.exceptions.ReadTimeout:
    print("Timed out (might have worked — camera keeps connection open)")
except Exception as e:
    print(f"Error: {e}")

# Approach 4: Use ffmpeg to pipe to curl via subprocess
print("\n=== Approach 4: ffmpeg | curl with --limit-rate ===")
result = subprocess.run(
    ["bash", "-c",
     f'ffmpeg -f lavfi -i "sine=frequency=880:duration=2" -f alaw -ar 8000 -ac 1 pipe:1 2>/dev/null | '
     f'curl --digest -u {CAMERA_USER}:{CAMERA_PASS} '
     f'--limit-rate 8K '
     f'-H "Content-Type: Audio/G.711A" '
     f'-X POST '
     f'--data-binary @- '
     f'"http://{CAMERA_IP}/cgi-bin/audio.cgi?action=postAudio&httptype=singlepart&channel=1"'
    ],
    capture_output=True, text=True, timeout=15,
)
print(f"stdout: {result.stdout[:100]}")
print(f"stderr: {result.stderr[:200]}")
print(f"returncode: {result.returncode}")

# Approach 5: Try Amcrest's play endpoint
print("\n=== Approach 5: audio.cgi?action=play ===")
try:
    r = requests.post(
        f"{BASE}/cgi-bin/audio.cgi?action=play&channel=1",
        data=audio,
        auth=AUTH,
        headers={"Content-Type": "Audio/G.711A"},
        timeout=5,
    )
    print(f"Result: {r.status_code} {r.text[:100]}")
except Exception as e:
    print(f"Error: {e}")

# Approach 6: List all audio capabilities
print("\n=== Audio capabilities ===")
r = requests.get(f"{BASE}/cgi-bin/audio.cgi?action=getCaps", auth=AUTH, timeout=5)
print(f"getCaps: {r.status_code} {r.text[:300]}")

r = requests.get(f"{BASE}/cgi-bin/audio.cgi?action=getAudioInfo", auth=AUTH, timeout=5)
print(f"getAudioInfo: {r.status_code} {r.text[:300]}")
