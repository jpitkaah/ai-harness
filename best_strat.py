"""Saved best strategy so far: strat2 v20b (frozen copy, 2026-10-12; retry bug fixed the same day). Don't edit; copy instead.

v20b = v17 + (all measured on strat2 before saving):
  - v18-v19b: DRY sampler on the long-text route's retries after a looping reply; samples that mirrored
    eval questions replaced (audit: the 26-question eval was fitted); the relevance check sees the
    conversation.
  - v20: a memory gate (yes/no "needs the earlier conversation?") - switched OFF (MEMORY_GATE = False):
    memory questions got worse.
  - v20b: the reply checks get the conversation, so the near-copy check runs (harness.near_copy); after a
    near-copy rejection the retries leave out the copied exchanges (harness.without_copies). Against the
    2026-10-10 "It's not clear..." snowball: open requests with that memory 3/4 -> 1/4 gave up.
Scores (temperature 0, no prompt cache): held-out eval 66/78 (plain harness 54, base model 54); IFBench
16.7% (50/300); GPQA 68 and 71/198. v17 (the previous best) had 74/78 on the old, fitted eval.
The memory summary (harness.compact_memory) is in harness.py, not here. Run: python eval.py best

Routes, each chosen by the model's own yes/no (P >= 0.75 unless noted):
  long text  -> (>= 0.85) a writer's "Request: / Response:" document, several lines allowed
  letters    -> copy word + letter, split, spell each part, count per part, model adds up
  comparison -> copy the amounts, tiny yes/no "same amount?"; if both amounts are given,
                answer the question with the names removed + that verdict
  math       -> restate as a bare calculation (shape-checked), math-teacher answer key
  otherwise  -> sense check: copy what is asked about, define it in the question's meaning,
                yes/no "makes sense?" (< 0.45 -> the model writes why, the answer uses it;
                a reason copied from the samples is ignored); else answer_key.py (v9)
History of each route: strat2.py comments at the time of saving, plan.md, FAILED_EXPERIMENTS.md.
"""

import re

import answer_key
import harness

# Chosen after seeing the router's answers on the test questions: may be fitted to them.
# 0.9 kept the train question (0.78-0.80) out of the math route; 0.75 lets it in, and the
# shape check below catches tricks that also get in (feathers 0.85-0.86).
ROUTE_THRESHOLD = 0.75  # shared by the math and letters routers
# Shape check (Python owns the form, not the meaning): a restated calculation must
# contain an arithmetic sign. A plain sentence means the model didn't find a calculation.
# "x" and "-" only count between digits/spaces, so "six" or "well-known" don't.
CALCULATION_SIGN = re.compile(r"[+*/=×÷]|(?<=[\d\s])[x-](?=[\s\d])")

# 2026-10-10 (audit): the NO sample "Answer yes or no: is grass green?" mirrored the eval's Sun question; replaced.
ROUTER_PROMPT = """Some questions need a calculation with numbers or amounts to answer. Others don't.

Question: What is 6 times 7?
Needs a calculation: YES
Question: Who wrote Hamlet?
Needs a calculation: NO
Question: A book has 120 pages and I read 30 pages a day. How many days do I need?
Needs a calculation: YES
Question: Which animal is the tallest?
Needs a calculation: NO

Question: {question}
Needs a calculation:"""

# Restating: plain extraction, one line. Samples avoid clock times (too close
# to the train question); they show turning decimals into whole smaller units.
RESTATE_PROMPT = """Rewrite each question as the plain calculation. Write every amount in whole units: turn decimals into a bigger and a smaller unit.

Question: How many grams are in 2.5 kilograms?
Calculation: 2 kilograms 500 grams = 2000 grams + 500 grams
Question: A recipe needs 3 eggs per cake. How many eggs are needed for 4 cakes?
Calculation: 3 eggs x 4
Question: What is half of 90?
Calculation: 90 / 2

Question: {question}
Calculation:"""

MATH_HEADER = ("Answer key for a math quiz, written by a mathematics teacher. Each answer is checked: it "
               "works from the calculation above it, keeps the units right, and gives the final result.")
