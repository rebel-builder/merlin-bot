"""Merlin v2 — Camera Probe Script.

Detects connected cameras, lists capabilities, tests PTZ control.
Run this first when the EMEET PIXY arrives.

Usage:
    python3 merlin/probe_camera.py
"""

import sys
import time

def probe_video():
    """Detect cameras via OpenCV."""
    print("=" * 60)
    print("VIDEO DEVICES (OpenCV)")
    print("=" * 60)

    try:
        import cv2
    except ImportError:
        print("ERROR: opencv-python not installed")
        print("  pip install opencv-python")
        return

    found = []
    for i in range(5):
        cap = cv2.VideoCapture(i)
        if cap.isOpened():
            ret, frame = cap.read()
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            fps = cap.get(cv2.CAP_PROP_FPS)
            backend = cap.getBackendName()

            status = "FRAME OK" if ret else "NO FRAME"
            print(f"  Index {i}: {w}x{h} @ {fps:.0f}fps [{backend}] — {status}")
            if frame is not None:
                print(f"           Frame shape: {frame.shape}")
            found.append(i)
            cap.release()
        else:
            pass  # Not a camera

    if not found:
        print("  No cameras found!")
    else:
        print(f"\n  Found {len(found)} camera(s) at indices: {found}")

    return found


def probe_audio():
    """Detect audio devices via sounddevice."""
    print("\n" + "=" * 60)
    print("AUDIO DEVICES (sounddevice)")
    print("=" * 60)

    try:
        import sounddevice as sd
    except ImportError:
        print("ERROR: sounddevice not installed")
        print("  pip install sounddevice")
        return

    devices = sd.query_devices()
    emeet_idx = None
    for i, dev in enumerate(devices):
        if dev["max_input_channels"] > 0:
            marker = ""
            if "EMEET" in dev["name"].upper() or "PIXY" in dev["name"].upper():
                marker = " ← PIXY?"
                emeet_idx = i
            print(f"  Input {i}: {dev['name']} ({dev['max_input_channels']}ch, {dev['default_samplerate']:.0f}Hz){marker}")

    if emeet_idx is not None:
        print(f"\n  PIXY audio device found at index {emeet_idx}")
    else:
        print("\n  PIXY audio device not found — check USB connection")

    return emeet_idx


def probe_ptz():
    """Test PTZ control via libuvc."""
    print("\n" + "=" * 60)
    print("PTZ CONTROL (libuvc)")
    print("=" * 60)

    try:
        from ptz_uvc import UVCPTZController
    except ImportError:
        print("ERROR: ptz_uvc.py not in path")
        return

    try:
        ptz = UVCPTZController()
        mode = "CLI fallback" if ptz._using_cli else "libuvc direct"
        print(f"  Mode: {mode}")

        print("  Testing home position...")
        ptz.home()
        time.sleep(0.5)

        pos = ptz.get_pantilt()
        print(f"  Current position: pan={pos[0]:.1f}°, tilt={pos[1]:.1f}°")

        print("  Testing pan right 15°...")
        ptz.set_pantilt(15.0, 0.0)
        time.sleep(1)

        print("  Testing pan left 15°...")
        ptz.set_pantilt(-15.0, 0.0)
        time.sleep(1)

        print("  Testing tilt up 10°...")
        ptz.set_pantilt(0.0, 10.0)
        time.sleep(1)

        print("  Returning home...")
        ptz.home()
        time.sleep(0.5)

        print("  PTZ test complete ✓")
        ptz.close()

    except Exception as e:
        print(f"  PTZ FAILED: {e}")
        print("  This may be normal if the PIXY uses proprietary extension units.")
        print("  Try: EMEET Studio app, or investigate USB descriptors with uvc-util.")


def probe_yunet():
    """Test YuNet face detection."""
    print("\n" + "=" * 60)
    print("FACE DETECTION (YuNet)")
    print("=" * 60)

    try:
        import cv2
    except ImportError:
        print("ERROR: opencv-python not installed")
        return

    # Check for YuNet model
    from pathlib import Path
    model_paths = [
        Path(__file__).parent / "models" / "face_detection_yunet_2023mar.onnx",
        Path(__file__).parent / "models" / "face_detection_yunet.onnx",
    ]

    model_path = None
    for p in model_paths:
        if p.exists():
            model_path = str(p)
            break

    if not model_path:
        print(f"  YuNet model not found. Expected at:")
        for p in model_paths:
            print(f"    {p}")
        return

    print(f"  Model: {model_path}")

    # Try to capture a frame and detect
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("  ERROR: Cannot open camera at index 0")
        return

    ret, frame = cap.read()
    cap.release()
    if not ret:
        print("  ERROR: Cannot read frame")
        return

    h, w = frame.shape[:2]
    detector = cv2.FaceDetectorYN.create(model_path, "", (w, h), 0.7)
    _, faces = detector.detect(frame)

    if faces is not None:
        print(f"  Detected {len(faces)} face(s) in test frame ✓")
        for i, face in enumerate(faces):
            x, y, fw, fh = int(face[0]), int(face[1]), int(face[2]), int(face[3])
            conf = face[14]
            print(f"    Face {i}: ({x},{y}) {fw}x{fh} confidence={conf:.2f}")
    else:
        print("  No faces detected in test frame (this is OK if no one is in front of camera)")


def main():
    print("EMEET PIXY Camera Probe")
    print("Run this after plugging in the PIXY to verify everything works.\n")

    cameras = probe_video()
    audio_idx = probe_audio()
    probe_ptz()
    probe_yunet()

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  Cameras found: {len(cameras) if cameras else 0}")
    print(f"  PIXY audio: {'Found' if audio_idx is not None else 'Not found'}")
    print(f"  Next: Run with PIXY plugged in to verify PTZ controls")
    print()


if __name__ == "__main__":
    main()
