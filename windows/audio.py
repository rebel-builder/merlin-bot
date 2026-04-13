"""
Audio capture from PIXY USB mic with energy-based voice activity detection.
Captures speech, buffers complete utterances, puts them in a queue for STT.
"""

import sounddevice as sd
import numpy as np
import threading
import queue
import time
from config import (
    PIXY_MIC_DEVICE, SAMPLE_RATE, CHANNELS,
    ENERGY_THRESHOLD, SILENCE_TIMEOUT,
    MIN_UTTERANCE_LENGTH, MAX_UTTERANCE_LENGTH,
)


class AudioPipeline:
    def __init__(self):
        self.mic_device = self._find_pixy_mic()
        self.speech_queue = queue.Queue()
        self._buffer = []
        self._accumulating = False
        self._silence_start = None
        self._running = False
        self._suppressed = threading.Event()

    def _find_pixy_mic(self):
        """Auto-detect PIXY mic by name, or use configured device."""
        if PIXY_MIC_DEVICE is not None:
            return PIXY_MIC_DEVICE

        devices = sd.query_devices()
        for i, d in enumerate(devices):
            if d["max_input_channels"] > 0:
                name = d["name"].lower()
                if "emeet" in name or "pixy" in name:
                    print(f"[audio] Found PIXY mic: [{i}] {d['name']}")
                    return i

        default = sd.default.device[0]
        print(f"[audio] PIXY mic not found by name. Using default input [{default}].")
        return default

    def _audio_callback(self, indata, frames, time_info, status):
        """Called for every audio block from the mic."""
        if status:
            print(f"[audio] Stream warning: {status}")

        # Echo suppression — skip while Merlin is speaking
        if self._suppressed.is_set():
            return

        audio = indata[:, 0].copy()
        rms = np.sqrt(np.mean(audio ** 2))

        if rms > ENERGY_THRESHOLD:
            # Speech detected
            if not self._accumulating:
                self._accumulating = True
                self._buffer = []
            self._buffer.append(audio)
            self._silence_start = None

        elif self._accumulating:
            # Still accumulating but silence now
            self._buffer.append(audio)
            if self._silence_start is None:
                self._silence_start = time.time()
            elif time.time() - self._silence_start > SILENCE_TIMEOUT:
                # Silence long enough — utterance is complete
                self._emit_utterance()

        # Force-cut very long utterances
        if self._accumulating and self._buffer:
            duration = len(self._buffer) * len(indata[:, 0]) / SAMPLE_RATE
            if duration >= MAX_UTTERANCE_LENGTH:
                self._emit_utterance()

    def _emit_utterance(self):
        """Send accumulated audio to the speech queue."""
        if self._buffer:
            utterance = np.concatenate(self._buffer)
            duration = len(utterance) / SAMPLE_RATE
            if duration >= MIN_UTTERANCE_LENGTH:
                self.speech_queue.put(utterance)
        self._buffer = []
        self._accumulating = False
        self._silence_start = None

    def start(self):
        """Start capturing audio from the PIXY mic."""
        self._running = True
        self._stream = sd.InputStream(
            device=self.mic_device,
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="float32",
            blocksize=int(SAMPLE_RATE * 0.03),  # 30ms frames
            callback=self._audio_callback,
        )
        self._stream.start()
        print(f"[audio] Capturing from device [{self.mic_device}] at {SAMPLE_RATE}Hz")

    def stop(self):
        """Stop audio capture."""
        self._running = False
        if hasattr(self, "_stream"):
            self._stream.stop()
            self._stream.close()

    def get_utterance(self, timeout=0.1):
        """Block until a complete utterance is available, or timeout."""
        try:
            return self.speech_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def suppress(self):
        """Suppress mic input (call when Merlin is speaking)."""
        self._suppressed.set()

    def unsuppress(self):
        """Resume mic input (call when Merlin stops speaking)."""
        self._suppressed.clear()
        # Clear any partial buffer from the tail end of playback
        self._buffer = []
        self._accumulating = False
