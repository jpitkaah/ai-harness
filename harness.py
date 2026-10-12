"""A tiny harness around a local llama server.

Rule 1 - generation limits: the harness streams tokens and decides itself
when to stop: on EOS, at the first sentence end after SOFT_LIMIT tokens,
or unconditionally at HARD_LIMIT. We deliberately do NOT send `max_tokens`
to the server: the point is that the harness, not the server, enforces it.

Rule 2 - validator: after generating, the harness asks the same model
whether the response addresses the user's message. YES -> show it,
NO -> reject it.

Rule 3 - retry loop: on NO, generate a fresh candidate and judge
again, up to MAX_ATTEMPTS times. The first YES goes to the user.

Rule 4 - repetition breaker: while streaming, the harness watches for a
phrase of REPEAT_NGRAM words appearing twice. If it does, generation is
killed on the spot and the candidate counts as rejected, so the
retry loop tries again.

Rule 5 - feedback on retry: a rejected attempt tells the next one *why*
it failed, via a note in front of the prompt. A retry becomes a revision
instead of a blind re-roll.

Rule 6 - stop sequences: generation stops at a newline, where this model
usually drifts into quizzes. The text is cut just before it.

Rule 7 - language guard: if the response switched to Chinese/Japanese/Korean
or French while the user's prompt didn't use it, the harness rejects it
without asking the model.

Rule 8 - memory: the last MEMORY_SIZE exchanges (user message + accepted AI
reply) are kept in memory.json and put in front of every prompt as a
"User: ... / AI: ..." transcript, so the model can refer back to them.
Older exchanges are folded by the model into a short summary (compact_memory),
which goes in front of them as one more exchange.

Ideas that were tested and made things worse are in FAILED_EXPERIMENTS.md.
"""

import difflib
import itertools
import json
import math
import os
import re
import secrets
import shutil
import sys
import textwrap
import threading
import urllib.request
from datetime import datetime
from pathlib import Path

# Windows consoles default to a legacy encoding that crashes on characters
# like Chinese text, which Qwen sometimes produces. Force UTF-8 output.
sys.stdout.reconfigure(encoding="utf-8")

# 127.0.0.1, not "localhost": on Windows "localhost" first tries IPv6 (::1) and waits
# ~2 s per request before falling back, since the server only listens on 127.0.0.1.
# Measured 2026-10-09: 2.07 s vs 0.02 s per call - most of every run's time.
SERVER_URL = "http://127.0.0.1:8090/v1/completions"
MODEL = "QuantFactory/Qwen2.5-3B-GGUF:Q4_K_M"
# Back to 50/100 (2026-10-10): long writing tasks now take strat2's long-text route, which sets
# its own limit (raised to 4000 here for one IFBench run: 14.7% -> 16.7%, still one line per answer).
# Temperature of final answers (user's rule, 2026-10-10): 0 = the model's most likely reply, so tests
# give the same answers every run and versions can be compared accurately. Sampled at 0.8, full-eval
# runs swung by up to 8 points and GPQA was mostly luck (raw 30.3% and v16 26.8% were right on mostly
# different questions; v16 at 0: 36.9%). Only the chat app raises it to USER_TEMPERATURE, so the user
# gets a little variety. Small steps (routers, copies, the validator) always use 0.
ANSWER_TEMPERATURE = 0
USER_TEMPERATURE = 0.8
# llama.cpp's prompt cache reuses the already computed start of the previous prompt. Fast, but the numbers
# then depend on which request came before, so temperature-0 answers change between runs. IFBench control
# set, two runs back to back (2026-10-10): identical replies 15/30 with the cache, 24/30 without; prompts
# whose pass/fail flipped 3 vs 1; ~30% slower. Tests run without it; the chat app turns it on (it samples).
CACHE_PROMPT = False

SOFT_LIMIT = 50   # after this many tokens, stop at the next sentence end
HARD_LIMIT = 100  # absolute ceiling, even if the sentence never ends
SENTENCE_ENDS = (".", "!", "?")

# Generation stops when the text contains one of these. The text is cut
# just before it. A newline is where this model usually drifts into
# quizzes and worksheets, so we stop there.
STOP_SEQUENCES = ("\n",)

# The validator only needs one word, so it gets one token. After that,
# the base model would just keep writing (e.g. copying the response).
VALIDATOR_LIMIT = 1

# How many candidates to try before giving up.
MAX_ATTEMPTS = 3

# A run of this many words appearing twice counts as repetition.
REPEAT_NGRAM = 4

# Memory: how many past exchanges (user message + AI reply) every prompt shows (10 since 2026-10-12, was 5).
# Older ones aren't lost: memory.json holds up to MEMORY_SIZE + COMPACT_BATCH exchanges, and when it is full
# the model folds the oldest COMPACT_BATCH into a summary of at most SUMMARY_LIMIT tokens (user's design,
# 2026-10-12). Until then those older ones wait unseen ("ghost" exchanges).
MEMORY_SIZE = 10
COMPACT_BATCH = 5
SUMMARY_LIMIT = 200
MEMORY_FILE = Path(__file__).parent / "memory.json"
TOKENIZE_URL = "http://127.0.0.1:8090/tokenize"  # llama.cpp's own tokenizer: exact token counts

# Logging: every attempt (accepted or rejected) becomes one JSON line in
# logs/YYYY-MM-DD.jsonl. One line per attempt makes it easy to count later
# which checks fire most.
LOG_DIR = Path(__file__).parent / "logs"

# Plain text ending in "Answer:" so the very next token is the verdict.
# (Qwen's chat format made the base model ramble "Assistant's message: ..." first.)
# "not whether it is correct": the validator doesn't see the memory, so
# without this it rejected "Your name is Jaakko." because it couldn't verify it.
# (Asking it to also check the language made it answer NO to everything, so
# the language check is done in code instead: see language_switch.)
# The last sentence stops it rejecting honest "I don't know your favourite
# food" replies (tested: 15/15 cases vs 11/15 without it).
# {conversation} (2026-10-10): the recent conversation when the caller passes one, else empty (the prompt is then
# exactly as before). Without it a correct bare "Jaakko." to "What is my name?" was judged unrelated.
VALIDATOR_PROMPT = """A user sent a message and an AI replied. Judge only whether the reply is related to the user's message, not whether it is correct. A polite reply saying the AI does not know or cannot answer also counts as related.

{conversation}User's message: {prompt}
AI's reply: {response}

Is the reply related to the user's message? Answer YES or NO.
Answer:"""

