# AI Harness — educational project

This is an **educational project**. The goal is to learn the basics of building a
harness around a language model, one small brick at a time.

## Ground rules
- Build only what is asked. Do not add extra features, abstractions, or files unprompted.
- Suggesting a next step is welcome, but wait for approval before building it.
- Keep code simple and readable — clarity over cleverness. Python, standard library preferred.
- **The one rule for the harness (user, 2026-10-10): don't hard-code anything very specific that
  helps the code solve a certain test question.** General mechanisms are fine: prompts, routes the
  model chooses, generic checks, tools, sampler methods such as DRY. Not fine: code written around
  particular test questions or their wording that steers or edits the answer (e.g. spotting "answer in
  one word" and trimming the reply). When unsure whether something counts, ask.
- **One test run per turn, then report and wait.** The user wants to give feedback, insights and
  questions between runs (2026-10-10: ~30 minutes of back-to-back runs and diagnostics left no room for
  that). If answering something needs more runs, say so and ask first.
- **Testing pace:** run one thing at a time, fix, and rerun. **At most 2 runs per test** (3 only once
  upgrades improve scores just slightly). Never run full benchmarks unless
  asked: test benchmark changes with `python small_test.py` (~3 min): per benchmark a hard set (30 items
  wrong in both of two full runs: fixes) and a control set (30 right in both: regressions), in
  `benchmark/`, rebuilt 2026-10-10 from stable items. Unchanged code should score hard 0, control 30; it
  reports fixed / broke per item (`benchmark/small_test_reference.json`) and a rough full-run estimate. Check our
  own eval (`python eval.py strat2`, ~1 min) when a change could affect it. Since 2026-10-12 it asks each
  question once (scores out of 26; older scores are out of 78 - three near-identical runs) and WRONG_PHRASES
  marks keyword-matching wrong answers ("steel is heavier, although both..."). Write the prediction in plan.md
  before the run. Don't rerun baselines whose scores are known; run only the newest/best version.
- **Tests are deterministic: final answers at temperature 0** (`harness.ANSWER_TEMPERATURE`), so every
  run gives the same answers and comparisons are accurate. Only the chat app samples
  (`USER_TEMPERATURE` 0.8) so the user gets a little variety. Never make test answers random again
  (sampling made scores luck); deterministic sampler methods such as DRY are allowed (user,
  2026-10-10). Tests also run without llama.cpp's prompt cache
  (`harness.CACHE_PROMPT = False`; since 2026-10-12 the chat app too, ~30% slower): with it, long answers depended on the request
  before (IFBench control set back to back: 15/30 replies identical with it, 24/30 without). The server is
  still only *nearly* deterministic: about 1 answer per 30-item set flips between runs, so a 1-point
  difference is noise.
- **Before proposing an idea, check `FAILED_EXPERIMENTS.md`.** Don't repeat what already failed.
  When something new fails, add it there with its numbers.

## Current goal
Improve the **base model + harness**. Full 26-question eval, averages of several runs
(`results/2026-10-09_eval-full-averages.txt`): base model with no harness **55.3/78**, base A **58.3/78**, best strategy
(best_strat.py = strat2 v16) **~72/78** (v15 with answer_key v9: 75, 73, 73, 71, 71; v16 adds a long-text route no eval question takes) (v14: 71.0 before the echo check, 72.3 after), instruct model with no harness
**73.0/78**. Since v17 (2026-10-10) tests run at temperature 0: one run is enough (v17: 74/78). The
older numbers were sampled and swung by up to ~8 points per run.
**The 26-question eval is fitted** (audit, 2026-10-10): four strat2 routes were each built for one of its questions,
and several samples mirror them. Judge changes on the **held-out set** (`python eval.py strat2 heldout`, 26 new
questions nobody tuned on; never tune on it): strat2 **66/78**, plain harness 54/78, base model without a harness 54/78
(also 54 at temperature 0 without the cache, 2026-10-12; v17 without the cache 66).
After the mirrored samples were replaced: held-out still 66/78, the old fitted eval 71/78 (was 74-75); with the
validator seeing the conversation: held-out 66, old eval 74.
Leave the instruct model alone for now.

## Setup
- Base model: `QuantFactory/Qwen2.5-3B-GGUF:Q4_K_M` (not instruct — it continues text).
- Instruct model (reference only): `Qwen/Qwen2.5-3B-Instruct-GGUF:Q4_K_M`.
- Server: `llama.exe serve --port 8090` (port 8080 is taken by something else).
  Always call it at `127.0.0.1`, never `localhost` (Windows adds ~2 s per request).
- API: OpenAI-compatible, `http://localhost:8090/v1/completions` (base),
  `/v1/chat/completions` (instruct, raw).

## Files
- `harness.py` — the harness (interactive app). `memory.json` — its memory.
- `eval.py` — fixed test set. `python eval.py` (base A, default), `best` (best_strat.py), `strat2` (strat2.py), `baseraw`
  (base model, no harness), `raw` (instruct, no
  harness), or `both`.
- `stats.py` — summary of `logs/*.jsonl` (real usage only; eval doesn't log).
- `view.py` — `python view.py <id>`: opens a browser page with one app answer's trace (route, chain,
  vote, every model call with prompt + output). The app prints the id under each answer and saves
  `traces/<id>.json` (harness.trace / harness.record; the yes/no calls record too). Tests don't record.
- `FAILED_EXPERIMENTS.md` — what was tested and didn't work.
- `plan.md` — changelog of the strategy versions. Write a prediction there before each run (adopted
  from the harness research: Meta-Harness, AHE, Self-Harness).
- `strat2.py` — the strategy being worked on: model-routed (long text (v16) / letters / comparison / math
  routes + answer_key.py (v9) as normal route). `best_strat.py` = frozen copy of the best (v20b since 2026-10-12, v17 before), which
  the app uses; a version that wins is copied there. harness.py has optional switches for them (defaults
  unchanged): `generate(stop_sequences=)`, `check_reply(use_validator=)`. They reuse
  `harness.check_reply()` (all reply checks) and `harness.top_token_probs()` (a yes/no read as
  probabilities). The older trials (strat1, strat3, strat4's verifier, `run_strats.py` with the failed
  set) were deleted on 2026-10-10; their numbers are in plan.md and FAILED_EXPERIMENTS.md.
- **Web search tool**: `search.py` (model writes a search phrase ->
  SearXNG at 127.0.0.1:8888 -> trafilatura extracts the top 3 pages, ~1500 chars each ->
  model answers from the sources or says "The sources don't say."). App: search is on by default (`/search` toggles it).
  Weather questions (model-routed) use wttr.in instead of web pages. Each look-up is logged with
  its phrase and source texts. Test: `search_eval.py` (helps / no_answer / misleading; correct,
  source-supported, abstained). **Keep search tests small**: ~100 searches/hour got SearXNG's
  engines rate-limited (0 results).
  SearXNG runs in Docker with `searxng/settings.yml` (JSON on, limiter off). Each search asks ONE engine, in turn (`search.ENGINES`: yahoo, yandex, yep, seznam) - asking all at once got them all blocked together. trafilatura is the
  project's first pip dependency.
- **Source chain** (plan + results: `sources.md`): `sources.py` - weather (wttr.in, model-routed) ->
  v8: "can public sources know this?" (model yes/no; if not, no lookup) ->
  Wikipedia (model names the article, picks from top 5 titles, summary) -> Grokipedia (same title,
  first ~1500 chars) -> web search. Moves on only when the model says "The sources don't say."
  The app uses it when search is on and `sources.needs_lookup` says yes: model yes/no >= 0.60 (`LOOKUP_THRESHOLD`) AND none of best_strat's special routes claims the question (letters, comparison with amounts, math with the question's own numbers, sense check); otherwise best_strat answers. Stage 0 of the chain is memory (the user's own earlier messages only). `python eval.py auto` / `search_eval.py auto` test that (69.8/78, 10.0/18; sources.md section 12). Best measured: v13 (need line + votes at both stages; 15.6/18 avg, eval auto 74.0; v10 15.0). The app's sources.py = v13 + the natural not-found reply (v15), "I couldn't find" instead of answering from no evidence (v17: 14.0/18) and the dim guess line (v18). Its frozen copy (best_sources.py, v13) was deleted on 2026-10-10.
  Test: `python search_eval.py sources`. Wikipedia rate-limits: keep
  requests paced (built in) and tests small.
- `benchmark/` — public benchmarks (see its README): IFBench (`run_ifbench.py raw|harness`: 14.3% vs 14.7%
  prompt-level loose), GPQA diamond (`run_gpqa.py`: raw 30.3%, v15 20.7%, v16 26.8%, v17 37.9%), hard and control sets of 30 (`... strat2 hard|control`;
  `small_test.py` runs all four and lists every wrong answer with its input and why, full texts in
  `benchmark/results/small_test.txt`; the runners log each answer's route, attempts, stop and fallback, shown
  under every wrong answer; `benchmark/small_test_reference.json` holds the reference and the group sizes
  for the estimate, rewritten when the sets are rebuilt). Full runs at temperature 0 without the prompt cache
  (2026-10-12): base model no harness GPQA 69/198, IFBench 49/300; v17 68 / 53; strat2 v20b 68 and 71 / 50 -
  level within noise. The old raw baselines (GPQA 30.3%, IFBench 14.3%) were sampled at the server's default 0.8
  with the cache on (raw paths sent no temperature until 2026-10-12); v17's 37.9% / 18.0% had the cache on.
  harness = best_strat without search. Run the IFBench scorer
  with PYTHONUTF8=1 (the runner does). Keep GPQA questions local (its authors ask that).

## What the harness does (harness.py)
1. **Generation limits** — streams tokens and stops itself (never sends `max_tokens`): on EOS
   (`finish_reason: "stop"`), at the first sentence end after `SOFT_LIMIT` (50), always at
   `HARD_LIMIT` (100).
2. **Stop sequences** — cut at `"\n"` (ignoring leading whitespace): stops the model writing
   the next `User:` line or drifting into quizzes.
3. **Prompt format** — a `User: / AI:` transcript ending in `AI:`, with the last
   `MEMORY_SIZE` (10, was 5 until 2026-10-12) accepted exchanges from `memory.json`. `/clear` empties memory.
   **Memory summary** (2026-10-12, user's design): memory.json holds up to 15 exchanges; when full, the model
   folds the oldest 5 into a summary of at most 200 tokens (`compact_memory`): (1) one fact per user message, shown
   the exchange before ("You ..." or "Nothing."), plus one "We talked about ..." topics line; a sentence must be
   grounded in what it summarises (the model copies prompt samples), and a fact whose every word is already in
   another is left out; (2) if too long, the model shortens sentences one at a time, longest first (kept only if
   nothing is lost); (3) code drops whole sentences: one of a similar pair (the topic one, else the shorter), then
   topics, then the oldest. `prompt_history` puts it in front as one more exchange ("What do you remember from our
   earlier conversation?"), one sentence per line (a trace: 12/16 vs 10/16 as one block), so every route shows it. Test: `python memory_test.py jack|long|heldout|fresh`
   (scripted conversations, ~25 s each; plan.md has every step). Latest: fresh 14/17, heldout 13/17 - all four have
   been tuned on by now (fresh least). Left: facts in the summary the answer still ignores ("beekeeper" -> hobby
   "read and write"), facts the notes miss, and a changed fact next to the old one (Bristol/Leeds). Tried and failed: FAILED_EXPERIMENTS.md "Memory summary".
4. **Checks on each reply**, in order, each rejecting the attempt:
   - empty reply (no word characters),
   - echo — the reply adds no new word to the user's message (filler words aside; yes/no exempt) and
     repeats at least half of its words (so picking one of several options isn't an echo),
   - repetition — any 4-word phrase twice, or a list that keeps repeating its items (4+ items, at most
     half different), checked while streaming (kills generation),
   - language switch — CJK characters or French (function-word count) when the prompt has none,
   - near-copy (2026-10-12) — a reply of 5+ words >= 0.8 similar to an earlier AI reply to a different message
     (only where the conversation is passed: strat2); strat2's retries then leave out the copied exchanges. Against
     the 2026-10-10 snowball ("It's not clear what you are referring to..." x4): `memory_test.py poison`, open
     requests that gave up 3/4 -> 1/4. A memory gate (yes/no "needs the earlier conversation?") was worse: off.
   - validator — same model, temperature 0, 1 token: "is the reply related to the user's
     message (not whether it is correct)? A polite I-don't-know counts as related." strat2 also shows it
     the recent conversation, so "Jaakko." to "What is my name?" isn't judged unrelated.
5. **Retry loop** — up to `MAX_ATTEMPTS` (3). The next attempt gets a `Note:` line saying why
   the previous reply failed (the repetition note quotes the repeated phrase - without the quote a retry
   copied a sample, 2026-10-10; a looping list gets "every item in the list is different").
   If all fail, the app shows the last non-empty rejected reply (`harness.last_rejected`; a reply stopped
   by the repetition check is cut back to before the loop), marked
   "unchecked" with the reason, and does NOT save it to memory (rejected echoes once poisoned it).
6. **Logging** — one JSON line per attempt in `logs/YYYY-MM-DD.jsonl`.
