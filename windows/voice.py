"""
Text-to-speech via Kokoro ONNX, played through the default audio output (BT speaker).
"""

import sounddevice as sd
from config import KOKORO_VOICE, KOKORO_SPEED, SPEAKER_DEVICE


class Voice:
    def __init__(self):
        self.tts = None
        print("[voice] Loading Kokoro TTS...")
        try:
            import kokoro_onnx
            import os

            # Kokoro needs model files in the working directory
            model_file = "kokoro-v1.0.onnx"
            voices_file = "voices-v1.0.bin"

            if not os.path.exists(model_file) or not os.path.exists(voices_file):
                print("[voice] Kokoro model files not found.")
                print(f"[voice] Download these two files into the merlin folder:")
                print(f"[voice]   1. {model_file}")
                print(f"[voice]   2. {voices_file}")
                print(f"[voice] From: https://github.com/thewh1teagle/kokoro-onnx/releases")
                print("[voice] TTS disabled until model files are present.")
                return

            self.tts = kokoro_onnx.Kokoro(model_file, voices_file)
            print(f"[voice] Kokoro loaded. Voice: {KOKORO_VOICE}")
        except ImportError:
            print("[voice] kokoro-onnx not installed. Run: pip install kokoro-onnx")
        except Exception as e:
            print(f"[voice] Failed to load Kokoro: {e}")

    def speak(self, text):
        """Generate speech and play it through the BT speaker."""
        if not self.tts or not text:
            return

        try:
            samples, sample_rate = self.tts.create(
                text,
                voice=KOKORO_VOICE,
                speed=KOKORO_SPEED,
            )
            # Play through default output device (BT speaker)
            sd.play(samples, samplerate=sample_rate, device=SPEAKER_DEVICE)
            sd.wait()  # Block until playback finishes
        except Exception as e:
            print(f"[voice] Playback error: {e}")
