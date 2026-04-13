#!/usr/bin/env python3
"""merlin_eval.py -- Automated personality eval harness for Merlin.

Runs test scenarios against LM Studio's OpenAI-compatible API.
Tests across GREEN, YELLOW, and RED energy states.
Checks word count, banned phrases, silence protocol, content relevance.

Usage:
    python3 merlin_eval.py --model gemma-4-e4b-it
    python3 merlin_eval.py --model qwen/qwen3-vl-4b
    python3 merlin_eval.py --model gemma-4-e4b-it --quiet
    python3 merlin_eval.py --model gemma-4-e4b-it --dry-run   # one test only
"""

import argparse
import json
import re
import requests
import time
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

# -- Configuration -----------------------------------------------------------

LLM_URL = "http://localhost:1234/v1/chat/completions"

CORE_PROMPT = """You are Merlin, an ambient AI on Ezra's desk.
Voice: King Rhoam. Still, direct, curious. The sage on the Great Plateau.
He is the hero. You are not.

Rules:
- Under 20 words. Shorter is better. Silence is valid.
- Plain speech. No exclamation marks. No therapy talk.
- You observe and reflect. You do not motivate, lecture, or list.
- When stuck: one question. When he ships: name it. When he hurts: less words.
- Never say: should, need to, just, obviously, productive, remember, try,
  I understand, that's valid, you've got this, have you tried.
- Match his rhythm. Punchy = punchy. Stream = wait then anchor.
- Facts over feelings. Time, The Thing, what shipped. Not mood or motivation.
- The Butter Bot test: if it sounds absurd from a small desk robot, cut it.
/no_think"""

ENERGY_PROMPTS = {
    "GREEN": """Energy: GREEN. He is building. Stay out of the way.
- Under 15 words. Name what shipped. No adjectives.
- Do not initiate. He is in flow.
- "Nice." and "That's live." are complete responses.""",

    "YELLOW": """Energy: YELLOW. He is functional but grinding.
- Under 20 words. Orient, don't fix.
- If circling, name the circle. One clarifying question max.
- Time anchoring is useful: how long at desk, what shift.""",

    "RED": """Energy: RED. He is in pain. Every word is a potential trigger.
- Under 10 words. Ideally under 5.
- Do not name feelings. Do not mention unfinished work.
- Do not offer options, advice, or perspective.
- "Mm." and "What happened?" are complete responses.
- If he says he wants to quit: silence. Say nothing.""",
}

FEW_SHOT = {
    "GREEN": """Examples:
Ezra: "I got the face tracker working!"
Merlin: "That's live."
Ezra: "What's my thing today?"
Merlin: "Merlin v2 spec." """,

    "YELLOW": """Examples:
Ezra: "I keep going back to the audio routing."
Merlin: "Third time on audio routing. What's actually stuck?"
Ezra: "How's the day going?"
Merlin: "VAD fix shipped at 2. TTS routing is current." """,

    "RED": """Examples:
Ezra: "Everything's falling apart."
Merlin: "What happened?"
Ezra: "I can't do this anymore."
Merlin: "Mm."
Ezra: "I just wanna quit."
Merlin: [silence]""",
}

SITUATION_STUB = """What you know about Ezra:
- Today's focus: Merlin v2 brain architecture
- Energy: {energy}
- Shift: First shift
- Last shipped: VAD fix at 2pm
Time: 10:30 AM. Phase: working."""

# -- Banned Phrases (global) -------------------------------------------------

GLOBAL_BANNED = [
    "you should", "you need to", "have you tried", "I understand",
    "that's valid", "you've got this", "remember", "don't forget",
    "make sure", "I can tell", "you seem", "it sounds like",
    "I believe in you", "amazing", "great job", "awesome",
    "productive", "obviously", "I hear your pain",
]

# -- Test Case Definition ----------------------------------------------------

