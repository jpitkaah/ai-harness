"""The small test: each benchmark's hard set and control set, compared item by item with the reference.

    python small_test.py            GPQA and IFBench (~3 min)
    python small_test.py gpqa       GPQA only (~1 min)
    python small_test.py ifbench    IFBench only (~2 min)

Runs strat2.py (the strategy being worked on) through the benchmark runners, at temperature 0 and without
the prompt cache, on two 30-item sets per benchmark (benchmark/README.md), picked from two full reference runs:
  hard     items wrong in both reference runs - shows fixes;
  control  items right in both reference runs - shows regressions.
So unchanged code should score hard 0 and control 30 (benchmark/small_test_reference.json). "fixed" = right
now on the hard set, "broke" = wrong now on the control set. A version is better when it fixes clearly more
than it breaks. The summary gives the chance that a split at least that uneven comes from luck alone, and a
rough estimate of a full run.

After the test, each wrong answer is shown with its input, the answer, how it was made (route, attempts,
why the last attempt stopped, whether it is the last rejected attempt) and why it was scored wrong:
  GPQA     the choice it picked and the right one, plus the question writer's explanation;
  IFBench  which of the prompt's instructions IFBench's own checker found not followed.
Long texts are cut in the middle on screen; benchmark/results/small_test.txt has them in full.
Also run our own eval (python eval.py strat2) when a change could affect it.
"""

import csv
import json
import re
import subprocess
import sys
from math import comb
from pathlib import Path

BENCHMARK = Path(__file__).resolve().parent / "benchmark"
sys.path.insert(0, str(BENCHMARK))
import run_gpqa  # noqa: E402  (the GPQA data and each question's choice order)
import run_ifbench  # noqa: E402  (the IFBench sets and the results folder)

MODE = "strat2"  # the runners' mode for strat2.py, the working copy
RESULTS = run_ifbench.RESULTS
REPORT = RESULTS / "small_test.txt"
SCREEN_LIMIT = 500  # characters per text on screen; the saved report has everything
SUBSETS = {"hard": "wrong in both reference runs", "control": "right in both reference runs"}
# Per benchmark: the reference's right items on each set (GPQA row numbers, IFBench keys), and from the two
# full reference runs: how many items were right in both, wrong in both, or differed (unstable), and the
# runs' scores. Rewritten whenever the sets are rebuilt.
REFERENCE = json.loads((BENCHMARK / "small_test_reference.json").read_text(encoding="utf-8"))


def shorten(text, limit):
    """The text, or its start and end with the middle cut out (instructions often come last)."""
    text = (text or "").strip()
    if limit is None or len(text) <= limit:
        return text
    half = limit // 2
    return f"{text[:half].rstrip()} [... {len(text) - 2 * half} characters cut ...] {text[-half:].lstrip()}"


def block(text):
    """The text as indented lines."""
    return [f"      {line}".rstrip() for line in (text or "(empty)").splitlines()]


def header(benchmark, subset, right, total):
    return f"=== {benchmark} {subset} set ({SUBSETS[subset]}): {right}/{total} right. The {total - right} wrong: ==="


def compared(benchmark, subset, item):
    """A wrong answer, compared with the reference."""
    return ("BROKE: right in both reference runs" if item in REFERENCE[benchmark][subset]
            else "wrong in both reference runs too")


def how(log):
    """How the answer was made, from the runner's log: route, attempts, why the last one stopped."""
    if not log or not log.get("attempts"):
        return "  How: not logged"
    route = (f"{log['route']} route (long-text router {log['long_score']:.2f})" if log.get("route")
             else "route not logged")
    text = f"  How: {route}; {log['attempts']} attempt(s), the last stopped at: {log['stopped_by']}"
    if log["rejected"]:
        text += f"; rejected: {'; '.join(log['rejected'])}"
    if log["unchecked"]:
        text += " - all rejected, so this is the last rejected attempt"
    return text


def gpqa_report(subset, limit):
    """(items answered right, set size, report lines) for the last run on a GPQA set."""
    rows = list(csv.DictReader(open(run_gpqa.DATA, encoding="utf-8")))
    results = [json.loads(line) for line in open(RESULTS / f"gpqa_{MODE}_{subset}.jsonl", encoding="utf-8")]
    right = {r["id"] for r in results if r["chosen"] == r["right"]}
    wrong = [r for r in results if r["id"] not in right]
    lines = [header("GPQA", subset, len(right), len(results))]
    for n, r in enumerate(wrong, 1):
        row = rows[r["id"]]
        choices = dict(zip(run_gpqa.LETTERS, (c.strip() for c in run_gpqa.shuffled_choices(row, r["id"]))))
        picked = (f'it picked ({r["chosen"]}) "{choices[r["chosen"]]}"' if r["chosen"]
                  else "no answer letter found in the reply")
        lines += ["", f"[{n}/{len(wrong)}] question {r['id']} - {row['High-level domain']} / {row['Subdomain']}"
                      f" - {compared('gpqa', subset, r['id'])}",
                  "  Input:", *block(shorten(row["Question"], limit)),
                  *(f"      ({letter}) {text}" for letter, text in choices.items()),
                  "  Answer:", *block(shorten(r["reply"], limit)),
                  how(r.get("log")),
                  f'  Why: {picked}; the right answer is ({r["right"]}) "{choices[r["right"]]}".',
                  "  The question writer's explanation:", *block(shorten(row["Explanation"], limit))]
    return right, len(results), lines


