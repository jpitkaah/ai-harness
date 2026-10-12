# Plan: a library of information sources

**Goal:** send each question to the source with the *cleanest* data, and only fall back to
general web search when nothing better fits. Lesson behind it (the first search tool): the weather tool
worked because one clean line ("Oslo: Light rain +10°C") beats thousands of characters of web
page. Clean sources also mean fewer SearXNG searches, which got rate-limited.

**Order (as decided):**
```
1. weather question?      -> wttr.in              (exists, in search.py)
2. encyclopedia question? -> Wikipedia            (new)
                          -> Grokipedia           (new)
3. otherwise, or nothing found -> web search via SearXNG (exists)
```

---

## 1. Design: `sources.py`, one list of sources

Each source is one entry with three parts:

| Part | Who | Example (Wikipedia) |
|---|---|---|
| **router** - a yes/no question, read as a probability | the model | "Is this about a topic an encyclopedia covers?" |
| **lookup** - what to fetch | the model (small step, temperature 0) | chooses an article title |
| **fetch** - get clean text, or nothing | Python | Wikipedia API -> the article's opening paragraph |

The pipeline goes down the list. A source is used when its router says yes **and** its fetch
returns text **and** the model's answer isn't "The sources don't say." Otherwise: next source.
Adding a source later = adding one entry. The answer step is the existing one in `search.py`
(answer key, "uses only the sources", generic checks).

Project rules hold: the model decides which kind of question it is and what to look up;
Python only calls the services, trims text and moves to the next source.

`search.py` keeps the SearXNG web search and the answer step; the weather tool moves from
`search.py` into `sources.py` as the first entry. `/search` in the app runs the whole list.

---

## 2. The sources

### Weather: wttr.in (exists)
Unchanged: router (weather questions 0.91-0.98, others <= 0.37), the model copies the place,
`wttr.in/<place, lowercase>?format=...`, tried twice.

### Wikipedia (new)
Free official API, no key; needs a descriptive `User-Agent` (their rule). Tested 2026-10-09:
search 0.5 s, summary 0.2 s.

1. **Search:** the model writes a short topic (like the search phrase today), Python asks the
   Wikipedia search API for the **top 5 article titles**.
2. **Pick:** the model chooses the title that best fits the question, from that list (a small
   copy-one-of-these step). *Needed:* for "Euro 2024 winner" the search's first hit was
   "UEFA Euro 2024 qualifying"; the article naming the winner wasn't in the top 3. Python never
   picks by itself.
3. **Fetch:** the article's **summary** (REST `page/summary/<title>`): its opening paragraph as
   plain text, typically 500-1500 characters, no menus or tables. That's the right size for a
   3B model.
4. If the answer is "The sources don't say." the summary didn't cover it -> next source.

### Grokipedia (new)
An encyclopedia written by xAI's Grok. Tested 2026-10-09: `grokipedia.com/page/<Title>` works
(~1 s), and trafilatura extracts clean text starting with the article's introduction.

- **No search found**, so it reuses the title the model picked in the Wikipedia step
  (`UEFA_Euro_2024` style titles match). If Wikipedia found no title, Grokipedia is skipped.
- **Articles are huge** (~55,000 characters, 800 KB of HTML): trimmed to the first ~1500
  characters, like web pages. A fact deep in the article will be missed - same limit as now.
- **Concern to measure, not assume:** its articles are AI-written and their accuracy is
  debated. The test (section 4) records which source each correct *and* wrong answer came from.

### Web search: SearXNG (exists)
The fallback, unchanged: search phrase -> top 3 pages + snippets -> answer.

---

## 3. When is something "an encyclopedia question"?

**Risk:** "Is this about history, an event, politics or science?" is much fuzzier than "is this
about the weather?". Our routers only worked when the yes/no separated cleanly.

**Plan:** prototype the router on the test questions first (like the weather router).
- If it separates cleanly: use it.
- If not: skip the router and **try Wikipedia for every non-weather question**. It's cheap
  (~1 s, two small API calls), and a "The sources don't say." moves on to the next source anyway.
  Either way, the decision is measured, not assumed.

---

## 4. Testing

`search_eval.py` gets one new option and one new column - no new test file:

- `python search_eval.py sources` runs the questions through the full source list.
- Every answer records **which source answered** (weather / wikipedia / grokipedia / web).
- Same three measures as before: correct, supported by the sources, abstained.
- Also counted: **how many SearXNG searches** a run needed (should drop a lot).
- Compare against the current web-search-only results (16.0/24 average).

