# Failed experiments

Ideas that were tested and did **not** work for the base model
(`QuantFactory/Qwen2.5-3B-GGUF:Q4_K_M`). Check here before trying something new,
so we don't repeat them. If you want to retry one anyway, say what is different this time.

Scores marked "eval" come from `eval.py` (keyword-scored, not model-judged).
"12q" = the original 12-question set (72 answers per config over two runs).
"26q" = the harder 26-question set (78 answers per config).

## Deliberation (a one-line plan before the reply)
- **What:** before replying, the model wrote `AI (plan): ...`, then the reply was generated after it.
  Re-planned on every retry; bad plans (repetition / language switch) were dropped.
- **Result (eval):** 12q — with plan 58/72 (3 attempts) and 43/72 (1 attempt), without 71/72 and 65/72.
  26q — with plan 51/78, without 59/78. Also ~60% slower.
- **Why it failed:** the reply copied the plan's style ("I will calculate 12 times 12...") instead of
  answering, echoed the question back, and memory got worse (the plan line sits between history and reply).
- **Removed** from the code.

## One attempt instead of three (no retries)
- **Result (eval):** 12q — 65/72 vs 71/72 with 3 attempts. 26q — 58/78 vs 59/78. Gave up more often.
- Not harmful as such, just worse. Retries stay.

## Validator: two conditions in one question
- **What:** "Does it stay on topic AND is it in the same language?" (three wordings).
- **Result:** first wording still said YES to Chinese replies; two stricter wordings made it answer
  NO to everything (4/8 on test cases).
- **Instead:** language is checked in code (`language_switch`). Rule of thumb: one question per model call.

## Structured multi-criteria critic (one call, JSON verdict)
- **What:** one call checking 5 things (answered? followed instructions? coherent? concise?
  contradicts conversation?) returning `{"pass", "reason", "revision"}`.
- **Result:** 7/8 valid JSON, but only 4/8 correct verdicts (coin flip). "reason" just repeated the
  checklist, "revision" copied the reply, unescaped quotes broke the JSON.

## Model-based language detection
- **What:** asking the model "what language is this text?" (few-shot).
- **Result:** 10/14. Guessed from the topic: "pizza" text in Chinese → Italian, an English sentence
  about France → French, and a Chinese quiz line → English.
- **Instead:** CJK characters + French/English function-word counting in code (13/13).

## Validator prompt wordings that lost
- Qwen chat-template format (`<|im_start|>...`): the base model wrote "Assistant's message:" before
  YES/NO, so every verdict was read as a rejection.
- "Does the response address the user's message?" on continuation prompts: rejected valid
  continuations ("My favourite food is" + "beef...").
- "Does the reply address the user's message?" (chat format): 6/10. Rejected "Your name is Jaakko."
  because the validator can't see the memory. Fixed with "judge relevance, not correctness".
- Without the "polite I-don't-know counts as related" sentence: 11/15. It rejected honest refusals.
- 5-token verdict limit: the model kept writing after YES/NO (e.g. copying Chinese from the reply).
  Harmless, but noisy; now 1 token.

## Retry notes
- Plain re-roll with no feedback: looping prompts ("I like cats. I like cats. ...") failed 3/3 every time.
- "Editor's note" in front of a *continuation* prompt: helped off-topic cases, did nothing for loops.
- Quoting the repeated phrase in the note: sometimes **plants** it in the next attempt (the base model
  copies what it sees). Still kept for repetition, because the quoted-user framing was a net win.
