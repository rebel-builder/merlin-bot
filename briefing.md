# Merlin Briefing
Date: 2026-04-12 (Sunday)
Energy: Green (incredible build day, product validated)
The Thing: Weekly review + W11 sprint planning
Sprint: W10, Day 7 (review tonight or tomorrow)

## What shipped today
- Face + voice recognition live — Merlin identifies Ezra vs Nate by face AND voice
- Vision working — saw sunglasses on Ezra's face, describes scenes from PIXY
- Prompt loosened — fortune cookie to real companion. Nate talked for HOURS.
- Identity pipeline: tracker → pi_client → brain. "Speaking: nate | Faces visible: ezra,nate"
- Brain migrated to Nate's Mac — LaunchAgent updated, all weekend code deployed
- Tailscale SSH fixed on both Macs (no more browser tab floods)
- 15 open loops pushed to ClickUp, checkpoint skill rewritten
- ARCHITECTURE.md and HOW-TO.md comprehensively updated

## Product validation
Nate (12yo) used Merlin as a building partner for hours. Designed a Minecraft castle together. Asked for design opinions on glass types. Set timers. Showed graph paper sketches. Said: "I like Merlin a lot more than Vector." This is body doubling working.

## What's next
- Weekly review (tonight or tomorrow morning)
- W11 sprint planning — Rise escalation as Big Three #1
- Monday: Grez meeting with Grant 1-2pm (one-pager ready)
- Apple Watch Series 8 arrives Tuesday
- Review night shift code deliverables (6 modules in workspace/review/)

## Reminders
- Brain runs on Nate's Mac now (LaunchAgent com.merlin.brain)
- Brain MUST use venv for Kokoro: ~/Code/merlin/venv/bin/python3
- Pi client points to Nate's Mac (100.123.211.1:8900)
- Monday Gate: no work until review + sprint plan done