# The base model only continues text. Written as a transcript ending in
# "AI:", the most natural continuation is the AI's reply. The newline stop
# sequence then ends it before the model writes the next "User:" line itself.
CHAT_HEADER = "A conversation between a user and a helpful AI assistant."


def build_prompt(history, prompt, note=None):
    """Transcript of past exchanges + the new message, ending in "AI:"."""
    lines = [CHAT_HEADER]
    # On a retry, the note tells the model why the previous reply failed.
    if note:
        lines.append(f"Note: {note}")
    lines.append("")
    for exchange in history:
        lines.append(f"User: {exchange['user']}")
        lines.append(f"AI: {exchange['ai']}")
    lines.append(f"User: {prompt}")
    lines.append("AI:")
    return "\n".join(lines)


def load_memory():
    """{"summary": text, "exchanges": [...]} from memory.json, or an empty memory on first run."""
    if not MEMORY_FILE.exists():
        return {"summary": "", "exchanges": []}
    memory = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
    if isinstance(memory, list):  # the old format: only the exchanges
        memory = {"summary": "", "exchanges": memory}
    return memory


def save_memory(memory):
    """Write the memory to memory.json and return it."""
    MEMORY_FILE.write_text(json.dumps(memory, indent=2, ensure_ascii=False), encoding="utf-8")
    return memory


# The summary goes in front of the conversation as one more exchange, so every strategy's prompt
# (each writes the history in its own format) shows it without changes.
SUMMARY_QUESTION = "What do you remember from our earlier conversation?"


def prompt_history(memory):
    """The exchanges a prompt shows: the summary (if any), then the last MEMORY_SIZE exchanges.
    The summary is shown one sentence per line (2026-10-12 trace, same summary and 16 questions: one block
    10/16, one per line 12/16 - "How old am I?" and the football team were answered from it only then)."""
    shown = memory["exchanges"][-MEMORY_SIZE:]
    if memory["summary"]:
        lines = "\n".join(split_sentences(memory["summary"]))
        shown = [{"user": SUMMARY_QUESTION, "ai": lines, "summary": True}] + shown
    return shown


def add_exchange(memory, exchange):
    """Memory with one more exchange; when it holds MEMORY_SIZE + COMPACT_BATCH, the oldest
    COMPACT_BATCH are folded into the summary (several model calls: a few seconds)."""
    exchanges = memory["exchanges"] + [exchange]
    summary = memory["summary"]
    if len(exchanges) >= MEMORY_SIZE + COMPACT_BATCH:
        summary = compact_memory(summary, exchanges[:COMPACT_BATCH])
        exchanges = exchanges[COMPACT_BATCH:]
    return {"summary": summary, "exchanges": exchanges}


# Step 1 of compacting, in two parts (2026-10-12). Written to the user ("You ..."): the summary is shown
# as the AI's reply.
# a) Facts: one call per user message - what does it tell about the user? One prompt for all 5 exchanges
#    turned told facts into topics ("I play the cello" -> "We talked about the cello"), which were then
#    dropped first, and took the AI's replies for facts ("dumplings are... famous in China"). Only the
#    user's own words: a wrong AI reply would otherwise stay in the summary for good.
#    Shown the exchange before (2026-10-12): alone, "I'll call her Cleopatra." and "And Mateo studies
#    architecture." told nothing. A sample about someone else: "Elena is a violin teacher" became
#    "You are a violin teacher".
FACT_PROMPT = """For each message a user sent, write what it tells about the user as one short sentence to them ("You ..."), or "Nothing." if it tells nothing about them. The line "Before" only shows what the message answers. Facts about other people stay theirs.

Before: User: Can you recommend a good book? / AI: Sure! What kind of books do you like?
Message: Mostly old detective stories.
About the user: You like old detective stories.

Before: (nothing)
Message: Why is the sky blue?
About the user: Nothing.

Before: User: I need a gift idea. / AI: Who is the gift for?
Message: My sister Ana, she's a doctor and loves gardening.
About the user: Your sister Ana is a doctor and loves gardening.

Before: User: Why is the sky blue? / AI: Sunlight scatters off the air, and blue light scatters the most.
Message: Thanks, that's really helpful!
About the user: Nothing.

Before: {before}
Message: {message}
About the user:"""

# b) Topics: one sentence naming what the exchanges talked about.
TOPICS_PROMPT = """Below are conversations between a user and an AI. After each one is a single short sentence naming what they talked about.

Conversation:
User: Why do volcanoes erupt?
AI: Magma rises because it is lighter than the rock around it, and pressure pushes it out.
User: Which volcano is the biggest?
AI: Mauna Loa in Hawaii is the largest active volcano.
Topics: We talked about why volcanoes erupt and the biggest volcano.

Conversation:
{conversation}
Topics:"""

# Tried and removed (2026-10-12): an out-of-date check (model yes/no for each new fact vs each older one)
# removed facts that were still true, and once the new "moved to Leeds" itself (FAILED_EXPERIMENTS.md).

# Step 2, when the summary is over SUMMARY_LIMIT: the model shortens sentences, the longest first, one at a
# time (2026-10-12). Shortening the whole summary at once never worked: cut off, "He ..." instead of "You ...",
# names lost; and the two versions that passed a name check silently lost "You have three hives".
SHORTEN_PROMPT = """Make each sentence shorter: keep every fact, but use fewer words.

Sentence: Your name is Maria and you work as a teacher, teaching maths at a school which is in Lisbon.
Shorter: You are Maria, a maths teacher in Lisbon.

Sentence: We talked about the reasons why volcanoes erupt and which volcano is the biggest one in the world.
Shorter: We talked about why volcanoes erupt and the biggest volcano.

Sentence: {sentence}
Shorter:"""


def count_tokens(text):
    """How many tokens `text` is for the model (llama.cpp's /tokenize)."""
    body = json.dumps({"model": MODEL, "content": text}).encode("utf-8")
    request = urllib.request.Request(TOKENIZE_URL, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request) as response:
        return len(json.loads(response.read())["tokens"])


