"""Run GPQA (Idavidrein/gpqa, downloaded to benchmark/gpqa) on our model.

    python benchmark/run_gpqa.py raw            base model, no harness (chat endpoint, like eval.py baseraw)
    python benchmark/run_gpqa.py harness        best_strat.py, as the app answers without search
    python benchmark/run_gpqa.py strat2 hard    the 30-question hard set (benchmark/gpqa_hard.json)
    python benchmark/run_gpqa.py strat2 control    the 30-question control set (gpqa_control.json)
    python benchmark/run_gpqa.py raw 20         only the first 20 questions

Uses the diamond set (198 questions, the one usually reported). Four choices per question, shuffled
with a fixed seed per question so both modes see the same order. The prompt is the GPQA paper's
zero-shot one; the chosen letter is read from the reply ("The correct answer is (B)"). Random
guessing scores 25%. Writes benchmark/results/gpqa_<mode>.jsonl and prints the accuracy.
"""

import csv
import json
import random
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_ifbench  # noqa: E402  (reuses raw_answer / harness_answer and the project path)

DATA = Path(__file__).resolve().parent / "gpqa" / "gpqa_diamond.csv"
# The small test's sets (benchmark/README.md): 30 questions v17 got wrong (hard) and 30 it got right
# (control: it shows regressions), each picked evenly over the three domains.
SETS = {"hard": Path(__file__).resolve().parent / "gpqa_hard.json",
        "control": Path(__file__).resolve().parent / "gpqa_control.json"}
RESULTS = run_ifbench.RESULTS
LETTERS = "ABCD"

PROMPT = """What is the correct answer to this question: {question}

Choices:
(A) {A}
(B) {B}
(C) {C}
(D) {D}

Format your response as follows: "The correct answer is (insert answer here)\""""


def shuffled_choices(row, i):
    """Question i's four choices in the order it shows them (seed = question number, the same in every mode)."""
    choices = [row["Correct Answer"], row["Incorrect Answer 1"], row["Incorrect Answer 2"],
               row["Incorrect Answer 3"]]
    random.Random(i).shuffle(choices)
    return choices


def chosen_letter(reply):
    """The letter the reply picks, or None. Tries "answer is (X)", then "(X)", then a reply that starts with X."""
    # Only "answer is" ignores case (2026-10-12, from a review): with the whole pattern case-insensitive, "The
    # correct answer is a transcription factor" was read as A.
    for pattern in (r"(?i:answer is):?\s*\(?([A-D])\b", r"\(([A-D])\)", r"^\s*\(?([A-D])[).:\s]?\s*$|^\s*\(?([A-D])[).:]"):
        match = re.search(pattern, reply)
        if match:
            return next(g for g in match.groups() if g).upper()
    return None


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    answer = {"raw": run_ifbench.raw_answer, "harness": run_ifbench.harness_answer,
              "strat2": run_ifbench.strat2_answer}.get(mode)
    if not answer:
        sys.exit("Usage: python benchmark/run_gpqa.py raw|harness|strat2 [number of questions | hard | control]")
    if not DATA.exists():
        sys.exit(f"No {DATA}. Download GPQA first (see benchmark/README.md).")
    rows = list(csv.DictReader(open(DATA, encoding="utf-8")))
    subset = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] in SETS else None
    ids = list(range(len(rows)))  # question numbers; each keeps its own shuffle seed
    if subset:
        ids = json.loads(SETS[subset].read_text(encoding="utf-8"))["ids"]
    elif len(sys.argv) > 2:
        ids = ids[:int(sys.argv[2])]
    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / f"gpqa_{mode}{'_' + subset if subset else ''}.jsonl"
    correct = unanswered = 0
    start = time.time()
    with open(out, "w", encoding="utf-8") as f:
        for n, i in enumerate(ids, 1):
            row = rows[i]
            choices = shuffled_choices(row, i)
            right = LETTERS[choices.index(row["Correct Answer"])]
            prompt = PROMPT.format(question=row["Question"].strip(),
                                   **{letter: c.strip() for letter, c in zip(LETTERS, choices)})
            reply, log = run_ifbench.answer_and_log(answer, prompt)
            reply = reply or ""
            letter = chosen_letter(reply)
            correct += letter == right
            unanswered += letter is None
            f.write(json.dumps({"id": i, "right": right, "chosen": letter, "reply": reply, "log": log},
                               ensure_ascii=False) + "\n")
            f.flush()
            if n % 20 == 0:
                print(f"  {n}/{len(ids)}  {correct} correct  {time.time() - start:.0f}s", flush=True)
    print(f"GPQA diamond{f' {subset} set' if subset else ''}, {mode}: {correct}/{len(ids)} = {correct / len(ids):.1%} correct "
          f"({unanswered} replies without a readable letter; random guessing: 25%), {time.time() - start:.0f}s")


if __name__ == "__main__":
    main()