The question set gets a few encyclopedia-type questions that show the source chain at work,
e.g. a historical date, a science fact, a recent event that Wikipedia covers (Euro 2024 is
already in). Small runs only (3 tries), averages over a few runs.

---

## 5. Logging
Each `/search` log line gets the **source chain**: which sources were tried, what each one
returned (title, text), and which one the answer came from - so a wrong answer can be traced to
its source.

---

## 6. Risks
- **Wrong article picked** - the model chooses from 5 titles; a wrong choice usually leads to
  "The sources don't say." and the next source, not a wrong answer. The test will show it.
- **Summary too short** - a Wikipedia summary may not contain the specific fact; then Grokipedia
  (longer intro), then web search.
- **Grokipedia accuracy and availability** - measured per source; easy to drop (one list entry).
- **Being a polite client** - descriptive User-Agent, no parallel requests, small test runs.

---

## 7. Order of work
1. `sources.py` with the list structure; move the weather tool in, behaviour unchanged.
2. Wikipedia: topic -> search -> model picks a title -> summary. Prototype the title pick.
3. Grokipedia: page by the picked title, trimmed.
4. Encyclopedia router prototype -> keep it or try Wikipedia always.
5. `/search` runs the list; log the source chain.
6. `search_eval.py sources`: per-source results, SearXNG search count, compare with 16.0/24.

---

## 8. First version: built and measured (2026-10-09)