- **The note without the quote** (2026-10-10, from the review: "The AI's previous reply started repeating
  itself."): one regression in the small test, no gain. IFBench prompt 77's retry (DRY + the plain note) ended
  with the long route's fruit sample ("Here's a list of three fruits: 1. Apple ..."); with the quote it had
  passed. The quote stays for phrases; looping lists get their own note ("every item in the list is different").

## Base-harness rules applied to the instruct model
- **Result (eval, 26q):** instruct with no harness 75/78; the same model inside this harness 63/78
  (3 attempts), 51/78 (1 attempt, 18 gave up), 47/78 (with deliberation).
- **Why:** newline stop cut its multi-line working before the result; the fake `User:/AI:` transcript
  made it write "Yellow. User: How about a kiwi fruit?"; the 50-token soft limit cut long answers.
- Lesson: harness rules are model-specific. Not a dead end for the instruct model, but it would need
  its own harness (own chat format, no newline stop).

## Strategy trials v1 (failed-question set, 55 answers; baseline 34/55)
Details in `plan.md` (changelog) and `results/2026-10-08_v1-all.txt`. All three lost overall,
but each fixed the instruction questions the baseline never gets (Sun yes/no: 0/5 → 3/5).
- **strat1 v1** (4 example exchanges in the prompt): 27/55. Short-answer examples seem to push
  quick, unconsidered answers: feathers vs steel 3/5 → 0/5.
- **strat2 v1** (strat1 + "direct answer in the asked form?" judge + revising the rejected draft):
  17/55 with 26 give-ups. The extra judge + revision loop rejects far too much.
- **strat3 v1** (model-filled task contract + one yes/no check per requirement): 25/55, ~4× slower.
  Train 0/5, feathers 0/5, "What is my name?" 1/5.
- Common pattern: everything that pushes for *short, direct* answers fixes instruction questions
  but breaks trick/reasoning questions (feathers 0/5 in all three).

## Strategy trials v2 (same set, 55 answers; baseline 34/55) — `results/2026-10-08_23-06-00.txt`
- **strat1 v2** (+ an example of catching a trick question): 29/55. Feathers still 0/5, so the trick
  example didn't help. Examples placed right before the real conversation **broke memory**
  (dog 1/5, name 1/5) — the model treated them as part of the conversation.
- **strat2 v2** (revising the rejected draft, no extra judge): 27/55. Give-ups fell 26 → 4, but no
  gain over strat1. **Revising a draft didn't help; dropped.**
- **strat3 v2**: 26/55. The memory fix worked (5/5, 5/5), but overall still below baseline and ~4× slower.

## Strategy trials v3 (30-answer set: 10 questions x 3; baseline 17/30) — `results/2026-10-09_11-45-33.txt`
None beat the baseline.
- **strat2 v3, calculator tool** (model writes `[calc: …]`): 10/30. Perfect on plain arithmetic
  (17×23, 15%: 3/3), but the model also used it where it makes no sense: train "3 + 2.5 = 5.5 pm",
  months "12/28 = 0.428…". A tool only helps if the model knows when *not* to use it.
- **strat3 v1–v3, task contract**: v3 10/30, 526s. Never fixed "one word" (ancient 0/3 even with a
  one-word example). Retired; strat3 is now strat1 + strat4 combined.
- **strat4 v1, LLM-as-a-Verifier** (3 baseline candidates, pairwise logprob verifier): 16/30 at
  1533s (10× slower). Strong when candidates differ (ancient 3/3, strawberry 3/3) but its verifier
  prefers longer answers, so the Sun yes/no stayed 0/3.

- **strat3 v4, strat1 + strat4 combined** (2 strat1 candidates + 1 baseline, strat4's verifier
  picks): **12/30 at 1408s** — worse than both parts (strat1 15, strat4 16) and 10× slower than the
  baseline. Strawberry 0/3 (strat4 alone: 3/3), months 1/3. Combining two non-winners didn't add up.
- **Conclusion of v1–v4 trials:** nothing beat the plain baseline (17/30). Extra stages (examples,
  revision, contracts, verifiers, tools) cost time and mostly lost points. strat2 and strat3 code
  was deleted on 2026-10-09; strat1 and strat4 kept for reference until 2026-10-10 (then deleted
  with run_strats.py and failed_questions.py; strat4's logprob reader lives on as harness.top_token_probs).

## Sampler settings (strat2 v4, 2026-10-09) — `results/2026-10-09_12-28-10.txt`
- **What:** baseline with temperature 0.2 on attempt 1, 0.8 on retries, and min_p 0.1.
- **Result:** 16/30 vs baseline 17/30, same speed (144s). Steadier on what the model knows
  (15% 3/3, colour 3/3), worse on months (1/3) and feathers (0/3). Within noise: no gain.
- **Ruled out going forward (user's call):** don't change temperature or sampler settings.
  (Narrowed 2026-10-10: test answers stay at temperature 0, but deterministic sampler methods such as
  DRY are allowed.)
  More wiggle room in sampling means test results depend more on luck.

## Re-reading / RE2 (strat2 v5, 2026-10-09) — `results/2026-10-09_12-32-38.txt`
- **What:** the question shown twice ("Read the question again: …") right before "AI:"
  (Xu et al. 2023, "Re-Reading Improves Reasoning in LLMs").
- **Result:** **10/30** vs baseline 17/30, 2 gave up, same speed. Worse almost everywhere
  (months 3/3 → 1/3, strawberry 2/3 → 0/3, ancient 1/3 → 0/3).
- **Why it failed:** the doubled question made the base model talk *about* the question instead of
  answering it: "The question is asking for the value of 15% of the number 200.", "I think you
  mean, If a plane crashes…", "…can be calculated by adding 2.5 hours to 3 pm." The paper's gains
  were on instruction-tuned models doing step-by-step reasoning, not a 3B base model.

## Answer-key styles on their own (prototypes, 2026-10-09; 9 questions × 6 first attempts)
- **"Final result in the first words"** (strat2 v8 alone): 38/54. Blurts on tricks ("Steel.") and
  skips unit steps ("3 pm + 2.5 h = 5.5 pm"): train 0/6, feathers 2/6.
- **"Show one short step, then the result"** alone: 38/54. Fixes tricks/conversions (feathers 5/6,
  train 2/6) but breaks memorised answers (15% of 200: 6/6 → 3/6, "15% is 100% divided by 6…").
- A sample ending in a decimal ("so 2.5 metres") seemed to encourage "5.5 pm"-style answers.
- Neither style wins alone; they fail on different questions → strat2 v9 writes both and picks.

## Dead ends on the way to strat2 v11 (2026-10-09, prototypes)
- **Picker between two answer styles** (strat2 v9): 20/30 at 305s; the picker chose "Steel." 3/3.
- **Spelling a long word in one go:** "s-traw-b-e-r-r-y", "s-t-r-e-a-m-b-l-e-r-t", numbered lists
  that ran to 18 letters. The model sees word pieces; short words spell fine, long ones don't.
- **Counting letters inside one long prompt:** "berry: none" right next to "b-e-r-r-y" (0/8, 1/10).
  Samples that all ended in "…has none" were copied. Counting in a short prompt per part works.
- **Scorer trap:** position lists ("at 3, 7, 9…") contain "3" and were scored as correct. Read the
  answers, don't trust the score, when an answer contains lists of numbers.
- **Converting amounts while copying** ("a kilogram" → "1 kilogram"): unreliable, often "not given".
  Copying the words exactly works better.
- **Comparing amounts inside the answer prompt:** the riddle wins ("The steel." 11/12 even with
  "a kilogram; a kilogram" right there). A separate yes/no on the amounts alone works.
- **Generic labels in samples** ("first rope", "second walk") made the model drop the names.
- **Router thresholds:** no math threshold separates the train question (0.78–0.80) from the
  feathers trick (0.85–0.86); fixed by route order + shape checks instead.

## Dead ends on the way to v12/v13 (2026-10-09, prototypes)
- **Feathers:** an "does the answer agree with the facts?" checker always leaned yes ("The steel."
  0.83 even with "Same amount: yes" and an explicit rule) — this base model can't say no as a judge.
  Multiple choice (A/B/C) still picked steel (0.71) and showed position bias toward the samples'
  answer letter. What worked: removing the names ("a kilogram or a kilogram?").
- **Plane:** a "trick question?" router was useless (all 0.57–0.82). A "meanings" step defined the
  wrong words ("crash", "border", "bury") until it was split into copy-what's-asked + define-it.
  Even with "survivors: people who are still alive" in view the answer didn't conclude anything
  (0/6); a separate yes/no "makes sense?" + a written reason did.
- Pattern across feathers, plane, strawberry: the model *has* the right fact but doesn't act on it
  inside a prompt that contains the familiar story. Small separate steps, each seeing only what it
  needs, work; one big prompt doesn't.

## "About:" line in the normal answer route (2026-10-09, prototypes, 8 questions)
- **What:** reuse the sense check's "asked about + definition" line in the normal answer-key prompt
  (no extra calls). v1 copied only the noun; v2 kept units ("…in Fahrenheit") and asked for a
  description without the answer.
- **Result:** v1 helped freezing point (1/5 → 5/5) and seasons, but planted the define step's own
  mistakes ("the gas plants use to make food, oxygen" → plants 0/5) and hurt Sun (5 → 3).
  v2: **50/64 vs 56/64 without the line** (Sun 8 → 5, my name 8 → 6).
- **Also learned:** without the line the freezing point was 6/8 — its "Zero." misses are flaky, not
  systematic. Plants is a knowledge gap (2/8 even on the plain route: the model half-believes
  "oxygen"); rephrasing steps can't supply a fact the model doesn't have.

## Web search: what didn't work (2026-10-09, search_eval.py)
- **Relevance validator on search answers:** it said NO to the honest "The sources don't say."
  (5/6 no-answer questions gave up). Adding "a reply saying the sources don't contain the answer
  also counts" didn't change its verdict. Removed for search answers (generic checks only).
- **Long sample answers:** "The sources disagree: one says it is…, one says it is…" was copied,
  tripped the repetition check, and was used even when all sources agreed; "…Bats can see" leaked
  into an answer about brains. Sample answers are now short and plain.
- **"Do the sources contain the answer?" yes/no gate:** no threshold separates cases — plants (the
  answer is in the sources) 0.39, neighbour's cat (it isn't) 0.53, breakfast 0.10.

## Saving /search answers to memory (2026-10-09)
- **Bug found in use:** a saved search answer ("18°" for Milan) became a pattern in memory; later
  weather questions *without* search got invented temperatures (11/12 in a test).
- **Marking it in memory didn't help, it made it worse** (12 tries each, New York + Oslo):
  "18° (from a web search: accuweather.com)" → invented 12/12 and copied the marker 10/12
  ("12° (from a web search: accuweather.com)"); "According to a web search (…): 18°" → 12/12;
  marking the question too ("[web search] …") → 12/12. Follow-ups didn't use the context anyway
  ("Is that warm for Milan?" → "Yes."/"No.").
- **Now:** /search answers are shown and logged but not saved to memory.
- **Underlying problem:** with empty memory v14 still invents live facts (Oslo 6/8, New York 3/8),
  and every invented answer that gets saved feeds the same pattern.

## Source chain v1 (2026-10-09)
- **"NONE" option when picking a Wikipedia article:** the model chose NONE even with the perfect
  article listed ("2024 Nobel Prize in Literature"). Removed.
- **Web-search phrases as Wikipedia queries:** "gas plants absorb from air" -> only junk titles.
  The model naming the expected article ("Photosynthesis") works.
- **Unpaced Wikipedia API calls:** HTTP 429 after 5 quick searches. Cache + 1 s spacing + Retry-After.
- **Reading whole encyclopedia summaries:** the model takes a prominent related fact ("Germany, the
  host country"; "Oxygen" from photosynthesis) instead of the answer. Not fixed yet.

## Source chain trials v3-v4 (2026-10-09; 18 questions, cached sources; v2 = 13.3 avg)
- **v3: yes/no "does this evidence answer the question?"** - 11.3 avg. On hand-made evidence lines it
  scored 9/9, but real lines differ: Han Kang's correct evidence ("Han Kang 'for her intense poetic
  prose...'", no "won") scored 0.37, wrong ones 0.09-0.34 - no threshold separates them. Too many
  rejections -> fell through to web search (11 searches/run), which answers worse.
- **v4: samples for "name the general topic" and "pick the asked year's article"** - 10.0 avg
  (9 web searches/run vs 5). The new samples steered picks to worse articles (list articles?).
- Also seen: the evidence step once "copied" a line from its own sample ("Paris is the capital of
  France") instead of the source; the 80%-copied check doesn't catch sample text.

- **Source chain v6: use the article the model named directly if it exists** (skip search + pick) -
  8.8 avg vs v5's 13.0. Skipping the pick step hurt the questions where picking helped, and it didn't
  even fix the Physics-2024 question it was meant for.

- **Source chain v7: add the top web page's evidence to the encyclopedias' before answering** -
  11.0 avg (11, 10, 12) vs v5 12.3 (11, 13, 13) on the same, now working, web search (18 SearXNG
  searches/run vs 5). Physics 2024 got fixed only once in 3: the web page's line had Hopfield and
  Hinton, but Grokipedia's "The 2026 prize was awarded to Francis Halzen" sat beside it and the answer
  step took that one, not noticing the wrong year. More (long) evidence lines also blurred easy
  answers: "Uhuru Peak of Kibo" / "Mafadi mountain" when all three lines named Kilimanjaro.

- **Source chain v9: an answer sample where the source is about a different year** ("The 2019
  chess championship was won by Anna Berg." / "Who won the 2021 chess championship?" -> "The
  sources don't say.") - 12.2 avg (13, 13, 12, 9, 14) vs v8 13.5 over 8 runs. Meant to stop "The
  2026 prize was awarded to Francis Halzen" being taken as the 2024 answer; still "Francis Halzen"
  in 4 of 5 runs. It only made the model give up more (4 fact questions "don't say" in one run).
  Note: a better *pick* can't fix Physics 2024 either - Wikipedia has no such article, and "2024
  Nobel Prizes" has a summary without winners and no Grokipedia page.

- **Removing or moving the "Today is ..." line in the search answer prompt** (2026-10-09). Reason: in
  the app "When was the first transformers model created?" got "Friday 09 October 2026, 22:41." - the
  line right above the question - though the source said 2017. Answer step only, 20 tries each:
  | variant | Kilimanjaro right | breakfast honest | transformers: date copied |
  |---|---|---|---|
  | "Today is Friday 09 October 2026, 22:41." above the question (kept) | 16 | 20 | 0/50 |
  | "Today is 09 October 2026." | 15 | 19 | 3/20 |
  | "End of sources." (no date) | 8 | 19 | 0/20 |
  | date in the header ("Answer key, written on ...") | 13 | 10 | 0/40 |
  | no line | 9 | 15 | 0/40 |
  The line also separates the sources from the question and frames "this morning". With the header
  version the 18-question search test fell to 12, 11, 12 (v8: 13.5). The copy is rare; kept the line.

- **Widening one-word evidence to its whole sentence** (2026-10-09, for "What gas do plants absorb?" ->
  "Oxygen."). Trace: Wikipedia's Photosynthesis summary never mentions carbon dioxide (only "releases
  oxygen as a byproduct"), but the evidence step copied the lone word "Oxygen" instead of NONE;
  Grokipedia's evidence was a bare "CO₂"; the answer step saw two bare words and took the first. Answer
  step with the evidence widened to its sentence, 20 tries: plants 2 -> 4/20 (still "Oxygen" mostly),
  Kilimanjaro 11 -> 13/20 (noise; one reply leaked a sample). Not built. Also not tried: rejecting evidence
  under 3 words - of the 4 short evidence lines in the 18 questions, 2 were right ("Canberra", "1912").
  The model's own belief ("plants -> oxygen") drives every step here, like "Jupiter" for most moons.

- **Source chain v11: in a vote tie, the first web vote wins** (2026-10-10) - search test 14.0 (14, 13, 15)
  vs v10 15.0. Ties are rare (2 of 18 questions) and went one each way: Physics 2024 fixed (web beat
  Grokipedia's "Francis Halzen."), Literature 2024 broken (web "Han." beat Wikipedia "Han Kang."). Didn't
  fix the case it was for: Oslo-Stockholm stayed wrong 3/3 - every vote there was invented from sources
  that don't hold the distance (the "Oslo" article, train-booking pages).

- **Source chain v14: two complementary search phrases + one follow-up search round** (2026-10-10, from
  about-web-search.md) - search test 15.7 (16, 15, 16) vs v13 15.6, but twice the SearXNG searches
  (19-22 per run) and time. The 3B model's "second phrase, another way" was a rewording, not another
  meaning, so it fetched the same kind of pages (Tour de France 2024 still "Vingegaard"); the follow-up
  round (only when the web vote has no majority) never fired in the tries.

- **"Same event, same year?" yes/no on each evidence line** (2026-10-10, prototype only - not built). Idea
  (from about-web-search.md's source dates): drop votes whose evidence is about another year or event,
  for "Who won the 2024 Tour de France?" (the 2023 race page and a pre-race preview outvoted Grokipedia's
  Pogačar). On the 47 evidence lines of the 15 fact/myth questions there is no threshold: right lines
  scored as low as 0.20-0.37 (Hopfield and Hinton 0.24/0.37, Rugby 0.20/0.23, the Einstein myth line
  0.49), wrong ones up to 0.44-0.46 (the 2023 Tour page 0.44, Halzen's 2026 prize 0.46). The model rewards
  evidence that repeats the need's words (0.89), not evidence about the same event. Same weakness as v3
  ("does this evidence answer the question?") and v9 (the different-year answer sample).

- **Related information in the "couldn't find" reply** (v16, 2026-10-10): when the chain ends in "The
  sources don't say.", offer its non-answering evidence lines as "Related:" so the reply can add them.
  Rarely triggers (most such endings have no evidence); of 2 cases, 1 misled - Caesar's first tunic ->
  "... but it was a shade of red that was redder than purple" (a general line about Roman purple
  presented as the answer). Kept v15 (the plain natural "I couldn't find ..." reply).

- **A model-routed "multiple choice" route for GPQA** (2026-10-10, router prototype only - not built).
  Yes/no "does the question list answer choices (A), (B), ... to pick from?", tried on the 26 eval + 18
  search questions, 50 GPQA (first half) and 30 IFBench prompts. Three variants, no separation:
  short samples - eval max 0.75, GPQA min 0.46; GPQA-shaped samples with a "Choices:" block - search max
  0.80 ("most known moons"), GPQA min 0.57; same on the last 300 characters only - search max 0.80, GPQA
  min 0.54. The 3B model doesn't reliably notice a choice list at the end of a long question.

- **strat2 v17: the request's instructions copied above "Response:" on the long-text route** (2026-10-10,
  IFBench). A small step copied the rules ("keyword meridian once | keyword gossamer twice | ...") into an
  "Instructions to follow:" line. IFBench first half, 2 runs each: v17 22.0, 23.3 (avg 22.7%) vs v16 24.0,
  20.7 (avg 22.4%) - no gain. The copies were mixed: some exact, some missed rules ("between 90 and 100
  words"), one distorted ("no more than 9% stop words" -> "no stop words").

- **Long-text route bar 0.85 -> 0.80** (2026-10-10, IFBench). 18 more of the first 150 prompts take the
  route (none of our 26 eval questions). First half, 2 runs: 26.0, 20.0 (avg 23.0%) vs 0.85: 24.0, 20.7
  (avg 22.4%) - inside the noise. On just the prompts that change route: 0.85 passed 3/16 and 2/16, 0.80
  passed 2/16 and 1/16 - the long route doesn't help these borderline prompts. Kept 0.85.
- **Long-text route bar 0.85 -> 0.75, deterministic retest** (strat2 v20, 2026-10-10; stable sets, no prompt
  cache). Measured first: 50 IFBench prompts sit at 0.75-0.85, 48 of them failing on the normal route. Small
  test: none of the 8 moved hard prompts got fixed (their rules fail on either route: trigram overlap, "repeat
  the request", no word more than 4 times...), and both moved passing prompts broke - "30 to 73 words" got 128
  words, "answer with one of yes/no/maybe" got "Yes, sensitive historical events could be ..." (the short route
  had answered "Yes."). IFBench hard 1 (a fix that was not a moved prompt: noise), control 27; GPQA and our eval
  unchanged. The bar's job is also to keep short-answer requests on the short route. Kept 0.85.

- **GPQA letter bias blamed on the long route's samples** (2026-10-10, diagnostic, no answers generated).
  v17 picks (C) 85 times and (D) only 8 times in 198 questions, though the right letters are even (47-52
  each); the raw model leans the same way, less (C 64, D 23). Suspect: the "3. Cherry" sample line right
  above every request. One-token probe after "The correct answer is (" on all 198: current samples 71
  right, (D) on top 8 times, mean P(D) 0.15; samples swapped 75, 6, 0.15; no samples 68, 8, 0.14. The
  avoidance of (D) is the model's own. Correcting it would need multiple-choice handling in code (ruled out)
  or a model-routed multiple-choice route (no separation, above).

- **More samples on the long-text route** (strat2 v18 and v18b, 2026-10-10; small test with control sets, no
  prompt cache; v17 on the sets: IFBench hard 4, control 22; GPQA hard 1, control 27). Idea: the route's two
  samples steer the answers' form strongly, so samples that follow everyday instructions exactly might teach
  following them.
  - v18 (fruit list moved first; + "Describe a lighthouse in exactly three sentences. Start your response with
    the word 'Alone'."; + "Give two tips ... Put - before each tip, and wrap your whole response in double
    quotation marks."): IFBench hard 2, control 19 (6 lost, 2 won); GPQA 2, 28. Answers starting with "1."
    fell from 17 to 6 of the 60 set prompts, and those lists had been passing (person names x2, emoji per
    line, alliteration); 4 answers came back wrapped in quotes (v17: 1).
  - v18b (only the lighthouse sample, placed first; v17's order kept): IFBench hard 3, control 24; GPQA 1, 29 -
    the noise. The flips were scattered over unrelated instruction types (5 won, 4 lost), and the "start with
    a verb" prompt still failed. Samples change the answers' form, not how well the model follows an
    instruction.

- **DRY on every long-route attempt** (strat2 v19, 2026-10-10, after the user allowed sampler methods;
  llama.cpp's "Don't Repeat Yourself" sampler: dry_multiplier 0.8, dry_base 1.75, dry_allowed_length 2,
  dry_penalty_last_n 1024). The loops went away: IFBench answers that were the last rejected attempt 19 -> 4
  of 60, repetition rejections 55 -> 6 (prompt 299: 4 -> 16 different names in 20). But IFBench got worse:
  hard 2, control 18 (v17's reference 4, 22; the run before, with the echo fix: 2, 23); against v17: fixed 1,
  broke 7 (a split that uneven comes from chance 7% of the time). DRY also discourages repetition that is
  wanted: "an emoji at the end of every sentence" lost its emojis; sentence-chain, alliteration and palindrome
  prompts that had passed narrowly broke. It looks back 1024 tokens, the prompt included, so phrases copied
  from the request count as repeats too. GPQA: the same items right and wrong as without DRY. Then tried
  (v19b, kept): DRY only on a retry after the repetition check fired. Not tried: a longer allowed repeat
  (dry_allowed_length ~6).

- **Memory written as what the person said** (answer_key.py, 2026-10-10): after the samples, "Earlier, the person
  asking the next question said:" with "Person: ..." / "Reply: ..." lines, instead of more "Question:/Answer:" lines.
  Meant for held-out "What is my job?" -> "I am a teacher." (the model answering about itself). Held-out unchanged
  (66/78: all three attempts still "I am a teacher."); old eval memory 11 -> 9/12: "What is my name?" now answered
  "Jaakko." on all three attempts, and the relevance check rejected it every time, even with the conversation shown
  (with quiz lines the retry used to say "Your name is Jaakko.", which it accepts). Reverted.

## IFBench: the checks and the retries (2026-10-12, simulated offline on a full run's logged attempts)
- **Cut instead of reject on the long route** (an attempt stopped by the repetition check cut back before the loop,
  accepted if the other checks pass): 51/300 vs the real 55 - of 107 changed prompts +3 -7. The retries after a
  repetition stop pass more often than the cut text. Not built.
- **Unchecked fallback = the first rejected attempt (cut), not the last:** 54 vs 55 (+1 -2). Not built.
- **Think first on the long-text route** (strat2 v21: a "Thinking:" line of up to 400 tokens before the response,
  samples with working + one small reasoning sample): GPQA small test hard 3/30 fixed, control 21/30 (9 broken) -
  net -6. The working was either the bare answer ("The correct answer is (C).", 26 characters) or confident nonsense
  ("The endoplasmic reticulum is the Golgi apparatus, and the Golgi apparatus is the mitochondrion. The correct answer
  is (D)."), and the response followed it. Switched off (strat2.LONG_THINK); IFBench not run.
- **Lettered choices answered by a rotated letter read** (strat2 v22, user's OK: code detects lines "(A) ...", "(B) ..."; the
  model's letter probabilities after "The answer is (" read once per rotation of the choices, summed per choice).
  Full GPQA: 67/198 rotated; the single unrotated read in the same run 71; generated answers 68-71. The picks even out
  (A 44, B 49, C 55, D 50 - single read: D 8, C 78) but accuracy doesn't move: with the position bias removed the
  model's guesses are spread differently, not better. (A first run read broken prompts - two choices glued onto one
  line - 62/198; fixed before this.) Switched off (strat2.CHOICES_ROUTE).

## Memory summary (2026-10-12; harness.compact_memory, `python memory_test.py`)
- **One notes prompt for 5 exchanges at once:** turned told facts into topics ("I play the cello" -> "We talked
  about the cello"), took the AI's replies as facts ("dumplings are... famous in China"), and copied its own sample
  ("the capital of Japan") in 3 of 6 compactions. long 13-14/18; replaced by one fact per user message (16-18/18).
- **The model shortening the whole summary at once:** never usable - cut off mid-sentence, switched to "He ...", or
  lost 8-12 names (Arjun, Sofia, Volvo, ...). A guard (no name/number lost, ends with a full stop) rejected it 9 times
  of 11; the 2 it passed silently lost facts ("You have three hives in the garden", Mateo's subject). Replaced by
  shortening one sentence at a time (also not safe: "Your roommate Yuki studies law" -> "Yuki studies law").
- **Duplicate rule "2/3 of the words already said":** left out "Aiko is a ... student in Osaka" (the name was the
  new word), "... and Mateo studies architecture", "... and are a beekeeper". Now: every word already said.
- **"Older of two similar sentences" with one shared word enough:** dropped "Your name is Priya." for the topic line
  "We talked about my name, my job, ..." (long 16/18). Now: at least 2 shared words, and the topic one goes.
- **Out-of-date check** (model yes/no for each new fact vs each older fact, >= 0.5, a YES removes the older): meant
  for "live in Bristol" -> "moved to Leeds". False YES often: removed "Your roommate Yuki studies law" (for a tutoring
  fact), "You have three hives" (for the move), "Your wife Elena and your sons... live in Bristol" (right about Bristol,
  but Elena's name went with it), and later "You moved to Leeds" itself (for a half-marathon fact). heldout 10 -> 8/17
  (with other changes; the city, wife's name and hives all lost to it). Removed.

- **Memory gate on the message alone** (strat2 v20: "does answering this message need the earlier conversation?",
  yes/no >= 0.5, no -> answered without memory; `memory_test.py fresh heldout --gate`): memory questions fresh
  15 -> 10/17, heldout 13 -> 11/17 ("What is my name?" 0.32-0.36 -> "I don't know. What's your name?"); general
  questions with memory present 20 -> 19/22 in both - memory wasn't hurting them. Scores overlap ("What do bees make?"
  0.58, "What am I cooking tonight?" 0.34). Switched off (strat2.MEMORY_GATE). Not tried: a gate shown the memory.

- **"Okay." saved as the reply that wasn't kept** (2026-10-12, so the user's message stays in memory): with 4 such
  exchanges in memory, 13 of 26 general answers were just "Okay." (general 9/22, without memory 19) - the same marker
  copying as 2026-10-09. Now the reply is None and the message is shown as a "You also said:" line (0/26 copies).

## Ruled out on principle (not tested)
- Detecting "answer with one word / only a number / yes or no" in the question and trimming the
  reply in code. Hard-coded task rules defeat the purpose: the model must figure it out itself.

## Considered and skipped
- Third-party language detectors (lingua-py, laya-multilingual): not needed yet. In 30 generations
  language switches were 6/30 Chinese, 0/30 French, all caught by the code check.
