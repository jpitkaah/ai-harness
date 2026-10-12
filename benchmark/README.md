# Benchmarks

Two public benchmarks, run on the base model with and
without the harness. Results go to `results/`.

| Benchmark | What it tests | Status |
|---|---|---|
| [IFBench](https://github.com/allenai/IFBench) (cloned in `IFBench/`) | following precise writing instructions ("use the word X three times", "start every sentence with a verb"), 300 prompts | runs: `run_ifbench.py` |
| [GPQA](https://huggingface.co/datasets/Idavidrein/gpqa) (diamond set, `gpqa/`) | graduate-level science multiple choice (4 options), 198 questions | needs a download with your Hugging Face account (below), then `run_gpqa.py` |

## Setup (once)

```powershell
# IFBench: its prompts and its own scorer (not in this repo's git - cloned here).
git clone https://github.com/allenai/IFBench benchmark\IFBench
# IFBench's scorer needs a few packages (spaCy, NLTK, ...), in the Python the project uses:
python -m pip install -r benchmark\IFBench\requirements.txt
```

**GPQA is gated:** Hugging Face asks you to log in and accept its terms before downloading.
1. Log in at <https://huggingface.co>, open <https://huggingface.co/datasets/Idavidrein/gpqa> and
   accept the terms (approval is automatic).
2. Create a token (read access is enough) at <https://huggingface.co/settings/tokens>.
3. Log in from the terminal and download into this folder:
   ```powershell
   hf auth login
   hf download Idavidrein/gpqa --repo-type=dataset --local-dir benchmark\gpqa
   ```
GPQA's authors ask that its questions are not posted online (so they don't leak into training
data): keep `gpqa/` and the result files local.

## Running

```powershell
python benchmark\run_ifbench.py raw        # base model, no harness
python benchmark\run_ifbench.py harness    # best_strat.py (the app's answers, without search)
python benchmark\run_gpqa.py raw
python benchmark\run_gpqa.py harness
```
Add a number to run only the first N items (`python benchmark\run_ifbench.py raw 20`).

- **raw** = the base model through the server's chat format, one call, the reply as it is (like
  `eval.py baseraw`), up to 1024 tokens.
- **harness** = `best_strat.answer`, as the app answers when nothing needs looking up. Search is
  left out: looking up 300 writing tasks or 198 exam questions on the web would only load SearXNG.
  If every attempt fails the checks, the last attempt is used (as the app shows it).

IFBench reports *prompt-level loose accuracy*: a prompt counts only if every instruction in it is
followed. Its scorer reads files with the system encoding, so `run_ifbench.py` runs it in Python's
UTF-8 mode (`PYTHONUTF8=1`) - without it, it crashes on Windows.

## Results

**Redone deterministically (2026-10-12): temperature 0, no prompt cache, full runs.** Until then the raw runs sent
no temperature, so the server's default applied (0.8) with its prompt cache on: the base-model numbers in the older
tables below were sampled. Old files kept as `results/*_raw_sampled_cache*`.

| | GPQA diamond (198) | IFBench (300, prompt-level loose / strict) |
|---|---|---|
| Base model, no harness | **69 = 34.8%** (sampled: 30.3%) | **49 = 16.3%** / 13.3% (sampled: 14.3%) |
| v17 (`results/*_v17_nocache*`) | **68 = 34.3%** (with the cache: 75) | **53 = 17.7%** / 14.3% (with the cache: 54) |
| strat2 v20b (best_strat since 2026-10-12) | 68 and 71 | 50 = 16.7% / 13.7% |

GPQA raw vs v17: the same letter on 157/198; right in both 56, only raw 13, only v17 12. On these benchmarks the
harness is level with the bare model; the earlier gap (30.3% -> 37.9%) came from the sampled baseline and the cache.
Our own held-out eval still shows a clear gain (base model 54/78 at temperature 0 -> 66/78).

**IFBench** (2026-10-10, all 300 prompts, one run each; with 300 prompts a run is roughly ±2-3 points):