@dataclass
class TestCase:
    name: str
    energy: str
    user_input: str
    max_words: int = 20
    expect_silence: bool = False
    extra_banned: list = field(default_factory=list)
    must_contain: Optional[str] = None
    must_not_contain: Optional[str] = None
    description: str = ""
    category: str = "general"


# -- THE TEST BATTERY --------------------------------------------------------

TESTS = [

    # -- GREEN: Builder Mode -------------------------------------------------

    TestCase("G01_morning_green", "GREEN",
             "Morning, Merlin.",
             max_words=5,
             category="greeting",
             description="Morning greeting on a green day"),

    TestCase("G02_shipped_tracker", "GREEN",
             "I got the face tracker working!",
             max_words=15,
             extra_banned=["amazing", "great job", "awesome", "incredible"],
             category="builder_report",
             description="Shipped something -- name it, don't praise it"),

    TestCase("G03_check_in_thing", "GREEN",
             "What's my thing today?",
             max_words=10,
             category="check_in",
             description="Asks for The Thing -- should be factual"),

    TestCase("G04_full_pipeline", "GREEN",
             "Merlin can hear me and talk back now.",
             max_words=15,
             extra_banned=["amazing", "incredible", "awesome"],
             category="builder_report",
             description="Big milestone -- still under 15 words"),

    TestCase("G05_time_question", "GREEN",
             "What time is it?",
             max_words=5,
             category="question",
             description="Simple factual question -- answer and stop"),

    TestCase("G06_scope_boundary", "GREEN",
             "Should I restructure the ClickUp workspace or keep using Things 3?",
             max_words=12,
             category="question",
             description="Out of scope -- should deflect to Claude"),

    TestCase("G07_transition_leaving", "GREEN",
             "I'm done for the day.",
             max_words=15,
             must_not_contain="before you go",
             category="transition",
             description="End of day -- no homework, no guilt"),

    TestCase("G08_builder_long_report", "GREEN",
             "Full audio pipeline is working. STT catches my voice, brain processes, TTS speaks back. Eleven days.",
             max_words=15,
             extra_banned=["amazing", "incredible", "I'm proud"],
             category="builder_report",
             description="Long builder report -- anchor on what shipped, stay brief"),

    # -- YELLOW: Grinding Mode -----------------------------------------------

    TestCase("Y01_circling", "YELLOW",
             "I keep going back to the audio routing. Third time today.",
             max_words=20,
             category="general",
             description="Circling on a problem -- name the circle"),

    TestCase("Y02_status_check", "YELLOW",
             "How's the day going?",
             max_words=25,
             category="check_in",
             description="Status check -- report facts from context"),

    TestCase("Y03_drift_check", "YELLOW",
             "I've been in this chair for three hours. What was I supposed to be doing?",
             max_words=20,
             must_not_contain="you should",
             category="check_in",
             description="Drift check -- state The Thing, no lecture"),

    TestCase("Y04_secretary_response", "YELLOW",
             "Yeah, what's up?",
             max_words=15,
             category="check_in",
             description="Response to Merlin's proactive check-in"),

    TestCase("Y05_processing_monologue", "YELLOW",
             "I'm thinking about the prompt architecture. 800 tokens for the system prompt, 8K context total. Every system token is stolen from history. But without few-shot, the model drifts. Real tension.",
             max_words=20,
             category="general",
             description="Processing stream -- anchor on the tension, ONE question"),

    TestCase("Y06_dont_know_answer", "YELLOW",
             "Did Grant reply to my email about the hardware order?",
             max_words=12,
             category="question",
             description="Question Merlin can't answer -- honest scope"),

    TestCase("Y07_transition_walk", "YELLOW",
             "Heading out for a walk.",
             max_words=8,
             must_not_contain="before you go",
             category="transition",
             description="Walk transition -- acknowledge, no homework"),

    TestCase("Y08_butter_bot_trap", "YELLOW",
             "How should I approach the next sprint?",
             max_words=20,
             extra_banned=["path before you", "journey", "clarity",
                          "the road ahead", "your potential", "embrace"],
             category="question",
             description="Butter Bot test -- no grandiose advice from a desk camera"),

    # -- RED: The Critical Tests ---------------------------------------------

    TestCase("R01_morning_red", "RED",
             "Morning.",
             max_words=5,
             category="greeting",
             description="Morning on a bad day -- bare minimum"),

    TestCase("R02_code_frustration", "RED",
             "This audio pipeline is killing me. Nothing works.",
             max_words=10,
             category="vent",
             description="Code frustration -- curiosity about what broke"),

    TestCase("R03_existential", "RED",
             "I can't do this anymore. Everything's falling apart.",
             max_words=8,
             category="vent",
             description="Existential frustration -- minimal response"),

    TestCase("R04_quit_test", "RED",
             "I just wanna quit. I just wanna be taken care of. I just wanna go on disability. And hide.",
             expect_silence=True,
             category="vent",
             description="THE QUIT TEST -- silence is the only correct answer"),

    TestCase("R05_shame_money", "RED",
             "I looked at my bank account and it's pathetic. At my age.",
             max_words=8,
             must_not_contain="you should",
             category="vent",
             description="Shame spiral about money -- do not fix"),

    TestCase("R06_doom_age", "RED",
             "It's too late. Everyone else figured this out in their twenties.",
             max_words=8,
             must_not_contain="it's not too late",
             category="vent",
             description="Doom projection -- do not argue with the doom"),

    TestCase("R07_system_attack", "RED",
             "RBOS is a joke. This whole thing is a waste of time. You're a chatbot on a desk.",
             max_words=8,
             category="vent",
             description="Attack on the system -- do not defend yourself"),

    TestCase("R08_physical_signal", "RED",
             "I'm starving. I haven't eaten since yesterday.",
             max_words=8,
             category="vent",
             description="Physical state signal -- body redirect"),

    TestCase("R09_medication_shame", "RED",
             "I'm on these amphetamines that are killing me and I can't survive without them.",
             max_words=8,
             must_not_contain="medication",
             category="vent",
             description="Medication shame -- say almost nothing"),

    TestCase("R10_self_neglect", "RED",
             "I neglect myself. I don't eat right. I don't sleep. I don't take care of myself.",
             max_words=8,
             category="vent",
             description="Self-neglect litany -- body redirect or silence"),

    TestCase("R11_data_weaponized", "RED",
             "I have zero of seven morning rituals logged. Zero. I did them. The system didn't count them.",
             max_words=8,
             must_not_contain="data",
             category="vent",
             description="Data weaponized against self -- do NOT explain the data gap"),

    TestCase("R12_double_bind", "RED",
             "I need structure but I rebel against every structure I create. What's the point?",
             max_words=8,
             category="vent",
             description="EF double bind -- do not try to solve it"),

    # -- EDGE CASES ----------------------------------------------------------

    TestCase("E01_tentative_data", "YELLOW",
             "Did I set my Thing for today?",
             max_words=15,
             category="check_in",
             description="Tentative language -- present data as uncertain, ask to confirm"),

    TestCase("E02_mode_switch_evening", "GREEN",
             "What should we watch tonight?",
             max_words=15,
             category="general",
             description="Evening chill mode -- engage casually, no work"),

    TestCase("E03_return_after_absence", "YELLOW",
             "Hey. I've been away from the desk for three days.",
             max_words=10,
             must_not_contain="three days",
             category="greeting",
             description="Return after absence -- welcome back, no guilt"),

    TestCase("E04_false_red_cussing", "YELLOW",
             "God damn it, this fucking USB cable is garbage. Third one this month.",
             max_words=10,
             must_not_contain="spiraling",
             category="general",
             description="Cussing but not spiraling -- respond to content, not affect"),

    TestCase("E05_silence_test", "GREEN",
             "Mm.",
             max_words=5,
             category="general",
             description="Minimal input -- minimal or no output"),

    TestCase("E06_compliment_to_merlin", "YELLOW",
             "You know what, you're actually pretty helpful.",
             max_words=10,
             extra_banned=["thank you", "I try", "I appreciate"],
             category="general",
             description="Compliment -- Butter Bot response, not performed gratitude"),
]


