"""
Speech-to-text via faster-whisper with CUDA acceleration.
Uses Whisper's built-in Silero VAD for extra filtering.
"""

from faster_whisper import WhisperModel
from config import WHISPER_MODEL, WHISPER_DEVICE, WHISPER_COMPUTE, WHISPER_LANGUAGE


class STT:
    def __init__(self):
        print(f"[stt] Loading Whisper '{WHISPER_MODEL}' on {WHISPER_DEVICE}...")
        self.model = WhisperModel(
            WHISPER_MODEL,
            device=WHISPER_DEVICE,
            compute_type=WHISPER_COMPUTE,
        )
        print("[stt] Whisper loaded.")

    def transcribe(self, audio):
        """
        Transcribe a numpy float32 audio array to text.
        Returns the transcribed string, or None if nothing recognized.
        """
        try:
            segments, info = self.model.transcribe(
                audio,
                language=WHISPER_LANGUAGE,
                vad_filter=True,
                vad_parameters=dict(
                    min_silence_duration_ms=500,
                    speech_pad_ms=200,
                ),
            )
            text = " ".join(s.text for s in segments).strip()
            return text if text else None
        except Exception as e:
            print(f"[stt] Transcription error: {e}")
            return None