| Setup | Prompt-level loose (the paper's measure) | Strict | Answer length (median) | Time |
|---|---|---|---|---|
| Base model, no harness | **14.3%** | 11.0% | ~1050 characters | 13.5 min |
| Base model + harness (best_strat) | **14.7%** | 14.3% | ~200 characters (one line) | 6 min |
| Base model + harness, token limits raised to 4000 (was 50/100) | **16.7%** | 16.0% | ~130 characters (one line; longest 1376) | 8 min |

About the same overall, for different reasons. The harness answers in one line (it stops at a
line break and at 100 tokens - built for short chat answers), so it wins checks a single line
passes easily (indentation, "each sentence one word longer", option formats: format 0.33 vs 0.23,
sentence 0.25 vs 0.07) and loses the counting ones that need a long text (unique words, pronouns:
count 0.18 vs 0.34). For scale: IFBench's own sample answers (from a much larger model) score 31%.

Raising the token limits (2026-10-10): 14.7% -> 16.7%, about the size of the run-to-run noise. Answers
can now be longer (longest 1376 characters instead of 476), and the "custom" and "words" checks rose
(0.00 -> 0.18, 0.03 -> 0.13). But every answer is still ONE line: the harness also stops at the first
line break (its newline stop sequence), so lists, paragraphs and sections - which many IFBench tasks ask
for - are impossible. The raw model wrote multi-line answers for 209 of 300 prompts.

**GPQA diamond** (2026-10-10, all 198 questions, one run each; run by the user; random guessing = 25%):

| Setup | Correct | Replies without a readable letter | Time |
|---|---|---|---|
| Base model, no harness | **60/198 = 30.3%** | 3 | 194 s |
| Base model + harness (best_strat, token limits 4000) | **41/198 = 20.7%** | 48 | 297 s |

The harness scores *below guessing*, mostly because a quarter of its replies pick no letter at all:
- best_strat's special routes answer a different question than the multiple-choice one. The math route
  rewrites the question as a calculation and replies with a bare value ("0.85", "5000", "6.", "The ratio
  of silicon atoms ... is approximately 12.6."), so the A-D choices are lost.
- Sample answers leak from the route prompts: "The swim." (a comparison sample), "12 eggs.".
- Of the replies that do pick a letter (150), 41 are right = 27%, about guessing level. The raw model
  (195 with a letter, 60 right = 31%) is a little above it. GPQA is built to be hard for experts with web
  access ("Google-proof"); a 3B model is expected to be near 25-30% either way.

**IFBench with strat2 v16, the long-text route** (2026-10-10). A model yes/no ("asks for a longer text?",
>= 0.85; none of our 44 eval/search questions reach it, IFBench's median 0.90) sends writing tasks to a
"Request: / Response:" prompt that may span several lines (stops at the next "Request:" or the end of
text, up to 1500 tokens; the repetition check there uses 10-word phrases instead of 4). The normal route
went back to the 50/100 token limits. Split in halves (the second half was not looked at while building):

| Setup | Prompts 1-150 | Prompts 151-300 (held out) | All 300 |
|---|---|---|---|
| Base model, no harness | 22.7% | 6.0% | 14.3% |
| Harness, limits 50/100 | 22.7% | 6.7% | 14.7% |
| Harness, limits 4000 | 20.0% | 13.3% | 16.7% |
| **strat2 v16 (long-text route)** | **24.0%** | **14.7%** | **19.3%** |

Multi-line answers: 154 of 300 (before: 0). Our own 26-question eval is unchanged (it never takes the
route): best_strat 75, 73, 73, 71, 71 and strat2 v16 70, 73, 68 - the same code path, so run-to-run noise.

**GPQA with strat2 v16:** 53/198 = **26.8%** (1 reply without a letter; v15: 20.7%, 48; raw: 30.3%). All 198
GPQA questions take the long-text route (a long question ending in "Format your response as follows"
looks like a writing request to the router), and its prompt answers "The correct answer is (B)" instead
of the math route's bare values. v16 is saved as best_strat.py, so `harness` mode now runs v16.

**Tried, not kept - v17, copied instructions as a reminder** (2026-10-10): on the long-text route a small
step copied the request's rules into an "Instructions to follow:" line above "Response:". IFBench first
half, 2 runs each: v17 22.0% and 23.3% (avg 22.7%) vs v16 24.0% and 20.7% (avg 22.4%) - no measurable gain
(single 150-prompt runs differ by ~3 points). Details in FAILED_EXPERIMENTS.md.

**Tried, not kept - long-route bar 0.80 instead of 0.85** (2026-10-10): 18 more first-half prompts take
the route. First half, 2 runs: 26.0% and 20.0% (avg 23.0%) vs 22.4% - noise; on the prompts that change
route only: 3/16 and 2/16 passed at 0.85, 2/16 and 1/16 at 0.80. Kept 0.85.

## Final answers at temperature 0 (2026-10-10) - adopted as v17 (best_strat.py)

Why: on GPQA the raw model and v16 were right on mostly *different* questions (both right 25, only raw
35, only v16 28): with answers sampled at temperature 0.8, a model that only slightly prefers the right
letter mostly guesses. Reading the most likely letter instead (as the routers read YES/NO) gave 39/99 on
the first half (raw 34, v16 26). Temperature 0 for final answers does the same in a general way, for
every answer. Measured with a scratch script that forces it (no project file changed), one run each -
temperature 0 is deterministic:

| | Raw (sampled) | v16 (sampled) | v16, temperature 0 |
|---|---|---|---|
| GPQA 1-99 / 100-198 (held out) / all | 34 / 26 / 30.3% | 26 / 27 / 26.8% | **41 / 32 / 36.9%** |
| IFBench 1-150 / 151-300 (held out) / all | 22.7% / 6.0% / 14.3% | 24.0% / 14.7% / 19.3% | 22.7% / 13.3% / **18.0%** |
| Our 26-question eval | - | ~72 (68-75 over 8 runs) | **74/78** |

Adopted (user's rule: tests deterministic, only the chat app samples). The adopted v17 run on GPQA:
**75/198 = 37.9%** (42 + 33; the experiment had 73: temperature 0 is *almost* deterministic - 2 of 198
answers differed between the runs, from the model server).

## The small test: hard and control sets, 30 questions each (2026-10-10)

Full runs take 10-20 minutes, so changes are tested on two small sets per benchmark. Rebuilt 2026-10-10 from
**stable items** (the review's suggestion): items with the same result in two full runs, so a narrow pass or
miss can't flip on the next run. GPQA: strat2's 68 run and the separating run's first attempts (71), both with
the echo fix and no prompt cache. IFBench: strat2's full run with the echo fix and no cache (50/300, below) and
v17's full run (54/300). Picked evenly over the themes (fixed random seed 2026; different subtypes first):
- **hard** - 30 items wrong in both runs; shows fixes.
  - `ifbench_hard.jsonl` (IFBench's own data format, scored by its own scorer): 4-5 per instruction theme
    (count, custom, format, ratio, repeat, sentence, words) - 27 instruction types. Drawn from all 300 prompts:
    the IFBench file is sorted by instruction type, so its halves were never a random split.
  - `gpqa_hard.json` (row numbers): 10 per domain (Biology, Chemistry, Physics).
- **control** - 30 items right in both runs; shows regressions, which a set of failures can't.
  - `ifbench_control.jsonl`: as even as the 39 stable passes allow - format 14, count 6, words 5, ratio 2,
    sentence 2, custom 1 - 18 instruction types.
  - `gpqa_control.json`: Physics 11, Chemistry 10, Biology 9.
- Items that differed between the two runs (GPQA 5, IFBench 26) are in neither set; the estimate counts them half.
The first sets (v17's misses and passes in one run) re-scored only 22-23 of 30 on IFBench's control set.

```powershell
python small_test.py                           # both benchmarks, both sets, then every wrong answer and why (~3 min)
python benchmark\run_ifbench.py strat2 hard    # one set, score only (~75 s)
python benchmark\run_gpqa.py strat2 control    # one set, score only (~25 s)
```
`small_test.py` (in the project folder; `gpqa` or `ifbench` runs one benchmark) shows each wrong answer's
input, answer and reason - still wrong on the hard set, regressions on the control set: for GPQA the choice it
picked and the right one, plus the question writer's explanation; for IFBench the instructions IFBench's
checker found not followed (loose), with their settings. Long texts are cut in the middle on screen;
`results/small_test.txt` has them in full. It ends with a rough estimate of a full run: the control set's share
right x the items right in both runs (GPQA 67, IFBench 39) + the hard set's share x those wrong in both (126,
235) + half the unstable ones, +- one standard error. For unchanged code it equals the two runs' average (GPQA
35.1%, IFBench 17.3%); compare an estimate with that, not with one full run.

**The reference** (`small_test_reference.json`): by construction, unchanged code scores hard 0 and control 30.
The small test compares every item with it: *fixed* (right now on the hard set) and *broke* (wrong now on the
control set), plus the chance that a split at least that uneven comes from luck alone (a sign test). The
noise with these sets is not measured yet; with the first sets, unchanged code flipped 0-2 items per set each
way.

**Full runs without the prompt cache** (2026-10-10, the current strat2: echo fix, DRY on retries): IFBench
50/300 = **16.7%** loose (strict 13.7%; against v17's 54 with the cache: 11 fixed, 15 broke - chance 56%, no
real difference), 745 s; 32 answers are the last rejected attempt, 113 prompts got a DRY retry. GPQA 68 and
71/198 (34-36%).

**How each answer was made** (2026-10-10). The runners keep a log with every answer (from the harness's
trace): the route strat2 chose and its long-text router score, the attempts, why the last one stopped, why
earlier ones were rejected, and whether all were rejected - then the reply is the last rejected attempt, as
the app shows it "unchecked". The small test prints it under each wrong answer. The first logged run (v17)
showed:
- **GPQA: every answer is such a fallback.** 177 of 180 attempts were rejected as an *echo*: "The correct
  answer is (A) G1 is a transcription factor, ..." uses only words from the prompt (its format line and the
  option), and the echo check rejects replies that add no new word. So GPQA scores so far came from the third
  attempt, made after two notes saying "This time it answers with something new." After the echo fix (every
  answer the first attempt) and without the prompt cache, the full run scored **68/198 = 34.3%** (v17: 75): 4
  questions fixed, 11 broke (chance 12%). A separating run (the old echo rule back, so third attempts again,
  no cache) scored 71/198, and its own first attempts 71 too: the echo fix is neutral. The drop from 75 is the
  prompt cache and noise - two identical cache-off setups gave 68 and 71. Without the cache GPQA is ~68-71/198.
- **IFBench: 22 of 60 answers are fallbacks**, mostly from the long route's repetition check (58 rejections;
  then echo 11, the relevance validator 9, language switch 2). These are the replies cut off mid-sentence.

**Prompt cache off in tests.** llama.cpp reuses the already computed start of the previous prompt
(`cache_prompt`, on by default); the numbers then depend on which request came before, and long
temperature-0 answers come out differently. IFBench control set, same code: 20/30 in one small test, 24 and
25 in two runs back to back; only 6 of its 30 replies matched v17's full run. Without the cache, back-to-back
runs matched 24/30 replies (with it 15/30) and pass/fail flipped on 1 prompt (with it 3); GPQA's short answers
barely changed (27 vs 26 of 30 identical). Tests now send `cache_prompt: false` (`harness.CACHE_PROMPT`; the
chat app keeps the cache for speed), and IFBench sets take ~30% longer. The full runs above had the cache on.

**Tried, not kept - more samples on the long-text route (v18, v18b):** IFBench hard 2 and 3 (v17: 4), control
19 and 24 (v17: 22); GPQA unchanged. v18 broke the numbered lists that had been passing and its quotes sample
leaked into answers; v18b was within the noise. Details in FAILED_EXPERIMENTS.md.