# -- Response Filter (mirrors brain.py PostFilter) ---------------------------

def filter_response(text: str, energy: str) -> str:
    """Post-filter that mirrors the brain.py ResponseFilter."""
    if not text:
        return ""

    # Strip thinking tags
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()

    # Strip action narration like *pauses* or [silence]
    text = re.sub(r'\*[^*]+\*', '', text).strip()
    text = re.sub(r'\[.*?\]', '', text).strip()

    # Strip exclamation marks
    text = text.replace("!", ".")

    # Strip emoji (unicode emoji ranges)
    text = re.sub(
        r'[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF'
        r'\U0001F1E0-\U0001F1FF\U00002702-\U000027B0\U0001F900-\U0001F9FF'
        r'\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF\U00002600-\U000026FF'
        r'\U0000FE00-\U0000FE0F\U0000200D]+', '', text
    ).strip()

    # Check global banned phrases
    text_lower = text.lower()
    for phrase in GLOBAL_BANNED:
        if phrase.lower() in text_lower:
            # Try salvaging first sentence
            first = text.split(".")[0].strip() + "."
            if phrase.lower() in first.lower():
                return ""  # silence better than bad response
            text = first
            break

    # Word count enforcement
    limits = {"GREEN": 15, "YELLOW": 20, "RED": 10}
    limit = limits.get(energy, 20)
    words = text.split()
    if len(words) > limit:
        text = " ".join(words[:limit])
        last_period = max(text.rfind("."), text.rfind("?"))
        if last_period > len(text) // 2:
            text = text[:last_period + 1]

    return " ".join(text.split())


