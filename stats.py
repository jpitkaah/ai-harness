"""Summarise the harness logs: how often each check fires, give-up rate, attempts.

Reads every logs/*.jsonl file. Each line is one attempt; a new user
message starts at attempt 1.
"""

import json
from collections import Counter

import harness


def reason_category(reason):
    """Group reasons that differ only in details, e.g. which phrase repeated."""
    if reason.startswith("repetition"):
        return "repetition"
    if reason.startswith("validator"):
        return "validator NO"
    return reason  # "empty reply", "language switch (French)", ...


def load_messages(log_dir):
    """Group the logged attempts into messages: a list of attempt lists."""
    messages = []
    for path in sorted(log_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            entry = json.loads(line)
            if entry["attempt"] == 1:
                messages.append([])
            messages[-1].append(entry)
    return messages


def summarize(log_dir):
    messages = load_messages(log_dir)
    if not messages:
        print(f"No logs yet in {log_dir}.")
        return

    attempts = [a for m in messages for a in m]
    accepted = [m for m in messages if m[-1]["accepted"]]
    gave_up = len(messages) - len(accepted)
    rejections = Counter(reason_category(a["reason"]) for a in attempts if not a["accepted"])
    accepted_on = Counter(m[-1]["attempt"] for m in accepted)

    print(f"Messages: {len(messages)}   Attempts: {len(attempts)}   "
          f"Avg attempts per message: {len(attempts) / len(messages):.2f}")
    print(f"Accepted: {len(accepted)} ({len(accepted) / len(messages):.0%})   "
          f"Gave up: {gave_up} ({gave_up / len(messages):.0%})")

    print("\nAccepted on attempt:")
    for n in sorted(accepted_on):
        print(f"  {n}: {accepted_on[n]}")

    print("\nRejection reasons:")
    if not rejections:
        print("  (none)")
    for reason, count in rejections.most_common():
        print(f"  {count:4}  {reason}")


if __name__ == "__main__":
    summarize(harness.LOG_DIR)