def compact_memory(summary, exchanges):
    """Fold `exchanges` into `summary` and keep it under SUMMARY_LIMIT tokens. Returns the new summary.
    1. the model writes notes on the exchanges, added to the end: one fact per user message (or none),
       then one topics sentence (only sentences grounded in what they summarise); a fact that another
       fact already says (most of its words) is left out;
    2. if too long, the model shortens sentences, longest first (each kept only if nothing is lost);
    3. if still too long, code drops whole sentences: one of two similar ones, then topics, then the oldest."""
    conversation = "\n".join(f"User: {e['user']}\nAI: {e['ai']}" for e in exchanges)
    # Notes are one line; a longer n-gram for the repetition check, as notes rightly repeat
    # phrases like "You have a" (the normal 4 would stop them early).
    # Grounding check (2026-10-12): the notes copied the prompt's sample ("the capital of Japan, and how
    # many people live there") in 3 of 6 compactions. A note sentence must come from what it summarises.
    notes, kept, facts = [], [], []
    for k, e in enumerate(exchanges):
        before = f"User: {exchanges[k - 1]['user']} / AI: {exchanges[k - 1]['ai']}" if k else "(nothing)"
        fact, _, _ = generate(FACT_PROMPT.format(before=before, message=e["user"]), soft_limit=60,
                              hard_limit=60, temperature=0, repeat_ngram=8)
        fact = fact.strip()
        if fact and not fact.lower().startswith("nothing"):
            notes.append(fact)
            facts += [s for s in split_sentences(fact) if grounded(s, f"{before} {e['user']}")]
    topics, _, _ = generate(TOPICS_PROMPT.format(conversation=conversation), soft_limit=60, hard_limit=60,
                            temperature=0, repeat_ngram=8)
    notes.append(topics.strip())
    topic_lines = [s for s in split_sentences(topics.strip()) if grounded(s, conversation)]
    steps = {"notes": " ".join(notes)}  # what each step did (memory_test.py prints it)
    steps["ungrounded"] = [s for n in notes for s in split_sentences(n) if s not in facts + topic_lines]
    # Duplicates (2026-10-12): shown the exchange before, the model often repeats its fact ("You got a
    # hamster yesterday. You got a hamster named Mochi.").
    sentences, steps["duplicates"] = split_sentences(summary), []
    for fact in facts:
        if any(covered(fact, s) for s in sentences if not is_topic(s)):
            steps["duplicates"].append(fact)
            continue
        steps["duplicates"] += [s for s in sentences if not is_topic(s) and covered(s, fact)]
        sentences = [s for s in sentences if is_topic(s) or not covered(s, fact)] + [fact]
    summary = " ".join(sentences + topic_lines).strip()
    steps["tokens"] = count_tokens(summary)
    if steps["tokens"] > SUMMARY_LIMIT:
        summary, steps["shortened"], steps["rejected"] = shorten_sentences(summary)
        summary, steps["dropped"] = drop_sentences(summary)
    record("compact", conversation, steps)
    return summary


def is_topic(sentence):
    """A topics line ("We talked about ..."), not a fact. By its start: a fact in the third person ("Aiko is a
    chemistry student") has no "you" either, and was dropped as a topic (2026-10-12)."""
    return sentence.startswith("We ")


def covered(a, b):
    """True if sentence `b` already says `a`: every word of a (filler aside) is in b.
    (2/3 of the words was too loose: "Aiko is a ... student in Osaka" was left out for "You are a ... student
    in Osaka", and "... and are a beekeeper" for "You have three hives" - the one new word mattered.)"""
    words_a = content_words(a) - NOTE_WORDS
    return bool(words_a) and words_a <= content_words(b)


def shorten_sentences(summary):
    """Step 2: the model shortens sentences, longest first, until the summary fits SUMMARY_LIMIT. A shorter
    version is kept only if it has fewer tokens, ends with a full stop, keeps every name and number, and
    adds no new words. Returns (summary, [(old, new) kept], [(old, new) rejected])."""
    sentences = split_sentences(summary)
    kept, rejected = [], []
    for old in sorted(sentences, key=lambda x: -len(x.split())):
        if count_tokens(" ".join(sentences)) <= SUMMARY_LIMIT:
            break
        new, _, _ = generate(SHORTEN_PROMPT.format(sentence=old), soft_limit=60, hard_limit=60,
                             temperature=0, repeat_ngram=8)
        new = new.strip()
        ok = (new.endswith((".", "!", "?")) and count_tokens(new) < count_tokens(old)
              and key_words(old) <= key_words(new) and content_words(new) <= content_words(old))
        (kept if ok else rejected).append((old, new))
        if ok:
            sentences[sentences.index(old)] = new
    return " ".join(sentences), kept, rejected


NUMBER_WORDS = {"one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
                "first", "second", "third", "half", "hundred", "thousand"}


def key_words(sentence):
    """Names and numbers in a sentence, lower case: capitalised words (the first word too, unless it is "You",
    "Your", "We"), digits, number words."""
    words = re.findall(r"\w+", sentence)
    return {w.lower() for w in words
            if w[0].isdigit() or w.lower() in NUMBER_WORDS or (w[0].isupper() and w not in ("You", "Your", "We"))}


def split_sentences(text):
    return [s for s in re.split(r"(?<=[.!?])\s+", text) if s]


def content_words(sentence):
    """The sentence's words without filler, for comparing two sentences."""
    return {w for w in re.findall(r"\w+", sentence.lower()) if w not in ECHO_FILLER}


# Words the notes use to describe a conversation ("We talked about..."): not taken from it.
NOTE_WORDS = {"talked", "talk", "discussed", "about", "also", "learned", "asked", "told", "said", "how", "who",
              "which", "why", "when", "where", "there", "its", "their", "his", "her", "with", "from", "by"}


def grounded(sentence, conversation):
    """True if at least half of the sentence's words (filler aside) appear in the conversation.
    Words match on their first 5 letters, so "lives" matches "live" and "learning" "learn"."""
    words = content_words(sentence) - NOTE_WORDS
    if not words:
        return True
    said = {w[:5] for w in re.findall(r"\w+", conversation.lower())}
    return sum(w[:5] in said for w in words) >= len(words) / 2


