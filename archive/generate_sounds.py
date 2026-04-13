#!/usr/bin/env python3
"""Generate short nonverbal sound clips for Merlin using ElevenLabs TTS API."""

import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    print("Installing python-dotenv...")
    os.system(f"{sys.executable} -m pip install python-dotenv -q")
    from dotenv import load_dotenv

try:
    import requests
except ImportError:
    print("Installing requests...")
    os.system(f"{sys.executable} -m pip install requests -q")
    import requests

# Load API key from .env
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(env_path)

API_KEY = os.getenv("ELEVENLABS_API_KEY")
if not API_KEY:
    print(f"ERROR: ELEVENLABS_API_KEY not found in {env_path}")
    sys.exit(1)

VOICE_ID = "iP95p4xoKVk53GoZ742B"
MODEL_ID = "eleven_flash_v2_5"
SOUNDS_DIR = Path(__file__).resolve().parent / "sounds"
SOUNDS_DIR.mkdir(exist_ok=True)

SOUNDS = {
    "oho.mp3": "Oho",
    "hmm.mp3": "Hmm",
    "mmhmm.mp3": "Mm-hmm",
    "huh.mp3": "Huh",
}

URL = f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}"

HEADERS = {
    "xi-api-key": API_KEY,
    "Content-Type": "application/json",
    "Accept": "audio/mpeg",
}


def generate_sound(filename: str, text: str) -> bool:
    payload = {
        "text": text,
        "model_id": MODEL_ID,
        "voice_settings": {
            "stability": 0.80,
            "similarity_boost": 0.70,
        },
    }

    print(f"Generating {filename} (text: '{text}')...", end=" ")
    resp = requests.post(URL, json=payload, headers=HEADERS, timeout=30)

    if resp.status_code != 200:
        print(f"FAILED (HTTP {resp.status_code}): {resp.text[:200]}")
        return False

    out_path = SOUNDS_DIR / filename
    out_path.write_bytes(resp.content)
    size = out_path.stat().st_size
    print(f"OK ({size:,} bytes)")
    return True


def main():
    print(f"Output directory: {SOUNDS_DIR}")
    print(f"Voice ID: {VOICE_ID}")
    print(f"Model: {MODEL_ID}")
    print()

    success = 0
    for filename, text in SOUNDS.items():
        if generate_sound(filename, text):
            success += 1

    print(f"\nDone: {success}/{len(SOUNDS)} files generated.")

    # Verify
    print("\nVerification:")
    for filename in SOUNDS:
        path = SOUNDS_DIR / filename
        if path.exists():
            size = path.stat().st_size
            status = "OK" if size > 0 else "EMPTY"
            print(f"  {filename}: {size:,} bytes [{status}]")
        else:
            print(f"  {filename}: MISSING")


if __name__ == "__main__":
    main()
