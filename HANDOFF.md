# Merlin v2 — Build Handoff

*Written 2026-04-03 after first successful live conversation. Read this before working on Merlin.*

**Start here:** `product/merlin-v2-spec.md` — the canonical architecture spec.

---

## What's Running Right Now

Merlin v2 is running on Nate's Mac via `test_loop.py` (not the full orchestrator). Three modules active:

- **audio_pipeline.py** — Silero VAD + mlx-whisper STT, pulling audio from Pi's go2rtc RTSP
- **voice.py** — Kokoro TTS (am_fenrir voice), SCP to Pi, go2rtc camera speaker playback
- **brain.py** — Gemma 4 26B-A4B via LM Studio, King Rhoam personality, RBOS briefing context

**To restart Merlin:**
```bash
ssh ezradrake@nates-m1-max.lan
export PATH=/opt/homebrew/bin:$PATH
cd ~/Documents/RBOS/merlin
source .venv/bin/activate
pkill -f test_loop.py
python3 -u test_loop.py  # or: nohup python3 -u test_loop.py >> /tmp/merlin-v2.log 2>&1 &
```

**To check if running:**
```bash
ssh ezradrake@nates-m1-max.lan 'ps aux | grep test_loop | grep -v grep'
```

**To check health (only works with main.py, not test_loop):**
```bash
curl http://100.123.211.1:8900/health
```

**To see logs:**
```bash
ssh ezradrake@nates-m1-max.lan 'tail -30 /tmp/merlin-v2.log'
```

---

## Known Issues (Priority Order)

### 1. RTSP Stream Drops During Speaker Playback
**Symptom:** Audio pipeline disconnects and reconnects right after Merlin speaks. Conversation gets interrupted.
**Cause:** The SCP + go2rtc speaker push likely disrupts the same go2rtc RTSP stream that audio_pipeline is reading from. Both are consumers of the same go2rtc "merlin" stream.
**Impact:** Merlin loses the last utterance during the drop. He reconnects automatically (1s backoff) but the in-progress transcription is lost.
**Possible fixes:**
- Separate go2rtc streams for audio input vs speaker output
- Use a dedicated RTSP consumer that buffers during speaker push
- Add a brief grace period after speaking where new audio is discarded anyway (echo suppression already does this, but the stream drop happens first)

### 2. Echo Suppression Needs Hardening
**Symptom:** Merlin occasionally hears himself and responds to his own speech, creating a loop.
**Cause:** Audio propagation delay (Mac → SCP → Pi → go2rtc → camera speaker → camera mic → RTSP → Mac) is variable. The 500ms post-speaking padding may not be enough.
**Current mitigations:** `speaking_started`/`speaking_finished` events suppress VAD, plus 500ms padding, plus echo detection in brain.py (ignores text >50% similar to last spoken via SequenceMatcher).
**Possible fixes:**
- Increase `ECHO_SUPPRESSION_PADDING` from 0.5s to 1.0s or more
- Keep a rolling buffer of last 3 spoken texts for echo matching
- Measure actual propagation delay and set padding dynamically

### 3. Whisper Mangles "Merlin" Consistently
**Symptom:** "Hey Merlin" transcribes as "Hey Erlin", "I'm Erlin", "Hey Berlin", etc.
**Current fix:** Expanded `WAKE_WORDS` in config.py to include common misheards: erlin, marlin, berlin, murlin.
**Better fix:** Switch to Parakeet v3 (already in the spec, not yet tested). Parakeet may handle the name better. Or: add a custom vocabulary/hotword to the STT model.

### 4. Parakeet v3 Not Working Yet
**Symptom:** audio_pipeline.py falls back to mlx-whisper because the mlx-audio STT API (`load_model` + `model.generate`) crashes with "Processor not found" on whisper-small-mlx.
**Cause:** The model loads but the HuggingFace processor is missing from the cached model files. This is an mlx-audio compatibility issue.
**Fix:** Try loading a Parakeet model directly (`mlx-community/parakeet-tdt-0.6b-v3`) instead of a Whisper model through the mlx-audio API. Or install `transformers` processor files for whisper-small-mlx.

### 5. Vision Not in Live Loop
**Symptom:** nanoLLaVA model loaded successfully in main.py but vision.py is not wired into test_loop.py.
**Fix:** Either switch from test_loop.py to main.py (full orchestrator), or add Vision to test_loop.py.

