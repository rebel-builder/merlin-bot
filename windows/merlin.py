"""
Merlin — Ambient AI Companion (Windows Edition)
================================================

Single-machine setup: EMEET PIXY + MSI laptop + BT speaker.
Everything runs locally. No cloud. No Pi needed.

Start:  python merlin.py
Stop:   Ctrl+C

Prerequisites:
  - LM Studio running with a model loaded (localhost:1234)
  - EMEET PIXY plugged in via USB
  - BT speaker connected
  - All pip dependencies installed (see requirements.txt)
  - Kokoro model files in this directory (see voice.py)
"""

import threading
import signal
import sys
import time

from config import WAKE_WORDS
from audio import AudioPipeline
from stt import STT
from voice import Voice
from brain import Brain
from tracker import FaceTracker
import sounds


class Merlin:
    def __init__(self):
        print()
        print("=" * 50)
        print("  MERLIN — Booting up...")
        print("=" * 50)
        print()

        self.audio = AudioPipeline()
        self.stt = STT()
        self.voice = Voice()
        self.brain = Brain()
        self.tracker = FaceTracker()

        # Wire face events to brain
        self.tracker.on_face_arrived = self._on_face_arrived
        self.tracker.on_face_lost = self._on_face_lost

        self._running = False

    def _on_face_arrived(self):
        """Someone sat down at the desk."""
        greeting = self.brain.on_face_arrived()
        if greeting:
            self.audio.suppress()
            sounds.greeting()
            print(f"  Merlin: {greeting}")
            self.voice.speak(greeting)
            self.audio.unsuppress()

    def _on_face_lost(self):
        """Desk is empty. Merlin stays quiet."""
        pass

    def start(self):
        """Start all modules and enter the main conversation loop."""
        self._running = True

        # Audio capture (continuous mic listening)
        self.audio.start()

        # Face tracker (background thread)
        tracker_thread = threading.Thread(target=self.tracker.run, daemon=True)
        tracker_thread.start()

        print()
        print("=" * 50)
        print("  Merlin is listening.")
        print(f"  Say \"{WAKE_WORDS[0]}\" to start talking.")
        print(f"  Say \"stop listening\" to mute.")
        print("  Press Ctrl+C to quit.")
        print("=" * 50)
        print()

        try:
            while self._running:
                # 1. Wait for a complete utterance from the mic
                audio_data = self.audio.get_utterance(timeout=0.2)
                if audio_data is None:
                    continue

                # 2. Transcribe speech to text
                text = self.stt.transcribe(audio_data)
                if not text:
                    continue

                print(f"  You: {text}")

                # 3. Play listening chime so user knows Merlin heard them
                self.audio.suppress()
                sounds.listening()

                # 4. Process through brain (wake word, mute, LLM)
                response = self.brain.process(text)
                if response is None:
                    self.audio.unsuppress()
                    continue

                # 5. Thinking sound while response was generated
                sounds.thinking()

                print(f"  Merlin: {response}")

                # 6. Speak the response
                self.voice.speak(response)
                time.sleep(0.3)  # small gap before re-opening mic
                self.audio.unsuppress()

        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def stop(self):
        """Graceful shutdown."""
        print("\n  Shutting down...")
        self._running = False
        self.audio.stop()
        self.tracker.stop()
        print("  Merlin offline. Goodbye.")


def main():
    merlin = Merlin()

    def handle_exit(sig, frame):
        merlin.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_exit)
    merlin.start()


if __name__ == "__main__":
    main()
