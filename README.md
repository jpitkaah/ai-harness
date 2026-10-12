# AI Harness

## Lyhyesti mallista

Harness on rakennettu Qwen2.5-3B-mallin **base**-version ympärille (julkaistu 2024). Base-mallia ei ole koulutettu keskustelemaan: se vain jatkaa tekstiä. Pienenä (3 miljardia parametria) ja ilman chat-koulutusta se on heikko lähtökohta: ilman harnessia se toistaa itseään, toistaa käyttäjän sanoja ja keksii vastauksia. Valitsin mallin juuri siksi: jos jokin malli hyötyy harnessista, niin se on tämä.

Harnessin avulla mallista tuli melko luotettava chat-ohjelma. Kysymyksillä, joihin harnessia ei ole viritetty, tulos nousee 18/26:sta 22/26:een, eniten ohjeiden noudattamisessa ja muistissa. Julkisissa testeissä (GPQA, IFBench) se on samalla tasolla kuin pelkkä malli: harness ei voi lisätä tietoa, jota mallilla ei ole.

## Lyhyesti arkkitehtuurista

Jokainen viesti kulkee näiden vaiheiden läpi:

1. **Muisti.** Malli näkee 10 viimeisintä viestiparia (20 viestiä). Vanhemmat viestit malli tiivistää itse lyhyeksi yhteenvedoksi (enintään 200 tokenia): yksi lyhyt fakta per käyttäjän viesti. Kun tila loppuu, ensin lyhennetään ja sitten pudotetaan vähiten tärkeät lauseet. Muistiin tallennetaan vain tarkistukset läpäisseet vastaukset, eikä vastaus saa kopioida aiempaa vastausta (suoja muistin myrkyttymistä vastaan). Kerrosta, joka päättäisi käytetäänkö muistia ollenkaan, kokeiltiin: se huononsi tuloksia, joten se on pois päältä.
2. **Tarvitaanko hakua?** Malli vastaa kyllä/ei-kysymykseen, ja vastaus luetaan todennäköisyytenä (token-luottamus). Esimerkiksi "Who is the current president of Finland?" saa 0,62, joten se haetaan: mallin oma tieto on vanhentunutta. Haku etenee järjestyksessä: oma muisti -> sää (wttr.in) -> Wikipedia ja Grokipedia -> verkkohaku (1-5 sivua). Jokaisesta lähteestä poimitaan vain kysymykseen vastaava kohta. Jokainen lähde saa yhden äänen, ja myös mallin oma vastaus (jota ei näytetä käyttäjälle, esim. "Sauli Niinistö") saa yhden. Vähintään kaksi samaa sanovaa ääntä voittaa.
3. **Reititys.** Jos hakua ei tarvita, malli valitsee kyllä/ei-kysymyksillä reitin: pitkä teksti, kirjainten laskeminen, vertailu, laskutoimitus tai muu. Vaikea kysymys pilkotaan pieniin erillisiin vaiheisiin, joista jokainen näkee vain tarvitsemansa. Esimerkiksi "kilo höyheniä vai kilo terästä?" -kysymyksessä verrataan pelkkiä määriä, jottei tuttu arvoitus johda harhaan. Tekniikan nimi on reititys ja tehtävän pilkkominen (prompt chaining).
4. **Vastaus ja tarkistukset.** Vastaus pysäytetään lauseen loppuun noin 50 tokenin jälkeen ja viimeistään 100 tokenissa (pitkät tekstit enintään 1500). Jokainen vastaus tarkistetaan: ei tyhjä, ei pelkkä käyttäjän sanojen toisto, ei silmukkaa, ei kielen vaihtoa, ei aiemman vastauksen kopio, ja mallin oma kyllä/ei: liittyykö vastaus viestiin. Hylätty vastaus generoidaan uudelleen (enintään 3 yritystä), ja uusi yritys saa huomautuksen siitä, mikä edellisessä oli vialla. Raaka base-malli jää helposti toistamaan itseään, joten tämä on harnessin tärkein osa.

## In English

An educational project: building a harness around a small local language model, one
brick at a time. The model is Qwen2.5-3B, the **base** version (it only continues text,
it was never trained to chat). The harness is plain Python (standard library only, plus
trafilatura for web search) that talks to a local llama.cpp server.

The question the project asks: how much better can a harness make a weak base model?
Short answer so far, measured on questions nobody tuned on:

