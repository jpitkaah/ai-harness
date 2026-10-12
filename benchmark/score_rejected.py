"""Score the attempts the harness rejected, with IFBench's own scorer: do the checks throw away replies that pass?

    python benchmark/score_rejected.py strat2_hard      after: python benchmark/run_ifbench.py strat2 hard
    python benchmark/score_rejected.py strat2_control   (any results/ifbench_<name>.jsonl written since 2026-10-12)

Reads the rejected attempts saved in each answer's log ("rejected_attempts"). IFBench's scorer takes one reply per
prompt, so it runs once per attempt number (the 1st rejected attempt of every prompt, then the 2nd, ...). Prints,
per rejection reason, how many rejected attempts would have passed, and how many of their prompts the final reply
passed. A reply stopped by the repetition check is scored as it was cut: a lower bound for what it would have been.
"""

import collections
import json
import os
import subprocess
import sys
from pathlib import Path

RESULTS = Path(__file__).resolve().parent / "results"
IFBENCH = Path(__file__).resolve().parent / "IFBench"


def score(rows, responses, name):
    """IFBench's loose scorer on {prompt: response}; returns {prompt: passed}."""
    data = RESULTS / f"{name}_data.jsonl"
    data.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    out = RESULTS / f"{name}.jsonl"
    out.write_text("".join(json.dumps({"prompt": p, "response": r}, ensure_ascii=False) + "\n"
                           for p, r in responses.items()), encoding="utf-8")
    scored = subprocess.run([sys.executable, "-m", "run_eval", f"--input_data={data}",
                             f"--input_response_data={out}", f"--output_dir={RESULTS}"],
                            cwd=IFBENCH, capture_output=True, text=True, encoding="utf-8",
                            env={**os.environ, "PYTHONUTF8": "1"})
    if scored.returncode:
        sys.exit(f"IFBench's scorer failed:\n{scored.stderr[-2000:]}")
    loose = RESULTS / f"{name}-eval_results_loose.jsonl"
    return {r["prompt"]: r["follow_all_instructions"] for r in map(json.loads, open(loose, encoding="utf-8"))}


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    stem = sys.argv[1]
    logs = [json.loads(line) for line in open(RESULTS / f"ifbench_{stem}.jsonl", encoding="utf-8")]
    if not any("rejected_attempts" in r["log"] for r in logs):
        sys.exit("No rejected attempts saved in this run (it predates the logging): rerun it first.")
    final = {r["prompt"]: r["follow_all_instructions"]
             for r in map(json.loads, open(RESULTS / f"ifbench_{stem}-eval_results_loose.jsonl", encoding="utf-8"))}
    test = {r["prompt"]: r for r in map(json.loads, open(IFBENCH / "data" / "IFBench_test.jsonl", encoding="utf-8"))}

    table = collections.defaultdict(lambda: [0, 0, 0])  # reason -> [rejected attempts passing, attempts, final passing]
    rescued = []  # prompts the final reply failed but a rejected attempt passed
    for k in range(3):
        batch = {r["prompt"]: r["log"]["rejected_attempts"][k] for r in logs if len(r["log"]["rejected_attempts"]) > k}
        if not batch:
            break
        passed = score([test[p] for p in batch], {p: a["reply"] for p, a in batch.items()}, f"ifbench_{stem}_rejected{k + 1}")
        for p, attempt in batch.items():
            reason = attempt["reason"].split(" (")[0].split(":")[0]
            table[reason][0] += passed[p]
            table[reason][1] += 1
            table[reason][2] += final[p]
            if passed[p] and not final[p]:
                rescued.append((k + 1, reason, p))

    print(f"{'rejected because':20} {'would pass':>10} {'attempts':>9} {'final reply passed':>19}")
    for reason, (ok, n, fin) in sorted(table.items(), key=lambda kv: -kv[1][1]):
        print(f"{reason:20} {ok:10} {n:9} {fin:19}")
    print(f"\nPrompts the final reply failed but a rejected attempt passed: {len(set(p for _, _, p in rescued))}")
    for k, reason, p in rescued:
        print(f"  attempt {k}, {reason}: {p[:90]!r}")


if __name__ == "__main__":
    main()