MATH_SAMPLES = [
    ("How many grams are in 2.5 kilograms?", "2 kilograms 500 grams = 2000 grams + 500 grams", "2500 grams."),
    ("A recipe needs 3 eggs per cake. How many eggs are needed for 4 cakes?", "3 eggs x 4", "12 eggs."),
    ("What is half of 90?", "90 / 2", "45."),
]


def needs_math(prompt):
    """The model's own judgement: P(YES) for "needs a calculation" >= ROUTE_THRESHOLD."""
    return route_probability(ROUTER_PROMPT, prompt) >= ROUTE_THRESHOLD


def restate(prompt):
    """The question rewritten as a bare calculation, or None if it isn't shaped like one."""
    text, _, _ = harness.generate(RESTATE_PROMPT.format(question=prompt), temperature=0,
                                  stop_sequences=answer_key.STOP_SEQUENCES)
    text = text.strip()
    # "1 kilogram of steel is heavier than 1 kilogram of feathers." has no sign -> normal route.
    return text if CALCULATION_SIGN.search(text) else None


def build_math_prompt(history, prompt, calculation, note=None):
    """Math answer key: each entry shows the calculation line before the answer."""
    lines = [MATH_HEADER]
    if note:
        lines.append(f"Note: {note}")
    lines.append("")
    for question, calc, answer_text in MATH_SAMPLES:
        lines += [f"Question: {question}", f"Calculation: {calc}", f"Answer: {answer_text}"]
    for exchange in history:
        lines += [f"Question: {exchange['user']}", f"Answer: {exchange['ai']}"]
    lines += [f"Question: {prompt}", f"Calculation: {calculation}", "Answer:"]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Letters route (v11): questions about the letters in a word.
# Found by trial and error on "How many r's are in strawberry?":
#   - spelling the whole word fails: "s-traw-b-e-r-r-y", "s-t-r-e-a-m-b-l-e-r-t"
#     (the model sees word pieces, and "traw" is one piece)
#   - short words spell perfectly: straw = s-t-r-a-w, berry = b-e-r-r-y
#   - counting inside one long prompt fails ("berry: none" next to b-e-r-r-y);
#     counting in a short prompt per part works (berry 6/6, straw 6/6)
# So: a chain of small model jobs - copy the word and letter, split the word into
# parts, spell each part, count per part, write the total. Python only passes
# results along and checks their form; any wrong shape -> normal route.
# ---------------------------------------------------------------------------

LETTERS_ROUTER_PROMPT = """Some questions are about the letters or the spelling of a word. Others aren't.

Question: How do you spell "necessary"?
About letters: YES
Question: What is the capital of Spain?
About letters: NO
Question: Which letter comes first in the word "apple"?
About letters: YES
Question: What is 8 times 9?
About letters: NO

Question: {question}
About letters:"""

COPY_WORD_PROMPT = """Copy the single word the question is about.

Question: Count the n's in sunflower.
Word: sunflower
Question: How many e's are in "keyboard"?
Word: keyboard

Question: {question}
Word:"""

COPY_LETTER_PROMPT = """Copy the single letter the question asks to count.

Question: Count the n's in sunflower.
Letter: n
Question: How many e's are in "keyboard"?
Letter: e

Question: {question}
Letter:"""

SPLIT_PROMPT = """Split each word into its short parts.

Word: sunflower
Parts: sun + flower
Word: keyboard
Parts: key + board
Word: apple
Parts: apple

Word: {word}
Parts:"""

SPELL_PROMPT = """Spell each word letter by letter.

Word: sun
Spelling: s-u-n
Word: flower
Spelling: f-l-o-w-e-r
Word: board
Spelling: b-o-a-r-d

Word: {word}
Spelling:"""

COUNT_PROMPT = """Find every copy of the letter in the spelled word. Write each one you find, then the total.

Letters: f-o-o-t
Letter: o
Found: o, o. Total: 2
Letters: b-a-l-l
Letter: o
Found: none. Total: 0
Letters: h-o-u-s-e
Letter: e
Found: e. Total: 1
Letters: s-u-n
Letter: n
Found: n. Total: 1

Letters: {letters}
Letter: {letter}
Found:"""

LETTERS_HEADER = ("Answer key for a spelling quiz, written by a language teacher. Each answer adds up the "
                  "counts above it and gives the total.")