Built simplest-first: **no encyclopedia router** - every non-weather question tries Wikipedia, then
Grokipedia, then web search (user's call). Each answer records which source it came from; the app's
/search log line holds the whole chain (titles, texts, answers per source).

Found while building:
- **Article choice:** the model first names the article it expects ("Photosynthesis", "UEFA Euro 2024"),
  Wikipedia searches that, the model picks from the top 5 titles. Search-engine phrases ("gas plants
  absorb from air") found only junk titles. A "NONE" option made the model refuse even when the
  perfect article was listed - removed; a bad pick just leads to "The sources don't say."
- **Wikipedia rate limit:** after 5 quick searches it answered HTTP 429 ("too many requests") and the
  chain silently fell through. Now: results cached, >= 1 s between requests per site, Retry-After honoured.

`python search_eval.py sources`, one run: **15/24** (web search only: 16.0 average), **5 SearXNG searches**.
- Myths 6/6 (web only: 9/18 avg) - encyclopedias are good at debunking.
- Recent facts 5/12 (web only: 31/36): the model **misreads the right article** - "Germany, the host
  country" for Euro 2024, "Oxygen." from the photosynthesis summary. Grokipedia found Pogačar when
  Wikipedia's short summary didn't name the winner.
- No-answer questions 4/6, but unrelated articles produced inventions ("I had a banana for breakfast").
  Only "The sources don't say." moves the chain on; an invented answer stops it.

**Next (trial and error):** one small step per source - "copy the words from this source that answer
the question, or NONE" - before answering, so the answer is written from that evidence line only.
Same small-steps idea that fixed strawberry and feathers.

---

## 9. Trial-and-error round (2026-10-09), like strat2 -> best_strat

Setup: `sources.py` is the working copy, `best_sources.py` the frozen best. `search_eval.py` now has
**18 questions, 1 try** (12 helps / 3 no-answer / 3 myths), saves reports to `results/search_*.txt`,
and a **disk cache** (`cache.py`, 1 day) so every version reads the same source texts - without it,
~60 Wikipedia requests per run got rate-limited and late questions failed for reasons unrelated to
the strategy. `python search_eval.py sources` (working) / `bestsources` (saved best).

| Version | Change | Runs (/18) | Avg | Result |
|---|---|---|---|---|
| v1 | the chain as first built | 10, 12, 13 | 11.7 | baseline |
| **v2** | + evidence step: copy the words that answer the question or NONE (Python checks they're really in the source); answer from that line only | 14, 13, 13 (later 12, 12) | **13.3** | **saved as best** |
| v3 | + yes/no "does this evidence answer the question?" | 12, 11, 11 | 11.3 | reverted |
| v4 | + topic sample "name the general topic, not a guessed answer"; pick sample "choose the asked year's article" | 11, 11, 11, 9, 8 | 10.0 | reverted |
| **v5** | v2 + evidence from **both** Wikipedia and Grokipedia, then one answer from all of it; + guard against evidence copied from the prompt's samples | 14, 15, 12, 11, 13 | **13.0** | **saved as best** (v2 over 7 runs: 12.1) |
| v6 | v5 + use the article the model named directly if it exists, skip search + pick | 9, 9, 9, 6, 11 | 8.8 | reverted |
| v7 | v5 + the top web page's evidence beside the encyclopedias' (working search; v5 rerun: 12.3) | 11, 10, 12 | 11.0 | reverted |
| **v8** | v5 + yes/no "can public sources know this?" first; if not, no lookup | 12, 14, 15, 13, 13 | **13.4** | **saved as best** (v5 rerun: 12.2) |
| v9 | v8 + an answer sample where the source is about a different year ("2019 ... Anna Berg" / "2021?" -> don't say) | 13, 13, 12, 9, 14 | 12.2 | reverted (v8 over 8 runs: 13.5) |

v2's remaining misses: evidence copies a related line ("Germany hosted the tournament" for who won;
a lone "Oxygen"); wrong article year (general "Nobel Prize in Physics" -> Grokipedia names the 2026
winner); the model's own wrong belief steers the lookup ("Moons of Jupiter"); a bare article title
as evidence leads to an invented favourite song.

v5's remaining misses (every run): Physics 2024 -> "Francis Halzen" (the 2026 winner: the general
article gets picked); plants -> "Oxygen."; most moons -> "Jupiter" (the model's own wrong belief);
"What did I have for breakfast?" -> an invented breakfast from the generic "Breakfast" article.

### v7 (2026-10-09): + the top web page's evidence, before answering

Before v7, SearXNG was fixed: each search now asks one engine in turn (yahoo, yandex, yep, seznam)
instead of all at once, and the junk web results were cleared from the cache. So best_sources (v5) is
rerun too: its web stage now reads real pages, which may change its 13.0.

**Change:** the evidence from the top web page joins the Wikipedia + Grokipedia evidence, and one
answer is written from all of it. If that answer is "The sources don't say.", the web stage runs as
before (evidence from every page). Every question now makes one SearXNG search (v5: only the ~5 that
fell through).

**Prediction:** Physics 2024 gets fixed when the top page names Hopfield/Hinton (the encyclopedias
picked the general article); Euro 2024 and other recent facts stay right. Risk: a web line that is only
related (or a myth stated as fact) outvotes a correct encyclopedia line, so myths may drop. Expect about
+1 over v5 (~14/18); plants -> "Oxygen" and moons -> "Jupiter" unchanged.

**Result: 11.0 avg (11, 10, 12) - reverted.** v5 rerun on the same working search: 11, 13, 13 = **12.3**.
- Physics 2024 fixed once in 3. The top page (nobelprize.org) gave the right evidence, but Grokipedia's
  "The 2026 prize was awarded to Francis Halzen" was beside it, and the answer step took that one.
- More long evidence lines blurred easy answers ("Uhuru Peak of Kibo", "Mafadi mountain" for Africa's
  tallest mountain, though all three lines named Kilimanjaro).
- 18 SearXNG searches per run instead of 5.

**Found on the way: working web search lowers v5 too (13.0 -> 12.3).** v5's earlier runs had blocked or
junk search results, so the no-answer questions that fell through to the web got "The sources don't say."
Now real pages come back ("Moose the neighbor cat", an article about a song called "My Favourite
Stranger") and the model answers from them. The no-answer questions are now the weakest group
(0-1 of 3 per run for both versions).

### v8 (2026-10-09): ask first whether public sources can know the answer

**Change:** before any lookup (after the weather check) the model answers one yes/no: "can public
sources, like an encyclopedia or a web page, answer this, or is it about the person's own life?"
If P(YES) < 0.25 (the model is >= 75% sure only the person knows), nothing is looked up and the
answer step gets no sources. Prototype on the 18 test questions + 6 others: personal 0.03-0.18,
public 0.61-1.00 (myths and weather included). With no sources, 9 of 10 test-style replies were
"The sources don't say."; "How old am I?" (not in the test) got invented ages.

**Prediction:** no-answer questions 0-1/3 -> ~3/3; everything else unchanged (all public questions
>= 0.61, far from 0.25). v5 12.3 -> ~14.

**Result: 13.4 avg (12, 14, 15, 13, 13) vs v5 12.2 (11, 13, 13, 12, 12) - saved as best.**
- No-answer questions 14/15 (v5: 2/15). The one miss: "I don't have a favourite song." (the model
  answered about itself).
- No public question skipped the lookup. Facts 43/60 vs v5's 47/60: the misses are v5's known ones
  (Oxygen, Francis Halzen, Jupiter), so that gap is answer-step noise, not the new check.
- Myths vary run to run in both versions (1-3/3).
- 3 SearXNG searches per run (v5: 3-5).
- Seen outside the test: "How old am I?" with no sources got invented ages ("38 years old.").

### v9 (2026-10-09): an answer sample where the source is about a different year

Checked first: Wikipedia has no "2024 Nobel Prize in Physics" article, and "2024 Nobel Prizes" (in
the top 5) has a summary without winners and no Grokipedia page. So a better pick can't fix Physics
2024; the answer step must not take "The 2026 prize was awarded to Francis Halzen" as the answer.

**Change:** one more answer sample (sources.py only; search.py's list and best_sources are unchanged):
"The 2019 chess championship was won by Anna Berg." / "Who won the 2021 chess championship?" ->
"The sources don't say." Then the chain moves on to the web (nobelprize.org has the 2024 winners).

**Prediction:** Physics 2024 right in most runs (via web); risk: more "The sources don't say." on
questions where the evidence was right (recent facts, myths). ~14 vs v8's 13.4.

**Result: 12.2 avg (13, 13, 12, 9, 14) vs v8 13.5 (8 runs: 12, 14, 15, 13, 13, 15, 13, 13) - reverted.**
- Physics 2024 not fixed: "Francis Halzen" in 4 of 5 runs, "The sources don't say." in the 5th. The
  near-miss sample didn't make the model notice "2026" vs "2024".
- It made the model give up more: in the 9/18 run four fact questions said "The sources don't say."
- Scorer note: "Einstein never failed math. His final certificate..." is right but has none of the
  myth keywords ("never" isn't one) - counted wrong. Not changed.

## 10. Search-decision layer (2026-10-09): should a question be looked up at all?

The app now has search on by default (`/search` toggles it): for each message the model first answers
one yes/no, `sources.needs_lookup` ("Needs a look-up?" - recent events, facts that change, specific
details), read as a probability; >= 0.75 -> the source chain, else best_strat as before.
(Idea from llm-classifier.md, rebuilt our way: a few-shot yes/no instead of a JSON classifier with a
rules list, and no keyword rules in code.)

**Prototype (44 questions, no web calls):** the 7 that need a look-up 0.77-0.95; math, memory and
instructions <= 0.58; but "In which year did WWII end?" 0.91 and "Who painted the Mona Lisa?" 0.80.
It separates factual questions from the rest, not recent from well known. 0 missed, 2/26 unnecessary.

**End-to-end prediction:** full eval (`python eval.py auto`): ~6 look-ups per run (WWII, Mona Lisa),
still right from the encyclopedias -> about best_strat's 72.0. Search test (`search_eval.py auto`):
recent facts and myths go to the chain; personal questions (0.09-0.25) now go to best_strat, which
may invent instead of "The sources don't say." -> ~12-13 vs the chain's 13.5.

**Result:**
- Full eval, `python eval.py auto`: **73, 71, 71 = 71.7** (best_strat alone: 72.0). Looked up 6-7 of 78
  answers (WWII, Mona Lisa; the feathers question once). No harm to normal questions.
- Search test, `python search_eval.py auto`: **8, 10, 11 = 9.7** (source chain for every question, v8:
  13.5). Where it loses:
  - personal questions (0.09-0.25) now skip the chain and go to best_strat, which invents ("Whiskers",
    "Let it be by the Beatles") or says "I don't." - 1/9 (v8's check inside the chain: 14/15).
  - plants (0.71) skips the chain; best_strat is flaky on it on its own (6 tries: CO2 3, gave up 2,
    Oxygen 1) and gave up in all 3 runs.
  - myths 2/9 (chain runs: ~2/3 per run) - they still go to the chain; likely noise.
- So the layer itself works as measured (recent facts looked up, math/memory/instructions not). The
  loss is best_strat inventing personal facts it can't know. In the app that's the right route
  (memory questions need best_strat and its memory), so the fix belongs in best_strat, not here.

## 11. Fix: trim sources at a sentence end (2026-10-09)

Traced from the app: "when was the first transformers AI model created?" -> "The sources don't say."
Wikipedia's "Transformer (deep learning)" page was cut at exactly 1500 characters: "...was proposed in
the 20" - the year cut in half. Now `search.trim()` finishes the sentence that crosses the limit (up to
300 more characters), else cuts at a whole word; used for web pages and Grokipedia.
- The same question, 5 tries: 2017 twice (before: 0 of 2 in the app); also "2026." (the date line
  copied, when the only evidence was the toy-franchise article), "...did not begin in 2017." (a
  negation copied as evidence) and "The sources don't say." once.
- 18-question test: 15, 13, 15 = 14.3 (v8 13.5; the scorer also counts two more honest phrasings since
  this morning, so part of the gain may be that).
- Still open from this trace: the topic step named "Transformers" (the toy franchise); evidence copied
  a negation and a search snippet's page date ("May 11, 2026 · ...").

**+ snippet dates removed** (same day): search engines put the crawl/publish date in front of a snippet
("May 11, 2026 · ...", "12 hours ago · ..."); the evidence step copied it. `search.SNIPPET_DATE` strips it
in gather_sources (only that prefix; cached raw results unchanged).
- Transformers question, 5 tries: 2017 four times (trim only: 2 of 5); the miss, "2007.", came from the
  toy-franchise article (the first live-action film).
- 18-question test: 14, 16, 12 = 14.0 (trim only: 14.3) - same within noise; few of these questions
  reach the web stage, where snippets are used.

## 12. Memory as the first source + a lower look-up bar (2026-10-09)

From the app: "Who is the president of Finland?" -> "Sauli Niinistö" with no look-up (0.62 < 0.75), and the
wrong answer was saved to memory and steered the follow-ups. "Who holds this office now" questions
scored 0.55-0.74. A lower bar would send memory questions to the chain, which can't see memory, so:
- **Memory is now stage 0 of the chain**: the user's own earlier messages (not the AI's replies - those
  can be wrong) as one source, with the usual evidence step. Eval memory history: city, dog, name and
  the changed favourite colour all answered from memory; Mona Lisa -> NONE -> encyclopedias.
- **Bar 0.75 -> 0.60.** Check alone (10 new "changing fact" questions, labelled before running):
  missed changing facts 4/10 -> 0/10; unnecessary look-ups on the 26 eval questions 1 -> 6 (WWII, Mona
  Lisa, plants, gold, and the trick questions feathers and plane crash).

**Prediction:** full eval auto ~71-72 (the two trick questions through the chain are the risk); search
test auto up from 9.7 (plants now looked up); changing-fact questions answered from current sources.

**Result at 0.60:**
- Full eval auto: 64, 67, 69 = **66.7** (at 0.75: 71.7). Through the chain, 3 tries each: gold, Mona Lisa,
  WWII 9/9; plane crash 2/3; **feathers 0/3** (gave up / "The sources don't say." - best_strat's comparison
  route isn't in the chain); **plants 0/3** ("Oxygen." - the chain's known miss).
- Changing facts, chain vs best_strat alone: Finland Alexander Stubb vs Sauli Niinistö; chess Gukesh vs
  Magnus Carlsen; UN "The sources don't say." vs Ban Ki-moon; UK PM, Apple CEO from current web pages vs
  Theresa May, Steve Jobs. best_strat's answers are years out of date.
- So the lower bar fixes what it was meant to fix, but questions with a best_strat special route
  (comparison, sense check) lose that route in the chain.

**+ best_strat's special routes first** (same day). The look-up only happens if, after the yes/no, none
of best_strat's own routes would answer from the question alone: letters; comparison with both amounts
given; math whose rewrite uses only the question's numbers; or the sense check finding a reason.
(Plain router claims were too broad: "price of bitcoin" -> math, rewritten "1 bitcoin = 10000000...";
"most known moons" -> comparison with no amounts.) Feathers -> comparison, plane -> sense check: both
now stay with best_strat. Bitcoin and moons are looked up.

**Result (bar 0.60 + best_strat's routes first):**
- Full eval auto: 71, 72, 66, 68, 72 = **69.8** (bar 0.60 alone: 66.7; bar 0.75: 71.7; best_strat alone 72.0).
  Looked up 12-15 of 78: WWII, Mona Lisa, gold (right from the encyclopedias), "Which city do I live
  in?" sometimes (answered from memory), and plants - "Oxygen." from the chain, most of the remaining gap.
- Search test auto: 10, 10, 10 = 10.0 (bar 0.75: 9.7). Personal questions still go to best_strat, which
  invents; the brain myth goes to best_strat's sense check.
- Changing facts (not in either test) are now looked up: at 0.75, 4 of 10 weren't.

## 13. v10: a vote (user's idea, 2026-10-09)

For "What gas do plants absorb?" Wikipedia's evidence was "Oxygen", Grokipedia's "CO₂", and the answer
step took the first bare word. Now: the model's own answer (best_strat, no sources) + one answer per
encyclopedia are votes; the model judges which say the same thing (yes/no >= 0.8 - at 0.5 it merged
"Jupiter | Saturn" 0.75 and "Oxygen | CO₂" 0.56); 2+ agreeing votes with no equal rival win, wording from a
source-backed vote. No majority -> votes from web pages up to 5 -> still none: the v8 answer.
Plants, 3 tries: CO₂ 3/3 (model "Carbon dioxide." + Grokipedia "CO₂" vs Wikipedia "Oxygen.").

**Prediction:** plants fixed (+~2 on eval auto, 69.8 -> ~71.5); search test ~14 (trim+snippet runs: 15, 13,
15, 14, 16, 12 = 14.2). Risks: the model's own wrong belief is a vote (Jupiter, Francis Halzen) and can
team up with one wrong source; more calls per looked-up question (~8 for best_strat + 1 per source).
(3 runs accidentally made with the judge at 0.5: 15, 14, 14.)

**Result (judge at 0.8): kept, saved as best.**
- Search test: 15, 15, 15 = **15.0** (before the vote: 14.2 over 6 runs; v8: 13.5). Plants right every run.
  Misses: Physics 2024 (Halzen), moons ("Jupiter" won the vote: model + one source share the wrong belief),
  the goldfish myth. More web searches: 9-11 per run (before: 3-6), from the extra web votes.
- Full eval auto: 70, 70, 71, 68, 71, 71 = 70.2 **as scored** (before: 69.8). But plants now answers "CO₂"
  3/3 per run, which eval.py's keywords ("carbon dioxide", "co2") don't count. With "CO₂" counted, the 3
  runs that listed failures: 71, 74, 74 = **73.0** (best_strat alone: 72.0; instruct raw: 73.0).

## 14. v11: in a tie, the web wins (user's idea, 2026-10-10)

Traced with view.py: "how far away is oslo from stockholm?" -> the topic step named "Oslo"; Grokipedia's
"evidence" was its whole Oslo paragraph; vote: model "It is not clear what you are asking...", grokipedia
"812 miles.", web "471.2 km." (both numbers invented: the web evidence was a page heading, "Oslo to
Stockholm Railway Route"). No majority -> v8's combined answer repeated the Oslo paragraph.
**Change:** no majority after the web votes -> the first web vote (best-ranked page) wins; no web vote ->
the combined answer as before.
**Prediction:** few test questions end in a tie, so ~15.0 on the search test; ties now give a short answer
from the web instead of a pasted paragraph, but it can be just as invented as the other votes.

**Result: reverted.** Search test 14, 13, 15 = 14.0 (v10: 15.0). Only 2 of the 18 questions end in a tie,
one each way: Physics 2024 fixed for the first time (web "John J. Hopfield and Geoffrey Hinton." beat
grokipedia "Francis Halzen."), Literature 2024 broken (wikipedia "Han Kang." vs web "Han." - the judge
scores that pair 0.46, a tie -> web "Han."). Oslo-Stockholm, 3 tries: "3.346 km" (the distance from Oslo
to the North Pole, from a Czech page), "It is a 734 km trip." (the web beat the model's closer "560 km."
in a tie), "270.6 km" - all wrong; 2 of 3 never reached a vote (no encyclopedia evidence).
The real problem is upstream: the lookups fetch the wrong things ("Oslo" article; train-booking pages).

## 15. v12: a thinking step - what exactly has to be found (user's idea, 2026-10-10)

One small call first writes the need ("the distance between Oslo and Stockholm"); it goes to the
article step (as a "Need:" line), to the evidence step (same), and is the web search phrase when it is
10 words or fewer. Preview: needs read well; the article step still names "Oslo", and plants changed from
"Photosynthesis" to "Plant respiration".
**Prediction:** Oslo-Stockholm better through the web (the phrase is now the need); evidence says NONE
more often on off-topic texts. Search test about v10's 15.0; plants is at risk (new article).

**Result: kept in sources.py (the app's chain), not frozen - same totals as v10, different mix.**
- Oslo-Stockholm, 3 tries: "416 kilometers." (straight line, right) and "472 km." twice, all from
  distancecalculator.net / rome2rio (the web phrase is now the need). Before: invented every time.
- Search test: 15, 14, 16 = 15.0 (v10: 15.0). Facts 34/36 (v10: 31/36; Physics 2024 right 3/3 for the
  first time, one run 12/12); myths 3/9 (v10: 5/9).
- Full eval auto (CO₂ counted): 74, 70, 73 = 72.3 (v10: 73.0) - same within noise.
- Why myths dropped (traced, Einstein): the need makes the evidence step stricter - both encyclopedia
  summaries now give NONE (right: they don't mention the myth) - so the question reaches the web stage.
  The top page's evidence is perfect ("Einstein didn't fail math as a child ... dismissed the story as a
  myth"), but the web stage writes ONE answer from all pages' evidence (also a publications list, Czech
  text) and says "The sources don't say."

## 16. v13: a vote at the web stage too (2026-10-10)

The web stage now votes like the encyclopedia stage: the model's own answer (asked once per question,
shared) + one answer per page with evidence; no majority -> one answer from all evidence, as before.
Myth trials: Einstein "No, this is a myth." (model "No, he didn't." + top page), brain "No, that's a myth.",
goldfish still "Yes." (two pages repeat the myth and outvote the model's "No.").
**Prediction:** myths back up (3/9 -> ~6/9), facts as v12 (34/36) -> search test ~16.

**Result: kept, saved as best (best_sources.py = v13).**
- Search test: 18, 15, 15, 15, 15 = **15.6** (v10 and v12: 15.0). The first 18/18. Myths 13/15 (v12: 3/9).
- Full eval auto (CO₂ counted): 72, 75, 75 = **74.0** (v10 73.0, v12 72.3; best_strat alone 72.0, instruct 73.0).
- Misses over 5 runs: moons "Jupiter" 4/5; **Tour de France 2024 "Jonas Vingegaard" 4/5** (new): Grokipedia
  had "Tadej Pogačar ... claimed his third overall victory" (1 vote), but the pages found for the need
  phrase were the 2023 Tour, a pre-race preview and a Czech results table (read off the second place) ->
  3 Vingegaard votes outvoted it. A popular wrong answer can win a vote. Goldfish myth 2/5, favourite song 2/5.

## 17. v14: complementary searches + one follow-up round (from about-web-search.md, 2026-10-10)

Adapted from the user's document (not literally: no JSON plans, no per-question-type rules in Python):
- **Two complementary search phrases.** Search 1 = the need (v12). A small call writes Search 2: the
  same fact looked for another way (other words, or another meaning the question could have). Both
  result lists are interleaved, duplicates (same URL) dropped, at most 6 web sources.
- **A bounded verification loop.** If the web vote finds no majority, the model sees the need and the
  evidence found so far and writes ONE follow-up search phrase for what is still missing; its pages
  join the vote. Still no majority -> one answer from all evidence, as before.
- Kept from before: the need line (the document's "requested fact"), the evidence step (its "evidence
  extraction"), the votes and the "same claim?" judge (its "compare claims"), view.py traces.
**Prediction:** Tour de France 2024 helped (a second phrase finds more 2024 pages; more right votes);
Oslo stays right. Search test ~16 (v13 15.6). Cost: about twice the web searches per run (v13: ~10).

**Result: reverted (v13 stays).** Search test 16, 15, 16 = 15.7 (v13: 15.6) - the same - at 19-22 SearXNG
searches per run instead of ~10, and ~115 s instead of ~50 s. The second phrases were rewordings, not other
meanings ("the winner of the 2024 Tour de France" / "2024 Tour de France winner"), so they found the same
kind of pages: Tour de France 2024 still "Jonas Vingegaard" 2/2; Oslo "472 km." and "574.21 km." (v13:
"416 kilometers" mostly). The follow-up search never fired in the tries: it only runs when the web vote
finds no majority, and most questions are settled before. Code kept outside the project (scratchpad).

## 18. Prototype: "same event, same year?" per evidence line (2026-10-10) - not built

To stop wrong-year pages winning votes (Tour de France 2024). On the 47 evidence lines of the 15 fact and
myth questions the yes/no doesn't separate: right lines 0.20-0.94, wrong-year/pre-event lines 0.30-0.46
(Halzen's 2026 prize 0.46 > the right Hopfield and Hinton 0.24/0.37). It rewards repeating the need's
words. Recorded in FAILED_EXPERIMENTS.md.

## 19. Personal questions -> the chain; chit-chat in the answer key (2026-10-10)

Measured first (best_strat, 3 tries each): personal questions it can't know 0/18 honest (empty memory),
1/18 (eval memory) - "Eggs, bacon, and coffee", "My sister's name is Mary"; chit-chat mostly fine but
"good morning" gave up 2/3 (echo) and small talk got quiz answers. Memory questions 12/12, facts 12/12.
- A header rule + sample "What is my brother's name?" -> "I don't know, you haven't told me." in the
  answer key: personal 17/18, but the sample leaked - memory questions 6/12 ("What is my name?" -> "I
  don't know, you haven't told me."). Not used.
- **Used:** (1) needs_lookup sends questions only the person can answer (the v8 yes/no < 0.25) to the
  chain: memory first, then the no-sources answer. Scores: personal unknowable 0.04-0.22, memory
  questions 0.04-0.36, chit-chat 0.38-0.91, statements 0.53-0.88, facts 0.89-0.99. (2) answer_key v9:
  "When the person is just chatting, the answer chats back in a short, friendly way." + sample "Hi
  there!" -> "Hi! What would you like to know?". Together: personal 15/18 and 12/18, memory 12/12, facts
  12/12, chat normal ("Good morning! How can I help you today?"), no give-ups.
- Full eval, best_strat alone (answer_key v9): 75, 73, 73 = **73.7** (was 72.0), 0 give-ups in all runs.
- Full eval auto: 71, 75, 68, 75, 73 = 72.4 (v13 + old answer key: 74.0 over 3 runs; runs swing ±4).
- Search test auto: 15, 14 = **14.5** (was 10.0); personal questions 6/6.
- Still open: the no-sources answer sometimes invents ("How old am I?" -> "13 years old."), and its honest
  reply is "The sources don't say." - accurate, but odd wording in a chat.

## 20. v15: "The sources don't say." said naturally (user's idea, 2026-10-10)

When the chain ends in "The sources don't say.", one more call (answer key: "says so in one short,
natural sentence about what was asked; never guesses") rewrites it. Shape checks: it must still say "I
couldn't find / don't know", and no number the question doesn't have; else the plain reply stays.
Trials: "What is my sister's name?" -> "I don't know your sister's name; you haven't told me." (2/2);
"How many hairs does the average cat have?" -> "I couldn't find a reliable source for how many hairs the
average cat has."; "How old am I?" -> "I don't know your age; you haven't told me."; one fallback to the
plain reply. Scorer: search_eval's ABSTAIN also accepts "couldn't find" / "haven't told".
**Prediction:** search test unchanged (~15.6); the no-answer questions now read naturally.

**v16 tried: related information in the not-found reply (user's idea 1.5) - not kept.** The chain's
evidence lines that didn't answer (up to 2) went in as "Related:", and the reply could add them ("...,
but ..."). It rarely triggers - most not-found endings have no evidence at all - and of the 2 that did,
one misled: "What colour was Julius Caesar's first tunic?" -> "I couldn't find ..., but it was a shade of
red that was redder than purple." (a general line about Roman purple turned into a claim about Caesar).
The other was fine ("... but it is an extensive network of walls ..." for the Great Wall's bricks).
Back to v15, plus a guard: a reply mentioning "answer key" is rejected ("King Olaf had a horse, but the
answer key couldn't find its name.").

## 21. v17: no evidence at all -> "I couldn't find ..." instead of an answer from nothing (2026-10-10)

Traced 8 unanswerable questions x 3 (scratchpad script): the inventions came from three kinds -
(1) the answer step run with NO evidence at all ("How many hairs does the average cat have?" -> "3500
hairs.", King Olaf's first horse -> "No.", "How old am I?" -> "13 years old."); (2) related evidence used
as the answer (Napoleon's 30th birthday -> "roast chicken and pasta" from "Napoleon preferred simple food,
such as roast chicken, pasta"); (3) not invented at all (Einstein's favourite food -> "Mushrooms": web
pages do say that). v17 fixes (1): when no source gave evidence (the v8 no-lookup path, or the web stage
with no evidence), the model writes the not-found reply directly; the answer step from no sources runs
only if every not-found attempt is malformed.
- Trace again: cat hairs, Caesar's tunic, King Olaf, How old am I - 12/12 honest (before: 4 invented).
  Napoleon (kind 2) unchanged.
- Search test: 15, 13, 14 = **14.0** (v15: 15.3, v13: 15.6). The cost: when the web gives no evidence
  for a question the model knows (Einstein's maths 2x, goldfish, Physics 2024 once), it now says "I
  couldn't find ..." where the answer step used to answer from the model's own knowledge - which is right
  for myths and wrong for obscure facts ("3500 hairs"). The cache had expired, so these runs also fetched
  new pages (part of the swing).

**v18 (user's option C):** when the web stage finds no evidence, the reply stays "I couldn't find ...", and
the model's own answer (best_strat, no sources; reused from the vote if it ran) is kept as `guess` in the
chain. The app shows it as a dim line, "my guess, not from a source: ...", and view.py shows it too. Never
for personal questions (the no-lookup path), and not when the guess is itself "I don't know". The tests
only score the reply, so their scores are v17's. Seen: King Olaf's first horse -> guesses "Snorri." /
"Frøya."; cat hairs -> "It's a trick question. The answer is zero." - which is why they are labelled.

**Cleanup (2026-10-10):** `best_sources.py` (the frozen v13) was deleted, with `search_eval.py
bestsources`: the app runs `sources.py` (v13 + v15, v17, v18), and v13's numbers are recorded above.
`search.md` (the first search tool's plan and results) was deleted too: its numbers are in README.md,
its failed ideas in FAILED_EXPERIMENTS.md.
