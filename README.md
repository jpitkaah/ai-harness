# AI Harness

An educational project: building a harness around a small local language model, one
brick at a time. The model is Qwen2.5-3B, the **base** version (it only continues text,
it was never trained to chat). The harness is plain Python (standard library only) that
talks to a local llama.cpp server.

The question the project asks: how much better can a harness make a weak base model?
Short answer so far: from about 58/78 to 74/78 on our test, a little above the instruct
version of the same model (73/78) — see [the results](#results). With a chain of
information sources (memory, Wikipedia, Grokipedia, the web) that the model consults when
it decides it needs to, it also answers questions it couldn't know (the app's default
setup scores about 72-74/78 on the same test).

---

## 1. Setup

You need **Python 3** and **llama.cpp**. Everything below is for Windows (PowerShell).

### Install llama.cpp

Install llama.cpp from its releases page: <https://github.com/ggml-org/llama.cpp/releases>.
This project was built with `llama.exe` version 0.6.0-dev (build 11429), which has the
`download`, `serve` and `cli` commands used below. Check yours with:

```powershell
llama.exe --version
```

### Download the two models

```powershell
# The base model - the one the harness is built around.
llama.exe download -hf QuantFactory/Qwen2.5-3B-GGUF:Q4_K_M

# The instruct (chat) model - only used as a reference in the tests.
llama.exe download -hf Qwen/Qwen2.5-3B-Instruct-GGUF:Q4_K_M
```

### Run the server on port 8090

```powershell
llama.exe serve --port 8090
```

Leave it running in its own terminal. It serves every downloaded model and loads one
the first time it is asked for. Check that it's up:

```powershell
curl http://127.0.0.1:8090/health      # -> {"status":"ok"}
```

### For web search (optional): trafilatura and SearXNG

Search (on by default in the app) needs two more things. Wikipedia, Grokipedia and wttr.in
need no setup, but SearXNG must be running: without it, a question that reaches the web
stage shows "Look-up failed ... Is SearXNG running?" instead of an answer.

```powershell
# trafilatura: downloads a web page and extracts its main text.
pip install -r requirements.txt

# SearXNG's settings: copy the template and put any long random string in secret_key
# (searxng/settings.yml itself is not in git, because it holds that key).
copy searxng\settings.example.yml searxng\settings.yml

# SearXNG: a local meta search engine, run in Docker (Docker Desktop must be running).
# ONE-TIME setup: this creates the container. Afterwards start it with `docker start searxng`
# (running this line again fails: "The container name "/searxng" is already in use").
# Port 8888 on this machine only (127.0.0.1: not reachable from your network, since the
# limiter is off); 8080 is the container's own internal port.
docker run -d --name searxng -p 127.0.0.1:8888:8080 -v "${PWD}\searxng:/etc/searxng" searxng/searxng
```

`searxng/settings.yml` (in this project) switches on SearXNG's JSON output, which the
code needs, enables extra engines, and rests a blocked engine for an hour or a day before
trying it again. Each search asks **one** engine, in turn (`search.ENGINES`: Yahoo, Yandex,
Yep, Seznam): SearXNG normally asks all of them at once, and that got all of them blocked
together. Check it works: open <http://127.0.0.1:8888> in a browser. After a restart,
start it again with `docker start searxng`.

> **Use `127.0.0.1`, not `localhost`.** On Windows, `localhost` first tries IPv6, waits
> about 2 seconds, and only then falls back to `127.0.0.1` - on every request. That made
> each model call 2.07 s instead of 0.02 s. The code already uses `127.0.0.1`.

---

## 2. Running it

All commands run in the project folder.

### The chat app (the harness)

```powershell
python harness.py
```

Type a message and press Enter. While it works, a spinner shows the current step
("deciding whether to look it up", "looking it up", "thinking").