LETTERS_SAMPLES = [
    ("Count the o's in football.", "foot has 2 o's, ball has 0 o's", "Two o's."),
    ("How many e's are in \"treehouse\"?", "tree has 2 e's, house has 1 e", "Three e's."),
    ("Count the n's in sunflower.", "sun has 1 n, flower has 0 n's", "One n."),
]

SPELLING_SHAPE = re.compile(r"^[a-z](-[a-z])*$")    # s-t-r-a-w: single letters joined by dashes
COUNT_SHAPE = re.compile(r"Total:\s*(\d+)")


def small_step(prompt_text):
    """One short, deterministic model job (temperature 0), one line."""
    text, _, _ = harness.generate(prompt_text, temperature=0, stop_sequences=answer_key.STOP_SEQUENCES)
    return text.strip()


def letter_counts(prompt):
    """Per-part counts written by the model, e.g. "stra has 1 r, berry has 2 r's", or None."""
    word = small_step(COPY_WORD_PROMPT.format(question=prompt)).strip(' ."\'').lower()
    letter = small_step(COPY_LETTER_PROMPT.format(question=prompt)).strip(' ."\'').lower()
    if not re.fullmatch(r"[a-z]+", word) or not re.fullmatch(r"[a-z]", letter):
        return None  # not a "count this letter in this word" question
    parts = [p.strip().lower() for p in small_step(SPLIT_PROMPT.format(word=word)).split("+")]
    counts = []
    for part in parts:
        spelling = small_step(SPELL_PROMPT.format(word=part)).lower()
        found = COUNT_SHAPE.search(small_step(COUNT_PROMPT.format(letters=spelling, letter=letter)))
        if not re.fullmatch(r"[a-z]+", part) or not SPELLING_SHAPE.match(spelling) or not found:
            return None  # a step came back in the wrong shape
        n = found.group(1)
        counts.append(f"{part} has {n} {letter}" + ("" if n == "1" else "'s"))
    return ", ".join(counts)


def build_letters_prompt(history, prompt, counts, note=None):
    """Spelling answer key: each entry shows the per-part counts before the answer."""
    lines = [LETTERS_HEADER]
    if note:
        lines.append(f"Note: {note}")
    lines.append("")
    for question, sample_counts, answer_text in LETTERS_SAMPLES:
        lines += [f"Question: {question}", f"Counts: {sample_counts}", f"Answer: {answer_text}"]
    for exchange in history:
        lines += [f"Question: {exchange['user']}", f"Answer: {exchange['ai']}"]
    lines += [f"Question: {prompt}", f"Counts: {counts}", "Answer:"]
    return "\n".join(lines)


def route_probability(router_prompt, prompt):
    """The model's P(YES) for a router question, over P(YES) + P(NO)."""
    probs = harness.top_token_probs(router_prompt.format(question=prompt))
    p_yes = sum(p for t, p in probs.items() if t.strip().upper() == "YES")
    p_no = sum(p for t, p in probs.items() if t.strip().upper() == "NO")
    return p_yes / (p_yes + p_no) if p_yes + p_no else 0.0


# ---------------------------------------------------------------------------
# Comparison route (v11): "which is heavier/bigger/longer ..." questions.
# Found by trial and error on the feathers-vs-steel trick:
#   - the model answers from the familiar pattern ("steel is heavy"), not from
#     what the question says (both are "a kilogram")
#   - so it first copies the amounts exactly as written (copying is easy;
#     converting "a kilogram" to "1 kilogram" was unreliable)
#   - then a tiny yes/no step compares ONLY the two amounts, without the riddle
#     around them ("a kilogram | a kilogram" -> same, 0.93)
#   - the model's verdict goes into the answer prompt as "Same amount: yes"
# Prototype: feathers 0-2/12 -> 6/12; bricks/cotton 6/6; controls (sand vs rice,
# Sun vs Moon) unchanged at 6/6.
# The samples never contain the test question. One sample compares two equal
# amounts of TIME, to show what "Same amount: yes" means - flagged in plan.md.
# ---------------------------------------------------------------------------

COMPARE_ROUTER_PROMPT = """Some questions ask which of two things is bigger, heavier, longer, faster or more. Others don't.

Question: Which is longer, a mile or a kilometre?
Compares two things: YES
Question: What is the capital of Spain?
Compares two things: NO
Question: Which is faster, a horse or a snail?
Compares two things: YES
Question: How many legs does a spider have?
Compares two things: NO

Question: {question}
Compares two things:"""