def drop_sentences(summary):
    """Drop whole sentences until the summary fits SUMMARY_LIMIT, in this order:
    1. one of two similar sentences (sharing at least 2 words and half the words of the shorter one):
       the topic one if one of them is a topic, else the one with fewer words (on a tie the older) -
       "the older" dropped "Your dad is a sushi chef in Kyoto." for "Your dad makes great sushi.";
    2. the oldest topic ("We talked about ...");
    3. the oldest sentence.
    (2026-10-12: with only 1 and 3, the name and job went first - the oldest sentences are who the user is.
    With "older of the two" and no 2-word minimum, "Your name is Priya." went for the topic line
    "We talked about my name, my job, ..." - one shared word.)
    Returns (summary, the dropped sentences)."""
    sentences = split_sentences(summary)
    dropped = []
    while len(sentences) > 1 and count_tokens(" ".join(sentences)) > SUMMARY_LIMIT:
        # "talked about"-words don't count: they made "We also talked about your Volvo..." and
        # "We also talked about the Roman Empire." similar (2026-10-12).
        words = [content_words(s) - NOTE_WORDS for s in sentences]
        topics = [is_topic(s) for s in sentences]
        pair = next(((i, j) for i in range(len(words)) for j in range(i + 1, len(words))
                     if len(words[i] & words[j]) >= max(2, min(len(words[i]), len(words[j])) / 2)), None)
        if pair:  # the topic one, else the one that says less (fewer words; on a tie the older)
            i, j = pair
            if topics[i] != topics[j]:
                drop = i if topics[i] else j
            else:
                drop = j if len(words[j]) < len(words[i]) else i
        else:
            drop = next((i for i, topic in enumerate(topics) if topic), 0)
        dropped.append(sentences.pop(drop))
    return " ".join(sentences), dropped


def has_cjk(text):
    """True if `text` contains Chinese/Japanese/Korean characters or CJK punctuation."""
    return re.search(r"[　-〿぀-ヿ一-鿿가-힯＀-￯]", text) is not None


# Common little words of each language. Topic words like "Paris" or
# "France" say nothing about the language, function words do.
FRENCH_WORDS = {
    "le", "la", "les", "un", "une", "des", "du", "de", "est", "et", "je", "tu",
    "il", "elle", "nous", "vous", "ils", "elles", "sont", "pas", "que", "qui",
    "dans", "pour", "avec", "sur", "au", "aux", "ce", "cette", "c'est", "mais",
    "très", "être", "avoir", "ne", "se", "sa", "son", "ses", "leur", "où",
}
ENGLISH_WORDS = {
    "the", "an", "is", "are", "and", "i", "you", "he", "she", "it", "we", "they",
    "not", "that", "which", "in", "for", "with", "to", "of", "this", "but",
    "very", "be", "have", "was", "my", "at",
}


def looks_french(text):
    """True if `text` has at least 2 French function words and more French than English ones."""
    words = re.findall(r"[a-zàâçéèêëîïôûùüÿœæ']+", text.lower())
    french = sum(w in FRENCH_WORDS for w in words)
    english = sum(w in ENGLISH_WORDS for w in words)
    return french >= 2 and french > english


def language_switch(prompt, response):
    """Return the language the response switched to, or None if it didn't."""
    # Checked in code: asked "what language is this?", the 3B model guessed
    # from the topic ("pizza" -> Italian) and missed Chinese entirely.
    if has_cjk(response) and not has_cjk(prompt):
        return "Chinese/Japanese/Korean"
    if looks_french(response) and not looks_french(prompt):
        return "French"
    return None


def find_repetition(text, n=REPEAT_NGRAM):
    """Return the first n-word phrase that appears twice in `text`, or None."""
    # \w+ drops punctuation, so "Canis Major." and "Canis Major" match.
    words = re.findall(r"\w+", text.lower())
    seen = set()
    for i in range(len(words) - n + 1):
        phrase = tuple(words[i:i + n])
        if phrase in seen:
            return " ".join(phrase)
        seen.add(phrase)
    return None


# A list item: a line starting with "1." / "1)" or a bullet ("-", "*", "•") and a space.
LIST_ITEM = re.compile(r"^[ \t]*(?:\d+[.)]|[-*•])[ \t]+(.+?)[ \t]*$", re.M)


def find_list_loop(text):
    """A list that keeps coming back to the same items: at least 4 finished items, at most half of them
    different. Returns the most repeated item, or None.

    The phrase check above can't see these loops: the item number changes on every line and the items
    are short (2026-10-10, IFBench: 20 brand names with 4 different ones; "race" listed 10 times as
    palindromes). On the 300 answers of a full IFBench run it would have stopped 5, all failing ones.
    """
    finished = text[:text.rfind("\n") + 1]  # only complete lines
    items = [m.group(1).lower().rstrip(".,;:!?") for m in LIST_ITEM.finditer(finished)]
    if len(items) >= 4 and len(set(items)) <= len(items) / 2:
        return max(set(items), key=items.count)
    return None


SENTENCE_END_RE = re.compile(r"[.!?…][\"')\]]*(?=\s|$)")
LIST_MARKER_END = re.compile(r"\n[ \t]*(?:\d+[.)]|[-*•])[ \t]*$")  # a list number left on its own


def trim_repetition(text, stopped_by):
    """A reply stopped by the repetition check, cut back to before the loop: a repeated phrase to the
    last full sentence before it came the second time; a looping list to the items before the first
    repeated one. Other replies are returned as they are.

    For the fallback (the last rejected reply, shown "unchecked"): 2026-10-10, IFBench answers that were
    the last rejected attempt ended mid-sentence, on the repeated phrase.
    """
    if stopped_by.startswith("repetition (list item"):
        seen, kept = set(), []
        for line in text.splitlines():
            m = LIST_ITEM.match(line)
            item = m.group(1).lower().rstrip(".,;:!?") if m else None
            if item in seen:
                break
            if item is not None:
                seen.add(item)
            kept.append(line)
        return "\n".join(kept).rstrip()
    if not stopped_by.startswith("repetition ('"):
        return text
    phrase = stopped_by[len("repetition ('"):-len("')")].split()
    words = list(re.finditer(r"\w+", text))
    lowered = [w.group().lower() for w in words]
    starts = [i for i in range(len(lowered) - len(phrase) + 1) if lowered[i:i + len(phrase)] == phrase]
    if len(starts) < 2:
        return text
    cut = words[starts[1]].start()  # where the phrase came the second time
    ends = [m.end() for m in SENTENCE_END_RE.finditer(text, 0, cut)]
    trimmed = text[:ends[-1]] if ends else text[:cut]
    return LIST_MARKER_END.sub("", trimmed.rstrip()).rstrip()


def find_stop(text, stop_sequences=STOP_SEQUENCES):
    """Return the index of the first stop sequence in `text`, or -1."""
    # Skip leading whitespace, otherwise a response that starts with "\n"
    # would be cut to nothing before the model said anything.
    start = len(text) - len(text.lstrip())
    hits = [i for i in (text.find(s, start) for s in stop_sequences) if i != -1]
    return min(hits) if hits else -1


