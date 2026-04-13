"""
LLM conversation engine via LM Studio.
Handles wake words, conversation window, mute/unmute, and chat history.
"""

import re
import requests
import time
from config import (
    LLM_URL, SYSTEM_PROMPT, MAX_HISTORY, MAX_TOKENS, TEMPERATURE,
    WAKE_WORDS, CONVERSATION_WINDOW,
    MUTE_WORDS, UNMUTE_WORDS, NEVERMIND_WORDS,
)


class Brain:
    def __init__(self):
        self.history = []
        self.last_response_time = 0
        self.muted = False
        self.greeted_today = False
        self._check_connection()

    def _check_connection(self):
        """Verify LM Studio is reachable."""
        try:
            url = LLM_URL.replace("/chat/completions", "/models")
            r = requests.get(url, timeout=3)
            if r.ok:
                models = r.json().get("data", [])
                if models:
                    print(f"[brain] LM Studio connected. Model: {models[0].get('id', 'unknown')}")
                else:
                    print("[brain] LM Studio connected but no model loaded. Load one in LM Studio.")
            else:
                print(f"[brain] LM Studio responded with {r.status_code}.")
        except requests.ConnectionError:
            print("[brain] WARNING: Cannot reach LM Studio at localhost:1234.")
            print("[brain] Start LM Studio and load a model, then restart Merlin.")
        except Exception as e:
            print(f"[brain] Connection check error: {e}")

    def process(self, text):
        """
        Process transcribed speech. Returns a response string, or None if
        the utterance should be ignored (not addressed to Merlin, muted, etc.).
        """
        if not text:
            return None

        text_lower = text.lower().strip()

        # --- Mute controls ---
        if any(w in text_lower for w in MUTE_WORDS):
            self.muted = True
            print("[brain] Muted. Say 'wake up' or 'Hey Merlin' to resume.")
            return None

        if any(w in text_lower for w in UNMUTE_WORDS):
            self.muted = False
            print("[brain] Unmuted.")
            return "I'm listening."

        # If muted, only wake word breaks through
        if self.muted:
            if any(w in text_lower for w in WAKE_WORDS):
                self.muted = False
                print("[brain] Unmuted via wake word.")
            else:
                return None

        # --- Nevermind ---
        if any(w in text_lower for w in NEVERMIND_WORDS):
            self.last_response_time = 0  # Close conversation window
            return None

        # --- Wake word or conversation window check ---
        in_window = (time.time() - self.last_response_time) < CONVERSATION_WINDOW
        has_wake = any(w in text_lower for w in WAKE_WORDS)

        if not has_wake and not in_window:
            return None  # Not talking to Merlin

        # Strip wake word prefix from the message
        message = text
        for w in sorted(WAKE_WORDS, key=len, reverse=True):
            if text_lower.startswith(w):
                message = text[len(w):].strip(" ,!?.")
                break

        if not message:
            message = "hello"

        # --- Call LLM ---
        response = self._call_llm(message)

        if response:
            self.last_response_time = time.time()
            self.history.append({"role": "user", "content": message})
            self.history.append({"role": "assistant", "content": response})
            # Trim history to last N exchanges
            if len(self.history) > MAX_HISTORY * 2:
                self.history = self.history[-(MAX_HISTORY * 2):]

        return response

    def _call_llm(self, message):
        """Send message to LM Studio and return the response text."""
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(self.history)
        messages.append({"role": "user", "content": message})

        try:
            r = requests.post(
                LLM_URL,
                json={
                    "messages": messages,
                    "max_tokens": MAX_TOKENS,
                    "temperature": TEMPERATURE,
                    "stream": False,
                },
                timeout=30,
            )
            if r.ok:
                raw = r.json()["choices"][0]["message"]["content"].strip()
                # Strip Gemma's <think>...</think> reasoning blocks
                cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
                return cleaned if cleaned else raw
            else:
                print(f"[brain] LLM returned {r.status_code}: {r.text[:200]}")
                return None
        except requests.Timeout:
            print("[brain] LLM timed out (30s). Model may be loading.")
            return None
        except requests.ConnectionError:
            print("[brain] Can't reach LM Studio. Is it running?")
            return None
        except Exception as e:
            print(f"[brain] LLM error: {e}")
            return None

    def on_face_arrived(self):
        """Called when face tracker detects someone at the desk."""
        if not self.greeted_today and not self.muted:
            self.greeted_today = True
            hour = time.localtime().tm_hour
            return "Morning." if hour < 12 else "Hey."
        return None