def settings(kwargs):
    """An instruction's settings without the empty ones, e.g. "min_words=77, max_words=99"."""
    parts = []
    for key, value in (kwargs or {}).items():
        if value is None:
            continue
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        parts.append(f"{key}={value!r}" if isinstance(value, str) else f"{key}={value}")
    return ", ".join(parts)


def ifbench_report(subset, limit):
    """(items answered right, set size, report lines) for the last run on an IFBench set, as IFBench's
    checker scored it."""
    rows = {row["prompt"]: row for row in map(json.loads, open(run_ifbench.SETS[subset], encoding="utf-8"))}
    logs = {r["prompt"]: r.get("log") for r in map(json.loads, open(RESULTS / f"ifbench_{MODE}_{subset}.jsonl",
                                                                        encoding="utf-8"))}
    scored = RESULTS / f"ifbench_{MODE}_{subset}-eval_results_loose.jsonl"
    results = [json.loads(line) for line in open(scored, encoding="utf-8")]
    right = {rows[r["prompt"]]["key"] for r in results if r["follow_all_instructions"]}
    wrong = [r for r in results if not r["follow_all_instructions"]]
    lines = [header("IFBench", subset, len(right), len(results))]
    for n, r in enumerate(wrong, 1):
        row = rows[r["prompt"]]
        words = len(re.findall(r"\w+", r["response"]))  # counted like IFBench's checker
        lines += ["", f"[{n}/{len(wrong)}] prompt {row['key']} - {', '.join(r['instruction_id_list'])}"
                      f" - {compared('ifbench', subset, row['key'])}",
                  "  Input:", *block(shorten(r["prompt"], limit)),
                  f"  Answer ({words} words):", *block(shorten(r["response"], limit)),
                  how(logs.get(r["prompt"])),
                  "  Why (IFBench's checker):"]
        for instruction, followed, kwargs in zip(r["instruction_id_list"], r["follow_instruction_list"],
                                                 row["kwargs"]):
            detail = settings(kwargs)
            lines.append(f"      {'followed    ' if followed else 'NOT followed'}  {instruction}"
                         + (f" ({detail})" if detail else ""))
    return right, len(results), lines


def chance(fixed, broke):
    """Two-sided sign test: how often a split at least this uneven comes from coin flips alone."""
    n, k = fixed + broke, min(fixed, broke)
    return min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / 2 ** n) if n else 1.0


def numbered(items):
    return ", ".join(str(i) for i in sorted(items, key=int))


TESTS = {"gpqa": ("GPQA", "run_gpqa.py", gpqa_report), "ifbench": ("IFBench", "run_ifbench.py", ifbench_report)}


def main():
    choice = sys.argv[1] if len(sys.argv) > 1 else "both"
    names = list(TESTS) if choice == "both" else [choice]
    if any(name not in TESTS for name in names):
        sys.exit("Usage: python small_test.py [gpqa|ifbench|both]")
    for name in names:
        title, script, _ = TESTS[name]
        for subset in SUBSETS:
            print(f"--- {title} {subset} set, {MODE} ---", flush=True)
            if subprocess.run([sys.executable, str(BENCHMARK / script), MODE, subset]).returncode:
                sys.exit(f"{script} failed. Is the llama server running on port 8090?")
    summary, screen, full = [], [], []
    for name in names:
        title, _, report = TESTS[name]
        summary.append(title)
        share, total, flips = {}, {}, [0, 0]
        for subset in SUBSETS:
            right, total[subset], lines = report(subset, SCREEN_LIMIT)
            share[subset] = len(right) / total[subset]
            screen += ["", *lines]
            full += ["", *report(subset, None)[2]]
            reference = set(REFERENCE[name][subset])
            fixed, broke = right - reference, reference - right
            flips[0] += len(fixed)
            flips[1] += len(broke)
            summary.append(f"  {subset:7} set ({SUBSETS[subset]}): {len(right):>2}/{total[subset]} right now, "
                           f"reference {len(reference):>2}  ->  fixed {len(fixed)}, broke {len(broke)}")
            if fixed:
                summary.append(f"      fixed: {numbered(fixed)}")
            if broke:
                summary.append(f"      broke: {numbered(broke)}")
        if any(flips):
            summary.append(f"  together: fixed {flips[0]}, broke {flips[1]} - a split at least this uneven comes "
                           f"from chance alone {chance(*flips):.0%} of the time")
        # The full benchmark = the items right in both reference runs (like the control set) + those wrong in
        # both (like the hard set) + the unstable ones, counted half. The sets are small samples of the first two
        # groups, so the estimate is rough: +- one standard error.
        ref = REFERENCE[name]
        weight = {"control": ref["stable_right"], "hard": ref["stable_wrong"]}
        estimate = sum(weight[s] * share[s] for s in SUBSETS) + ref["unstable"] / 2
        error = sum(weight[s] ** 2 * share[s] * (1 - share[s]) / total[s] for s in SUBSETS) ** 0.5
        size = ref["size"]
        summary.append(f"  rough estimate of a full run: {estimate / size:.1%} +- {error / size:.1%} "
                       f"(the reference runs: {', '.join(f'{r / size:.1%}' for r in ref['runs'])})")
    print("\n".join(screen))
    REPORT.write_text("\n".join(full).lstrip() + "\n", encoding="utf-8")
    print(f"\n=== Small test ({MODE}), compared item by item with the reference ===")
    print("\n".join(summary))
    print("Unchanged code should score hard 0, control 30; a 1-item difference is noise.")
    print(f"Full texts: {REPORT}")


if __name__ == "__main__":
    main()