def generate(prompt, soft_limit=SOFT_LIMIT, hard_limit=HARD_LIMIT, temperature=None,
             stop_sequences=STOP_SEQUENCES, repeat_ngram=REPEAT_NGRAM, sampling=None):
    """Stream a completion and return (text, token_count, stopped_by).
    temperature: None = ANSWER_TEMPERATURE (read at call time, so the app can raise it).
    sampling: extra llama.cpp sampler fields for this request (e.g. DRY), or None."""
    if temperature is None:
        temperature = ANSWER_TEMPERATURE
    body = json.dumps({
        "model": MODEL,
        "prompt": prompt,
        "stream": True,
        "temperature": temperature,
        "cache_prompt": CACHE_PROMPT,
        **(sampling or {}),
    }).encode("utf-8")
    request = urllib.request.Request(
        SERVER_URL, data=body, headers={"Content-Type": "application/json"}
    )

    pieces = []
    token_count = 0
    stopped_by = "unknown"

    # The response is Server-Sent Events: lines like `data: {...json...}`.
    # llama.cpp sends one token per event, so we count events as tokens.
    with urllib.request.urlopen(request) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8").strip()
            if not line.startswith("data: "):
                continue
            data = line[len("data: "):]
            if data == "[DONE]":
                break

            choice = json.loads(data)["choices"][0]

            # EOS: the model itself decided it is done. The server does not
            # send the EOS token as text, it reports finish_reason "stop".
            if choice.get("finish_reason") == "stop":
                stopped_by = "EOS"
                break

            text = choice.get("text", "")
            if not text:
                continue
            pieces.append(text)
            token_count += 1
            full = "".join(pieces)

            # Stop sequence: a token may hold more than the stop text
            # (e.g. ".\nA"), so cut the text exactly where the sequence starts.
            cut = find_stop(full, stop_sequences)
            if cut != -1:
                pieces = [full[:cut]]
                stopped_by = f"stop sequence ({full[cut:cut + 1]!r})"
                break

            # Check the whole text so far after every token: catching the
            # loop early saves generating tokens we will throw away anyway.
            repeated = find_repetition(full, repeat_ngram)
            if repeated:
                stopped_by = f"repetition ({repeated!r})"
                break
            item = find_list_loop(full) if "\n" in text else None  # a line just ended: a looping list?
            if item:
                stopped_by = f"repetition (list item {item!r})"
                break

            # Leaving the `with` block closes the connection -> server stops generating.
            if token_count >= hard_limit:
                stopped_by = "hard limit"
                break
            if token_count >= soft_limit and text.rstrip().endswith(SENTENCE_ENDS):
                stopped_by = "soft limit (sentence end)"
                break

    text = "".join(pieces)
    record("generate", prompt, {"text": text, "tokens": token_count, "stopped_by": stopped_by,
                                "temperature": temperature})
    return text, token_count, stopped_by


# Troubleshooting (view.py): while the app answers a message, `trace` is a list and every model
# call (generate, yes/no) and reply check is appended to it; the app saves it as traces/<id>.json.
# None the rest of the time (tests), so nothing is recorded.
trace = None


def record(kind, prompt, output):
    """Append one step to `trace`, if a trace is being recorded."""
    if trace is not None:
        trace.append({"kind": kind, "prompt": prompt, "output": output})


TRACE_DIR = Path(__file__).parent / "traces"


def save_trace(answer_id, info, calls):
    """Save one answer's trace for view.py: what the app knew + every model call, as traces/<id>.json."""
    TRACE_DIR.mkdir(exist_ok=True)
    data = {"id": answer_id, "time": datetime.now().isoformat(timespec="seconds"), **info, "calls": calls}
    (TRACE_DIR / f"{answer_id}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1),
                                                  encoding="utf-8")


def validate(prompt, response, history=()):
    """Ask the model whether `response` addresses `prompt`. Returns (ok, raw_verdict).
    history: the recent exchanges, shown to the validator so a reply that only makes sense with the
    conversation ("Jaakko." to "What is my name?") isn't judged unrelated."""
    conversation = ""
    if history:  # what the prompt showed (prompt_history: summary + the last exchanges), all of it
        lines = "\n".join(f"User: {e['user']}\nAI: {e['ai']}" for e in history)
        conversation = f"Conversation so far:\n{lines}\n\n"
    question = VALIDATOR_PROMPT.format(prompt=prompt, response=response, conversation=conversation)
    # temperature 0 = always pick the most likely token, so the verdict is repeatable.
    verdict, _, _ = generate(question, soft_limit=VALIDATOR_LIMIT,
                             hard_limit=VALIDATOR_LIMIT, temperature=0)
    # Anything other than a clear YES counts as a rejection.
    ok = verdict.strip().upper().startswith("YES")
    return ok, verdict.strip()


# The yes/no routers (best_strat, sources) read the model's next token as probabilities: one token
# with logprobs. A probe, not generation, so the streaming limits aren't needed.
# (From strat4.py, the LLM-as-a-Verifier trial, deleted 2026-10-10.)
TOP_LOGPROBS = 20  # how many most likely next tokens the server reports


def top_token_probs(prompt):
    """Ask for one token and return {token: probability} for the most likely next tokens."""
    body = json.dumps({"model": MODEL, "prompt": prompt, "max_tokens": 1, "temperature": 0,
                       "logprobs": TOP_LOGPROBS, "cache_prompt": CACHE_PROMPT}).encode("utf-8")
    request = urllib.request.Request(SERVER_URL, data=body,
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request) as response:
        logprobs = json.loads(response.read())["choices"][0]["logprobs"]
    probs = parse_top_logprobs(logprobs)
    record("yes/no", prompt, {t: round(p, 3) for t, p in sorted(probs.items(), key=lambda x: -x[1])[:5]})
    return probs


def parse_top_logprobs(logprobs):
    """Handle both OpenAI-style shapes llama.cpp may return.

    Newer: {"content": [{"top_logprobs": [{"token": " A", "logprob": -0.1}, ...]}]}
    Older: {"top_logprobs": [{" A": -0.1, " B": -2.3}]}
    """
    if "content" in logprobs:
        top = logprobs["content"][0]["top_logprobs"]
        return {t["token"]: math.exp(t["logprob"]) for t in top}
    return {token: math.exp(lp) for token, lp in logprobs["top_logprobs"][0].items()}


