# ⚠️ AUTONOMOUS OUTPUT — UNREVIEWED
"""
test_vision.py — Vision module test suite for Merlin Windows build.

Tests:
  1. Camera detection — confirms EMEET PIXY (or any webcam) is accessible
  2. Frame capture — captures and saves a test frame to disk
  3. Model load — verifies moondream2 can be imported and loaded
  4. describe_scene() — runs a real inference on the test frame
  5. API contract — confirms Vision class has all required public methods
  6. VRAM check — reports current GPU memory usage
  7. Integration — simulates brain.py calling describe_scene() for context

Run:
  python test_vision.py

All tests are independent. A failure in one does not block others.
"""

import sys
import time
import logging
import threading
from pathlib import Path

# ── Test helpers ──────────────────────────────────────────────────────────────

PASS = "[PASS]"
FAIL = "[FAIL]"
SKIP = "[SKIP]"
INFO = "[INFO]"

results = []


def report(status: str, name: str, detail: str = "") -> None:
    tag = f"{status} {name}"
    if detail:
        tag += f" — {detail}"
    print(tag)
    results.append((status, name))


# ── Test 1: Camera detection ──────────────────────────────────────────────────

def test_camera_detection():
    """Scan camera indices 0-3 and report which ones open."""
    print("\n--- Test 1: Camera Detection ---")
    try:
        import cv2
    except ImportError:
        report(FAIL, "camera_detection", "opencv-python not installed")
        return None

    found = []
    for idx in range(4):
        cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
        if cap.isOpened():
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            name_id = int(cap.get(cv2.CAP_PROP_GUID)) if hasattr(cv2, "CAP_PROP_GUID") else -1
            print(f"  {INFO} Camera index {idx}: {w}x{h}")
            found.append(idx)
            cap.release()
        else:
            cap.release()

    if found:
        report(PASS, "camera_detection", f"found cameras at indices: {found}")
        return found[0]
    else:
        report(FAIL, "camera_detection", "no cameras found at indices 0-3")
        return None


# ── Test 2: Frame capture ─────────────────────────────────────────────────────

def test_frame_capture(camera_index: int = 0) -> bytes:
    """Open camera, capture one frame, save as test_frame.jpg."""
    print("\n--- Test 2: Frame Capture ---")
    try:
        import cv2
    except ImportError:
        report(SKIP, "frame_capture", "opencv-python not installed")
        return None

    try:
        cap = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            report(FAIL, "frame_capture", f"camera {camera_index} failed to open")
            return None

        # Warm up: skip a few frames (camera auto-exposure settling)
        for _ in range(5):
            cap.read()

        ret, frame = cap.read()
        cap.release()

        if not ret or frame is None:
            report(FAIL, "frame_capture", "cap.read() returned no frame")
            return None

        h, w = frame.shape[:2]
        out_path = Path("test_frame.jpg")
        cv2.imwrite(str(out_path), frame)
        report(PASS, "frame_capture", f"captured {w}x{h} frame → {out_path.absolute()}")

        # Return JPEG bytes for use in later tests
        success, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return buf.tobytes() if success else None

    except Exception as e:
        report(FAIL, "frame_capture", str(e))
        return None


# ── Test 3: moondream2 model load ─────────────────────────────────────────────

def test_model_load():
    """Verify moondream can be imported and model can be initialized."""
    print("\n--- Test 3: moondream2 Model Load ---")
    try:
        import moondream as md
    except ImportError:
        report(FAIL, "model_load", "moondream not installed. Run: pip install moondream")
        return None

    print(f"  {INFO} moondream imported. Loading model (may download ~2GB on first run)...")
    t0 = time.time()
    try:
        model = md.vl(model="moondream-2b-int8.mf")
        elapsed = time.time() - t0
        report(PASS, "model_load", f"loaded in {elapsed:.1f}s")
        return model
    except Exception as e:
        report(FAIL, "model_load", str(e))
        return None


# ── Test 4: describe_scene() inference ───────────────────────────────────────

def test_inference(model, jpeg_bytes: bytes):
    """Run describe_scene() inference on the test frame."""
    print("\n--- Test 4: describe_scene() Inference ---")

    if model is None:
        report(SKIP, "inference", "model not loaded")
        return

    if jpeg_bytes is None:
        # Try loading saved test frame
        p = Path("test_frame.jpg")
        if p.exists():
            jpeg_bytes = p.read_bytes()
        else:
            report(SKIP, "inference", "no frame available")
            return

    try:
        from PIL import Image
        import io
        pil_image = Image.open(io.BytesIO(jpeg_bytes))
    except ImportError:
        report(FAIL, "inference", "Pillow not installed. Run: pip install Pillow")
        return
    except Exception as e:
        report(FAIL, "inference", f"failed to decode frame: {e}")
        return

    print(f"  {INFO} Sending frame to moondream2...")
    t0 = time.time()
    try:
        encoded = model.encode_image(pil_image)
        result = model.query(encoded, "Describe what you see in one sentence. Be factual and brief.")
        answer = result.get("answer", "").strip()
        elapsed = time.time() - t0

        if answer and len(answer) > 5:
            report(PASS, "inference", f"({elapsed:.1f}s) → '{answer[:80]}'")
        else:
            report(FAIL, "inference", f"empty or too-short response: '{answer}'")

    except Exception as e:
        report(FAIL, "inference", str(e))


# ── Test 5: API contract ──────────────────────────────────────────────────────

