# Merlin v2 — Architecture & Troubleshooting Guide

*Living document. Update this file when the system changes. Last updated: 2026-04-12.*

---

## System Overview

Merlin is an ambient AI companion on Ezra's desk. All local, no cloud. Can run on two or three machines depending on configuration.

**Weekend/portable mode:** Brain runs on Ezra's Mac (when Nate's Mac is offline).
**Full mode:** Brain runs on Nate's Mac (more GPU headroom).

```
                    PIXY camera (USB)
                         |
                     Pi 5 (8GB)
                  100.87.156.70
                  ┌──────────────────────────────┐
                  │ tracker_pi  — face tracking   │
                  │   + face recognition (dlib)   │
                  │   + saccadic eye contact      │
                  │   + zone-based idle explore   │
                  │   + breathing animation       │
                  │ pi_client   — mic + speaker   │
                  │   + voice recognition         │
                  │   + identity pipeline         │
                  │ go2rtc      — RTSP relay       │
                  │ reactions   — LLM reactions    │
                  │ Ollama      — Qwen3.5:0.8b    │
                  └──────────────────────────────┘
                         │
                    Tailscale VPN
                         │
                  Brain Mac (Ezra's or Nate's)
                  ┌──────────────────────────────────────┐
                  │ main.py :8900     (orchestrator)      │
                  │   ├─ audio_pipeline (camera RTSP mic) │
                  │   ├─ brain.py      (LM Studio LLM)   │
                  │   │    + identity-aware prompting     │
                  │   │    + native tool calling          │
                  │   ├─ voice.py      (Kokoro TTS)       │
                  │   └─ vision.py     (Gemma 4 vision)   │
                  │                                       │
                  │ LM Studio :1234   (Gemma 26B-A4B)     │
                  └──────────────────────────────────────┘
```

---

## What Runs Where

### Pi 5 (100.87.156.70, user: `pi`)

| Process | What It Does | Service | Log |
|---------|-------------|---------|-----|
| `tracker_pi.py` | Face tracking (YuNet) + face recognition (dlib) + PTZ animation + saccadic gaze + idle exploration | `merlin-tracker.service` | `journalctl -u merlin-tracker` |
| `merlin_pi_client.py` | PIXY mic capture, VAD, voice recognition (resemblyzer), sends audio to brain, plays responses | `merlin-pi-client.service` | `journalctl -u merlin-pi-client` |
| `go2rtc` | RTSP relay for camera + speaker audio push | `merlin-go2rtc.service` | stdout |
| Ollama | Qwen3.5:0.8b for reactions layer (local LLM on Pi) | system service | `ollama logs` |

**systemd services (auto-restart on crash):**
```bash
sudo systemctl start|stop|restart merlin-tracker
sudo systemctl start|stop|restart merlin-pi-client
sudo systemctl start|stop|restart merlin-go2rtc
```

### Brain Mac (Ezra's Mac 100.80.221.50 OR Nate's Mac 100.123.211.1)

| Process | What It Does | How to Run | Log |
|---------|-------------|------------|-----|
| `main.py` | Orchestrator — HTTP :8900, supervises all modules | `~/Code/merlin/venv/bin/python3 -u main.py` | `/tmp/merlin-brain.log` |
| LM Studio | Serves Gemma 4 26B-A4B (text + vision + tools) | GUI app | LM Studio GUI |

**IMPORTANT:** Brain must run from the venv (`~/Code/merlin/venv/bin/python3`) for Kokoro TTS. System Python lacks `mlx_audio` and will fall back to macOS `say` command (wrong voice).

**main.py modules:**
- `audio_pipeline.py` — captures camera RTSP audio, Silero VAD, Whisper STT
- `brain.py` — identity-aware LLM conversation, native tool calling, intent classification
- `voice.py` — Kokoro TTS (am_fenrir voice), EQ, speaker push via go2rtc
- `vision.py` — frame capture from Pi snapshot (SCP fallback), Gemma 4 26B scene description

**HTTP endpoints (port 8900):**
- `POST /event` — receives face_arrived/face_lost from tracker
- `POST /stt` — receives WAV audio, returns transcription
- `POST /think` — receives `{text, identity, people_present}`, returns LLM response
- `POST /tts` — receives text, returns WAV audio
- `GET /health` — returns module status JSON

---

## Identity Pipeline

Merlin identifies WHO is talking using both face and voice recognition.

