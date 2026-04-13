#!/usr/bin/env python3
"""
Merlin Face Enrollment — capture training photos for face recognition.

Run on Pi while sitting in front of the PIXY camera.
Takes 15 photos over ~30 seconds — move your head slightly between captures
(straight, left, right, up, down, tilt) for better recognition.

Usage:
    python3 face_enroll.py ezra
    python3 face_enroll.py nate
    python3 face_enroll.py mel
"""

import cv2
import os
import sys
import time

# Where to save face photos
FACES_DIR = "/home/pi/RBOS/merlin/faces"
NUM_PHOTOS = 15
CAPTURE_INTERVAL = 2.0  # seconds between captures

def find_camera():
    """Find the PIXY capture device."""
    for dev in [0, 1, 2]:
        cap = cv2.VideoCapture(dev)
        if cap.isOpened():
            ret, frame = cap.read()
            if ret and frame is not None and frame.shape[0] > 100:
                print(f"Camera found at /dev/video{dev}")
                return cap
            cap.release()
    return None


def enroll(name):
    person_dir = os.path.join(FACES_DIR, name.lower())
    os.makedirs(person_dir, exist_ok=True)

    existing = len([f for f in os.listdir(person_dir) if f.endswith('.jpg')])
    print(f"\nEnrolling: {name}")
    print(f"Existing photos: {existing}")
    print(f"Will capture {NUM_PHOTOS} new photos.")
    print(f"\nLook at the camera. Move your head slightly between shots:")
    print("  - Straight ahead, slight left, slight right")
    print("  - Slight up, slight down, slight tilt")
    print("  - With glasses / without if you wear them")
    print(f"\nStarting in 3 seconds...")
    time.sleep(3)

    # Try to use the camera — but the tracker might have it locked
    # In that case, read from the tracker's snapshot
    cap = find_camera()
    use_snapshot = False

    if cap is None:
        print("Camera is locked by tracker. Using tracker snapshots instead.")
        print("(Photos will be lower quality but should work)")
        use_snapshot = True

    captured = 0
    for i in range(NUM_PHOTOS):
        if use_snapshot:
            # Read from tracker's saved snapshot
            snapshot = "/tmp/merlin-snapshot.jpg"
            if os.path.exists(snapshot):
                frame = cv2.imread(snapshot)
                if frame is None:
                    print(f"  [{i+1}/{NUM_PHOTOS}] Snapshot unreadable, skipping...")
                    time.sleep(CAPTURE_INTERVAL)
                    continue
            else:
                print(f"  [{i+1}/{NUM_PHOTOS}] No snapshot available, waiting...")
                time.sleep(CAPTURE_INTERVAL)
                continue
        else:
            ret, frame = cap.read()
            if not ret:
                print(f"  [{i+1}/{NUM_PHOTOS}] Frame capture failed, skipping...")
                time.sleep(CAPTURE_INTERVAL)
                continue

        filename = f"{name.lower()}_{existing + captured + 1:03d}.jpg"
        filepath = os.path.join(person_dir, filename)
        cv2.imwrite(filepath, frame)
        captured += 1
        print(f"  [{i+1}/{NUM_PHOTOS}] Captured {filename}")

        if i < NUM_PHOTOS - 1:
            print(f"           Move your head slightly... ({CAPTURE_INTERVAL:.0f}s)")
            time.sleep(CAPTURE_INTERVAL)

    if cap is not None:
        cap.release()

    print(f"\nDone. {captured} photos saved to {person_dir}/")
    print(f"Total photos for {name}: {existing + captured}")
    print(f"\nNext: run face_train.py to generate embeddings.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 face_enroll.py <name>")
        print("  e.g.: python3 face_enroll.py ezra")
        sys.exit(1)

    enroll(sys.argv[1])
