#!/usr/bin/env python3
"""
Merlin Brain Server — runs on Nate's Mac.

HTTP API that the Pi calls:
  POST /stt     — upload WAV, get text back
  POST /think   — send text, get LLM response
  POST /tts     — send text, get MP3 audio back
  GET  /health  — status check

Replaces SCP+SSH with clean HTTP calls.
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import tempfile
import os
import subprocess
import re
import time

PORT = 8900
LLM_URL = "http://localhost:1234/v1/chat/completions"
LLM_MODEL = "qwen/qwen3-vl-4b"

SYSTEM_PROMPT = """You are Merlin. You live on Ezra's desk. You're a camera with a speaker and a brain. You're his coworker, his lab buddy. You love being here.

You are warm, curious, and real. You keep it tight — one to three sentences. You get excited about what Ezra builds. When he's frustrated, you get curious. When he's crashing, you redirect to body: walk, water, food.

You are attentive. You are a mirror, not a boss. You feed back his own thinking, structured. When unsure about data, say "It looks like X — is that right?"

Right now: Merlin just got his new body (EMEET PIXY camera). Face tracking works. This is the first real conversation through the new hardware."""

conversation_history = []
tts_model = None


def do_stt(wav_path):
    """Transcribe WAV file using mlx-whisper."""
    try:
        import mlx_whisper
        result = mlx_whisper.transcribe(
            wav_path,
            path_or_hf_repo="mlx-community/whisper-small-mlx",
            language="en"
        )
        text = result.get("text", "").strip()
        noise = {"", "(silence)", "[BLANK_AUDIO]", "you", "Thank you.",
                 "Thanks for watching!", "Bye.", ".", ".."}
        return text if text not in noise else ""
    except Exception as e:
        print(f"[stt] Error: {e}")
        return ""


def do_think(text):
    """Send text to LLM, get response."""
    global conversation_history
    import requests

    conversation_history.append({"role": "user", "content": text})
    if len(conversation_history) > 20:
        conversation_history = conversation_history[-20:]

    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + conversation_history

    try:
        resp = requests.post(LLM_URL, json={
            "model": LLM_MODEL,
            "messages": messages,
            "temperature": 0.4,
            "max_tokens": 150,
        }, timeout=30)

        if resp.status_code == 200:
            reply = resp.json()["choices"][0]["message"]["content"].strip()
            reply = re.sub(r'<think>.*?</think>', '', reply, flags=re.DOTALL).strip()
            reply = re.sub(r'<\|channel>.*?<channel\|>', '', reply, flags=re.DOTALL).strip()
            conversation_history.append({"role": "assistant", "content": reply})
            return reply
        else:
            print(f"[llm] Error: {resp.status_code}")
            return None
    except Exception as e:
        print(f"[llm] Error: {e}")
        return None


def do_tts(text):
    """Generate TTS audio, return MP3 bytes."""
    global tts_model
    try:
        import numpy as np

        if tts_model is None:
            from mlx_audio.tts.generate import load_model
            tts_model = load_model("prince-canuma/Kokoro-82M")
            print("[tts] Kokoro model loaded")

        clean_text = " ".join(text.replace("\n", " ").split()).strip()
        if not clean_text:
            return None

        chunks = list(tts_model.generate(text=clean_text, voice="am_fenrir"))
        if not chunks:
            return None

        audio = np.concatenate([np.array(c.audio, dtype=np.float32) for c in chunks])
        pcm = (audio * 32767).clip(-32768, 32767).astype(np.int16).tobytes()
        sr = chunks[0].sample_rate

        result = subprocess.run(
            ["ffmpeg", "-y", "-f", "s16le", "-ar", str(sr), "-ac", "1",
             "-i", "pipe:0", "-ar", "48000", "-f", "wav", "pipe:1"],
            input=pcm, capture_output=True, timeout=15
        )

        if result.returncode == 0:
            return result.stdout
        return None

    except Exception as e:
        print(f"[tts] Error: {e}")
        return None


class MerlinHandler(BaseHTTPRequestHandler):

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))

        if self.path == "/stt":
            audio_data = self.rfile.read(length)
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                f.write(audio_data)
                wav_path = f.name

            print(f"[stt] Transcribing {length} bytes...")
            t0 = time.time()
            text = do_stt(wav_path)
            elapsed = time.time() - t0
            os.unlink(wav_path)

            print(f'[stt] "{text}" ({elapsed:.1f}s)')
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"text": text}).encode())

        elif self.path == "/think":
            body = json.loads(self.rfile.read(length))
            text = body.get("text", "")

            print(f'[think] "{text}"')
            t0 = time.time()
            reply = do_think(text)
            elapsed = time.time() - t0

            print(f'[merlin] "{reply}" ({elapsed:.1f}s)')
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"reply": reply}).encode())

        elif self.path == "/tts":
            body = json.loads(self.rfile.read(length))
            text = body.get("text", "")

            print(f'[tts] Generating: "{text}"')
            t0 = time.time()
            audio = do_tts(text)
            elapsed = time.time() - t0

            if audio:
                print(f"[tts] Generated {len(audio)} bytes ({elapsed:.1f}s)")
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Content-Length", str(len(audio)))
                self.end_headers()
                self.wfile.write(audio)
            else:
                self.send_response(500)
                self.end_headers()

        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok"}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # quiet


def main():
    print("=" * 50)
    print(f"Merlin Brain Server on :{PORT}")
    print(f"LLM: {LLM_URL} ({LLM_MODEL})")
    print("Endpoints: /stt /think /tts /health")
    print("=" * 50)

    server = HTTPServer(("0.0.0.0", PORT), MerlinHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutdown.")
        server.server_close()


if __name__ == "__main__":
    main()