```
PIXY camera frame
  → tracker_pi.py: YuNet detects face(s)
  → on face_arrived: dlib face_recognition compares against embeddings
  → writes identity to /tmp/merlin-identity.txt ("ezra" or "ezra,nate")
  → writes to /tmp/merlin-people-present.txt

PIXY mic audio
  → pi_client records utterance as WAV
  → resemblyzer generates speaker embedding
  → compares against stored voice embeddings
  → identifies speaker (threshold 0.85)

pi_client sends to brain:
  POST /think {
    "text": "What's the weather?",
    "identity": "nate",           ← voice match (who is SPEAKING)
    "people_present": "ezra,nate"  ← face match (who is VISIBLE)
  }

brain.py formats system prompt:
  "Speaking: nate | Faces visible: ezra,nate"
  → LLM knows to address Nate, acknowledge Ezra is present
```

### Face Recognition

- **Library:** `face_recognition` (dlib) on Pi 5
- **Embeddings:** `/home/pi/RBOS/merlin/faces/embeddings.json`
- **Training photos:** `/home/pi/RBOS/merlin/faces/{name}/*.jpg`
- **Current people:** Ezra (10 encodings), Nate (15), Mel (13)
- **Threshold:** 0.45 (distance — lower is more confident)
- **When it runs:** Once on `face_arrived` state transition (not every frame)
- **Performance:** ~370ms per recognition on Pi 5

**To enroll a new person:**
```bash
# Stop pi_client to free the mic (not needed for face, but good practice)
ssh pi@100.87.156.70 "sudo systemctl stop merlin-pi-client"
# Take 15 photos (uses tracker snapshots since camera is locked)
ssh pi@100.87.156.70 "cd /home/pi/RBOS/merlin && python3 face_enroll.py <name>"
# Train embeddings
ssh pi@100.87.156.70 "cd /home/pi/RBOS/merlin && python3 face_train.py"
# Restart tracker to load new embeddings
ssh pi@100.87.156.70 "sudo systemctl restart merlin-tracker"
```

### Voice Recognition

- **Library:** `resemblyzer` on Pi 5
- **Embeddings:** `/home/pi/RBOS/merlin/voices/voice_embeddings.json`
- **Training audio:** `/home/pi/RBOS/merlin/voices/{name}/*.wav`
- **Current people:** Ezra (1 recording, 30s), Nate (2 recordings, 30s + 2min)
- **Threshold:** 0.85 (cosine similarity — higher is more confident)
- **When it runs:** On every utterance (background thread, non-blocking)
- **Note:** Father/son voices through same mic can be challenging. More recordings help.

**To enroll a new voice:**
```bash
# Stop pi_client to free the mic
ssh pi@100.87.156.70 "sudo systemctl stop merlin-pi-client; pkill arecord"
# Record 2 minutes of natural speech (one person only!)
ssh pi@100.87.156.70 "cd /home/pi/RBOS/merlin && python3 voice_enroll.py <name>"
# Train embeddings
ssh pi@100.87.156.70 "cd /home/pi/RBOS/merlin && python3 voice_train.py"
# Restart pi_client to load new embeddings
ssh pi@100.87.156.70 "sudo systemctl restart merlin-pi-client"
```

---

## Animation System

### Saccadic Eye Contact (during TRACKING)
Replaces continuous drift. Human-like gaze pattern:
- **GAZE** (2-4s dead still on eyes) → **GLANCE** (0.4-0.8s to nearby face point) → **GAZE**
- Glance targets: forehead, temples, cheeks, mouth — always diagonal (both pan + tilt)
- 40% chance of double-glance before returning to gaze
- Resets on face arrival or significant face movement

### Always-On Breathing
Golden-ratio sine layering (quasi-Perlin, never repeats). Scales by state:
- **Tracking:** scale 0.50 — visible life signal while gazing
- **Attentive:** scale 0.65 — restless while waiting for face return
- **Idle:** scale 1.0 — full organic breathing (~5° pan, ~15° tilt)
- Tilt-dominant ratio (2.8x more up/down than side-to-side)
- Upward bias (BREATH_TILT_BIAS = 5°) — Merlin looks UP at person on desk

### Gaze Hold
On face arrival: 2 seconds of absolute zero movement (dead-still eye contact), then breathing fades in over 2 seconds. Re-locks on significant face movement.

### Zone-Based Idle Exploration
12 zones of interest (desk surface, left/right desk, monitor, rooms, behind, ceiling, person_check). Picks a zone, examines 2-4 points, moves to nearby zone. Never returns to center. Person_check zone sweeps center every 15s to find faces.

### Retrack Cooldown
After 60s of tracking, ignores faces for 45s while exploring. Exception: during active conversation (sounds_muted = True), tracking never times out.

### Attention Command
"Hey Merlin" → pi_client sends UDP "attention" to tracker. If idle: snaps to center, enters SEARCHING. If tracking: resets 60s timer.

### Sound Muting
During conversations, tracker mutes all sounds (idle tinks, glance sounds). Pi_client sends "mute" on conversation open, "unmute" on close.