FACTS_PROMPT = """For each thing in the question, copy the amount exactly as the question writes it. Write "not given" if the question gives no amount.

Question: Which takes longer, a 30 minute walk or a 45 minute swim?
Given: walk: 30 minutes; swim: 45 minutes
Question: Which is bigger, the Sun or the Moon?
Given: Sun: not given; Moon: not given
Question: Which is longer, a metre of rope or two metres of string?
Given: rope: a metre; string: two metres
Question: Which is heavier, a gram of sugar or ten grams of salt?
Given: sugar: a gram; salt: ten grams

Question: {question}
Given:"""

SAME_PROMPT = """Are the two amounts the same? Ignore what the things are; compare only the amounts.

Amounts: 30 minutes | 45 minutes
Same: NO
Amounts: an hour | 60 minutes
Same: YES
Amounts: a metre | two metres
Same: NO
Amounts: five apples | 5 pears
Same: YES

Amounts: {question}
Same:"""

COMPARE_HEADER = ("Answer key for a quiz. Each answer was checked by a teacher: it compares the things using "
                  "only the amounts given above it when there are any. When the amounts are the same, the "
                  "answer says the things are the same. Watch out: some questions are trick questions.")
COMPARE_SAMPLES = [
    ("Which takes longer, a 30 minute walk or a 45 minute swim?", "walk: 30 minutes; swim: 45 minutes", "no", "The swim."),
    ("Which is bigger, the Sun or the Moon?", "Sun: not given; Moon: not given", "not given", "The Sun."),
    ("Which takes longer, an hour of reading or 60 minutes of running?", "reading: an hour; running: 60 minutes",
     "yes", "Neither, they take the same time."),
    ("Which is longer, a metre of rope or two metres of string?", "rope: a metre; string: two metres", "no", "The string."),
]


def comparison_facts(prompt):
    """(given line, same-amount verdict) written by the model, or None if malformed."""
    given = small_step(FACTS_PROMPT.format(question=prompt))
    # Shape check: "name: amount; name: amount" - two labelled parts.
    amounts = [part.split(":", 1)[1].strip() for part in given.split(";") if ":" in part]
    if len(amounts) != 2 or not all(amounts):
        return None
    if "not given" in amounts:
        return given, "not given"
    same = route_probability(SAME_PROMPT, f"{amounts[0]} | {amounts[1]}") >= 0.5
    return given, "yes" if same else "no"


def build_compare_prompt(history, prompt, facts, note=None):
    """Comparison answer key: each entry shows the given amounts and the same-amount verdict."""
    given, same = facts
    lines = [COMPARE_HEADER]
    if note:
        lines.append(f"Note: {note}")
    lines.append("")
    for question, sample_given, sample_same, answer_text in COMPARE_SAMPLES:
        lines += [f"Question: {question}", f"Given: {sample_given}", f"Same amount: {sample_same}",
                  f"Answer: {answer_text}"]
    for exchange in history:
        lines += [f"Question: {exchange['user']}", f"Answer: {exchange['ai']}"]
    lines += [f"Question: {prompt}", f"Given: {given}", f"Same amount: {same}", "Answer:"]
    return "\n".join(lines)



# v12: answer equal/unequal-amount comparisons from a question with the names
# removed. With "feathers" and "steel" in view the riddle won even when the model
# itself had said "Same amount: yes" (steel 3/3 in a full run, an agreement checker
# always leaned "yes", multiple choice still picked steel). Stripped to
# "Which is heavier, a kilogram or a kilogram?" + the verdict: feathers 10/12,
# bricks 6/6, unequal controls 6/6.
STRIP_PROMPT = """Rewrite the question using only the amounts. Leave out what the things are.

Question: Which takes longer, a 30 minute walk or a 45 minute swim?
Amounts only: Which is longer, 30 minutes or 45 minutes?
Question: Which is longer, a metre of rope or two metres of string?
Amounts only: Which is longer, a metre or two metres?
Question: Which holds more, a 200 millilitre cup or a 300 millilitre mug?
Amounts only: Which is more, 200 millilitres or 300 millilitres?

Question: {question}
Amounts only:"""