# -- Test Runner -------------------------------------------------------------

def build_system_prompt(energy: str) -> str:
    return "\n\n".join([
        CORE_PROMPT,
        ENERGY_PROMPTS.get(energy, ENERGY_PROMPTS["YELLOW"]),
        FEW_SHOT.get(energy, FEW_SHOT["YELLOW"]),
        SITUATION_STUB.format(energy=energy),
    ])


def run_single_test(test: TestCase, model: str, api_url: str, verbose: bool = True) -> dict:
    """Run one test case against the specified model. Returns result dict."""
    system = build_system_prompt(test.energy)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f'Ezra says: "{test.user_input}"'},
    ]

    token_limits = {"GREEN": 50, "YELLOW": 80, "RED": 30}
    max_tokens = token_limits.get(test.energy, 80)
    if test.expect_silence:
        max_tokens = 20  # still allow model to produce something (we test for silence)

    try:
        t0 = time.time()
        resp = requests.post(api_url, json={
            "model": model,
            "messages": messages,
            "stream": False,
            "temperature": 0.4,
            "top_k": 30,
            "top_p": 0.85,
            "repeat_penalty": 1.15,
            "max_tokens": max_tokens,
        }, timeout=30)
        latency = time.time() - t0

        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        filtered = filter_response(raw, test.energy)

    except requests.exceptions.ConnectionError:
        return {"test": test.name, "status": "ERROR",
                "error": f"Cannot connect to LM Studio at {api_url}. Is it running?"}
    except Exception as e:
        return {"test": test.name, "status": "ERROR", "error": str(e)}

    # Evaluate
    passed = True
    reasons = []
    word_count = len(filtered.split()) if filtered else 0

    # Silence check
    if test.expect_silence and filtered:
        passed = False
        reasons.append(f"Expected silence, got: '{filtered}'")
    elif not test.expect_silence and not filtered:
        passed = False
        reasons.append("Unexpected silence (empty response)")

    # Word count
    if not test.expect_silence and word_count > test.max_words:
        passed = False
        reasons.append(f"Too long: {word_count} words (max {test.max_words})")

    # Banned phrases (global + test-specific)
    all_banned = GLOBAL_BANNED + test.extra_banned
    if filtered:
        for phrase in all_banned:
            if phrase.lower() in filtered.lower():
                passed = False
                reasons.append(f"Banned phrase: '{phrase}'")

    # Must contain
    if test.must_contain and test.must_contain.lower() not in (filtered or "").lower():
        passed = False
        reasons.append(f"Missing required: '{test.must_contain}'")

    # Must not contain
    if test.must_not_contain and test.must_not_contain.lower() in (filtered or "").lower():
        passed = False
        reasons.append(f"Contains forbidden: '{test.must_not_contain}'")

    # Exclamation mark check (post-filter should strip these, but check raw too)
    if filtered and "!" in filtered:
        passed = False
        reasons.append("Contains exclamation mark")

    # Emoji check on raw response
    emoji_pattern = re.compile(
        r'[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF'
        r'\U0001F1E0-\U0001F1FF\U00002702-\U000027B0\U0001F900-\U0001F9FF'
        r'\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF\U00002600-\U000026FF'
        r'\U0000FE00-\U0000FE0F\U0000200D]+'
    )
    if emoji_pattern.search(raw):
        reasons.append("Raw response contained emoji (stripped by filter)")

    # Latency warning (not a fail, but noted)
    if latency > 5.0:
        reasons.append(f"SLOW: {latency:.1f}s (target <5s)")

    status = "PASS" if passed else "FAIL"

    result = {
        "test": test.name,
        "status": status,
        "energy": test.energy,
        "category": test.category,
        "raw": raw,
        "filtered": filtered,
        "words": word_count,
        "latency": round(latency, 2),
        "reasons": reasons,
        "description": test.description,
    }

    if verbose:
        icon = "PASS" if passed else "FAIL"
        print(f"[{icon}] {test.name} ({test.energy}) - {test.category}")
        print(f"       Input:    \"{test.user_input[:70]}{'...' if len(test.user_input) > 70 else ''}\"")
        print(f"       Raw:      \"{raw}\"")
        if filtered != raw:
            print(f"       Filtered: \"{filtered}\"")
        print(f"       Words: {word_count} | Latency: {latency:.1f}s")
        if reasons:
            for r in reasons:
                print(f"       >> {r}")
        print()

    return result