- **Chat skills** (facts, short instructions, memory, trick questions): from **18/26** for
  the bare model to **22/26** with the harness. Instructions go 1 -> 3 of 4, memory 1 -> 3 of 4.
- **Public benchmarks** (GPQA, IFBench): level with the bare model, within noise. The harness
  can't add knowledge or writing skill the model doesn't have.

With a chain of information sources (memory, Wikipedia, Grokipedia, the web) that the model
consults when it decides it needs to, it also answers questions it couldn't know. See
[the results](#results).

---

## 1. Setup

You need **Python 3** and **llama.cpp**. The commands below are for Windows (PowerShell);
the install script also has a version for macOS/Linux.

### Install llama.cpp

```powershell
# PowerShell (Windows)
irm https://llama.app/install.ps1 | iex
```

```bash
# curl (macOS / Linux)
curl -LsSf https://llama.app/install.sh | sh
```

This project was built with llama.cpp version 0.6.0-dev (build 11429), which has the
`download`, `serve` and `cli` commands used below. Check yours with `llama --version`.

### Download the model

```powershell
llama download -hf QuantFactory/Qwen2.5-3B-GGUF:Q4_K_M
```

### Run the server on port 8090

```powershell
llama serve --port 8090
```

Leave it running in its own terminal. It loads the model the first time it is asked for it.
Check that it's up:

```powershell
curl http://127.0.0.1:8090/health      # -> {"status":"ok"}
```

> **Use `127.0.0.1`, not `localhost`.** On Windows, `localhost` first tries IPv6, waits
> about 2 seconds, and only then falls back to `127.0.0.1` - on every request. That made
> each model call 2.07 s instead of 0.02 s. The code already uses `127.0.0.1`.

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

The settings switch on SearXNG's JSON output, which the code needs, enable extra engines,
and rest a blocked engine for an hour or a day before trying it again. Each search asks
**one** engine, in turn (`search.ENGINES`: Yahoo, Yandex, Yep, Seznam): SearXNG normally
asks all of them at once, and that got all of them blocked together. Check it works: open
<http://127.0.0.1:8888> in a browser. If `docker start searxng` fails with "failed to
connect to the docker API", Docker Desktop isn't running: start it first.

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
app shows which source answered and its URLs; if not, the best strategy answers as usual.
`/search` turns search off and on again. `/clear` empties the memory; an empty line or Ctrl+C
quits (Ctrl+C while it works cancels that answer).

**Memory:** the app remembers the last **10 exchanges** in `memory.json`, plus a short
summary of everything older that the model writes itself (at most 200 tokens). Whenever the
summary changes, the app prints it as a dim `memory summary:` line, so you can see what it
kept. Only accepted replies are saved: if every attempt fails the checks, the app still shows
the last one, marked "unchecked" with the reason, but doesn't save it. Looked-up answers are
not saved either.

The app answers with the best strategy (**v20b**, `best_strat.py`). Every message is logged
to `logs/YYYY-MM-DD.jsonl`; `python stats.py` summarises the logs.

**Troubleshooting one answer:** every answer gets a short id, printed under it (`id 9007ad`).
`python view.py 9007ad` opens a page in your browser with everything behind that answer:
the route, the source chain (texts, evidence, the vote), memory, and every model call in
order with its full prompt and output. `python view.py` lists the recent ids. The traces
are saved in `traces/`. To chat with the plain harness (base A) instead,
set `USE_BEST_STRATEGY = False` at the bottom of `harness.py`.

### Test 1: our own eval (`eval.py`)

26 fixed questions in five categories (facts, math, trick questions, format instructions,
memory), each asked once at temperature 0, so every run gives the same answers. An answer
counts as correct if it contains an expected keyword, stays within a word limit where the
question asks for one, and doesn't contain a phrase that makes it wrong ("steel is heavier"
for the kilogram riddle). The model never grades itself.

There are two question sets. The original set was used while building the strategy, and an
audit found it **fitted**: several routes and samples were built around its questions. The
**held-out set** (`heldout`) has 26 new questions of the same kinds that nobody tuned on;
judge changes on that one.

```powershell
python eval.py best heldout   # the best strategy (best_strat.py) on the held-out set
python eval.py strat2 heldout # the strategy being worked on (strat2.py)
python eval.py baseraw heldout # the base model, no harness at all
python eval.py base heldout   # base A: base model + plain harness (every config also runs without "heldout")
python eval.py auto           # the app's default: the best strategy, or the source chain on a look-up
```

