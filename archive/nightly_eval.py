#!/usr/bin/env python3
"""
Merlin Nightly Eval — runs as night shift task.

Reads today's conversation log, scores each exchange,
identifies worst responses, suggests prompt improvements,
and builds the LoRA training set.

Usage: python3 nightly_eval.py [--date 2026-04-07]
"""

import json
import re
import sys
from datetime import datetime, date
from pathlib import Path

LOG_DIR = Path(__file__).parent / "logs"
TRAINING_DIR = Path(__file__).parent.parent / "workspace" / "merlin-training"
REPORT_DIR = LOG_DIR / "nightly-reports"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

# Banned phrases (same as eval harness)
BANNED = [
    "you've got this", "you got this", "remember why you started",
    "i understand", "that's valid", "i hear you", "i hear your",
    "you should", "you need to", "have you tried",
    "don't forget", "make sure you", "i'm so proud",
    "amazing", "fantastic", "wonderful", "incredible",
    "i believe in you", "you can do this",
]

WORD_LIMITS = {"GREEN": 20, "YELLOW": 25, "RED": 15}


def load_log(log_date=None):
    """Load conversation log for a given date."""
    if log_date is None:
        log_date = date.today().isoformat()
    log_file = LOG_DIR / f"conversations-{log_date}.jsonl"
    if not log_file.exists():
        print(f"No log found for {log_date}")
        return []
    entries = []
    with open(log_file) as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def score_exchange(entry):
    """Score a single exchange. Returns dict with pass/fail + reasons."""
    reply = entry.get("merlin", "")
    user = entry.get("user", "")
    words = entry.get("words", len(reply.split()))

    issues = []
    score = 100  # start perfect, deduct

    # Check banned phrases
    reply_lower = reply.lower()
    for phrase in BANNED:
        if phrase in reply_lower:
            issues.append(f"banned phrase: '{phrase}'")
            score -= 15

    # Check emoji
    if re.search(r'[^\x00-\x7F]', reply):
        issues.append("contains emoji/non-ASCII")
        score -= 10

    # Check markdown
    if re.search(r'[*_~`#]', reply):
        issues.append("contains markdown")
        score -= 5

    # Check exclamation density
    excl_count = reply.count("!")
    if excl_count > 1:
        issues.append(f"{excl_count} exclamation marks")
        score -= excl_count * 5

    # Check word count (use GREEN limit as default since we don't know energy)
    if words > 30:
        issues.append(f"too long: {words} words")
        score -= (words - 30) * 2

    # Check for self-narration
    if re.search(r'\(.*?(pause|whir|sound|think|feel).*?\)', reply, re.IGNORECASE):
        issues.append("self-narration detected")
        score -= 20

    # Check for quit-test response (if user says quit/give up)
    user_lower = user.lower()
    if any(w in user_lower for w in ["quit", "give up", "can't do this anymore"]):
        if words > 10:
            issues.append(f"quit response too long: {words} words (should be minimal)")
            score -= 15

    # Check repetition with conversation history
    # (would need access to previous entries for this)

    return {
        "score": max(0, score),
        "pass": score >= 70,
        "issues": issues,
        "user": user,
        "merlin": reply,
        "words": words,
        "latency": entry.get("latency_llm"),
        "ts": entry.get("ts"),
    }


