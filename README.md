# Merlin

An ambient AI companion that lives on your desk. Sees, hears, thinks, speaks. All local, no cloud.

Merlin is built as an executive functioning (EF) prosthetic for ADHD. It runs on commodity hardware using only open-source, locally-hosted models. When you talk to Merlin, it responds with awareness of your state, schedule, and environment. When you leave the desk and come back, it tells you what you were doing. When you drift for 90 minutes, it says "Still here." When you vent, it reflects instead of advising.

---

## Architecture

Single Mac. No cloud. USB camera + local LLMs.

```
EMEET PIXY (USB-C) ─── Video + Audio ───> Mac (M1 Max 32GB)
                                            ├── tracker_usb.py  (OpenCV + YuNet face detection + UVC PTZ)
                                            ├── audio_usb.py    (sounddevice mic capture)
                                            ├── audio_pipeline   (Silero VAD + Whisper STT)
                                            ├── brain.py         (LM Studio LLM + intent classifier)
                                            ├── vision.py        (async scene descriptions via VLM)
                                            ├── voice.py         (Kokoro TTS + afplay speaker)
                                            └── main.py          (orchestrator + HTTP /health)

USB Speaker ◄──── afplay ────────────────── voice.py
```

| Module | File | What it does |
|--------|------|-------------|
| Orchestrator | `main.py` | Starts modules, supervises, restarts on crash, HTTP server |
| Audio Pipeline | `audio_pipeline.py` | Mic capture (USB or RTSP), Silero VAD, Whisper STT |
| Audio USB | `audio_usb.py` | Drop-in USB mic capture via sounddevice |
| Voice | `voice.py` | Kokoro TTS, afplay speaker output |
| Brain | `brain.py` | Intent classifier, conversation state machine, LM Studio LLM |
| Vision | `vision.py` | Async frame capture + VLM scene description |
| Event Bus | `event_bus.py` | In-process pub/sub connecting all modules |
| Config | `config.py` | All settings, env var overrides |
| Face Tracker | `tracker_usb.py` | YuNet face detection + UVC PTZ motor control |
| PTZ Controller | `ptz_uvc.py` | libuvc ctypes wrapper for UVC pan/tilt/zoom |
| Camera Probe | `probe_camera.py` | Hardware detection and verification script |

### Agent Subsystem

The `agent/` directory contains a separate ReAct agent that gives Merlin's local LLM access to the filesystem, Apple Notes, iMessages, and arbitrary Mac apps via MCP:

| File | What it does |
|------|-------------|
| `agent/kernel.py` | ReAct loop with tool execution |
| `agent/mcp_client.py` | JSON-RPC 2.0 MCP client over stdio |
| `agent/tools/filesystem.py` | Sandboxed file read/write/list |
| `agent/tools/mcp_bridge.py` | Discovers and wraps MCP server tools |

---

## How brain.py Works

brain.py v2 uses an intent-aware conversation architecture:

1. **Speech arrives** from the audio pipeline (via event bus)
2. **Echo detection** filters out Merlin hearing its own voice
3. **Wake word check** -- "Hey Merlin" or within 60s conversation window
4. **Intent classification** -- regex rules classify into 7 intents:
   - `GREETING`, `VENT`, `CHECK_IN`, `COMMAND`, `TRANSITION`, `QUESTION`, `GENERAL`