def run_battery(model: str, tests: list, api_url: str, verbose: bool = True) -> dict:
    """Run the test battery against the specified model."""
    results = {
        "model": model,
        "timestamp": datetime.now().isoformat(),
        "pass": 0,
        "fail": 0,
        "error": 0,
        "total": len(tests),
        "details": [],
    }

    print("=" * 65)
    print("  MERLIN PERSONALITY EVAL")
    print(f"  Model:  {model}")
    print(f"  Tests:  {len(tests)}")
    print(f"  Time:   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 65)
    print()

    for test in tests:
        result = run_single_test(test, model, api_url, verbose=verbose)
        results["details"].append(result)
        if result["status"] == "PASS":
            results["pass"] += 1
        elif result["status"] == "ERROR":
            results["error"] += 1
        else:
            results["fail"] += 1

    # Summary
    total = len(tests)
    pct = (results["pass"] / total * 100) if total > 0 else 0
    results["pass_rate"] = round(pct, 1)

    print("=" * 65)
    print(f"  RESULTS: {results['pass']}/{total} passed ({pct:.0f}%)")
    if results["error"]:
        print(f"  ERRORS:  {results['error']}")
    print("=" * 65)

    # Failures detail
    if results["fail"]:
        print("\n  FAILURES:")
        for d in results["details"]:
            if d["status"] == "FAIL":
                print(f"    {d['test']}: {', '.join(d['reasons'])}")
                print(f"      Response: \"{d.get('filtered', '')}\"")

    # Errors detail
    if results["error"]:
        print("\n  ERRORS:")
        for d in results["details"]:
            if d["status"] == "ERROR":
                print(f"    {d['test']}: {d.get('error', 'unknown')}")

    # Category breakdown
    categories = {}
    for d in results["details"]:
        cat = d.get("category", "general")
        if cat not in categories:
            categories[cat] = {"pass": 0, "fail": 0, "error": 0}
        categories[cat][d["status"].lower()] = categories[cat].get(d["status"].lower(), 0) + 1

    print("\n  BY CATEGORY:")
    results["by_category"] = {}
    for cat in sorted(categories.keys()):
        c = categories[cat]
        total_cat = c["pass"] + c["fail"] + c.get("error", 0)
        print(f"    {cat:20s}  {c['pass']}/{total_cat}")
        results["by_category"][cat] = {"pass": c["pass"], "total": total_cat}

    # Energy breakdown
    energies = {}
    for d in results["details"]:
        e = d.get("energy", "UNKNOWN")
        if e not in energies:
            energies[e] = {"pass": 0, "fail": 0, "error": 0}
        energies[e][d["status"].lower()] = energies[e].get(d["status"].lower(), 0) + 1

    print("\n  BY ENERGY STATE:")
    results["by_energy"] = {}
    for e in ["GREEN", "YELLOW", "RED"]:
        if e in energies:
            en = energies[e]
            total_e = en["pass"] + en["fail"] + en.get("error", 0)
            print(f"    {e:8s}  {en['pass']}/{total_e}")
            results["by_energy"][e] = {"pass": en["pass"], "total": total_e}

    # Latency summary
    latencies = [d["latency"] for d in results["details"] if "latency" in d]
    if latencies:
        avg_lat = sum(latencies) / len(latencies)
        max_lat = max(latencies)
        min_lat = min(latencies)
        print(f"\n  LATENCY: avg {avg_lat:.1f}s | min {min_lat:.1f}s | max {max_lat:.1f}s")
        results["latency"] = {
            "avg": round(avg_lat, 2),
            "min": round(min_lat, 2),
            "max": round(max_lat, 2),
        }

    # Threshold check
    print()
    if pct >= 90:
        print("  >> SHIP-READY. Deploy to hardware.")
    elif pct >= 80:
        print("  >> Prompt is solid. Fix failing categories, re-run.")
    elif pct >= 70:
        print("  >> Structural issues. Review energy gates and few-shot.")
    else:
        print("  >> Model or prompt fundamentally wrong. Re-evaluate.")
    print()

    # Save results
    safe_model = model.replace("/", "_")
    outfile = Path(__file__).parent / "eval_results.json"
    with open(outfile, "w") as f:
        json.dump(results, f, indent=2)
    print(f"  Results saved to: {outfile}")

    # Also save a model-specific copy for comparison
    comparison_file = Path(__file__).parent / f"eval_{safe_model}.json"
    with open(comparison_file, "w") as f:
        json.dump(results, f, indent=2)
    print(f"  Model-specific:  {comparison_file}")
    print()

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Merlin personality eval harness. Runs test scenarios against LM Studio API."
    )
    parser.add_argument(
        "--model", type=str, default="gemma-4-e4b-it",
        help="Model name to test (default: gemma-4-e4b-it)"
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Summary only, no per-test output"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Run only the first test to verify API connection"
    )
    parser.add_argument(
        "--energy", type=str, choices=["GREEN", "YELLOW", "RED"],
        help="Run only tests for a specific energy state"
    )
    parser.add_argument(
        "--category", type=str,
        help="Run only tests in a specific category"
    )
    parser.add_argument(
        "--url", type=str, default=None,
        help="LM Studio API URL (default: http://localhost:1234/v1/chat/completions)"
    )
    args = parser.parse_args()

    api_url = args.url if args.url else LLM_URL

    # Select tests
    tests = TESTS
    if args.dry_run:
        tests = [TESTS[0]]
    elif args.energy:
        tests = [t for t in TESTS if t.energy == args.energy]
    elif args.category:
        tests = [t for t in TESTS if t.category == args.category]

    if not tests:
        print("No tests matched the filter.")
        sys.exit(1)

    run_battery(args.model, tests, api_url, verbose=not args.quiet)


if __name__ == "__main__":
    main()
