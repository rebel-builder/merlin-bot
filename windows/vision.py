# ⚠️ AUTONOMOUS OUTPUT — UNREVIEWED
"""
Merlin Windows Vision — Frame capture + async scene description.

Uses moondream2 (via the official `moondream` pip package) for scene description.
moondream2 runs locally on NVIDIA GPU via ONNX/PyTorch at ~1.3GB VRAM — well
within the 8GB budget alongside Whisper (~1GB) and the LLM (~5.7GB).

VRAM budget:
  Whisper small  ~  1.0 GB
  LLM (Gemma 9B) ~  5.7 GB
  moondream2     ~  1.3 GB
  ─────────────────────────
  Total          ~  8.0 GB  (just fits RTX 4060 8GB)

Architecture:
  - Vision owns its frame buffer (shared via `get_latest_frame()`).
  - tracker.py holds the OpenCV VideoCapture. Vision reads frames from
    the tracker's cap via a callback, OR opens its own capture on a
    separate camera handle (configurable via VISION_CAMERA_INDEX).
  - `describe_scene()` returns the cached description — zero latency.
  - Background thread refreshes description every N seconds.

Setup (one-time):
  pip install moondream
  # Model weights auto-download on first use (~2GB to ~/.cache/moondream).

Fallback:
  If moondream fails (no GPU, OOM, import error), Vision degrades gracefully:
  describe_scene() returns "" and the brain skips vision context.
"""

import base64
import io
import logging
import threading
import time
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from config import (
    CAMERA_INDEX,
    CAMERA_WIDTH,
    CAMERA_HEIGHT,
    CAMERA_FPS,
)

log = logging.getLogger("merlin.vision")

# ── Vision-specific config (not in config.py to keep it thin) ────────────────
# How often to refresh the scene description (seconds)
DESCRIBE_INTERVAL_IDLE = 120       # No face present
DESCRIBE_INTERVAL_FACE = 45        # Face visible
DESCRIBE_INTERVAL_ACTIVE = 30      # Conversation active

# Max age of a frame before we skip description (stale frames = misleading)
FRAME_STALE_THRESHOLD = 30.0       # seconds

# moondream2 model revision — pin for reproducibility
MOONDREAM_REVISION = "2025-01-09"

# Optional: use a different camera index than the tracker
# Set to None to share the same physical camera (opens second handle)
VISION_CAMERA_INDEX = None  # None = use CAMERA_INDEX from config