def log_attempt(entry):
    """Append one attempt as a JSON line to today's file in the logs folder."""
    LOG_DIR.mkdir(exist_ok=True)
    now = datetime.now()
    entry = {"time": now.isoformat(timespec="seconds"), **entry}
    with open(LOG_DIR / f"{now:%Y-%m-%d}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# Small words that don't count as "new" when checking for an echo.
ECHO_FILLER = {
    "a", "an", "the", "i", "you", "it", "my", "your", "me", "we", "he", "she", "they",
    "is", "are", "was", "were", "be", "am", "have", "has", "had", "do", "does", "did",
    "of", "to", "in", "on", "for", "at", "and", "or", "that", "this", "what", "so",
}


def is_echo(prompt, response):
    """True if the reply only repeats the user's words: no new word apart from filler, and it
    repeats at least half of what was asked (the message's non-filler words).

    Found in the chat app (2026-10-09): "what did I have for breakfast?" -> "What I had
    for breakfast." was accepted (the validator only asks "related?") and saved; with that
    one echo in memory, "hello" -> "Hello", "what is the capital of France?" -> "The
    capital of France." (3 of 4 tries). "Yes"/"no" are real answers, never an echo.
    The "at least half" part (2026-10-10): picking one of several options also uses only
    the message's words ("Is a tomato a fruit or a vegetable?" -> "A fruit."). On GPQA,
    "The correct answer is (A) <option>" was rejected as an echo in 177 of 180 attempts,
    so every answer was the third, after two "answer with something new" notes.
    """
    reply = re.findall(r"\w+", response.lower())
    asked = re.findall(r"\w+", prompt.lower())
    if {"yes", "no"} & set(reply):
        return False
    if [w for w in reply if w not in set(asked) and w not in ECHO_FILLER]:
        return False  # it adds something
    asked_content = {w for w in asked if w not in ECHO_FILLER}
    return len({w for w in reply if w in asked_content}) >= len(asked_content) / 2


# The most recent rejected (non-empty) reply and why: {"reply": ..., "reason": ...}. The app
# shows it, marked, when every attempt failed - instead of only "(gave up after 3 attempts)".
# (2026-10-10: "hello" gave up every time - "Hello." 3/3 rejected as an echo.)
last_rejected = {}


def check_reply(prompt, response, stopped_by, use_validator=True, history=()):
    """Run all checks on one reply. Returns (ok, reason, note for the next attempt).

    use_validator=False skips the topic validator (only the generic checks run).
    history: the conversation so far, for the near-copy check and the validator (empty = neither sees it).
    A rejected reply with any text is remembered in `last_rejected`.
    """
    ok, reason, note = run_checks(prompt, response, stopped_by, use_validator, history)
    record("check", prompt, {"reply": response, "ok": ok, "reason": reason, "stopped_by": stopped_by})
    if not ok and re.search(r"\w", response):
        last_rejected.update(reply=trim_repetition(response, stopped_by), reason=reason)
    return ok, reason, note


# Near-copy check (2026-10-12): the app's memory once snowballed - after one "It's not clear what you are
# referring to, could you provide more context or details?" the next three replies, to different messages,
# copied it. With that memory, 3 of 4 open requests ("Name three fruits.") gave up (memory_test.py poison).
NEAR_COPY = 0.8  # difflib similarity of the two texts


def near_copy(prompt, response, history):
    """An earlier AI reply that `response` (5+ words) nearly copies, though the message is a different one;
    else None. (Asked the same thing again, the same reply is fine.) The memory summary doesn't count."""
    if len(re.findall(r"\w+", response)) < 5:
        return None
    for e in history:
        if (not e.get("summary")
                and difflib.SequenceMatcher(None, response.lower(), e["ai"].lower()).ratio() >= NEAR_COPY
                and difflib.SequenceMatcher(None, prompt.lower(), e["user"].lower()).ratio() < NEAR_COPY):
            return e["ai"]
    return None


def without_copies(history, response):
    """`history` without the exchanges whose AI reply `response` nearly copies (for the retry after a
    near-copy rejection: the model can't copy what it doesn't see)."""
    return [e for e in history if e.get("summary")
            or difflib.SequenceMatcher(None, response.lower(), e["ai"].lower()).ratio() < NEAR_COPY]


def run_checks(prompt, response, stopped_by, use_validator, history=()):
    """The checks themselves, in order; the first that fails rejects the reply."""
    if not re.search(r"\w", response):
        # Empty (or only punctuation): every other check looks for something
        # wrong IN the text, so with no text they all pass. The validator
        # even said YES to an empty reply in eval.py.
        return False, "empty reply", "The AI's previous reply was empty. This time it answers the user."
    if is_echo(prompt, response):
        return (False, "echo",
                "The AI's previous reply only repeated the user's words. This time it answers with something new.")
    if stopped_by.startswith("repetition"):
        # No need to ask the validator: the harness already knows it is bad.
        if stopped_by.startswith("repetition (list item"):
            # 2026-10-10: "Race" listed 10 times as palindromes -> real palindromes on the retry.
            return False, stopped_by, ("The AI's previous reply listed the same items again and again. "
                                       "This time every item in the list is different.")
        # The repeated phrase is quoted. That can plant it in the next attempt (a base model copies what it
        # sees: "a bat and a" -> the bat-and-ball riddle, on the short-answer route), but it was a net win on
        # short chat loops, and a plain "started repeating itself" made a long-route retry copy a sample
        # instead (2026-10-10, IFBench prompt 77). On the long route the retry also uses DRY, which
        # discourages copying the quote.
        phrase = stopped_by[len("repetition ('"):-len("')")]
        return False, stopped_by, (f'The AI\'s previous reply started repeating "{phrase}" over and over. '
                                   "This time it answers without repeating itself.")
    if language_switch(prompt, response):
        return (False, f"language switch ({language_switch(prompt, response)})",
                "The AI's previous reply switched to another language. This time it answers in the user's language.")
    if near_copy(prompt, response, history):
        return (False, "near-copy of an earlier reply",
                "The AI's previous reply copied one of its earlier replies word for word. "
                "This time it answers this message itself.")
    if not use_validator:
        return True, "generic checks passed", None
    # The validator judges the latest message, with the conversation if the caller passed it.
    ok, verdict = validate(prompt, response, history)
    return ok, f"validator said: {verdict!r}", "The AI's previous reply drifted off topic. This time it stays on topic."


def answer(history, prompt, max_attempts=MAX_ATTEMPTS, verbose=True, log=True):
    """Run the full loop for one user message.

    Returns (reply or None if it gave up, attempts used).
    """
    note = None  # first attempt: no note
    for attempt in range(1, max_attempts + 1):
        response, token_count, stopped_by = generate(build_prompt(history, prompt, note))
        response = response.strip()
        ok, reason, note = check_reply(prompt, response, stopped_by)

        if log:
            log_attempt({
                "prompt": prompt, "attempt": attempt, "response": response,
                "accepted": ok, "reason": reason, "stopped_by": stopped_by, "tokens": token_count,
            })

        if ok:
            if verbose:
                print(f"\n=== FINAL (attempt {attempt}) ===")
                print(response)
                print(f"\n[{token_count} tokens, stopped by {stopped_by} | {reason}]")
            return response, attempt
        if verbose:
            # Shown so you can watch the retry loop work.
            print(f"\n--- attempt {attempt} REJECTED ({reason}) ---")
            print(response)
            print(f"[note for next attempt: {note}]")

    if verbose:
        print(f"\n=== GAVE UP after {max_attempts} attempts ===")
    return None, max_attempts


class Spinner:
    """A loading animation on one line while the app works and has nothing to print.

    Use as `with Spinner("thinking") as spin:`; change `spin.label` to show the current step.
    Only drawn in a real terminal (not when the output goes to a file or pipe).
    """
    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, label):
        self.label = label
        self._stop = threading.Event()
        self._thread = None

    def __enter__(self):
        if sys.stdout.isatty():
            self._thread = threading.Thread(target=self._spin, daemon=True)
            self._thread.start()
        return self

    def _spin(self):
        for frame in itertools.cycle(self.FRAMES):
            # \r goes back to the line start, \033[K clears the rest of the line.
            sys.stdout.write(f"\r{style(frame + ' ' + self.label + '...', 'dim')}\033[K")
            sys.stdout.flush()
            if self._stop.wait(0.1):
                break

    def __exit__(self, *exc):
        self._stop.set()
        if self._thread:
            self._thread.join()
            sys.stdout.write("\r\033[K")  # wipe the spinner line
            sys.stdout.flush()