**Search is on by default** (shown in the prompt: `You [search on] ›`). For each message the
model first decides whether it needs a look-up (recent events, facts that change, like who
holds an office now); if yes, the [source chain](#the-source-chain-sourcespy) answers and the
app shows which source answered and its URLs; if not, the best strategy answers as usual. `/search` turns search off and on again. Looked-up
answers are not saved to memory. `/clear` empties the memory; an empty line or Ctrl+C quits
(Ctrl+C while it works cancels that answer). If every attempt fails the checks, the app
still shows the last one, marked "unchecked" with the reason, and doesn't save it to memory.
The app answers with the best strategy (v20b, `best_strat.py`, since 2026-10-12; v17 before) and remembers
the last 10 exchanges in `memory.json`, plus a short summary of everything older that the model writes itself
(at most 200 tokens; printed as a dim line whenever it changes; `python memory_test.py` tests it). Every message is logged to `logs/YYYY-MM-DD.jsonl`;
`python stats.py` summarises the logs.

**Troubleshooting one answer:** every answer gets a short id, printed under it (`id 9007ad`).
`python view.py 9007ad` opens a page in your browser with everything behind that answer:
the route, the source chain (texts, evidence, the vote), memory, and every model call in
order with its full prompt and output. `python view.py` lists the recent ids. The traces
are saved in `traces/`. To chat with the plain harness (base A) instead,
set `USE_BEST_STRATEGY = False` at the bottom of `harness.py`.

### Test 1: the full test (`eval.py`)

26 fixed questions in five categories (facts, math, trick questions, format
instructions, memory), each asked 3 times = **78 answers**. An answer counts as correct
if it contains an expected keyword (and stays within a word limit where the question
asks for one). The model never grades itself.

```powershell
python eval.py            # base A: base model + plain harness
python eval.py best       # v20b: base model + the best strategy (best_strat.py)
python eval.py baseraw    # base model, no harness at all
python eval.py raw        # instruct model, no harness at all
python eval.py both       # base A + instruct raw
python eval.py auto       # the app's default: v20b, or the source chain when the model says it needs a look-up
```

### Test 2: looking things up (`search_eval.py`)

18 questions, 1 try each: 12 facts (recent ones the model can't know, and ones it gets
wrong), 3 questions nothing can answer (the model should say "The sources don't say."), and
3 popular myths (it shouldn't state the myth as fact). Measures correctness, whether the
answer is backed by the sources, abstaining, which source answered, and how many SearXNG
searches the run needed. Each report is saved in `results/search_*.txt`.

```powershell
python search_eval.py sources       # the source chain (sources.py), as the app uses it
python search_eval.py search        # web search only (search.py)
python search_eval.py nosearch      # no lookup at all (best_strat.py)
python search_eval.py auto          # the app's default: the model decides whether to look it up
```

Web lookups (Wikipedia, Grokipedia, SearXNG, pages) are cached on disk for a day
(`cache/`), so every version is compared on the same texts and the sites aren't asked
again. Delete `cache/` to start fresh.

Results:
- **Web search only** (the earlier 8-question version, 3 tries, averages over 3 runs):
  6.7/24 without search, 16.0/24 with it.
- **Source chain** (18 questions, averages over 3-8 runs): first version 11.7/18, v5
  13.0/18 - but only 12.2 once web search worked properly again, because questions nothing
  can answer then found real pages and the model answered from them. v8 (13.5/18) asks
  first whether public sources can know the answer at all: those questions went from 2/15
  to 14/15 right. Cutting pages at a sentence end and dropping search snippets' dates:
  14.2/18. v10 (15.0/18) adds a vote. **Best (v13) 15.6/18** adds a thinking step (what
  exactly has to be found) and a vote at the web stage too, and got the first 18/18 (see
  [the source chain](#the-source-chain-sourcespy)). The remaining misses: the model's own
  wrong belief ("Jupiter" has the most moons) and popular wrong pages outvoting a right
  source (Tour de France 2024). Details in `sources.md`.

> **Keep search tests small.** About 100 searches in an hour got SearXNG's engines
> blocked (rate limits, CAPTCHAs: 0 results), and Wikipedia answers "too many requests"
> after ~5 quick requests (the code paces itself to one request per second per site).

### Test 3: public benchmarks (`benchmark/`)

Two public benchmarks, base model with and without the harness (details and commands in
`benchmark/README.md`):

| Benchmark | Base model, no harness | Harness v15 | Harness v16 (long-text route) | **Harness v17** (temperature 0) |
|---|---|---|---|---|
| IFBench (300 writing tasks with precise instructions; prompt-level loose) | 14.3% | 14.7% (limits 4000: 16.7%) | 19.3% | **18.0%** |
| GPQA diamond (198 science multiple-choice questions; guessing = 25%) | 30.3% | 20.7% | 26.8% | **37.9%** |

**Redone on 2026-10-12, everything at temperature 0 without the prompt cache:** base model, no harness: GPQA
34.8%, IFBench 16.3%; v17: 34.3%, 17.7%; v20b (the current best): 34.3-35.9%, 16.7%. The harness is level with the
bare model on these benchmarks - the numbers above compare a sampled raw model (the server's default 0.8, cache on)
with v17 at temperature 0 with the cache. Details: `benchmark/README.md`.

v15 was built for short chat answers, and it showed: on IFBench every answer was one line (it
stops at the first line break), and on GPQA its math and comparison routes answered with a bare value
instead of a letter (48 of 198 replies). v16 adds a long-text route (below); our own eval doesn't
change, since none of its questions take it. v17 answers at temperature 0 in tests: on GPQA the raw
model and v16 were right on mostly *different* questions - with sampled answers the model mostly
guessed, though it does lean towards the right letter. The raw model was sampled too (as it normally
runs); a raw model at temperature 0 would gain from that as well.

For quick tests each benchmark has two sets of 30 questions, picked evenly over its themes from two full runs:
a **hard set** (questions wrong in both runs, which shows fixes) and a **control set** (questions right in both,
which shows regressions). Questions that came out differently in the two runs are left out, so unchanged code
scores hard 0 and control 30. The **small test** runs the sets on the strategy being worked on (`strat2.py`),
then lists every wrong answer with its input, the answer, how it was made and why it was scored wrong (GPQA:
the choice it picked and the right one, plus the question writer's explanation; IFBench: which instructions
its checker found not followed). It ends with fixed / broke counts per set and a rough estimate of a full run.
Full texts are saved in `benchmark/results/small_test.txt`.

```powershell
python small_test.py            # both benchmarks, ~3 min (instead of 10-20 per full run)
python small_test.py gpqa       # GPQA only, ~1 min
python small_test.py ifbench    # IFBench only, ~2 min
```

### Results

Full test, 78 answers per run. Since v17 tests run at **temperature 0**, so every run gives the same
answers and one run is enough. The older rows were sampled (temperature 0.8): single runs swung by up
to ~8 points, so they are averages over several runs (details in `results/2026-10-09_eval-full-averages.txt`).

| Setup | Runs | **Average** | Time per run |
|---|---|---|---|
| Base model, no harness | 55, 56, 55 | **55.3 / 78** | ~81 s |
| Base A (base model + plain harness) | 62, 58, 55 | **58.3 / 78** | ~20 s |
| v15 (base model + best strategy, sampled) | 75, 73, 73, 71, 71 | 72.6 / 78 | ~45 s |
| **v17** (+ long-text route, temperature 0) | 74 (deterministic) | **74 / 78** | ~45 s |
| Instruct model, no harness | 73, 73, 74, 72 | **73.0 / 78** | ~41 s |
| **The app's default** (`eval.py auto`: v15 + look-ups, v13 chain) | 71, 75, 68, 75, 73 | **72.4 / 78** | ~75 s |

The last row counts "CO₂" as a right answer for "What gas do plants absorb?" (added to the
keywords on 2026-10-10). Both rows use answer_key v9 (a chit-chat sentence and sample, 2026-10-10:
before it, v15 averaged 72.0 with give-ups in most runs; now 0). The app row also sends
questions only you can answer to the source chain (memory first); runs swing ±4 there.

What the numbers say:

- The plain harness (base A) adds only ~3 points over the raw base model.
- v15 adds ~15: math was 18/18 in every run where we looked at the categories, and it
  is strong on trick questions, where even the instruct model struggles.
- v15 is now slightly above the instruct model. Most of its lost points used to be
  *give-ups* (every attempt rejected), many on small talk ("good morning" echoed back); one
  chit-chat sentence and sample in its answer key brought them to 0.
- With look-ups (the app's default) it scores about the same (72.4, runs swing ±4): stable
  facts like WWII or the Mona Lisa come from the encyclopedias and the vote fixes "plants
  absorb oxygen", but the look-up path has a little more variation. Its real gain is on
  questions the model can't know (the search test: 10.0 -> 14.5/18 in auto mode).

Ideas that were tried and made things worse are listed in `FAILED_EXPERIMENTS.md`;
the full history of the strategy trials is in `plan.md`.

---

## 3. Architecture

### Base A: the plain harness (`harness.py`)

The model only continues text, so the harness writes a document for it to continue:
a `User: ... / AI: ...` transcript (with the last 5 exchanges as memory) that ends in
`AI:`. Around that it adds:

- **Generation limits** - the harness streams tokens and stops the model itself: at the
  end-of-text token, at the first sentence end after 50 tokens, always at 100, and at a
  newline (so the model can't write the next `User:` line or drift into a quiz).
- **Checks on every reply** - rejected if it is empty, only echoes the user's own words, repeats a 4-word phrase, switches
  to Chinese/Japanese/Korean or French, or if the model itself (asked one yes/no
  question) says the reply isn't related to the user's message.
- **Retries** - up to 3 attempts; each retry gets a note saying why the previous reply
  was rejected. If all 3 fail, the harness gives up.

### v17: the best strategy (`best_strat.py`)

v17 keeps base A's limits, checks and retries, and changes how each question is
answered. Its core idea, found by trial and error: **the model often knows the right
fact but doesn't use it while the familiar story of the question is in view.** So hard
questions are broken into small separate model calls, each seeing only what it needs.

Two rules hold throughout: Python never decides what kind of question it is (the
model does, with yes/no answers), and Python never writes or edits an answer (it only
passes results between steps and checks their *form*).

**Format.** Instead of a chat transcript, every prompt is an exam **answer key**
(`Question: ... / Answer: ...`) with a header describing a good answer and a few sample
entries. A base model has seen far more answer keys than chat logs, and answer keys
answer directly.

**Routing.** For each question, the model answers up to three yes/no questions,
read as probabilities (a route is taken at 75% or more):

| Route | Model's question | What happens |
|---|---|---|
| 1. Letters | "Is it about the letters of a word?" | The model copies the word and the letter, splits the word into short parts, spells each part, counts per part (each a separate small call), then adds up the counts in its answer. Long words can't be spelled in one go ("s-traw-b-e-r-r-y"); short ones can. |
| 2. Comparison | "Does it compare two things?" | The model copies the amounts exactly as written, then a separate yes/no compares *only the amounts* ("a kilogram \| a kilogram" -> same). If both amounts are given, it answers the question with the things' names removed ("Which is heavier, a kilogram or a kilogram?") plus that verdict - so a famous riddle can't override the facts. |
| 3. Math | "Does it need a calculation?" | The model rewrites the question as a bare calculation in whole units ("3 pm + 2 hours 30 minutes") and answers in a math-teacher answer key. If the rewrite contains no arithmetic sign, it wasn't really a calculation and the question moves on. |
| 0. Long text (v16) | "Does it ask for a longer text: paragraphs, a list, an email, an essay?" (85% or more) | A writer's "Request: / Response:" prompt that may span several lines (stops at the next "Request:", up to 1500 tokens; the repetition check looks at 10-word phrases instead of 4). None of our eval questions take it; IFBench writing tasks and GPQA's long multiple-choice questions do. |
| 4. Everything else | - | **Sense check:** the model copies what the question asks about and defines it, in the question's meaning ("the survivors: people who are still alive"). A yes/no asks whether the question makes sense; if the model says no (below 45%), it writes why ("survivors are still alive, and cannot be buried") and the answer uses that reason (a reason copied from the prompt's own samples is rejected). Otherwise it answers in the plain answer-key format. |

The small steps run at temperature 0, so the same question always gets the same
routing and rewrites; only the final answer uses the harness's normal temperature.

**Cost:** a normal question takes about 8 model calls instead of base A's 2 (3 routing
checks, 3 sense-check steps, the answer and the relevance check). Most are short, so a
full test run takes ~50 s instead of base A's ~20 s.

### The web search tool (`search.py`)

The first lookup tool; now the last stage of the source chain (below).

```
question -> model writes a short search phrase   (one small call)
         -> SearXNG returns results               (one engine per search, in turn; top 3 pages)
         -> trafilatura extracts each page's text (~1500 characters, cut at a sentence end)
         -> model answers from those sources      (answer key: "uses only the sources")
```

**Weather questions** take a shortcut: the model decides the question is about the
weather (a yes/no) and copies the place, and Python asks [wttr.in](https://wttr.in) for one
clean line ("Oslo: Light rain +10°C (feels like +10°C)") instead of reading weather pages,
which are mostly menus and forecast tables. If wttr.in fails, the normal web search runs.

Python only runs the tools (search, fetch, trim, wttr.in); the model writes the search
phrase and the answer. If the sources don't contain the answer, the model is shown (by a sample) to
say "The sources don't say." Only the generic checks run on search answers (empty,
repetition, language): the relevance validator kept rejecting that honest answer.

Two formatting fixes, found by tracing wrong answers: pages are cut at a sentence end, not
mid-word (a hard cut left "...was proposed in the 20" - the year 2017 cut in half), and
the date a search engine puts in front of a snippet ("May 11, 2026 · ...") is dropped (the
model copied it as an answer). The "Today is ..." line above the question stays on purpose:
removing or moving it made answers worse (`FAILED_EXPERIMENTS.md`).

### The source chain (`sources.py`)

Used by the app when search is on and the model decides a question needs a look-up (see
*When does the app look things up?* below). The lesson from the weather tool: one clean
line beats thousands of characters of web page. So each question goes to the cleanest source first, and the web
is only the last resort. Plan, every version and its results: `sources.md`.

```
question -> memory: your own earlier messages      (same evidence step as any source)
         -> weather question? (model, yes/no) -> wttr.in, one line
         -> can public sources know this? (model, yes/no) -> if not: no lookup, the model
            answers with no sources ("The sources don't say.")
         -> model writes what exactly has to be found ("the distance between Oslo and Stockholm")
            - shown to the article and evidence steps, and used as the web search phrase
         -> model names the article it expects  ("Fall of the Berlin Wall")
         -> Wikipedia search returns the top 5 titles; the model picks one
         -> Wikipedia: that article's summary   (its opening paragraph)
            Grokipedia: the same title          (first ~1500 characters)
         -> evidence step on each text
         -> vote: the model's own answer + one answer per encyclopedia; 2+ agreeing win
            (no majority: web pages add votes up to 5; still none: one answer from all evidence)
         -> if no encyclopedia had evidence, or the answer is "The sources don't say.":
            web search (search.py), with the same evidence step on each page,
            and a vote again: the model's own answer + one answer per page
```

There is no topic router: every public question tries the encyclopedias (two cheap API
calls), and an unhelpful article just leads to "The sources don't say." and the next stage.

**Can public sources know this?** (v8): questions only the asker can answer ("What did I
have for breakfast?") used to reach the web, find a real page ("Moose the neighbor cat")
and get answered from it. Now the model first says yes or no; read as a probability,
personal questions scored 0.03-0.18 and public ones 0.61-1.00. Below 0.25 nothing is
looked up, and the answer step, given no sources, says "The sources don't say."

**The evidence step** (the v2 idea, the biggest gain): before answering, one small call
per source copies the words that answer the question, or writes NONE. The answer is then
written from those evidence lines only, not the whole text. Reading a whole summary, the
model took a prominent *related* fact ("Germany, the host country" for who won Euro 2024).
Python checks only the form of the evidence: at least 80% of its words must appear in the
source (so it can't be invented), and it must not be copied from the prompt's own samples.

**Evidence from both encyclopedias** (v5): Wikipedia's short summary sometimes has only a
related line while Grokipedia has the answer (or the other way round). Seeing both, the
answer step can pick the line that actually answers the question.

**A vote** (v10): the model's own answer (no sources) and one answer per encyclopedia each
count as a vote, and the model judges which votes say the same thing. Two or more agreeing
votes win; with no majority, web pages add votes up to 5. Fixed "What gas do plants absorb?":
Wikipedia's evidence said "Oxygen", but the model ("Carbon dioxide.") and Grokipedia ("CO₂")
outvoted it. Search test 14.2 -> 15.0/18.

**What has to be found** (v12, a thinking step): before any look-up the model writes the
need in a few words. Traced with `view.py`: "how far away is oslo from stockholm?" looked up
the article "Oslo" and train-booking pages, and every vote was invented ("812 miles.").
With the need as the search phrase it found distance pages: "416 kilometers." The need
also makes the evidence step stricter (an article about Oslo now gives NONE).

**A vote at the web stage too** (v13): that stricter evidence sent the myths to the web,
where one answer from all pages' evidence let junk drown a perfect line (Einstein: "The
sources don't say." although the top page said it's a myth). Now each page votes, like the
encyclopedias. Search test 15.0 -> 15.6/18, myths 3/9 -> 13/15.

Python only calls the services, trims the text and moves down the chain; the model
decides what to look up and writes every answer. Every looked-up answer's log line holds the whole
chain (titles, texts, evidence, answers), so a wrong answer can be traced to its source.

**Memory first**: your own earlier messages are the chain's first source, with the same
evidence step ("Which city do I live in?" -> Helsinki). Only your words, not the AI's past
replies: a saved wrong answer ("Sauli Niinistö is the president") would otherwise be read
back as evidence and stop every later look-up.

**When does the app look things up?** The model answers "Needs a look-up?" (yes at 0.60
or more; at 0.75 it skipped "Who is the president of Finland?", 0.62, and answered from
outdated knowledge). Then the best strategy's own routes get the first claim: letters,
comparisons whose amounts are in the question, math using the question's own numbers, and
the sense check's trick questions never need a look-up, and the chain doesn't have those
routes (feathers-vs-steel failed 0/3 through it).

Known misses of v13: the model's own wrong belief winning the vote together with one source
("Jupiter" has the most moons), and popular wrong pages outvoting a right source: for "Who
won the 2024 Tour de France?" Grokipedia said Pogačar, but the 2023 Tour page, a pre-race
preview and a results table read off the second place gave three "Vingegaard" votes.

**Questions only you can answer** ("What is my sister's name?") also go to the chain: memory
first, then, if your messages don't tell, the answer with no sources. The best strategy
invented these (0/18 honest: "My sister's name is Mary"); now 15/18. The honest reply reads
"The sources don't say." inside the chain; the app says it naturally instead (v15: "I don't
know your sister's name; you haven't told me.", "I couldn't find a reliable source for how many
hairs the average cat has."). When no source gives any evidence, it says it couldn't find it
instead of answering from nothing (that invented: "3500 hairs"), and the app shows the model's
own answer underneath, dimmed and marked "my guess, not from a source".