5. **Command short-circuit** -- capture, time, remind bypass the LLM entirely
6. **Conversation state machine** -- tracks phase (idle, greeted, working, winding down, venting) with time-based decay
7. **Intent-specific prompting** -- each intent gets a tailored system prompt injection and token limit
8. **LLM call** via OpenAI-compatible API (LM Studio) with assembled context:
   - Character prompt (voice rules)
   - RBOS context (today's focus, energy, shift, schedule, shipped items)
   - Scene description (what the camera sees, pre-computed in background)
   - Conversation history (last 10 exchanges)
9. **Response** emitted on the event bus, picked up by voice module

### EF Prosthetic Modes

- **Context recovery**: When you return to the desk after 5+ minutes, Merlin tells you what you were working on, graduated by absence length
- **Shift cues**: Proactive time-of-day announcements at shift boundaries
- **Drift detection**: After 90 minutes of silence during work hours, a gentle "Still here."
- **Evening send-off**: When face lost after 10pm, names what shipped today
- **Vent mode**: Emotional expression triggers reflection, not advice

---

## AI Stack

All models run locally. No API keys required for core operation.

| Component | Model | Size | Purpose |
|-----------|-------|------|---------|
| LLM + Vision | Qwen3 VL 4B (LM Studio, MLX) | ~4-5 GB | Conversation + image understanding |
| STT | Whisper Small (mlx-whisper) | ~0.5 GB | Speech to text |
| TTS | Kokoro 82M (mlx-audio) | ~0.2 GB | Text to speech |
| VAD | Silero VAD (torch) | ~0.1 GB | Voice activity detection |
| Face Detection | YuNet (OpenCV) | ~2 MB | Face tracking |

Total memory footprint: ~5-6 GB. Fits comfortably on a 32GB Apple Silicon Mac with plenty of headroom.

**Model evaluation:** A custom eval harness (`tools/merlin-model-eval.py`) tests models across 5 tiers: speed, instruction following, context grounding, conversation quality, and vision. Qwen3 VL 4B scored 81% vs 8B's 80% — the 4B wins on speed with nearly identical quality.

---

## Hardware

### Current Setup
- **Camera**: EMEET PIXY (USB-C PTZ webcam, 4K, 310° pan, 180° tilt, 3-mic array)
- **Speaker**: USB speaker for voice output
- **Compute**: Apple Silicon Mac with 16GB+ RAM
- **Connection**: Single USB-C cable. No network, no Pi, no RTSP.

### Also Works With
- **IP Camera**: Amcrest IP4M-1041B or similar ONVIF PTZ camera (use `tracker.py` instead of `tracker_usb.py`)
- **Raspberry Pi 5**: For remote face tracking with ONVIF cameras (legacy `tracker.py`)

---

## Setup

### 1. Clone and create venv

```bash
cd merlin
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install Python dependencies

```bash
pip install mlx-audio mlx-whisper sounddevice requests python-dotenv opencv-python
pip install torch  # for Silero VAD
```

### 3. Install LM Studio + model

1. Download [LM Studio](https://lmstudio.ai) for Apple Silicon
2. Search for and download `qwen/qwen3-vl-4b` (MLX format)
3. Start the server (Developer tab → Start Server, port 1234)

Alternative: Use any OpenAI-compatible local LLM server.

### 4. Install libuvc (for PTZ control)

```bash
brew install libusb cmake
git clone https://github.com/libuvc/libuvc.git /tmp/libuvc
cd /tmp/libuvc && mkdir build && cd build
cmake -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=$HOME/.local ..
make && make install
```

### 5. Configure environment

```bash
cp .env.example .env
# Edit .env — most defaults work out of the box for USB setup
```

### 6. Plug in camera and verify

```bash
python probe_camera.py
```

### 7. Run

```bash
# Full system
python main.py

# Test audio only
python audio_usb.py

# Test face tracking only
python tracker_usb.py

# Agent REPL
python agent/main.py
```

### 8. Health check

```bash
curl http://localhost:8900/health
```

---

## Project Structure

```
merlin/
  main.py              # Orchestrator
  audio_pipeline.py    # VAD + STT pipeline (source-agnostic)
  audio_usb.py         # USB mic capture via sounddevice
  voice.py             # TTS + speaker output (afplay)
  brain.py             # Intent classifier + LLM conversation
  vision.py            # Async frame capture + VLM scene description
  event_bus.py         # Pub/sub event system
  config.py            # All configuration
  tracker_usb.py       # USB face tracking (OpenCV + YuNet + UVC PTZ)
  tracker.py           # Legacy ONVIF face tracking (for IP cameras)
  ptz_uvc.py           # UVC PTZ controller (libuvc ctypes)
  probe_camera.py      # Camera hardware probe script
  gestures.py          # PTZ body language

  agent/               # ReAct agent with tool use
  personality/         # Character source material
  sounds/              # Nonverbal audio (oho, hmm, mmhmm, huh)
  models/              # YuNet face detection model
  briefing/            # RBOS state JSONs (gitignored)
  systemd/             # LaunchAgent service files
  archive/             # Previous versions
```

---

## Event Bus

All modules communicate through a simple in-process pub/sub bus.

Key events:
- `speech(text, rms, duration)` -- utterance transcribed
- `speak(text)` -- request Merlin to say something
- `face_arrived()` / `face_lost()` -- presence from tracker
- `scene_update(description, ts)` -- what the camera sees
- `frame_ready(ts)` -- fresh camera frame available
- `speaking_started()` / `speaking_finished()` -- echo suppression
- `mute_toggled(muted)` -- mute/unmute

---

## Status

This is an active build. The platform (hear, think, speak, see, track) is functional. The character and conversation design are being iterated. Future programs (morning quest, drift nudges, capture system) plug into the event bus when ready.

Built by [Ezra Drake](https://x.com/Ezra_Drake) as part of the Rebel-Builder Operating System (RBOS).

---

## License

MIT License. See [LICENSE](LICENSE).

## Organon Concepts

- [[Extended Mind]]
- [[Integration (Mental)]]
- [[Automatization]]
- [[Abstraction (process of)]]