# The raw column (2026-10-12, user's request): next to every answer the app shows what the bare base model
# writes for the same message - the message exactly as typed, one plain completion: no chat format, no memory,
# no stop sequences, no checks, the server's own sampling settings. Only a token cap, or a loop would never end.
# It shows what the harness is for: the repetition and word spam of a base model left alone.
RAW_MAX_TOKENS = 200


def raw_completion(prompt):
    """The bare base model's continuation of `prompt` (nothing added), cut off hard at RAW_MAX_TOKENS tokens -
    mid-sentence if that's where it is. Returns (text, cut): cut is True if the cap stopped it, not the model."""
    body = json.dumps({"model": MODEL, "prompt": prompt, "max_tokens": RAW_MAX_TOKENS}).encode("utf-8")
    request = urllib.request.Request(SERVER_URL, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request) as response:
        choice = json.loads(response.read())["choices"][0]
    return choice["text"], choice.get("finish_reason") == "length"


def print_side_by_side(left_title, left, right_title, right):
    """Two texts in two columns, each wrapped to half the terminal width."""
    width = max(20, (shutil.get_terminal_size().columns - 3) // 2)

    def lines(text):
        out = []
        for line in text.strip().splitlines() or [""]:
            out += textwrap.wrap(line, width) or [""]
        return out

    print(style(left_title.ljust(width), "bold", "cyan") + " │ " + style(right_title, "bold", "yellow"))
    print(" " * width + " │")
    for a, b in itertools.zip_longest(lines(left), lines(right), fillvalue=""):
        print(f"{a.ljust(width)} │ {b}")


STYLES = {"dim": "2", "bold": "1", "cyan": "36", "green": "32", "yellow": "33", "red": "31"}


def style(text, *names):
    """Colour text with ANSI codes, only in a real terminal."""
    if not sys.stdout.isatty():
        return text
    return "".join(f"\033[{STYLES[n]}m" for n in names) + text + "\033[0m"


if __name__ == "__main__":
    # The app answers with the saved best strategy (best_strat.py), which builds on
    # this file's generate() and checks. Imported here, not at the top: best_strat
    # imports harness itself. To chat with the plain harness instead, set this to False.
    USE_BEST_STRATEGY = True
    import best_strat
    # This file runs as __main__, but best_strat and sources import it as `harness`: a second copy
    # of the module. Their check_reply() calls record into harness.last_rejected, so read that one.
    import harness

    # The user gets a little variety; tests (eval.py, benchmarks) keep answers at temperature 0.
    # Set on both copies of this module: harness (used by best_strat, sources) and __main__ (answer()).
    harness.ANSWER_TEMPERATURE = ANSWER_TEMPERATURE = USER_TEMPERATURE
    # The prompt cache stays off in the app too (user, 2026-10-12): ~30% slower, but the same message gets
    # the same handling as in the tests.

    # Search (the source chain, sources.py) is on by default: for each message the model first
    # decides whether it needs a look-up (sources.needs_lookup); if not, the normal answer runs.
    # Imported here: it needs trafilatura (pip), and SearXNG for the web stage.
    try:
        import search
        import sources as source_chain
        search_available = search_on = True
    except ImportError as error:
        print(f"Search unavailable ({error}); answering without it.")
        search_available = search_on = False

    os.system("")  # switches on ANSI colours in older Windows consoles
    memory = load_memory()
    history = prompt_history(memory)  # what every prompt shows: the summary + the last MEMORY_SIZE exchanges
    count = len(memory["exchanges"])
    print(style("AI Harness", "bold", "cyan") + style(f"  ·  {MODEL}  ·  "
          + ("best strategy" if USE_BEST_STRATEGY else "plain harness"), "dim"))
    print(style(f"memory: {count} exchange{'' if count == 1 else 's'}"
                + (" + summary" if memory["summary"] else "") + "  ·  /search turns search on/off  ·  "
                "/raw hides/shows the raw model  ·  /clear empties memory  ·  empty line quits", "dim"))
    show_raw = True  # the raw base model's answer next to the harness's (/raw toggles)

    while True:
        label = style("search on", "green") if search_on else style("search off", "dim")
        try:
            prompt = input(f"\n{style('You', 'bold')} [{label}] {style('›', 'cyan')} ").strip()
        except (KeyboardInterrupt, EOFError):
            break
        if not prompt:
            break
        if prompt == "/clear":
            memory = save_memory({"summary": "", "exchanges": []})
            history = prompt_history(memory)
            print(style("Memory cleared.", "dim"))
            continue
        if prompt == "/search":
            if not search_available:
                print(style("Search is unavailable (see the message at the start).", "yellow"))
            else:
                search_on = not search_on
                print(style(f"Search is now {'on' if search_on else 'off'}.", "dim"))
            continue
        if prompt == "/raw":
            show_raw = not show_raw
            print(style(f"The raw model's column is now {'on' if show_raw else 'off'}.", "dim"))
            continue
        if prompt.startswith("/"):
            print(style("Commands: /search (on/off), /raw (on/off), /clear. Anything else is a message.", "yellow"))
            continue

        looked_up = False
        harness.last_rejected.clear()
        answer_id = secrets.token_hex(3)  # names this answer's trace: python view.py <id>
        harness.trace = []                # record every model call from here on
        chain, used_sources = [], ()
        try:
            if not USE_BEST_STRATEGY and not search_on:
                reply, attempts = answer(history, prompt)  # prints its own attempts: no spinner
            else:
                with Spinner("deciding whether to look it up" if search_on else "thinking") as spin:
                    looked_up = search_on and source_chain.needs_lookup(prompt)
                    if looked_up:
                        # Source chain (sources.py, plan in sources.md): weather -> can public
                        # sources know this? -> Wikipedia + Grokipedia -> web search.
                        spin.label = "looking it up"
                        reply, attempts, used_sources, chain = source_chain.answer(prompt, history)
                    elif USE_BEST_STRATEGY:
                        spin.label = "thinking"
                        reply, attempts = best_strat.answer(history, prompt)
                if not looked_up and not USE_BEST_STRATEGY:
                    reply, attempts = answer(history, prompt)
        except KeyboardInterrupt:
            harness.trace = None
            print(style("Cancelled.", "yellow"))
            continue
        except OSError as error:  # SearXNG not running, no network, ...
            print(style(f"Look-up failed: {error}. Is SearXNG running at {search.SEARXNG_URL}?", "red"))
            save_trace(answer_id, {"prompt": prompt, "search_on": search_on, "looked_up": looked_up,
                                   "error": str(error)}, harness.trace)
            harness.trace = None
            print(style(f"   id {answer_id}", "dim"))
            continue

        # Every attempt failed the checks: show the last non-empty one anyway (user's call, 2026-10-10),
        # marked as unchecked. It is NOT saved to memory (below: only `reply` is): rejected echoes in
        # memory once made later answers echo too ("capital of France?" -> "The capital of France.").
        unchecked = harness.last_rejected.get("reply") if reply is None else None
        raw = None
        if show_raw and (USE_BEST_STRATEGY or looked_up):
            try:
                with Spinner("asking the raw model"):
                    raw, cut = raw_completion(prompt)
                if cut:
                    raw += f"\n\n[cut off here: the {RAW_MAX_TOKENS}-token limit]"
            except (KeyboardInterrupt, OSError) as error:
                raw = f"(no raw answer: {error or 'cancelled'})"
            shown = reply if reply is not None else unchecked or f"(gave up after {attempts} attempts)"
            print_side_by_side("Harness:", shown, "Raw base model, no harness:", raw)
            if unchecked:
                print(style(f"   unchecked: all {attempts} attempts failed the checks (last: "
                            f"{harness.last_rejected['reason']}) · not saved to memory", "yellow"))
        elif USE_BEST_STRATEGY or looked_up:
            if reply is not None:
                print(f"{style('AI', 'bold', 'cyan')} {style('›', 'cyan')} {reply}")
            elif unchecked:
                print(f"{style('AI', 'bold', 'cyan')} {style('›', 'cyan')} {unchecked}")
                print(style(f"   unchecked: all {attempts} attempts failed the checks (last: "
                            f"{harness.last_rejected['reason']}) · not saved to memory", "yellow"))
            else:
                print(style(f"(gave up after {attempts} attempts)", "yellow"))

        used = next((c["source"] for c in chain if c.get("used")), "none") if looked_up else None
        if looked_up:
            print(style(f"   looked up · answered from: {used}", "dim"))
            # Nothing found: the model's own answer, clearly marked as a guess (sources.own_guess).
            guess = next((c.get("guess") for c in chain if c.get("guess")), None)
            if guess:
                print(style(f"   my guess, not from a source: {guess}", "dim"))
            for i, (url, _) in enumerate(used_sources, 1):
                print(style(f"   [{i}] {url}", "dim"))
        elif search_on:
            print(style("   no look-up needed", "dim"))
        save_trace(answer_id, {"prompt": prompt, "search_on": search_on, "looked_up": looked_up,
                               "reply": reply, "unchecked": unchecked,
                               "unchecked_reason": harness.last_rejected.get("reason") if unchecked else None,
                               "attempts": attempts, "answered_from": used,
                               "sources": [{"url": url, "text": text} for url, text in used_sources],
                               "chain": chain, "memory": history, "raw": raw}, harness.trace)
        harness.trace = None
        print(style(f"   id {answer_id}", "dim"))

        if looked_up:
            log_attempt({"id": answer_id, "prompt": prompt, "attempt": 1, "response": reply or "",
                         "accepted": reply is not None,
                         "reason": f"search ({attempts} attempts)" if reply else "gave up (search)",
                         "stopped_by": "search", "tokens": None, "shown_unchecked": unchecked,
                         # The whole chain, so a bad answer can be traced: every source tried,
                         # what it returned (titles, text) and what the model answered from it.
                         "chain": chain,
                         "sources": [{"url": url, "chars": len(text), "text": text} for url, text in used_sources]})
            # Not saved to memory. A saved "18°" became a pattern: later weather questions
            # WITHOUT a search got invented temperatures (11/12). Marking it in memory
            # ("18° (from a web search: accuweather.com)") was worse: invented 12/12, and
            # the fake answers copied the marker. Tested 2026-10-09.
            continue
        if USE_BEST_STRATEGY:
            # best_strat doesn't report single attempts, so one log line per message.
            log_attempt({"id": answer_id, "prompt": prompt, "attempt": 1, "response": reply or "",
                         "accepted": reply is not None,
                         "reason": f"best_strat ({attempts} attempts)" if reply else "gave up (best_strat)",
                         "stopped_by": "best_strat", "tokens": None, "shown_unchecked": unchecked})
        if reply is not None:
            # Only accepted replies are remembered; a rejected one would teach
            # the model its own mistakes.
            # When memory is full, the model folds the oldest exchanges into the summary (a few seconds).
            old_summary = memory["summary"]
            with Spinner("updating memory"):
                memory = save_memory(add_exchange(memory, {"user": prompt, "ai": reply}))
            history = prompt_history(memory)
            if memory["summary"] != old_summary:
                print(style(f"   memory summary: {memory['summary']}", "dim"))