class Vision:
    """
    Vision module for Merlin Windows build.

    Public API (matches Mac merlin/vision.py):
        vision.start()                   → starts background threads
        vision.stop()                    → clean shutdown
        vision.is_alive() -> bool        → health check
        vision.describe_scene() -> str   → cached scene description (instant)
        vision.on_face_arrived()         → call from tracker when face appears
        vision.on_face_lost()            → call from tracker when face gone
        vision.set_conversation_active(bool) → call from brain during conversation
    """

    def __init__(self):
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._model = None
        self._model_loaded = False
        self._model_error: Optional[str] = None
        self._cap: Optional[cv2.VideoCapture] = None

        # Frame buffer
        self._latest_frame: Optional[np.ndarray] = None
        self._frame_time: float = 0.0
        self._frame_lock = threading.Lock()

        # Scene description cache
        self._scene_description: str = ""
        self._scene_timestamp: float = 0.0
        self._last_describe_time: float = 0.0
        self._describing: bool = False
        self._describe_lock = threading.Lock()

        # State flags (set externally)
        self._face_present: bool = False
        self._conversation_active: bool = False

    # ── Public control ────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the vision module. Load model and begin capture/describe loop."""
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="vision")
        self._thread.start()

    def stop(self) -> None:
        """Stop vision module and release resources."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
        if self._cap and self._cap.isOpened():
            self._cap.release()
            self._cap = None
        log.info("Vision stopped")

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ── Public API ────────────────────────────────────────────────────────────

    def describe_scene(self) -> str:
        """
        Return the current cached scene description.
        Always instant — description updates in the background.
        Returns empty string if vision is unavailable or not yet initialized.
        """
        return self._scene_description

    def get_latest_frame(self) -> Optional[np.ndarray]:
        """Return the most recently captured frame (BGR numpy array), or None."""
        with self._frame_lock:
            return self._latest_frame.copy() if self._latest_frame is not None else None

    # ── State callbacks (wire these from merlin.py / tracker.py) ─────────────

    def on_face_arrived(self) -> None:
        """Call this when tracker detects a face. Triggers immediate describe."""
        self._face_present = True
        # Describe immediately — someone just sat down
        if not self._describing and self._model_loaded:
            threading.Thread(
                target=self._describe_current_frame,
                daemon=True,
                name="vision-describe-face",
            ).start()

    def on_face_lost(self) -> None:
        """Call this when tracker loses the face."""
        self._face_present = False

    def set_conversation_active(self, active: bool) -> None:
        """Call this from brain.py during active conversation."""
        self._conversation_active = active

    # ── Internal ──────────────────────────────────────────────────────────────

    def _get_describe_interval(self) -> float:
        if self._conversation_active:
            return DESCRIBE_INTERVAL_ACTIVE
        if self._face_present:
            return DESCRIBE_INTERVAL_FACE
        return DESCRIBE_INTERVAL_IDLE

    def _load_model(self) -> bool:
        """Load moondream2. Returns True on success. Runs in background thread."""
        try:
            import moondream as md  # pip install moondream
            log.info("Loading moondream2 model (~1.3GB VRAM)...")
            print("[vision] Loading moondream2 — first run downloads ~2GB weights...")
            self._model = md.vl(model="moondream-2b-int8.mf")
            self._model_loaded = True
            print("[vision] moondream2 loaded. Vision active.")
            log.info("moondream2 loaded successfully")
            return True
        except ImportError:
            msg = "moondream not installed. Run: pip install moondream"
            self._model_error = msg
            log.warning(f"[vision] {msg}")
            print(f"[vision] WARNING: {msg}")
            return False
        except Exception as e:
            msg = f"moondream load failed: {e}"
            self._model_error = msg
            log.warning(f"[vision] {msg}")
            print(f"[vision] WARNING: {msg}")
            return False

    def _init_camera(self) -> bool:
        """Open a separate OpenCV handle for vision frame capture."""
        idx = VISION_CAMERA_INDEX if VISION_CAMERA_INDEX is not None else CAMERA_INDEX
        log.info(f"Vision opening camera index {idx}...")
        cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_HEIGHT)
        cap.set(cv2.CAP_PROP_FPS, CAMERA_FPS)

        if cap.isOpened():
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            log.info(f"Vision camera opened: {w}x{h}")
            self._cap = cap
            return True
        else:
            log.warning("Vision camera failed to open — running without vision")
            print("[vision] WARNING: Could not open camera. Vision disabled.")
            print("[vision] Try setting VISION_CAMERA_INDEX in vision.py.")
            cap.release()
            return False

    def _capture_frame(self) -> bool:
        """Read one frame from the camera and store in buffer."""
        if self._cap is None or not self._cap.isOpened():
            return False
        ret, frame = self._cap.read()
        if ret and frame is not None:
            with self._frame_lock:
                self._latest_frame = frame
                self._frame_time = time.time()
            return True
        return False

    def _frame_to_jpeg_bytes(self, frame: np.ndarray) -> Optional[bytes]:
        """Encode a BGR frame to JPEG bytes."""
        try:
            success, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            if success:
                return buf.tobytes()
            return None
        except Exception:
            return None

    def _describe_current_frame(self) -> None:
        """
        Send the current frame to moondream2 and update the scene cache.
        Runs in a background thread — never blocks conversation.
        """
        with self._describe_lock:
            if self._describing:
                return
            self._describing = True

        try:
            if not self._model_loaded or self._model is None:
                return

            # Get latest frame
            with self._frame_lock:
                frame = self._latest_frame
                frame_time = self._frame_time

            if frame is None:
                return

            # Reject stale frames
            age = time.time() - frame_time
            if age > FRAME_STALE_THRESHOLD:
                log.debug(f"Frame is {age:.1f}s old — skipping description")
                return

            # Convert to PIL Image for moondream
            try:
                from PIL import Image
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                pil_image = Image.fromarray(frame_rgb)
            except ImportError:
                # Fallback: encode as JPEG bytes and use moondream's bytes API
                jpeg_bytes = self._frame_to_jpeg_bytes(frame)
                if jpeg_bytes is None:
                    return
                import io
                from PIL import Image
                pil_image = Image.open(io.BytesIO(jpeg_bytes))

            # Run moondream inference
            log.debug("Sending frame to moondream2...")
            encoded = self._model.encode_image(pil_image)
            answer = self._model.query(encoded, "Describe what you see in one sentence. Be factual and brief.")["answer"]
            text = answer.strip()

            if text and len(text) > 5:
                old = self._scene_description
                self._scene_description = text
                self._scene_timestamp = time.time()
                self._last_describe_time = time.time()

                if text != old:
                    log.info(f"Scene: {text}")
                    print(f"[vision] Scene: {text}")

        except Exception as e:
            log.debug(f"Scene description failed: {e}")
        finally:
            with self._describe_lock:
                self._describing = False

    def _run(self) -> None:
        """Main vision loop: load model, open camera, capture + periodically describe."""
        log.info("Vision thread starting")
        print("[vision] Starting vision module...")

        # Load model in background so startup is non-blocking
        model_thread = threading.Thread(target=self._load_model, daemon=True, name="vision-model-load")
        model_thread.start()

        # Open camera
        cam_ok = self._init_camera()

        if not cam_ok:
            log.warning("Vision: no camera — exiting thread")
            return

        log.info("Vision: capture loop active")
        frame_interval = 1.0 / CAMERA_FPS

        while self._running:
            t0 = time.time()

            # Always capture frames (needed by tracker too, via get_latest_frame)
            self._capture_frame()

            # Periodically refresh scene description
            if self._model_loaded and not self._describing:
                since_last = time.time() - self._last_describe_time
                describe_interval = self._get_describe_interval()

                if since_last >= describe_interval:
                    threading.Thread(
                        target=self._describe_current_frame,
                        daemon=True,
                        name="vision-describe",
                    ).start()

            # Pace the loop to ~CAMERA_FPS
            elapsed = time.time() - t0
            sleep_time = frame_interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

        log.info("Vision capture loop exited")


# ── Integration helpers ───────────────────────────────────────────────────────

def inject_vision_context(system_prompt: str, vision: Optional[Vision]) -> str:
    """
    Prepend the current scene description to a system prompt.
    Call this from brain.py before sending to LM Studio.

    Usage in brain.py:
        from vision import inject_vision_context
        ...
        system = inject_vision_context(SYSTEM_PROMPT, self._vision)
    """
    if vision is None:
        return system_prompt

    desc = vision.describe_scene()
    if not desc:
        return system_prompt

    return f"[Current scene: {desc}]\n\n{system_prompt}"


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    print("Vision standalone test — Ctrl+C to quit")
    print("Will describe scene every 15 seconds for testing.")

    # Patch describe interval for fast testing
    DESCRIBE_INTERVAL_IDLE = 15
    DESCRIBE_INTERVAL_FACE = 10

    vision = Vision()
    vision.start()

    try:
        while True:
            time.sleep(5)
            desc = vision.describe_scene()
            if desc:
                print(f"\n>>> SEES: {desc}\n")
            else:
                print("  (no description yet — model may still be loading)")
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        vision.stop()
        print("Done.")