---

## Tool Calling

Gemma 4 26B supports native function calling. Brain.py defines 5 tools:

| Tool | What It Does |
|------|-------------|
| `get_time` | Current date and time |
| `get_weather` | Open-Meteo API, Hope Mills NC (free, no key) |
| `look` | Fresh PIXY frame → Gemma 4 26B vision description |
| `capture` | Save note/reminder to RBOS inbox |
| `get_briefing` | Today's RBOS context (The Thing, energy, what shipped) |

Flow: Ezra asks → Gemma decides to call tool → tool executes → result fed back → Gemma responds naturally.

---

## Reactions Layer (built, not yet wired into live system)

`reactions.py` on Pi 5 — three-tier non-verbal reaction system using Qwen3.5:0.8b via Ollama:
1. **Reflexes** (<100ms, no LLM): loud noise → startle, face → greeting
2. **Behavior chains** (LLM-directed): after reflex, LLM scripts unique follow-up sequence from 10 micro-behaviors
3. **Context classification** (LLM): ambiguous events → 9 reaction types

Adaptive volume: speaker volume tracks ambient noise (√ curve, ALSA mixer, 5s updates).

---

## Audio Architecture

### Path 1: Pi Client (primary conversational path)
```
PIXY mic (USB on Pi, card 3, plughw:3,0)
  → merlin_pi_client.py (RMS VAD on Pi)
  → voice recognition (resemblyzer, background thread)
  → POST /stt on Brain Mac :8900 (Whisper transcription)
  → POST /think {text, identity, people_present} (LLM response)
  → POST /tts (Kokoro audio generation)
  → mpv playback on Pi speaker (plughw:1,0)
```

### Path 2: Camera RTSP (background listening on Mac)
```
Amcrest camera mic (if connected — currently offline)
  → audio_pipeline.py on Brain Mac (ffmpeg → Silero VAD → Whisper)
  → speech event on event bus → brain.py → voice.py → go2rtc speaker push
```

---

## Vision

- **Model:** Gemma 4 26B-A4B (IS a vision model — no separate VL model needed)
- **Frame source:** tracker snapshot via SCP (`/tmp/merlin-snapshot.jpg` on Pi)
- **Passive:** vision.py captures frames every 5-45s, describes scene in background
- **Active:** `look` tool grabs fresh frame, sends inline to Gemma 4 26B
- **Passive vision module:** `passive_vision.py` (built, not deployed) — Qwen3.5:0.8b on Pi classifies presence every 60s to JSONL

---

## LLM Stack

### On Brain Mac (LM Studio :1234)

| Model | Purpose | Notes |
|-------|---------|-------|
| google/gemma-4-26b-a4b | Brain — conversation + vision + tools | Standard model, NOT LoRA (LoRA breaks vision) |
| Kokoro-82M-bf16 | TTS (via mlx-audio in venv) | Voice: am_fenrir |

### On Pi 5 (Ollama :11434)

| Model | Purpose | Notes |
|-------|---------|-------|
| qwen3.5:0.8b | Reactions layer classification | 1GB, ~3-4s per classification |

---

## File Structure

```
merlin/                          # In RBOS (syncs to Pi via Syncthing)
  tracker_pi.py                  # Face tracker + recognition + animation
  merlin_pi_client.py            # Pi-side conversation + voice recognition
  reactions.py                   # LLM-directed non-verbal reactions
  passive_vision.py              # Pi-local presence tracking
  face_enroll.py                 # Capture training photos for face recognition
  face_train.py                  # Generate face embeddings from photos
  voice_enroll.py                # Record voice samples for speaker recognition
  voice_train.py                 # Generate voice embeddings from recordings
  camera_detect.py               # EMEET PIXY camera detection utility
  easing.py                      # PTZ animation easing functions
  ptz_uvc.py                     # UVC PTZ controller for EMEET PIXY

  faces/                         # Face recognition data
    embeddings.json              # Trained face encodings (38 total)
    ezra/                        # 15 training photos
    nate/                        # 26 training photos
    mel/                         # 13 training photos

  voices/                        # Voice recognition data
    voice_embeddings.json        # Trained voice embeddings
    ezra/                        # 1 recording (30s)
    nate/                        # 2 recordings (30s + 2min)

  sounds/                        # Audio files
    *.wav                        # Named sounds (greeting, alert, curious, etc.)
    n1-n5_*.wav                  # Musical note sequences
    tts_cache/                   # Cached TTS responses

  personality/                   # Character sheets (Brand/TARS, NOT King Rhoam)
  briefing/                      # RBOS briefing JSONs
  models/                        # YuNet face detection model
  logs/                          # Session logs, tracking CSVs

~/Code/merlin/                   # On Brain Mac (not in RBOS)
  main.py                        # Orchestrator + HTTP server
  brain.py                       # Identity-aware LLM + tools + conversation
  voice.py                       # Kokoro TTS
  vision.py                      # Frame capture + Gemma 4 scene description
  audio_pipeline.py              # Camera RTSP audio → VAD → STT
  event_bus.py                   # In-process pub/sub
  config.py                      # All settings
  venv/                          # Python 3.14 virtualenv (HAS mlx_audio)
```