def test_api_contract():
    """Confirm Vision class has all required public methods."""
    print("\n--- Test 5: API Contract ---")
    try:
        from vision import Vision, inject_vision_context
    except ImportError as e:
        report(FAIL, "api_contract", f"could not import vision.py: {e}")
        return

    required_methods = [
        "start",
        "stop",
        "is_alive",
        "describe_scene",
        "get_latest_frame",
        "on_face_arrived",
        "on_face_lost",
        "set_conversation_active",
    ]

    v = Vision.__new__(Vision)  # Don't call __init__ — just check methods
    missing = [m for m in required_methods if not hasattr(Vision, m)]

    if missing:
        report(FAIL, "api_contract", f"missing methods: {missing}")
    else:
        report(PASS, "api_contract", f"all {len(required_methods)} required methods present")

    # Check inject_vision_context helper
    if callable(inject_vision_context):
        # Test with None vision (should return prompt unchanged)
        prompt = "You are Merlin."
        result = inject_vision_context(prompt, None)
        if result == prompt:
            report(PASS, "inject_vision_context", "returns unchanged prompt when vision=None")
        else:
            report(FAIL, "inject_vision_context", f"unexpected result: '{result}'")
    else:
        report(FAIL, "inject_vision_context", "not callable")


# ── Test 6: VRAM check ────────────────────────────────────────────────────────

def test_vram():
    """Report GPU memory. Warn if budget would be exceeded."""
    print("\n--- Test 6: VRAM Budget ---")
    try:
        import torch
        if not torch.cuda.is_available():
            report(INFO, "vram", "CUDA not available — will run on CPU (slower)")
            return

        device = torch.cuda.current_device()
        total = torch.cuda.get_device_properties(device).total_memory / (1024**3)
        allocated = torch.cuda.memory_allocated(device) / (1024**3)
        free = total - allocated

        print(f"  {INFO} GPU: {torch.cuda.get_device_name(device)}")
        print(f"  {INFO} Total VRAM: {total:.1f} GB")
        print(f"  {INFO} Allocated:  {allocated:.1f} GB")
        print(f"  {INFO} Free:       {free:.1f} GB")
        print(f"  {INFO} Expected budget: Whisper ~1.0GB + LLM ~5.7GB + moondream ~1.3GB = ~8.0GB")

        if total >= 8.0:
            report(PASS, "vram", f"VRAM sufficient ({total:.0f}GB total)")
        elif total >= 6.0:
            report(INFO, "vram", f"VRAM tight ({total:.0f}GB) — use int8 moondream (default) to save memory")
        else:
            report(FAIL, "vram", f"VRAM too low ({total:.0f}GB) — vision may OOM with full stack")

    except ImportError:
        report(INFO, "vram", "PyTorch not installed — skipping VRAM check")


# ── Test 7: Vision module integration (live run) ─────────────────────────────

def test_vision_integration():
    """Start the Vision module and wait for a real scene description."""
    print("\n--- Test 7: Vision Module Integration ---")
    print(f"  {INFO} Starting Vision module. Will wait up to 120s for a description...")
    print(f"  {INFO} (moondream2 may need to load — first run takes 60-90s)")
    print(f"  {INFO} Press Ctrl+C to skip this test.")

    try:
        from vision import Vision
    except ImportError as e:
        report(FAIL, "vision_integration", f"could not import vision.py: {e}")
        return

    vision = Vision()
    vision.start()

    # Simulate face arriving (triggers immediate describe)
    time.sleep(2)
    vision.on_face_arrived()

    deadline = time.time() + 120
    description = ""
    try:
        while time.time() < deadline:
            time.sleep(2)
            description = vision.describe_scene()
            if description:
                break
            elapsed = int(time.time() - (deadline - 120))
            print(f"  {INFO} Waiting... ({elapsed}s) — is_alive={vision.is_alive()}")
    except KeyboardInterrupt:
        report(SKIP, "vision_integration", "user skipped")
        vision.stop()
        return
    finally:
        vision.stop()

    if description:
        report(PASS, "vision_integration", f"description: '{description[:80]}'")
    else:
        report(FAIL, "vision_integration", "no description returned within 120s")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  Merlin Windows — Vision Module Test Suite")
    print("=" * 60)

    # Tests 1-2: Hardware
    camera_idx = test_camera_detection()
    jpeg_bytes = test_frame_capture(camera_idx if camera_idx is not None else 0)

    # Test 3-4: moondream model (may take a while on first run)
    model = test_model_load()
    test_inference(model, jpeg_bytes)

    # Test 5: API contract (no hardware needed)
    test_api_contract()

    # Test 6: VRAM
    test_vram()

    # Test 7: Full integration (optional, can be skipped with Ctrl+C)
    run_integration = "--integration" in sys.argv or "--full" in sys.argv
    if run_integration:
        test_vision_integration()
    else:
        print("\n--- Test 7: Vision Module Integration ---")
        print(f"  {INFO} Skipped (pass --integration or --full to run live test)")
        print(f"  {INFO} Usage: python test_vision.py --integration")

    # Summary
    print("\n" + "=" * 60)
    print("  RESULTS SUMMARY")
    print("=" * 60)
    passed = sum(1 for s, _ in results if s == PASS)
    failed = sum(1 for s, _ in results if s == FAIL)
    skipped = sum(1 for s, _ in results if s == SKIP)

    for status, name in results:
        print(f"  {status} {name}")

    print(f"\n  {passed} passed, {failed} failed, {skipped} skipped")

    if failed == 0:
        print("\n  All tests passed. Vision module is ready.")
    else:
        print("\n  Some tests failed. See details above.")
        print("  Common fixes:")
        print("    pip install moondream Pillow        # model + image decode")
        print("    pip install torch torchvision       # GPU support (optional)")
        print("    Adjust CAMERA_INDEX in config.py    # if camera not found")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
