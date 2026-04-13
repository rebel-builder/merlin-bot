"""
Merlin Configuration — All settings in one place.
Edit this file to match your hardware.
"""

import os

# ============================================================
# LM Studio (runs on your laptop)
# ============================================================
LLM_URL = "http://localhost:1234/v1/chat/completions"
LLM_MODEL = "local-model"

# ============================================================
# Audio Devices
# ============================================================
# Set to None for auto-detect, or specify device index number
PIXY_MIC_DEVICE = None    # Auto-searches for "EMEET" / "PIXY"
SPEAKER_DEVICE = None     # None = default output (your BT speaker)
SAMPLE_RATE = 16000
CHANNELS = 1

# ============================================================
# Voice Activity Detection (energy-based)
# ============================================================
ENERGY_THRESHOLD = 0.02       # RMS threshold — raise if picking up background noise
SILENCE_TIMEOUT = 1.5         # Seconds of silence = end of utterance
MIN_UTTERANCE_LENGTH = 0.5    # Ignore very short sounds
MAX_UTTERANCE_LENGTH = 15.0   # Force-cut very long speech

# ============================================================
# Speech-to-Text (faster-whisper)
# ============================================================
WHISPER_MODEL = "small"       # Options: tiny, base, small, medium
WHISPER_DEVICE = "cuda"       # "cuda" for GPU, "cpu" for CPU-only
WHISPER_COMPUTE = "float16"   # "float16" for GPU, "int8" for CPU
WHISPER_LANGUAGE = "en"

# ============================================================
# Text-to-Speech (Kokoro ONNX)
# ============================================================
KOKORO_VOICE = "am_fenrir"    # 54 voice presets available
KOKORO_SPEED = 1.0

# ============================================================
# Brain / Conversation
# ============================================================
SYSTEM_PROMPT = """You are Merlin, a calm and direct AI companion sitting on a desk.
You respond in under 30 words. You are still, patient, curious, and unwavering.
No motivation speeches. No lectures. Observe and reflect.
You can see via a camera and hear via a microphone."""

MAX_HISTORY = 10        # Conversation exchanges to remember
MAX_TOKENS = 100        # Max response length
TEMPERATURE = 0.7

# ============================================================
# Wake Word & Controls
# ============================================================
WAKE_WORDS = ["merlin", "hey merlin", "hi merlin", "ok merlin"]
CONVERSATION_WINDOW = 30   # Seconds after response — no wake word needed

MUTE_WORDS = ["stop listening", "mute", "go to sleep"]
UNMUTE_WORDS = ["start listening", "unmute", "wake up"]
NEVERMIND_WORDS = ["nevermind", "never mind"]

# ============================================================
# Face Tracking (OpenCV + YuNet)
# ============================================================
CAMERA_INDEX = 0          # USB camera index (try 0, 1, or 2)
CAMERA_WIDTH = 640
CAMERA_HEIGHT = 480
CAMERA_FPS = 30
FACE_CONFIDENCE = 0.7     # Detection threshold (0-1)

PTZ_ENABLED = True
PTZ_SPEED = 0.3           # How aggressively to follow (0-1)
PTZ_DEADZONE = 0.15       # Don't move if face is near center

# ============================================================
# Model Files (auto-downloaded on first run)
# ============================================================
YUNET_MODEL = "face_detection_yunet_2023mar.onnx"