### Test 2: memory (`memory_test.py`)

Scripted conversations played into an empty memory (the replies are written in the script,
so only the memory is tested). Memory compacts after the 15th exchange and then every 5;
each compaction is printed step by step. Then the strategy answers questions whose answers
are by then only in the summary.

```powershell
python memory_test.py heldout   # 40 exchanges, never tuned on
python memory_test.py fresh     # 40 exchanges, another untuned one
python memory_test.py poison    # a memory full of one bad, repeated reply: do new answers copy it?
```

### Test 3: looking things up (`search_eval.py`)

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

> **Keep search tests small.** About 100 searches in an hour got SearXNG's engines
> blocked (rate limits, CAPTCHAs: 0 results), and Wikipedia answers "too many requests"
> after ~5 quick requests (the code paces itself to one request per second per site).

### Test 4: public benchmarks (`benchmark/`)

IFBench (300 writing tasks with precise instructions) and GPQA diamond (198 graduate-level
science multiple-choice questions). Setting them up (cloning IFBench, getting GPQA) and the
commands are in `benchmark/README.md`; the data and results stay out of git.

For quick tests each benchmark has two sets of 30 questions, picked from two full runs:
a **hard set** (wrong in both runs, which shows fixes) and a **control set** (right in both,
which shows regressions). Unchanged code scores hard 0 and control 30. The small test runs
them on `strat2.py`, then lists every wrong answer with its input, the answer, how it was
made and why it was scored wrong, and ends with fixed / broke counts and a rough estimate of
a full run.

```powershell
python small_test.py            # both benchmarks, ~3 min (instead of 10-20 per full run)
python small_test.py gpqa       # GPQA only, ~1 min
python small_test.py ifbench    # IFBench only, ~2 min
```

### Results

All at temperature 0 without llama.cpp's prompt cache (with it, answers depended on the
request before), on the base model Qwen2.5-3B (Q4_K_M).

**Our held-out eval** (26 questions nobody tuned on):

| Setup | Score | Facts (8) | Math (6) | Trick (4) | Instructions (4) | Memory (4) |
|---|---|---|---|---|---|---|
| Base model, no harness | 18 / 26 | 7 | **6** | 3 | 1 | 1 |
| Base A (plain harness) | 18 / 26 | | | | | |
| **v20b** (best strategy) | **22 / 26** | **8** | 5 | 3 | **3** | **3** |

On the original, fitted set the best strategy scored 74 of 78 (each question asked three
times, about 25/26): the gap to 22 is what fitting to those questions was worth.

**Public benchmarks:**

| | GPQA diamond (guessing = 25%) | IFBench (prompt-level loose) |
|---|---|---|
| Base model, no harness | 69 / 198 = 34.8% | 49 / 300 = 16.3% |
| v17 (previous best) | 68 = 34.3% | 53 = 17.7% |
| **v20b** | **68 and 71 = 34.3-35.9%** (two runs) | **50 and 55 = 16.7-18.3%** (two runs) |

One run varies by about ±3 questions on GPQA and ±2-3 points on IFBench, so these are level.
On GPQA the harness only wraps the question in a different prompt: both versions answer at
once ("The correct answer is (C) ...") and pick the same letter on 157 of 198. On IFBench
the harness wins where its first attempt passes the checks (33 vs 23 of 140 prompts) and
loses where it has to retry (22 vs 26 of 160): a second try is rarely better than the bare
model's first.

**Memory** (scripted 40-exchange conversations, facts only in the summary by then): 13/17
and 14/17; without a summary 1/17 (only the question about the last 10 exchanges). With a
memory full of one repeated bad reply, open requests that gave up: 3/4 -> 1/4 since the
copy protection.

Older numbers in the history (`plan.md`) were often higher: until 2026-10-12 the no-harness
baselines were sampled at temperature 0.8 with the prompt cache on (GPQA 30.3%, IFBench
14.3%), the best strategy ran with the cache on (GPQA 37.9%), and the eval was the fitted
set. Measured fairly, those gaps went away.

Ideas that were tried and made things worse are listed in `FAILED_EXPERIMENTS.md`
(for GPQA, for example: writing out its working first, and reading the letter with the
choices rotated); the full history of the strategy trials is in `plan.md`.

