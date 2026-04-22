# Merlin — Windows Edition

*Claude Code: read this first. This tells you what Merlin is and how to fix it.*

## What This Is

Merlin is an ambient AI desk companion. Camera follows your face (PTZ), listens via mic, thinks via local LLM (LM Studio), speaks via TTS. All local, no cloud.

This `windows/` directory is the single-machine Windows build. Everything runs on one laptop — no Pi, no second Mac.

## Architecture

```
EMEET PIXY (USB) → face tracking + mic input
     ↓
Python scripts → VAD → STT (faster-whisper, CUDA) → LLM (LM Studio) → TTS (Kokoro ONNX) → speaker
```

## Files

| File | Purpose |
|------|---------|
| `merlin.py` | Main orchestrator — starts everything, conversation loop |
| `config.py` | All settings (thresholds, devices, wake words, LLM URL) |
| `audio.py` | Mic capture from PIXY via sounddevice + energy-based VAD |
| `stt.py` | Speech-to-text via faster-whisper (CUDA) |
| `voice.py` | Text-to-speech via Kokoro ONNX → speaker |
| `brain.py` | LLM conversation via LM Studio API + wake word + mute logic |
| `tracker.py` | Face detection (YuNet) + PTZ tracking via OpenCV |
| `sounds.py` | Synthesized notification tones (listening, thinking, greeting) |
| `setup.bat` | One-time setup: venv, deps, model downloads |
| `requirements.txt` | Python dependencies |

## How to Start

```powershell
cd C:\merlin
venv\Scripts\activate
python merlin.py
```

**Prerequisites:**
1. LM Studio running with a model loaded (localhost:1234)
2. EMEET PIXY plugged in via USB
3. Speaker connected (BT or USB)

## Common Issues and Fixes

### "No module named 'sounddevice'" (or any module)
Not in the venv. Run: `venv\Scripts\activate` first. Look for `(venv)` in the prompt.

### SSL error downloading YuNet model
Windows Python SSL certs. Download manually from browser:
https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx
Save to the merlin folder.

### "Library cublas64_12.dll is not found"
Missing CUDA math library. Run: `pip install nvidia-cublas-cu12`

### LM Studio not responding / blank responses
- Is LM Studio open? Is the server tab running?
- Is a model loaded? (Most common issue — LM Studio runs but no model selected)
- Check: `curl http://localhost:1234/v1/models` — should list a model

### Camera doesn't open / wrong camera
Change `CAMERA_INDEX` in `config.py` to 0, 1, or 2. The PIXY may not be index 0 if the laptop has a built-in webcam.

### Merlin speaks his thinking out loud
The LLM is outputting `<think>...</think>` blocks. brain.py strips these, but if it's not working, check the model's settings in LM Studio — some models have a "thinking" toggle.

### Smart App Control blocks merlin.py
Windows security feature. Disable in System Settings > Privacy & Security > Smart App Control.

### Git not installed (Claude Code won't start)
Download Git for Windows: https://git-scm.com/download/win
Check "Add to PATH" during install.

### Sounds are system default, not custom
Sound files may not be found. Check that `sounds.py` is in the merlin folder. The Windows build uses synthesized tones, not WAV files.

### No audio output
Check that the speaker is set as default in Windows Sound Settings. `config.py` uses `SPEAKER_DEVICE = None` which means default output.

## Deployment Checklist (for Grant)

1. [ ] TeamViewer QuickSupport on user's machine
2. [ ] Connect via TeamViewer, disable computer sound (prevents echo)
3. [ ] Install Git if missing
4. [ ] Install Python 3.11+ (check "Add to PATH")
5. [ ] Copy merlin folder to C:\merlin
6. [ ] Run setup.bat (or manual: venv, pip install, model downloads)
7. [ ] Download Kokoro models (kokoro-v1.0.onnx + voices-v1.0.bin)
8. [ ] Install LM Studio, download a model (gemma-4-4b-it), start server
9. [ ] Disable Smart App Control if blocking
10. [ ] Run `python merlin.py` — verify hear/think/speak loop
11. [ ] Create desktop shortcut with "Run as Administrator"

## What Merlin Can Do (Windows)

- **Wake word:** "Hey Merlin" / "Merlin" (+ many fuzzy variants)
- **Conversation:** responds via LLM, 30s window after response (no wake word needed)
- **Face tracking:** follows your face with PTZ (if OpenCV PTZ works on your camera)
- **Mute:** "Stop listening" / "Mute" / "Go to sleep"
- **Unmute:** "Wake up" / "Start listening" / "Hey Merlin" (always overrides mute)
- **Dismiss:** "Nevermind" / "Never mind"
