#!/usr/bin/env python3
"""
Merlin Voice Training — generate speaker embeddings from voice samples.

Reads WAV files from voices/<name>/*.wav, generates speaker embeddings
using resemblyzer, saves to voices/voice_embeddings.json.

Usage:
    python3 voice_train.py
"""

import json
import os
import sys
import numpy as np

VOICES_DIR = "/home/pi/RBOS/merlin/voices"
EMBEDDINGS_FILE = os.path.join(VOICES_DIR, "voice_embeddings.json")


def train():
    try:
        from resemblyzer import VoiceEncoder, preprocess_wav
        from pathlib import Path
    except ImportError:
        print("ERROR: resemblyzer not installed.")
        print("Run: pip3 install resemblyzer --break-system-packages")
        sys.exit(1)

    print("Loading voice encoder model (first run downloads ~17MB)...")
    encoder = VoiceEncoder()

    if not os.path.exists(VOICES_DIR):
        print(f"No voices directory at {VOICES_DIR}")
        sys.exit(1)

    people = {}
    total_files = 0

    for name in sorted(os.listdir(VOICES_DIR)):
        person_dir = os.path.join(VOICES_DIR, name)
        if not os.path.isdir(person_dir) or name.startswith('.'):
            continue

        wavs = [f for f in os.listdir(person_dir) if f.endswith('.wav')]
        if not wavs:
            continue

        print(f"\nProcessing {name}: {len(wavs)} recordings...")
        all_embeds = []

        for wav_file in sorted(wavs):
            filepath = os.path.join(person_dir, wav_file)
            try:
                wav = preprocess_wav(Path(filepath))
                if len(wav) < 1600:  # less than 0.1s of audio
                    print(f"  {wav_file}: too short, skipping")
                    continue

                embed = encoder.embed_utterance(wav)
                all_embeds.append(embed)
                print(f"  {wav_file}: OK (embed shape: {embed.shape})")
                total_files += 1

            except Exception as e:
                print(f"  {wav_file}: error — {e}")

        if all_embeds:
            # Average all embeddings for this person → one representative vector
            avg_embed = np.mean(all_embeds, axis=0)
            people[name] = {
                "embedding": avg_embed.tolist(),
                "num_samples": len(all_embeds),
            }
            print(f"  → averaged {len(all_embeds)} embeddings for {name}")

    if not people:
        print("\nNo voice data found. Run voice_enroll.py first.")
        sys.exit(1)

    with open(EMBEDDINGS_FILE, 'w') as f:
        json.dump(people, f, indent=2)

    print(f"\n{'='*40}")
    print(f"Voice training complete.")
    print(f"  Speakers: {len(people)} ({', '.join(people.keys())})")
    print(f"  Recordings processed: {total_files}")
    print(f"  Saved to: {EMBEDDINGS_FILE}")
    print(f"\nMerlin will use these for speaker identification.")


if __name__ == "__main__":
    train()
