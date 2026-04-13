# Merlin — How To

*Simple commands for starting, stopping, and controlling Merlin. No technical knowledge needed.*

---

## Is Merlin Running?

```bash
# Check Pi services
ssh pi@100.87.156.70 "systemctl is-active merlin-tracker merlin-pi-client merlin-go2rtc"

# Check brain (on whichever Mac is running it)
curl -s http://localhost:8900/health | python3 -m json.tool
```

---

## Start Merlin

**Pi services (auto-start on boot):**
```bash
ssh pi@100.87.156.70 "sudo systemctl start merlin-tracker merlin-pi-client"
```

**Brain server (must use venv for Kokoro voice):**
```bash
cd ~/Code/merlin && ./venv/bin/python3 -u main.py > /tmp/merlin-brain.log 2>&1 &
```

**LM Studio** must be open with `google/gemma-4-26b-a4b` loaded.

---

## Stop Merlin

```bash
ssh pi@100.87.156.70 "sudo systemctl stop merlin-tracker merlin-pi-client"
lsof -i :8900 -t | xargs kill -9  # kill brain
```

---

## Restart Merlin

```bash
# Pi services
ssh pi@100.87.156.70 "sudo systemctl restart merlin-tracker merlin-pi-client"

# Brain (kill old, start new)
lsof -i :8900 -t | xargs kill -9; sleep 2
cd ~/Code/merlin && ./venv/bin/python3 -u main.py > /tmp/merlin-brain.log 2>&1 &
```

---

## Volume Control

Merlin speaks through the USB speaker on the Pi (ALSA card 1, control "PCM", range 0–240).

```bash
ssh pi@100.87.156.70 "amixer -c 1 sget PCM"       # see current volume
ssh pi@100.87.156.70 "amixer -c 1 sset PCM 103"    # ~43%, normal desk
ssh pi@100.87.156.70 "amixer -c 1 sset PCM 24"     # ~10%, whisper
ssh pi@100.87.156.70 "amixer -c 1 sset PCM 200"    # ~83%, loud
```

| Level | Raw Value | Use Case |
|-------|-----------|----------|
| 24    | 10%       | Whisper — someone sleeping nearby |
| 60    | 25%       | Quiet desk, focused work |
| 103   | 43%       | Normal desk conversation |
| 135   | 56%       | Louder — noisy room |
| 200   | 83%       | Max useful |

---

## Voice Commands

| Say this | What happens |
|----------|-------------|
| "Hey Merlin" | Wakes him up, starts conversation, camera snaps to center |
| "Nevermind" | Ends conversation |
| "Stop listening" / "Mute" | Mutes until "Hey Merlin" |
| "Back to work" / "That's all" | Politely ends conversation |

After Merlin responds, you have ~60 seconds to keep talking without saying "Hey Merlin" again.

---

## Face & Voice Recognition

Merlin recognizes Ezra, Nate, and Mel by face AND voice.

**Enroll a new face:**
```bash
# Person sits in front of PIXY, moves head slightly for 30 seconds
ssh pi@100.87.156.70 "cd /home/pi/RBOS/merlin && python3 face_enroll.py <name>"
ssh pi@100.87.156.70 "cd /home/pi/RBOS/merlin && python3 face_train.py"
ssh pi@100.87.156.70 "sudo systemctl restart merlin-tracker"
```

**Enroll a new voice:**
```bash
# Must free mic first! Person talks alone for 2 minutes.
ssh pi@100.87.156.70 "sudo systemctl stop merlin-pi-client; pkill arecord"
ssh pi@100.87.156.70 "cd /home/pi/RBOS/merlin && python3 voice_enroll.py <name>"
ssh pi@100.87.156.70 "cd /home/pi/RBOS/merlin && python3 voice_train.py"
ssh pi@100.87.156.70 "sudo systemctl start merlin-pi-client"
```

**Check who Merlin sees:**
```bash
ssh pi@100.87.156.70 "cat /tmp/merlin-identity.txt"
```

---

## Tools (ask Merlin these)

| Ask | Tool Used |
|-----|-----------|
| "What time is it?" | get_time |
| "What's the weather?" | get_weather (Open-Meteo, no API key) |
| "What do you see?" | look (fresh PIXY frame → Gemma 4 vision) |
| "Capture: buy milk" | capture (saves to RBOS inbox) |
| "What's my briefing?" | get_briefing (The Thing, energy, what shipped) |

---

## Quick Troubleshooting

**Wrong voice (robotic macOS voice):**
Brain started with wrong Python. Kill and restart with venv:
```bash
lsof -i :8900 -t | xargs kill -9; sleep 2
cd ~/Code/merlin && ./venv/bin/python3 -u main.py > /tmp/merlin-brain.log 2>&1 &
```

**Mic busy:**
```bash
ssh pi@100.87.156.70 "sudo systemctl restart merlin-pi-client"
```

**Merlin doesn't recognize me:**
Step away from camera for 10 seconds, come back. Recognition fires on arrival.

**Camera not found after USB replug:**
```bash
ssh pi@100.87.156.70 "sudo systemctl restart merlin-tracker"
```

**Check logs:**
```bash
# Tracker (face recognition, animation)
ssh pi@100.87.156.70 "journalctl -u merlin-tracker --since '5 min ago' --no-pager | tail -20"

# Pi client (voice, conversations)
ssh pi@100.87.156.70 "journalctl -u merlin-pi-client --since '5 min ago' --no-pager | tail -20"

# Brain (LLM responses, tools, vision)
tail -30 /tmp/merlin-brain.log
```

---

*Last updated: 2026-04-12 — Face + voice recognition, systemd services, tool calling, identity pipeline.*

## Organon Concepts

- [[Automatization]]
- [[Theory-Practice Dichotomy]]
- [[Productiveness]]
