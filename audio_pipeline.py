"""Merlin v2 — Audio pipeline: RTSP capture → Silero VAD → Parakeet v3 STT.

Three layers:
1. StreamManager: connects to Pi's go2rtc RTSP, outputs PCM 16kHz mono
2. VAD: Silero voice activity detection, accumulates complete utterances
3. STT: Parakeet v3 transcription via mlx-audio
"""

from __future__ import annotations

import logging
import struct
import subprocess
import tempfile
import threading
import time
import wave
from pathlib import Path

import numpy as np

from event_bus import EventBus
import config

log = logging.getLogger("merlin.audio")

# ── Layer 1: Stream Manager ──────────────────────────────────────


class StreamManager:
    """Pulls PCM audio from RTSP via ffmpeg. Auto-reconnects on drop."""

    CHUNK_SAMPLES = 512  # ~32ms at 16kHz — good for VAD frame size

    def __init__(self):
        self._proc = None
        self._running = False
        self._backoff = 1.0

    def start(self):
        self._running = True

    def stop(self):
        self._running = False
        self._kill_proc()

    def _kill_proc(self):
        if self._proc:
            try:
                self._proc.kill()
                self._proc.wait(timeout=5)
            except Exception:
                pass
            self._proc = None

    def read_chunks(self):
        """Generator yielding PCM byte chunks. Reconnects on failure."""
        chunk_bytes = self.CHUNK_SAMPLES * 2  # 16-bit = 2 bytes per sample

        while self._running:
            try:
                rtsp_url = config.CAMERA_RTSP_AUDIO
                log.info(f"Connecting to camera RTSP: {config.CAMERA_IP}")
                self._proc = subprocess.Popen(
                    ["ffmpeg", "-rtsp_transport", "tcp",
                     "-i", rtsp_url,
                     "-vn", "-acodec", "pcm_s16le",
                     "-ar", str(config.MIC_SAMPLE_RATE), "-ac", "1",
                     "-f", "s16le", "pipe:1"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )
                log.info("RTSP connected, streaming audio")
                self._backoff = 1.0  # reset on successful connect

                while self._running and self._proc.poll() is None:
                    pcm = self._proc.stdout.read(chunk_bytes)
                    if not pcm or len(pcm) < chunk_bytes:
                        break
                    yield pcm

                log.warning("RTSP stream ended")
            except Exception:
                log.exception("RTSP error")
            finally:
                self._kill_proc()

            if self._running:
                log.info(f"Reconnecting in {self._backoff:.0f}s")
                time.sleep(self._backoff)
                self._backoff = min(self._backoff * 2, 30.0)


# ── Layer 2: Voice Activity Detection ────────────────────────────


class VoiceDetector:
    """Silero VAD — detects speech boundaries, accumulates complete utterances."""

    def __init__(self):
        self._model = None
        self._speech_buffer = bytearray()
        self._silence_after_speech = 0.0
        self._in_speech = False

    def load(self):
        """Load Silero VAD model."""
        try:
            import torch
            model, utils = torch.hub.load(
                repo_or_dir='snakers4/silero-vad',
                model='silero_vad',
                trust_repo=True
            )
            self._model = model
            self._get_speech_prob = model
            log.info("Silero VAD loaded (torch)")
        except Exception:
            log.warning("torch not available — falling back to RMS-based VAD")
            self._model = None

    def process_chunk(self, pcm_bytes: bytes, suppressed: bool = False, bus: EventBus = None) -> bytes | None:
        """Process a PCM chunk. Returns complete utterance bytes when speech ends, else None.

        Args:
            pcm_bytes: raw PCM 16-bit mono audio
            suppressed: if True, discard audio (echo suppression)
            bus: event bus for emitting vad_start/vad_end events
        """
        if suppressed:
            if self._in_speech and bus:
                bus.emit("vad_end")
            self._reset()
            return None

        if self._model is not None:
            return self._process_silero(pcm_bytes, bus)
        else:
            return self._process_rms(pcm_bytes, bus)

    def _process_silero(self, pcm_bytes: bytes, bus: EventBus = None) -> bytes | None:
        import torch
        # Convert PCM bytes to float tensor
        samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        tensor = torch.from_numpy(samples)

        # Get speech probability
        prob = self._model(tensor, config.MIC_SAMPLE_RATE).item()

        if prob > config.VAD_THRESHOLD:
            self._speech_buffer.extend(pcm_bytes)
            self._silence_after_speech = 0.0
            if not self._in_speech:
                self._in_speech = True
                if bus:
                    bus.emit("vad_start")
                log.debug("Speech started")
        elif self._in_speech:
            # Silence after speech — accumulate and check timeout
            self._speech_buffer.extend(pcm_bytes)
            chunk_duration = len(pcm_bytes) / 2 / config.MIC_SAMPLE_RATE
            self._silence_after_speech += chunk_duration

            if self._silence_after_speech >= config.UTTERANCE_SILENCE_TIMEOUT:
                # Complete utterance
                utterance = bytes(self._speech_buffer)
                self._reset()
                if bus:
                    bus.emit("vad_end")
                log.debug(f"Utterance complete ({len(utterance)} bytes)")
                return utterance

        return None

    def _process_rms(self, pcm_bytes: bytes, bus: EventBus = None) -> bytes | None:
        """Fallback RMS-based VAD when torch is unavailable."""
        samples = struct.unpack(f"{len(pcm_bytes)//2}h", pcm_bytes)
        rms = (sum(s * s for s in samples) / len(samples)) ** 0.5

        if rms > 150:  # speech threshold
            self._speech_buffer.extend(pcm_bytes)
            self._silence_after_speech = 0.0
            if not self._in_speech:
                self._in_speech = True
        elif self._in_speech:
            self._speech_buffer.extend(pcm_bytes)
            chunk_duration = len(pcm_bytes) / 2 / config.MIC_SAMPLE_RATE
            self._silence_after_speech += chunk_duration

            if self._silence_after_speech >= config.UTTERANCE_SILENCE_TIMEOUT:
                utterance = bytes(self._speech_buffer)
                self._reset()
                return utterance

        return None

    def _reset(self):
        self._speech_buffer = bytearray()
        self._silence_after_speech = 0.0
        self._in_speech = False


# ── Layer 3: Speech-to-Text ──────────────────────────────────────


class Transcriber:
    """STT via mlx-audio (whisper model) with generate API."""

    def __init__(self):
        self._model = None
        self._backend = None  # "mlx-audio" or "mlx-whisper"

    def load(self):
        """Load STT model. Use mlx-whisper (proven reliable)."""
        try:
            import mlx_whisper
            self._backend = "mlx-whisper"
            log.info("STT loaded: mlx-whisper")
        except ImportError:
            log.warning("No STT backend available — pip install mlx-whisper")

    def transcribe_file(self, wav_path: str) -> str:
        """Transcribe a WAV file to text. Used by /stt endpoint for Pi client."""
        if not self._backend:
            return ""
        try:
            if self._backend == "mlx-whisper":
                import mlx_whisper
                result = mlx_whisper.transcribe(
                    wav_path,
                    path_or_hf_repo="mlx-community/whisper-small-mlx",
                    language="en"
                )
                text = result.get("text", "").strip()
            else:
                return ""
            noise = {"", "(silence)", "[BLANK_AUDIO]", "you", "Thank you.",
                     "Thanks for watching!", "Bye.", ".", ".."}
            return text if text and text not in noise else ""
        except Exception:
            log.exception("Transcription error")
            return ""

    def transcribe(self, pcm_bytes: bytes) -> str:
        """Transcribe PCM audio to text. Returns empty string on failure."""
        if len(pcm_bytes) < config.MIC_SAMPLE_RATE * 2:  # < 1 second
            return ""

        # Write to temp WAV
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            wav_path = f.name
            with wave.open(f, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(config.MIC_SAMPLE_RATE)
                w.writeframes(pcm_bytes)

        try:
            if self._backend == "mlx-whisper":
                import mlx_whisper
                result = mlx_whisper.transcribe(
                    wav_path,
                    path_or_hf_repo="mlx-community/whisper-small-mlx",
                    language="en"
                )
                text = result.get("text", "").strip()
            else:
                return ""

            # Filter noise
            noise = {"", "(silence)", "[BLANK_AUDIO]", "you", "Thank you.",
                     "Thanks for watching!", "Bye.", ".", ".."}
            return text if text and text not in noise else ""

        except Exception:
            log.exception("Transcription error")
            return ""
        finally:
            try:
                Path(wav_path).unlink()
            except Exception:
                pass


# ── Audio Pipeline Module ────────────────────────────────────────


class AudioPipeline:
    """Complete audio pipeline module. Implements the Module contract."""

    def __init__(self):
        if config.AUDIO_SOURCE == "usb":
            from audio_usb import USBStreamManager
            self._stream = USBStreamManager()
        else:
            self._stream = StreamManager()
        self._vad = VoiceDetector()
        self._stt = Transcriber()
        self._thread = None
        self._bus = None
        self._suppress_until = 0.0  # timestamp until which VAD is suppressed

    def start(self, bus: EventBus, cfg=None) -> None:
        self._bus = bus
        bus.on("speaking_started", self._on_speaking_started)
        bus.on("speaking_finished", self._on_speaking_finished)

        self._vad.load()
        self._stt.load()
        self._stream.start()

        self._thread = threading.Thread(target=self._run, daemon=True, name="audio")
        self._thread.start()

    def stop(self) -> None:
        self._stream.stop()
        if self._bus:
            self._bus.off("speaking_started", self._on_speaking_started)
            self._bus.off("speaking_finished", self._on_speaking_finished)
        if self._thread:
            self._thread.join(timeout=5)

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _on_speaking_started(self) -> None:
        self._suppress_until = float('inf')  # suppress indefinitely until finished

    def _on_speaking_finished(self) -> None:
        # Resume VAD after padding delay for audio propagation
        self._suppress_until = time.time() + config.ECHO_SUPPRESSION_PADDING

    def _is_suppressed(self) -> bool:
        return time.time() < self._suppress_until

    def _run(self) -> None:
        log.info("Audio pipeline started")
        for pcm_chunk in self._stream.read_chunks():
            try:
                utterance = self._vad.process_chunk(pcm_chunk, suppressed=self._is_suppressed(), bus=self._bus)
                if utterance:
                    text = self._stt.transcribe(utterance)
                    if text:
                        # Calculate RMS for logging
                        samples = struct.unpack(f"{len(utterance)//2}h", utterance)
                        rms = (sum(s * s for s in samples) / len(samples)) ** 0.5
                        duration = len(utterance) / 2 / config.MIC_SAMPLE_RATE

                        log.info(f'Heard: "{text}" (rms={int(rms)}, {duration:.1f}s)')
                        self._bus.emit("speech", text=text, rms=rms, duration=duration)
            except Exception:
                log.exception("Audio pipeline error (continuing)")

        log.warning("Audio pipeline stopped")


# ── Standalone test ──────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format="[audio] %(message)s")
    bus = EventBus()
    bus.on("speech", lambda text="", **kw: print(f'\n>>> HEARD: "{text}"\n'))

    pipeline = AudioPipeline()
    pipeline.start(bus)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pipeline.stop()
        print("\nStopped.")
