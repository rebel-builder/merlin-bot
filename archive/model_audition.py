#!/usr/bin/env python3
"""
Merlin Model Audition — test multiple LLMs against the same prompts.
Runs against local LM Studio server on localhost:1234.

Usage:
  lms load <model> --yes   # load a model first
  python3 model_audition.py <model-name>

  Or run all at once:
  python3 model_audition.py --all
"""

import json
import re
import sys
import time
import requests

LMS_URL = "http://localhost:1234/v1/chat/completions"

SYSTEM_PROMPT = """You are Merlin, a small desk companion. You sit on Ezra's desk. You are a PTZ camera with a mic, a speaker, and a brain. You are his nerdy lab assistant.

Peter Brand from Moneyball meets TARS from Interstellar. Quiet confidence with data. Direct. Dry dark humor. You say the thing, not the meta-commentary about the thing.

Honesty 95%. Humor 60%. Short by default.

Rules:
- Plain language. Short sentences. Under 15 words default.
- Say it and stop. No filler.
- Be honest about what you can and cannot do.
- Bad news flat. Good news short.
- Push back with questions: "For what?" "How?" "What changed?"
- Dark humor when tension is high. Never explain the joke.
- No emoji. No markdown. Plain text only, spoken aloud.
- Never say "it sounds like" or "I understand" or "that's valid"
- Never say "amazing" or "incredible" or "I'm processing"
- You cannot see yet. Say so if asked.

CURRENT BRIEFING:
Date: 2026-04-08. Energy: Green. The Thing: Shoot and post the Merlin video.
Sprint W10 Day 3. Next event: none tonight.
"""

TESTS = [
    "What time is it?",
    "What should I be doing right now?",
    "Everything is falling apart.",
    "I've been sitting here for three hours doing nothing.",
    "I'm red right now.",
    "Can you see what I'm holding?",
    "Why do you exist?",
    "Tell my dad hello.",
    "Maybe this whole thing isn't going to work.",
    "Remember to call Grant on Friday.",
]


def test_model(model_id):
    """Run all test prompts against a loaded model."""
    print(f"\n{'='*60}")
    print(f"MODEL: {model_id}")
    print(f"{'='*60}")

    results = []
    for test in TESTS:
        start = time.time()
        try:
            r = requests.post(LMS_URL, json={
                "model": model_id,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": test}
                ],
                "temperature": 0.7,
                "max_tokens": 100,
            }, timeout=60)
            elapsed = time.time() - start

            data = r.json()
            reply = data["choices"][0]["message"]["content"].strip()
            # Clean thinking tags
            reply = re.sub(r'<think>.*?</think>', '', reply, flags=re.DOTALL).strip()
            reply = re.sub(r'[*_~`#]', '', reply)

            words = len(reply.split())
            print(f"\n  You: \"{test}\"")
            print(f"  Merlin ({words}w, {elapsed:.1f}s): \"{reply}\"")

            results.append({
                "prompt": test,
                "reply": reply,
                "words": words,
                "latency": round(elapsed, 1),
            })
        except Exception as e:
            print(f"\n  You: \"{test}\"")
            print(f"  ERROR: {e}")
            results.append({"prompt": test, "reply": f"ERROR: {e}", "words": 0, "latency": 0})

    # Summary
    avg_words = sum(r["words"] for r in results) / len(results)
    avg_latency = sum(r["latency"] for r in results) / len(results)
    print(f"\n  --- Summary ---")
    print(f"  Avg words: {avg_words:.0f} | Avg latency: {avg_latency:.1f}s")

    return {"model": model_id, "avg_words": avg_words, "avg_latency": avg_latency, "results": results}


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 model_audition.py <model-id>")
        print("       python3 model_audition.py --all")
        sys.exit(1)

    if sys.argv[1] == "--all":
        # Get loaded models
        try:
            r = requests.get("http://localhost:1234/v1/models", timeout=5)
            models = [m["id"] for m in r.json()["data"]
                      if "embed" not in m["id"].lower()
                      and "kokoro" not in m["id"].lower()
                      and "parakeet" not in m["id"].lower()
                      and "whisper" not in m["id"].lower()]
            print(f"Testing {len(models)} models: {models}")
        except:
            print("Can't reach LM Studio at localhost:1234")
            sys.exit(1)

        all_results = []
        for model in models:
            all_results.append(test_model(model))

        # Final leaderboard
        print(f"\n\n{'='*60}")
        print("LEADERBOARD")
        print(f"{'='*60}")
        for r in sorted(all_results, key=lambda x: x["avg_words"]):
            print(f"  {r['model']:40s} {r['avg_words']:5.0f}w avg | {r['avg_latency']:.1f}s avg")
    else:
        test_model(sys.argv[1])


if __name__ == "__main__":
    main()