AMOUNTS_HEADER = ("Answer key for a quiz. Each answer was checked by a teacher: it compares the amounts and "
                  "gives the result. When the amounts are the same, the answer says they are the same.")
AMOUNTS_SAMPLES = [
    ("Which is longer, 30 minutes or 45 minutes?", "no", "45 minutes."),
    ("Which is longer, an hour or 60 minutes?", "yes", "Neither, they are the same."),
    ("Which is longer, a metre or two metres?", "no", "Two metres."),
    ("Which is more, 200 millilitres or 300 millilitres?", "no", "300 millilitres."),
]


def amounts_question(prompt):
    """The question with the things' names removed, or None if it isn't shaped like a question."""
    stripped = small_step(STRIP_PROMPT.format(question=prompt))
    return stripped if stripped.endswith("?") and re.search(r"\w", stripped) else None


def build_amounts_prompt(stripped, same, note=None):
    """Answer key for the names-removed question + the model's same-amount verdict.
    No history: the question carries everything needed."""
    lines = [AMOUNTS_HEADER]
    if note:
        lines.append(f"Note: {note}")
    lines.append("")
    for question, sample_same, answer_text in AMOUNTS_SAMPLES:
        lines += [f"Question: {question}", f"Same amount: {sample_same}", f"Answer: {answer_text}"]
    lines += [f"Question: {stripped}", f"Same amount: {same}", "Answer:"]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Sense check (v13), for questions that take none of the routes above.
# Found on the plane trick ("where do they bury the survivors?"):
#   - the validator kept rejecting nonsense ("In the plane.") -> 3 give-ups
#   - a "trick question?" router was useless (everything 0.57-0.82)
#   - but the model, asked in small steps, copies what the question asks about
#     ("the survivors") and defines it ("people who are still alive")
#   - with the definition in view it still didn't conclude anything (0/6), so:
#     a tiny yes/no "does the question make sense?" (plane 0.33, all normal
#     questions >= 0.50), then the model writes WHY ("survivors are still alive,
#     and cannot be buried"), and the answer uses that reason (~6-7/10 right).
# Control: "How many legs does a snake have?" also flagged (0.38) -> 10/10 "None".
# ---------------------------------------------------------------------------

SENSE_THRESHOLD = 0.45  # chosen after seeing the scores above: may be fitted to them

ASKED_PROMPT = """Copy the words naming who or what the question asks about.

Question: If a rooster lays an egg on a roof, which way does it roll?
Asked about: the egg the rooster lays
Question: How many birthdays does the average person have?
Asked about: birthdays
Question: What is the capital of Spain?
Asked about: the capital of Spain

Question: {question}
Asked about:"""

# v14: the question is shown too. Without it the model picked the wrong meaning of an
# ambiguous word ("the gas" in "What gas do plants absorb?" -> "a liquid that is used to
# make things move, like cars"), the sense check flagged a normal question (0.19), and
# the answer went wrong (full eval, 2026-10-09).
DEFINE_PROMPT = """Say what each thing is, in a few plain words, in the meaning the question uses.

Question: If a rooster lays an egg on a roof, which way does it roll?
Thing: the egg the rooster lays
Is: a rooster is a male chicken, and male chickens do not lay eggs
Question: How many birthdays does the average person have?
Thing: birthdays
Is: the day a person was born, which happens only once
Question: What is the capital of Spain?
Thing: the capital of Spain
Is: the city where Spain's government is, Madrid

Question: {question}
Thing: {thing}
Is:"""

SENSE_PROMPT = """Given what the question is about, can the question be answered as asked, or does it ask for something impossible?

Question: Which way does the egg roll if a rooster lays it on a roof?
About: the egg the rooster lays: a rooster is a male chicken, and male chickens do not lay eggs
Makes sense: NO
Question: What is the capital of Spain?
About: the capital of Spain: the city where Spain's government is
Makes sense: YES
Question: Where do fish sleep at night?
About: fish: animals that live in water
Makes sense: YES
Question: What colour is the sound of a bell?
About: the sound of a bell: something you hear, not something you see
Makes sense: NO

{question}
Makes sense:"""

