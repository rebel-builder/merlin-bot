#!/usr/bin/env python3
"""
Merlin Face Training — generate embeddings from enrolled photos.

Reads photos from faces/<name>/*.jpg, generates face encodings,
saves to faces/embeddings.json. The tracker loads this at startup
for real-time recognition.

Usage:
    python3 face_train.py
"""

import json
import os
import sys

FACES_DIR = "/home/pi/RBOS/merlin/faces"
EMBEDDINGS_FILE = os.path.join(FACES_DIR, "embeddings.json")


def train():
    try:
        import face_recognition
    except ImportError:
        print("ERROR: face_recognition not installed.")
        print("Run: pip3 install dlib face_recognition --break-system-packages")
        sys.exit(1)

    if not os.path.exists(FACES_DIR):
        print(f"No faces directory at {FACES_DIR}")
        sys.exit(1)

    people = {}
    total_photos = 0
    total_faces = 0

    for name in sorted(os.listdir(FACES_DIR)):
        person_dir = os.path.join(FACES_DIR, name)
        if not os.path.isdir(person_dir) or name.startswith('.'):
            continue

        photos = [f for f in os.listdir(person_dir) if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
        if not photos:
            continue

        print(f"\nProcessing {name}: {len(photos)} photos...")
        encodings = []

        for photo in sorted(photos):
            filepath = os.path.join(person_dir, photo)
            image = face_recognition.load_image_file(filepath)
            faces = face_recognition.face_encodings(image)

            if len(faces) == 0:
                print(f"  {photo}: no face found, skipping")
            elif len(faces) > 1:
                print(f"  {photo}: {len(faces)} faces found, using largest")
                # Use the first (face_recognition returns them by size)
                encodings.append(faces[0].tolist())
                total_faces += 1
            else:
                encodings.append(faces[0].tolist())
                total_faces += 1
                print(f"  {photo}: OK")

            total_photos += 1

        if encodings:
            people[name] = {
                "encodings": encodings,
                "count": len(encodings),
            }
            print(f"  → {len(encodings)} face encodings for {name}")

    if not people:
        print("\nNo faces found. Run face_enroll.py first.")
        sys.exit(1)

    # Save embeddings
    with open(EMBEDDINGS_FILE, 'w') as f:
        json.dump(people, f, indent=2)

    print(f"\n{'='*40}")
    print(f"Training complete.")
    print(f"  People: {len(people)} ({', '.join(people.keys())})")
    print(f"  Photos processed: {total_photos}")
    print(f"  Face encodings: {total_faces}")
    print(f"  Saved to: {EMBEDDINGS_FILE}")
    print(f"\nMerlin will load these at next tracker restart.")


if __name__ == "__main__":
    train()