---

## Common Issues & Troubleshooting

### Merlin not responding at all
1. **Check Pi services:** `ssh pi@100.87.156.70 "systemctl is-active merlin-tracker merlin-pi-client"`
2. **Check brain:** `curl -s http://localhost:8900/health | python3 -m json.tool`
3. **Brain not running:** `cd ~/Code/merlin && ./venv/bin/python3 -u main.py > /tmp/merlin-brain.log 2>&1 &`
4. **Check logs:** `journalctl -u merlin-tracker --since '5 min ago' --no-pager | tail -20`

### Wrong voice (macOS "say" instead of Kokoro)
- Brain was started with system Python instead of venv
- **Fix:** Kill brain, restart with `~/Code/merlin/venv/bin/python3 -u main.py`
- **Verify:** Check log for "Kokoro TTS ready (voice: am_fenrir)"

### Mic busy (arecord error)
- Another process has the PIXY mic locked
- **Check:** `ssh pi@100.87.156.70 "fuser /dev/snd/pcmC3D0c"`
- **Fix:** `ssh pi@100.87.156.70 "sudo systemctl restart merlin-pi-client"`

### Face recognition says "unknown"
- Recognition only fires on face_arrived (not every frame)
- **Force re-identify:** Have person step away for 10s, then return
- **Check embeddings:** `ssh pi@100.87.156.70 "python3 -c 'import json; d=json.load(open(\"/home/pi/RBOS/merlin/faces/embeddings.json\")); print({k:v[\"count\"] for k,v in d.items()})'"` 
- **Retrain:** Run `face_enroll.py` + `face_train.py` + restart tracker

### Voice recognition misidentifies speaker
- Need more voice samples (especially for similar voices like father/son)
- **Add recording:** Stop pi_client, run `voice_enroll.py <name>`, run `voice_train.py`, restart pi_client
- **Check threshold:** Currently 0.85 in pi_client. Lower = more matches but more errors.

### Camera not found after USB replug
- PIXY device nodes change on replug (/dev/video0 vs /dev/video1)
- `camera_detect.py` handles this automatically on tracker restart
- **Fix:** `ssh pi@100.87.156.70 "sudo systemctl restart merlin-tracker"`

### Tracker goes idle during conversation
- Fixed: tracker checks `sounds_muted` flag before going idle
- During conversation (pi_client sends "mute"), tracker keeps tracking indefinitely
- If still happening: check that pi_client sends "mute" on conversation open

### Tailscale SSH re-auth browser tabs
- Fixed via `~/.ssh/config` on Ezra's Mac — bypasses Tailscale SSH interception
- If tabs return: `ssh pi@100.87.156.70` should use direct SSH keys, not Tailscale SSH

### Port 8900 already in use
- Old brain process didn't die: `lsof -i :8900 -t | xargs kill -9`
- Wait 3 seconds, then restart

---

## Deployment Checklist (after code changes)

### Pi files (tracker, pi_client, reactions, enrollment scripts):
```bash
scp file.py pi@100.87.156.70:/home/pi/RBOS/merlin/
sudo systemctl restart merlin-tracker    # or merlin-pi-client
```

### Brain files (main.py, brain.py, voice.py, vision.py):
```bash
# Edit in ~/Code/merlin/ on the Brain Mac
lsof -i :8900 -t | xargs kill -9; sleep 2
cd ~/Code/merlin && ./venv/bin/python3 -u main.py > /tmp/merlin-brain.log 2>&1 &
```

---

## Key Config Values (config.py)

- `LLM_URL`: `http://localhost:1234/v1/chat/completions` (LM Studio)
- `LLM_MODEL`: `google/gemma-4-26b-a4b`
- `PI_HOST`: `100.87.156.70`
- `KOKORO_VOICE`: `am_fenrir`
- `WAKE_WORDS`: merlin, hey merlin, erlin, marlin, berlin (whisper mangling variants)
- `CONVERSATION_WINDOW`: 60 seconds (no wake word needed within window)

---

*Updated: 2026-04-12 — Face + voice recognition, identity pipeline, animation system, tool calling, vision, reactions layer, systemd services.*

## Organon Concepts

- [[Integration (Mental)]]
- [[Automatization]]
- [[Agency Protection Rule]]