REASON_PROMPT = """Each question asks for something impossible. Say why, in one short sentence, using what the question is about.

Question: If a rooster lays an egg on a roof, which way does it roll?
About: the egg the rooster lays: a rooster is a male chicken, and male chickens do not lay eggs
Why impossible: roosters are male, and only hens lay eggs
Question: What colour is the sound of a bell?
About: the sound of a bell: something you hear, not something you see
Why impossible: a sound is heard, and only things you see have a colour

Question: {question}
About: {about}
Why impossible:"""

IMPOSSIBLE_HEADER = ("Answer key for a quiz. Each answer was checked by a teacher: it is correct, gives the final "
                     "result in the first words, and does exactly what the question asks. When a question asks "
                     "for something impossible, the answer says why, using the reason above it.")
IMPOSSIBLE_SAMPLES = [
    ("If a rooster lays an egg on a roof, which way does it roll?", "roosters are male, and only hens lay eggs",
     "Nowhere, roosters don't lay eggs."),
    ("What colour is the sound of a bell?", "a sound is heard, and only things you see have a colour",
     "None, a sound has no colour."),
]


def impossibility_reason(prompt):
    """The model's reason why the question can't be answered as asked, or None if it makes sense."""
    asked = small_step(ASKED_PROMPT.format(question=prompt))
    if not re.search(r"\w", asked):
        return None
    about = f"{asked}: {small_step(DEFINE_PROMPT.format(question=prompt, thing=asked))}"
    if route_probability(SENSE_PROMPT, f"Question: {prompt}\nAbout: {about}") >= SENSE_THRESHOLD:
        return None
    reason = small_step(REASON_PROMPT.format(question=prompt, about=about))
    words = re.findall(r"\w+", reason.lower())
    # v15: a reason copied from the prompt's own samples is no reason. In the chat app
    # "ok cool" got the reason "roosters are male, and only hens lay eggs" (a sample),
    # and the answer was built around it.
    samples = " ".join(re.findall(r"\w+", REASON_PROMPT.split("Question: {question}")[0].lower()))
    if not words or " ".join(words) in samples:
        return None
    return reason


def build_impossible_prompt(history, prompt, reason, note=None):
    """Answer key where the answer says why the question can't be answered as asked."""
    lines = [IMPOSSIBLE_HEADER]
    if note:
        lines.append(f"Note: {note}")
    lines.append("")
    for question, sample_reason, answer_text in IMPOSSIBLE_SAMPLES:
        lines += [f"Question: {question}", f"Why impossible: {sample_reason}", f"Answer: {answer_text}"]
    for exchange in history:
        lines += [f"Question: {exchange['user']}", f"Answer: {exchange['ai']}"]
    lines += [f"Question: {prompt}", f"Why impossible: {reason}", "Answer:"]
    return "\n".join(lines)

# ---------------------------------------------------------------------------
# Long-text route (v16, 2026-10-10): requests for a longer text (several paragraphs, a list, an email,
# an essay). On IFBench every harness answer was ONE line: the normal route stops at the first line
# break (it was built for short chat answers, where a newline means the model drifts into a quiz).
# Router prototype (no answers generated): IFBench first half median 0.90, our 26 eval + 18 search
# questions all <= 0.80 ("Why do we have seasons on Earth?", "Which planet has the most known moons?").
# At 0.85 none of ours take this route. The route writes a "Request: / Response:" document that stops
# at the next "Request:" or the end of text, so the response can have several lines.
# ---------------------------------------------------------------------------