### 6. main.py Orchestrator Not Deployed
**Symptom:** Running test_loop.py manually instead of main.py with supervision.
**Impact:** No auto-restart on crash, no /health endpoint, no tracker bridge HTTP listener.
**Fix:** Switch to main.py. Need to add `SO_REUSEADDR` to the HTTP server to prevent "Address already in use" on restart. Then bootstrap the LaunchAgent.

### 7. LaunchAgent Not Installed
**Symptom:** Merlin must be started manually via SSH. No auto-start on login.
**Fix:** Copy updated plist to `~/Library/LaunchAgents/` on Nate's Mac and bootstrap:
```bash
cp ~/Documents/RBOS/merlin/systemd/com.merlin.brain.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.merlin.brain.plist
```
**Prereq:** Grant Full Disk Access to `/Users/ezradrake/Documents/RBOS/merlin/.venv/bin/python3` in System Settings.

### 8. iCloud Sync Lag for Code Changes
**Symptom:** Code edited on Ezra's Mac doesn't appear on Nate's Mac for minutes. Had to manually SCP files.
**Workaround:** After editing code, push directly: `cat merlin/file.py | ssh ezradrake@nates-m1-max.lan 'cat > ~/Documents/RBOS/merlin/file.py'`
**Better fix:** Consider a post-edit script or rsync trigger. Or just always SCP after edits.

### 9. EQ Needs Proper Tuning
**Symptom:** Current EQ is the "least bad" option — too harsh but clearest. Proper tuning needs a visualizer.
**Fix:** Use a parametric EQ tool with visual feedback. The ffmpeg EQ chain in voice.py's `apply_speaker_eq()` function is easily adjustable.

---

## What's Working Well

- **Audio pipeline architecture** — three-layer design (stream → VAD → STT) with auto-reconnect and independent error handling. Stream drops recover in 1s.
- **Silero VAD** — correctly detects speech vs silence/noise. No more RMS threshold false triggers.
- **Kokoro TTS** — generates high-quality speech locally in ~2s. am_fenrir voice selected and fits the character.
- **SCP speaker path** — reliable once we figured out go2rtc reads files from Pi's local filesystem.
- **Brain conversation quality** — King Rhoam personality is dialed in. RBOS context works. Conversation history maintained.
- **Modular architecture** — each module starts/stops independently. Adding new modules = import + register.

---

## Next Session Priorities

1. **Fix RTSP drop during playback** — this is the #1 stability issue
2. **Deploy main.py** — add SO_REUSEADDR, test supervision, install LaunchAgent
3. **Wire vision into live loop** — test "What do you see?"
4. **Test Parakeet v3** — better STT quality if we can get the API working
5. **Stability soak** — run 24h, monitor /health, fix what breaks

---

## File Map

```
merlin/
  main.py              # Orchestrator (written, not deployed — use test_loop.py for now)
  test_loop.py         # Simplified loop: audio + brain + voice (currently running)
  audio_pipeline.py    # RTSP → Silero VAD → mlx-whisper STT
  voice.py             # Kokoro TTS → SCP to Pi → go2rtc speaker
  brain.py             # Gemma 4 26B-A4B via LM Studio + King Rhoam + RBOS context
  vision.py            # nanoLLaVA frame capture (written, not in live loop)
  event_bus.py         # Pub/sub connecting modules
  config.py            # All settings
  tracker.py           # Pi face tracking + brain notification bridge

  .venv/               # Python venv on Nate's Mac (not in git)
  archive/brain_v1.py  # Old monolithic brain.py
  archive/senses_v1.py # Old senses.py
  briefing/*.json      # RBOS context files for brain
  personality/         # Character source material
  sounds/              # Nonverbal audio files
  systemd/             # LaunchAgent plist
```

---

## Environment (Nate's Mac)

- **Python:** 3.14.3 via Homebrew (`/opt/homebrew/bin/python3`)
- **Venv:** `~/Documents/RBOS/merlin/.venv/`
- **LM Studio:** Gemma 4 26B-A4B loaded (OpenAI-compatible API on port 1234)
- **Models cached:** whisper-small-mlx, nanoLLaVA-1.5-4bit, Kokoro-82M, Silero VAD
- **SSH:** `ssh ezradrake@nates-m1-max.lan` or `ssh ezradrake@100.123.211.1`
- **Pi SSH:** `ssh pi@100.87.156.70` (Tailscale, no password)

---

*"You are Ezra. What does that mean for the build?"*
*— Merlin's first real conversation, April 3, 2026*

## Organon Concepts

- [[Theory-Practice Dichotomy]]
- [[Automatization]]
- [[Proof]]
- [[Grammar]]
