"""Run IFBench (allenai/IFBench, cloned in benchmark/IFBench) on our model and score it.

    python benchmark/run_ifbench.py raw        base model, no harness (chat endpoint, like eval.py baseraw)
    python benchmark/run_ifbench.py harness    best_strat.py, as the app answers without search
    python benchmark/run_ifbench.py strat2     strat2.py, the working copy
    python benchmark/run_ifbench.py strat2 hard   the 30-prompt hard set (benchmark/ifbench_hard.jsonl)
    python benchmark/run_ifbench.py strat2 control   the 30-prompt control set (ifbench_control.jsonl)
    python benchmark/run_ifbench.py raw 20     only the first 20 prompts (quick check)

Writes benchmark/results/ifbench_<mode>.jsonl (prompt + response, IFBench's format), then runs
IFBench's own scorer on it. The paper reports prompt-level LOOSE accuracy: a prompt counts only
if every instruction in it is followed.
"""

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent      # the project folder (harness.py etc.)
IFBENCH = Path(__file__).resolve().parent / "IFBench"
RESULTS = Path(__file__).resolve().parent / "results"
# The small test's sets (benchmark/README.md): 30 prompts v17 got wrong (hard) and 30 it got right
# (control: it shows regressions), each picked evenly over the instruction themes.
SETS = {"hard": Path(__file__).resolve().parent / "ifbench_hard.jsonl",
        "control": Path(__file__).resolve().parent / "ifbench_control.jsonl"}
sys.path.insert(0, str(ROOT))

import best_strat  # noqa: E402  (after the path change)
import eval as project_eval  # noqa: E402
import harness  # noqa: E402
import strat2  # noqa: E402

RAW_MAX_TOKENS = 1024  # IFBench answers are long (sample answers: median ~1300 characters)


def raw_answer(prompt):
    """Base model, no harness: the server's chat format, one call, reply as-is."""
    # Temperature 0 and no prompt cache, like every test (2026-10-12; before, the server's defaults: 0.8, cache on).
    body = json.dumps({"model": project_eval.BASE, "messages": [{"role": "user", "content": prompt}],
                       "max_tokens": RAW_MAX_TOKENS, "temperature": harness.ANSWER_TEMPERATURE,
                       "cache_prompt": harness.CACHE_PROMPT}).encode("utf-8")
    request = urllib.request.Request(project_eval.CHAT_URL, data=body,
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read())["choices"][0]["message"]["content"]


def harness_answer(prompt):
    """best_strat, as the app answers without search; if every attempt fails the checks, the last one
    (the app shows it, marked unchecked)."""
    harness.last_rejected.clear()
    reply, _ = best_strat.answer([], prompt)
    return reply if reply is not None else harness.last_rejected.get("reply", "")


def strat2_answer(prompt):
    """strat2.py, the working copy (v16: + the long-text route); same fallback as harness_answer."""
    harness.last_rejected.clear()
    reply, _ = strat2.answer([], prompt)
    return reply if reply is not None else harness.last_rejected.get("reply", "")


def how_answered(calls):
    """From the harness's trace of one answer: the route strat2 chose, its long-text router score, the
    attempts, why the last one stopped, the reasons for rejected ones, and whether every attempt was
    rejected (then the reply is the last rejected one - the app's "unchecked" fallback)."""
    route = next((c["output"] for c in calls if c["kind"] == "route"), {})
    checks = [c["output"] for c in calls if c["kind"] == "check"]
    return {"route": route.get("route"), "long_score": route.get("long_score"),
            "route_info": {k: v for k, v in route.items() if k not in ("route", "long_score")},  # e.g. v22's letter reads
            "attempts": len(checks),
            "stopped_by": checks[-1]["stopped_by"] if checks else None,
            "rejected": [c["reason"] for c in checks if not c["ok"]],
            "unchecked": bool(checks) and not any(c["ok"] for c in checks),
            # The rejected attempts' texts (2026-10-12), so benchmark/score_rejected.py can score them: do the
            # checks throw away replies that would pass? A reply stopped by the repetition check is the text up
            # to where the loop was caught.
            "rejected_attempts": [{"reason": c["reason"], "stopped_by": c["stopped_by"], "reply": c["reply"]}
                                  for c in checks if not c["ok"]]}


def answer_and_log(answer, prompt):
    """answer(prompt), plus how_answered() from the harness's trace of it (empty for the raw model)."""
    harness.trace = []
    try:
        reply = answer(prompt)
    finally:
        calls, harness.trace = harness.trace, None
    return reply, how_answered(calls)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    answer = {"raw": raw_answer, "harness": harness_answer, "strat2": strat2_answer}.get(mode)
    if not answer:
        sys.exit("Usage: python benchmark/run_ifbench.py raw|harness|strat2 [number of prompts | hard | control]")
    rows = [json.loads(line) for line in open(IFBENCH / "data" / "IFBench_test.jsonl", encoding="utf-8")]
    subset = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] in SETS else None
    if subset:
        rows = [json.loads(line) for line in open(SETS[subset], encoding="utf-8")]
    elif len(sys.argv) > 2:
        rows = rows[:int(sys.argv[2])]
    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / f"ifbench_{mode}{'_' + subset if subset else ''}.jsonl"
    start = time.time()
    with open(out, "w", encoding="utf-8") as f:
        for i, row in enumerate(rows, 1):
            response, log = answer_and_log(answer, row["prompt"])
            # "log" is ours (IFBench's scorer reads only prompt and response): how the answer was made.
            f.write(json.dumps({"prompt": row["prompt"], "response": response, "log": log},
                               ensure_ascii=False) + "\n")
            f.flush()
            if i % 10 == 0:
                print(f"  {i}/{len(rows)}  {time.time() - start:.0f}s", flush=True)
    # IFBench's scorer. It reads files with the system encoding: UTF-8 mode fixes that on Windows.
    # With a subset, score against the same subset of the test data.
    data = IFBENCH / "data" / "IFBench_test.jsonl"
    if subset:
        data = SETS[subset]
    elif len(sys.argv) > 2:
        data = RESULTS / "ifbench_subset.jsonl"
        data.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    scored = subprocess.run([sys.executable, "-m", "run_eval", f"--input_data={data}",
                             f"--input_response_data={out}", f"--output_dir={RESULTS}"],
                            cwd=IFBENCH, capture_output=True, text=True, encoding="utf-8",
                            env={**os.environ, "PYTHONUTF8": "1"})
    if scored.returncode:  # a crashed scorer used to leave an empty report and print nothing (2026-10-12)
        sys.exit(f"IFBench's scorer failed:\n{scored.stderr[-2000:]}")
    report = scored.stdout
    (RESULTS / f"{out.stem}_report.txt").write_text(report, encoding="utf-8")
    for line in report.splitlines():
        if "Accuracy Scores" in line or line.startswith(("prompt-level", "instruction-level")):
            print(line.split("\\")[-1])
    print(f"{len(rows)} prompts, {time.time() - start:.0f}s. Full report: {RESULTS / f'{out.stem}_report.txt'}")


if __name__ == "__main__":
    main()