---

## 3. Architecture

### Base A: the plain harness (`harness.py`)

The model only continues text, so the harness writes a document for it to continue:
a `User: ... / AI: ...` transcript (with the memory) that ends in `AI:`. Around that it adds:

- **Generation limits** - the harness streams tokens and stops the model itself: at the
  end-of-text token, at the first sentence end after 50 tokens, always at 100, and at a
  newline (so the model can't write the next `User:` line or drift into a quiz).
- **Checks on every reply** - rejected if it is empty, only echoes the user's own words,
  repeats a 4-word phrase or a list item, switches to Chinese/Japanese/Korean or French, or
  if the model itself (asked one yes/no question) says the reply isn't related to the
  message. The best strategy also shows that question the conversation (so "Jaakko." to
  "What is my name?" counts as related) and rejects a reply that nearly copies an earlier
  reply to a different message.
- **Retries** - up to 3 attempts; each retry gets a note saying why the previous reply
  was rejected (a repeated phrase is quoted). In the best strategy, after a near-copy the
  retry no longer sees the exchanges it copied. If all 3 fail, the app shows the last one,
  marked "unchecked".

### Memory

Every prompt shows the last 10 exchanges. Older ones are folded into a summary by the model
itself: memory holds up to 15 exchanges, and when it is full the oldest 5 are compacted.

1. **Facts:** for each of your messages (shown the exchange before it, so "Mochi" after
   "What did you name it?" makes sense) the model writes what it tells about you, in one short
   sentence ("Your hamster is called Mochi.") or "Nothing.". Only your words count as facts: a
   wrong AI reply would otherwise stay in the summary for good. Plus one line naming the topics.
   A sentence is kept only if its words come from the exchanges (the model sometimes copied
   its own prompt's samples), and not if another fact already says all of it.
2. **Shortening:** if the summary is over 200 tokens, the model shortens its sentences, the
   longest first, one at a time; a shorter sentence is kept only if no name or number is lost.
3. **Dropping:** if it is still too long, code drops whole sentences: one of two similar ones
   (the topic line, else the shorter), then topic lines, then the oldest.

The summary goes in front of the conversation as one more exchange ("What do you remember
from our earlier conversation?"), one sentence per line, so every route sees it.

### v20b: the best strategy (`best_strat.py`)

v20b keeps base A's limits, checks and retries, and changes how each question is
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

**Routing.** For each question, the model answers up to four yes/no questions,
read as probabilities (a route is taken at 75% or more):

| Route | Model's question | What happens |
|---|---|---|
| 0. Long text | "Does it ask for a longer text: paragraphs, a list, an email, an essay?" (85% or more) | A writer's "Request: / Response:" prompt that may span several lines (stops at the next "Request:", up to 1500 tokens; the repetition check looks at 10-word phrases instead of 4). After a reply that loops, the retries use llama.cpp's DRY sampler, which makes repeating a phrase less likely. |
| 1. Letters | "Is it about the letters of a word?" | The model copies the word and the letter, splits the word into short parts, spells each part, counts per part (each a separate small call), then adds up the counts in its answer. Long words can't be spelled in one go ("s-traw-b-e-r-r-y"); short ones can. |
| 2. Comparison | "Does it compare two things?" | The model copies the amounts exactly as written, then a separate yes/no compares *only the amounts* ("a kilogram \| a kilogram" -> same). If both amounts are given, it answers the question with the things' names removed ("Which is heavier, a kilogram or a kilogram?") plus that verdict - so a famous riddle can't override the facts. |
| 3. Math | "Does it need a calculation?" | The model rewrites the question as a bare calculation in whole units ("3 pm + 2 hours 30 minutes") and answers in a math-teacher answer key. If the rewrite contains no arithmetic sign, it wasn't really a calculation and the question moves on. |
| 4. Everything else | - | **Sense check:** the model copies what the question asks about and defines it, in the question's meaning ("the survivors: people who are still alive"). A yes/no asks whether the question makes sense; if the model says no (below 45%), it writes why ("survivors are still alive, and cannot be buried") and the answer uses that reason. Otherwise it answers in the plain answer-key format. |

The small steps run at temperature 0, so the same question always gets the same
routing and rewrites. Final answers run at temperature 0 in the tests and at 0.8 in the
app, for a little variety.

**Cost:** a normal question takes about 8 model calls instead of base A's 2 (the routing
checks, 3 sense-check steps, the answer and the relevance check). Most are short.

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
question -> memory: your own earlier messages and the memory summary (same evidence step as any source)
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

**Can public sources know this?** Questions only the asker can answer ("What did I
have for breakfast?") used to reach the web, find a real page ("Moose the neighbor cat")
and get answered from it. Now the model first says yes or no; read as a probability,
personal questions scored 0.03-0.18 and public ones 0.61-1.00. Below 0.25 nothing is
looked up, and the answer step, given no sources, says "The sources don't say."

**The evidence step** (the biggest gain): before answering, one small call per source
copies the words that answer the question, or writes NONE. The answer is then written from
those evidence lines only, not the whole text. Reading a whole summary, the model took a
prominent *related* fact ("Germany, the host country" for who won Euro 2024). Python checks
only the form of the evidence: at least 80% of its words must appear in the source (so it
can't be invented), and it must not be copied from the prompt's own samples.

**Evidence from both encyclopedias:** Wikipedia's short summary sometimes has only a
related line while Grokipedia has the answer (or the other way round). Seeing both, the
answer step can pick the line that actually answers the question.

**A vote:** the model's own answer (no sources) and one answer per encyclopedia each
count as a vote, and the model judges which votes say the same thing. Two or more agreeing
votes win; with no majority, web pages add votes up to 5. Fixed "What gas do plants absorb?":
Wikipedia's evidence said "Oxygen", but the model ("Carbon dioxide.") and Grokipedia ("CO₂")
outvoted it.

**What has to be found** (a thinking step): before any look-up the model writes the
need in a few words. Traced with `view.py`: "how far away is oslo from stockholm?" looked up
the article "Oslo" and train-booking pages, and every vote was invented ("812 miles.").
With the need as the search phrase it found distance pages: "416 kilometers." The need
also makes the evidence step stricter (an article about Oslo now gives NONE).

**A vote at the web stage too:** that stricter evidence sent the myths to the web, where
one answer from all pages' evidence let junk drown a perfect line (Einstein: "The sources
don't say." although the top page said it's a myth). Now each page votes, like the
encyclopedias. Search test 15.0 -> 15.6/18, myths 3/9 -> 13/15.

Python only calls the services, trims the text and moves down the chain; the model
decides what to look up and writes every answer. Every looked-up answer's log line holds the whole
chain (titles, texts, evidence, answers), so a wrong answer can be traced to its source.

**Memory first**: your own earlier messages (and the memory summary) are the chain's first
source, with the same evidence step ("Which city do I live in?" -> Helsinki). Only your words,
not the AI's past replies: a saved wrong answer ("Sauli Niinistö is the president") would
otherwise be read back as evidence and stop every later look-up.

**When does the app look things up?** The model answers "Needs a look-up?" (yes at 0.60
or more; at 0.75 it skipped "Who is the president of Finland?", 0.62, and answered from
outdated knowledge). Then the best strategy's own routes get the first claim: letters,
comparisons whose amounts are in the question, math using the question's own numbers, and
the sense check's trick questions never need a look-up, and the chain doesn't have those
routes (feathers-vs-steel failed 0/3 through it).

Search test results (18 questions): the first chain 11.7/18, **best 15.6/18** with the
thinking step and votes at both stages (first 18/18 run). Known misses: the model's own wrong
belief winning the vote together with one source ("Jupiter" has the most moons), and popular
wrong pages outvoting a right source: for "Who won the 2024 Tour de France?" Grokipedia said
Pogačar, but the 2023 Tour page, a pre-race preview and a results table read off the second
place gave three "Vingegaard" votes.

**Questions only you can answer** ("What is my sister's name?") also go to the chain: memory
first, then, if your messages don't tell, the answer with no sources. The best strategy
invented these (0/18 honest: "My sister's name is Mary"); now 15/18. The honest reply reads
"The sources don't say." inside the chain; the app says it naturally instead ("I don't
know your sister's name; you haven't told me.", "I couldn't find a reliable source for how many
hairs the average cat has."). When no source gives any evidence, it says it couldn't find it
instead of answering from nothing (that invented: "3500 hairs"), and the app shows the model's
own answer underneath, dimmed and marked "my guess, not from a source".
