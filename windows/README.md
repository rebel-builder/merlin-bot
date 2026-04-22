# Merlin — Windows Edition

## What Is Merlin

Merlin is a local AI companion that lives on your desk. It listens for your voice, responds with speech, and watches the room with a camera — all running on your own hardware, no cloud, no subscription. Talk to it by saying "Hey Merlin," and it answers in a calm, direct voice using a local language model you control.

---

## `Hardware Requirements`

| Component | Minimum | Recommended |
|---|---|---|
| GPU | NVIDIA GTX 1060 (6 GB VRAM) | RTX 4060 or better |
| RAM | 8 GB | 16 GB |
| Camera | Any USB webcam | EMEET PIXY (auto-detected) |
| Speaker | Any output device | Bluetooth speaker |
| Python | 3.11+ | 3.11+ |

> No NVIDIA GPU? Set `WHISPER_DEVICE = "cpu"` and `WHISPER_COMPUTE = "int8"` in `config.py`. It will work — just slower.

---

## `Quick Start`

**Step 1 — Install Python**
Download Python 3.11+ from [python.org](https://python.org). During install, check **"Add Python to PATH"**.

**Step 2 — Install and start LM Studio**
Download [LM Studio](https://lmstudio.ai), load any chat model (Gemma, Llama, Mistral — anything works), and click **Start Server**. Leave it running.

**Step 3 — Get Kokoro voice files**
Download these two files from [kokoro-onnx releases](https://github.com/thewh1teagle/kokoro-onnx/releases) and put them in this folder:
- `kokoro-v1.0.onnx`
- `voices-v1.0.bin`

**Step 4 — Run setup**
Double-click `setup.bat`. It installs all Python packages, downloads the face detection model, and verifies your LM Studio connection. Takes 2–5 minutes.

**Step 5 — Start Merlin**
```
venv\Scripts\activate
python merlin.py
```
Say **"Hey Merlin"** to start talking.

---

## `Architecture`

```
YOU (voice)
    |
    v
[EMEET PIXY mic]
    |
    v
[audio.py] — energy-based VAD, buffers speech
    |
    v
[stt.py] — faster-whisper (Whisper Small, CUDA)
    |
    v
[brain.py] — wake word check, conversation window, LM Studio call
    |         (strips <think> tokens from reasoning models)
    v
[voice.py] — Kokoro ONNX → speech audio
    |
    v
[SPEAKER output]

[tracker.py] ← runs in parallel, YuNet face detection
    |
    +-- greets you when you sit down
    +-- PTZ camera follow (if supported by your camera)
```

---

## `Known Issues`

- **PTZ camera follow** only works with cameras that expose pan/tilt via DirectShow. The EMEET PIXY supports it; most webcams do not. Merlin still detects faces either way.
- **First boot is slow.** Whisper downloads its model on first run (~150 MB). Kokoro loads a large ONNX file. Expect 20–30 seconds before Merlin says it's listening.
- **Reasoning models emit thinking tokens.** Models like Gemma 3 include `<think>...</think>` blocks. Merlin strips these automatically, but if response time is very long (10+ seconds), your model may be thinking too hard. Use a smaller or non-reasoning model.
- **GPU memory errors.** If Whisper crashes with a CUDA out-of-memory error, switch to `WHISPER_MODEL = "tiny"` in `config.py`.

---

## `Troubleshooting`

**"SSL certificate error" when downloading the face model**
Merlin disables SSL verification for the YuNet model download. If it still fails, download the file manually from:
`https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx`
Save it as `face_detection_yunet_2023mar.onnx` in the Merlin folder.

**"Cannot reach LM Studio at localhost:1234"**
LM Studio must be running with a model loaded before you start Merlin. Open LM Studio, pick a model, and click **Start Server** in the Local Server tab. Then restart Merlin.

**Merlin responds but nothing comes out of the speaker**
Check that your speaker is set as the Windows default output device. If you want to use a specific device, set `SPEAKER_DEVICE` to a device index number in `config.py`. Run `python -c "import sounddevice; print(sounddevice.query_devices())"` to list available devices.

**Merlin picks up its own voice / echo loops**
This is usually caused by mic sensitivity. Raise `ENERGY_THRESHOLD` in `config.py` (try `0.04` or `0.06`). Also make sure your mic is not close to the speaker.

**CUDA errors on startup**
Make sure you have the NVIDIA CUDA drivers installed (not just the game driver — the full CUDA toolkit). Or bypass GPU entirely by setting `WHISPER_DEVICE = "cpu"` and `WHISPER_COMPUTE = "int8"` in `config.py`.

**Merlin ignores everything I say**
By default, Merlin requires "Hey Merlin" to start a conversation. After Merlin responds, you have 30 seconds to keep talking without a wake word. If you said "stop listening" at any point, say "wake up" or "Hey Merlin" to resume.

---

## `Contributing`

Pull requests welcome. The codebase is intentionally flat — one file per module:

| File | What it does |
|---|---|
| `merlin.py` | Entry point, wires all modules together |
| `config.py` | All settings — edit this first |
| `audio.py` | Mic capture and voice activity detection |
| `stt.py` | Speech-to-text via faster-whisper |
| `brain.py` | Wake word logic and LM Studio conversation |
| `voice.py` | Text-to-speech via Kokoro ONNX |
| `tracker.py` | Face detection and PTZ camera control |
| `sounds.py` | Short audio feedback cues |

Fork, change what you need, and open a PR. If you add a new audio device or camera that works well, please add it to the Known Issues and Troubleshooting sections.
