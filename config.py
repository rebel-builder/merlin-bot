"""Merlin v2 — All configuration in one place."""

import os
from pathlib import Path
from dotenv import load_dotenv
from requests.auth import HTTPDigestAuth

# Load .env from RBOS root
load_dotenv(Path(__file__).parent.parent / ".env")

# ── Network ──────────────────────────────────────────────────────
PI_HOST = os.getenv("MERLIN_PI_HOST", "100.87.156.70")
GO2RTC_RTSP = f"rtsp://{PI_HOST}:8554/merlin"
GO2RTC_API = f"http://{PI_HOST}:1984"
GO2RTC_STREAM = "merlin"
TRACKER_LISTEN_PORT = 8900
BRAIN_EVENT_URL = os.getenv("MERLIN_BRAIN_URL", f"http://localhost:{TRACKER_LISTEN_PORT}/event")

# ── Camera (direct RTSP) ────────────────────────────────────────
CAMERA_IP = os.getenv("MERLIN_CAMERA_IP", "192.168.1.26")
CAMERA_USER = os.getenv("MERLIN_CAMERA_USER", "admin")
CAMERA_PASS = os.getenv("MERLIN_CAMERA_PASS", "")
CAMERA_RTSP_SUB = (
    f"rtsp://{CAMERA_USER}:{CAMERA_PASS}@{CAMERA_IP}:554"
    f"/cam/realmonitor?channel=1&subtype=1"
)
# Audio input reads directly from camera — NOT through go2rtc.
# go2rtc's RTSP stream drops when speaker audio is pushed to it.
# Camera's own RTSP is independent and stays up during playback.
CAMERA_RTSP_AUDIO = (
    f"rtsp://{CAMERA_USER}:{CAMERA_PASS}@{CAMERA_IP}:554"
    f"/cam/realmonitor?channel=1&subtype=0"
)
CAMERA_RTSP_MAIN = CAMERA_RTSP_AUDIO  # alias — subtype=0 is main stream
CAMERA_AUTH = HTTPDigestAuth(CAMERA_USER, CAMERA_PASS)
CAMERA_PTZ_BASE = f"http://{CAMERA_IP}/cgi-bin/ptz.cgi"
CAMERA_ONVIF_PTZ = f"http://{CAMERA_IP}/onvif/ptz_service"

# ── LLM — LM Studio (OpenAI-compatible API) ─────────────────────
LLM_URL = os.getenv("MERLIN_LLM_URL", "http://localhost:1234/v1/chat/completions")
LLM_MODEL = os.getenv("MERLIN_MODEL", "qwen/qwen3-vl-4b")

# Legacy Ollama (kept for fallback)
OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "gemma4:e4b"

# ── Audio Pipeline ───────────────────────────────────────────────
AUDIO_SOURCE = os.getenv("MERLIN_AUDIO_SOURCE", "rtsp")  # "rtsp" (Amcrest camera mic) or "usb" (PIXY — only if on same machine)
MIC_SAMPLE_RATE = 16000
VAD_THRESHOLD = 0.5
UTTERANCE_SILENCE_TIMEOUT = 1.5
ECHO_SUPPRESSION_PADDING = 0.5   # USB path is much shorter than RTSP (was 1.5s)

# ── TTS ──────────────────────────────────────────────────────────
KOKORO_VOICE = os.getenv("MERLIN_VOICE", "am_fenrir")  # nerdy sage in a security camera body

# ── USB Camera (EMEET PIXY) ─────────────────────────────────────
USB_CAMERA_INDEX = int(os.getenv("MERLIN_CAMERA_INDEX", "0"))
USB_CAMERA_WIDTH = 1920
USB_CAMERA_HEIGHT = 1080
USB_CAMERA_FPS = 30

# ── Vision ───────────────────────────────────────────────────────
VISION_MODEL = os.getenv("MERLIN_VISION_MODEL", "mlx-community/nanoLLaVA-1.5-4bit")
VISION_INTERVAL_DEFAULT = 5
VISION_INTERVAL_IDLE = 15
VISION_INTERVAL_ACTIVE = 3
VISION_INTERVAL_MUTED = 30
VISION_PROMPT = "Briefly describe what you see at this desk. One sentence."

# ── Conversation ─────────────────────────────────────────────────
WAKE_WORDS = ["merlin", "hey merlin", "hi merlin", "ok merlin",
              "erlin", "hey erlin", "i'm erlin", "murlin", "marlin",
              "hey marlin", "berlin", "hey berlin"]  # whisper frequently mangles "Merlin"
CONVERSATION_WINDOW = 60  # seconds after Merlin speaks before requiring wake word again
CONVERSATION_HISTORY_SIZE = 10
MUTE_WORDS = ["stop listening", "mute", "go to sleep"]
UNMUTE_WORDS = ["start listening", "unmute", "wake up"]
NEVERMIND_WORDS = ["nevermind", "never mind"]

# ── RBOS ─────────────────────────────────────────────────────────
RBOS_ROOT = Path("/Users/ezradrake/Documents/RBOS")
STATE_PATH = RBOS_ROOT / "core" / "STATE.md"
BRIEFING_DIR = RBOS_ROOT / "merlin" / "briefing"
BRIEFING_POLL_INTERVAL = 900  # 15 minutes

# ── Paths ────────────────────────────────────────────────────────
LOG_FILE = Path("/tmp/merlin-v2.log")
FRAME_PATH = Path("/tmp/merlin_frame.jpg")
STATE_PERSIST_PATH = Path("/tmp/merlin-state.json")
SOUNDS_DIR = Path(__file__).parent / "sounds"

# Ensure homebrew binaries are on PATH
os.environ["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + os.environ.get("PATH", "")