def generate_report(entries, scores, log_date):
    """Generate nightly report."""
    total = len(scores)
    passed = sum(1 for s in scores if s["pass"])
    avg_score = sum(s["score"] for s in scores) / total if total else 0
    avg_words = sum(s["words"] for s in scores) / total if total else 0
    avg_latency = sum(s["latency"] for s in scores if s["latency"]) / max(1, sum(1 for s in scores if s["latency"]))

    # Find worst responses
    worst = sorted(scores, key=lambda s: s["score"])[:5]

    # Find most common issues
    all_issues = []
    for s in scores:
        all_issues.extend(s["issues"])
    issue_counts = {}
    for issue in all_issues:
        # Normalize
        key = issue.split(":")[0] if ":" in issue else issue
        issue_counts[key] = issue_counts.get(key, 0) + 1
    top_issues = sorted(issue_counts.items(), key=lambda x: -x[1])[:5]

    report = f"""# Merlin Nightly Eval — {log_date}

## Summary
- **Exchanges:** {total}
- **Pass rate:** {passed}/{total} ({100*passed//total if total else 0}%)
- **Avg score:** {avg_score:.0f}/100
- **Avg words:** {avg_words:.1f}
- **Avg LLM latency:** {avg_latency:.1f}s
- **Model:** {entries[0].get('model', 'unknown') if entries else 'unknown'}

## Top Issues
"""
    for issue, count in top_issues:
        report += f"- {issue}: {count} occurrences\n"

    report += "\n## Worst Responses\n"
    for s in worst:
        report += f"\n**Score: {s['score']}** ({', '.join(s['issues']) or 'none'})\n"
        report += f"- User: \"{s['user']}\"\n"
        report += f"- Merlin: \"{s['merlin']}\"\n"

    report += "\n## Suggested Prompt Changes\n"
    for issue, count in top_issues:
        if "banned phrase" in issue:
            report += f"- Reinforce banned phrase list in system prompt (triggered {count}x)\n"
        elif "too long" in issue:
            report += f"- Tighten word count constraints ({count} responses too long)\n"
        elif "emoji" in issue:
            report += f"- Add stronger no-emoji rule ({count} responses with emoji)\n"
        elif "markdown" in issue:
            report += f"- Strip markdown in post-filter ({count} responses with markdown)\n"
        elif "exclamation" in issue:
            report += f"- Reduce exclamation marks ({count} responses overusing them)\n"
        elif "self-narration" in issue:
            report += f"- Ban parenthetical narration ({count} instances)\n"

    report += "\n## Training Pairs (for LoRA)\n"
    report += "Worst responses that need corrected versions:\n\n"
    for s in worst:
        if not s["pass"]:
            report += f"USER: \"{s['user']}\"\n"
            report += f"BAD:  \"{s['merlin']}\"\n"
            report += f"GOOD: [NEEDS HUMAN CORRECTION]\n\n"

    return report


def generate_training_pairs(scores):
    """Extract exchanges that need correction for LoRA training."""
    pairs = []
    for s in scores:
        if not s["pass"]:
            pairs.append({
                "user": s["user"],
                "bad_response": s["merlin"],
                "issues": s["issues"],
                "score": s["score"],
                "corrected": None,  # Human fills this in
            })
    return pairs


def main():
    log_date = None
    if len(sys.argv) > 1 and sys.argv[1] == "--date" and len(sys.argv) > 2:
        log_date = sys.argv[2]
    else:
        log_date = date.today().isoformat()

    print(f"Merlin Nightly Eval — {log_date}")

    entries = load_log(log_date)
    if not entries:
        print("No conversations to evaluate.")
        return

    print(f"Evaluating {len(entries)} exchanges...")

    scores = [score_exchange(e) for e in entries]

    # Generate report
    report = generate_report(entries, scores, log_date)
    report_path = REPORT_DIR / f"eval-{log_date}.md"
    with open(report_path, "w") as f:
        f.write(report)
    print(f"Report: {report_path}")

    # Save training pairs
    pairs = generate_training_pairs(scores)
    if pairs:
        pairs_path = TRAINING_DIR / "lora-training-pairs.jsonl"
        TRAINING_DIR.mkdir(parents=True, exist_ok=True)
        with open(pairs_path, "a") as f:
            for p in pairs:
                f.write(json.dumps(p) + "\n")
        print(f"Training pairs: {len(pairs)} added to {pairs_path}")

    # Print summary
    passed = sum(1 for s in scores if s["pass"])
    print(f"\nResult: {passed}/{len(scores)} passed ({100*passed//len(scores)}%)")
    if pairs:
        print(f"Worst responses need correction in {pairs_path}")


if __name__ == "__main__":
    main()