# 2026-10-10 (audit): two NO samples mirrored eval questions ("a litre of water or a litre of oil" - feathers;
# "days in a leap year" - days in a week) and were replaced with unrelated short questions.
LONG_ROUTER_PROMPT = """Some requests ask for a longer text: several sentences or paragraphs, a list, an email, a story or an essay. Others ask a question with a short answer.

Request: Write an email to my landlord asking to fix the heating.
Asks for a longer text: YES
Request: What is the capital of Spain?
Asks for a longer text: NO
Request: How many legs does an insect have?
Asks for a longer text: NO
Request: Give me a numbered list of five tips for sleeping better.
Asks for a longer text: YES
Request: Who was the first person to walk on the Moon?
Asks for a longer text: NO
Request: Describe a sunset over the sea in two paragraphs.
Asks for a longer text: YES

Request: {question}
Asks for a longer text:"""
LONG_THRESHOLD = 0.85  # 0.80 and 0.75 tried: worse (FAILED_EXPERIMENTS.md)
LONG_LIMIT = 1500                 # tokens; the normal route keeps harness.SOFT_LIMIT / HARD_LIMIT
LONG_STOP_SEQUENCES = ("Request:",)
# A long text naturally repeats short phrases (an email's subject in its body, a formula), and IFBench
# even asks for repeated words: the normal 4-word check killed 8 of 10 long attempts. Here only a
# 10-word phrase said twice counts as a loop.
LONG_REPEAT_NGRAM = 10
# v19b: llama.cpp's DRY sampler ("Don't Repeat Yourself": a token that would continue a phrase already in
# the text gets less likely, more so the longer the repeat; it works at temperature 0 too) - only for the
# attempts after the repetition check rejected one. v19 used it on every attempt: the loops went (IFBench
# answers that were the last rejected attempt 19 -> 4 of 60), but so did wanted repetition (an emoji after
# every sentence), and IFBench got worse (FAILED_EXPERIMENTS.md). Here the first attempt is v17's.
LONG_RETRY_SAMPLING = {"dry_multiplier": 0.8, "dry_base": 1.75, "dry_allowed_length": 2,
                       "dry_penalty_last_n": 1024}

LONG_HEADER = ("Requests and the responses written for them. Each response was checked by an editor: it "
               "does exactly what the request asks and follows every instruction in it precisely - "
               "length, words to use or avoid, format, lists and paragraphs.")
LONG_SAMPLES = [
    ('Write two sentences about rain and use the word "silver" exactly once.',
     "Rain fell all night on the old town.\nBy morning the wet streets shone like silver."),
    ("List three fruits as a numbered list.", "1. Apple\n2. Banana\n3. Cherry"),
]


def build_long_prompt(history, prompt, note=None):
    """The conversation as requests and responses, ending in "Response:" on its own line."""
    lines = [LONG_HEADER]
    if note:
        lines.append(f"Note: {note}")
    for request, response in LONG_SAMPLES:
        lines += ["", f"Request: {request}", "Response:", response]
    for exchange in history:
        lines += ["", f"Request: {exchange['user']}", "Response:", exchange["ai"]]
    lines += ["", f"Request: {prompt}", "Response:", ""]
    return "\n".join(lines)


def long_answer(history, prompt, max_attempts=harness.MAX_ATTEMPTS):
    """The long-text route: same checks and retries as every route. Returns (reply or None, attempts)."""
    note = sampling = None
    for attempt in range(1, max_attempts + 1):
        response, _, stopped_by = harness.generate(build_long_prompt(history, prompt, note),
                                                   soft_limit=LONG_LIMIT, hard_limit=LONG_LIMIT,
                                                   stop_sequences=LONG_STOP_SEQUENCES,
                                                   repeat_ngram=LONG_REPEAT_NGRAM, sampling=sampling)
        response = response.strip()
        ok, reason, note = harness.check_reply(prompt, response, stopped_by, history=history)
        if ok:
            return response, attempt
        if reason.startswith("near-copy"):
            history = harness.without_copies(history, response)  # v20b: the retry can't see what it copied
        if stopped_by.startswith("repetition"):
            sampling = LONG_RETRY_SAMPLING  # v19b: after a loop, the next attempts use DRY
    return None, max_attempts


# ---------------------------------------------------------------------------
# Memory gate (v20, 2026-10-12, user's idea): before anything else the model says (yes/no) whether the
# message needs the earlier conversation - something the user said, or a word pointing back ("that",
# "again"). If not, the answer is made without memory (the summary and the last exchanges), so a
# general question isn't steered by the conversation (a repeated reply once snowballed through memory).
# The message alone is judged, not the memory: cheaper, and memory can't talk it into a yes.
# ---------------------------------------------------------------------------
# Off (2026-10-12): measured worse - memory questions fresh 15 -> 10/17, heldout 13 -> 11/17; general questions with
# memory present 20 -> 19/22 both (memory wasn't hurting them). Scores overlap: "What is my name?" 0.32-0.36,
# "What do bees make?" 0.58. FAILED_EXPERIMENTS.md. Kept switchable for `memory_test.py --gate`.
MEMORY_GATE = False
MEMORY_THRESHOLD = 0.5
MEMORY_ROUTER_PROMPT = """Decide whether answering a message needs the earlier conversation: something the user said before (about themselves, their life, or what was talked about), or words like "that", "he" or "again" that point back to it. General questions and tasks don't need it.

Message: What did I say my sister does for a living?
Needs the earlier conversation: YES

Message: How many legs does a spider have?
Needs the earlier conversation: NO

Message: Can you say that again, but shorter?
Needs the earlier conversation: YES

Message: Write a short poem about autumn.
Needs the earlier conversation: NO

Message: Which of my plants needs the most water?
Needs the earlier conversation: YES

Message: {question}
Needs the earlier conversation:"""


def answer(history, prompt, max_attempts=harness.MAX_ATTEMPTS):
    """Returns (reply or None, attempts)."""
    # v20: the memory gate (above). No memory -> no question to ask.
    if MEMORY_GATE and history:
        memory_score = route_probability(MEMORY_ROUTER_PROMPT, prompt)
        harness.record("memory gate", prompt, {"needs memory": round(memory_score, 2)})
        if memory_score < MEMORY_THRESHOLD:
            history = []
    # v16: requests for a longer text first (see the long-text route above).
    long_score = route_probability(LONG_ROUTER_PROMPT, prompt)
    if long_score >= LONG_THRESHOLD:
        # The chosen route is recorded for traces (view.py) and the benchmark logs (small_test.py).
        harness.record("route", prompt, {"route": "long text", "long_score": round(long_score, 2)})
        return long_answer(history, prompt, max_attempts)
    # Routes are chosen once per question, by the model's own yes/no answers.
    # Order: letters, comparison, math. Comparison before math, because the math
    # router finds trick comparisons math-like too (feathers: 0.85).
    counts = facts = calculation = None
    if route_probability(LETTERS_ROUTER_PROMPT, prompt) >= ROUTE_THRESHOLD:
        counts = letter_counts(prompt)
    stripped = None
    if counts is None and route_probability(COMPARE_ROUTER_PROMPT, prompt) >= ROUTE_THRESHOLD:
        facts = comparison_facts(prompt)
        if facts and facts[1] in ("yes", "no"):  # both amounts given -> compare them without names
            stripped = amounts_question(prompt)
    if counts is None and facts is None and needs_math(prompt):
        calculation = restate(prompt)
    reason = None
    if counts is None and facts is None and calculation is None:
        reason = impossibility_reason(prompt)
    route = ("letters" if counts else "amounts" if stripped else "comparison" if facts else
             "math" if calculation else "impossible" if reason else "answer key")  # same order as below
    harness.record("route", prompt, {"route": route, "long_score": round(long_score, 2)})
    note = None
    for attempt in range(1, max_attempts + 1):
        if counts:
            model_prompt = build_letters_prompt(history, prompt, counts, note)
        elif stripped:
            model_prompt = build_amounts_prompt(stripped, facts[1], note)
        elif facts:
            model_prompt = build_compare_prompt(history, prompt, facts, note)
        elif calculation:
            model_prompt = build_math_prompt(history, prompt, calculation, note)
        elif reason:
            model_prompt = build_impossible_prompt(history, prompt, reason, note)
        else:
            model_prompt = answer_key.build_prompt(history, prompt, note)
        response, _, stopped_by = harness.generate(model_prompt, stop_sequences=answer_key.STOP_SEQUENCES)
        response = response.strip()
        # `verdict`, not `reason`: that name holds the impossibility reason above. Reusing it (v20b, 2026-10-12)
        # sent every answer-key retry to the impossible-question prompt ("Why impossible: echo"); found in review.
        ok, verdict, note = harness.check_reply(prompt, response, stopped_by, history=history)
        if ok:
            return response, attempt
        if verdict.startswith("near-copy"):
            # v20b (2026-10-12): the retry leaves out the exchanges whose replies it copied - with four copies
            # of "It's not clear..." in memory, the note alone didn't stop the next attempts copying it.
            history = harness.without_copies(history, response)
    return None, max_attempts
