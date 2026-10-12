"""Web search: the harness's first tool. Now the last stage of the source chain (sources.py).

    prompt -> model writes a search phrase -> SearXNG finds pages
           -> trafilatura extracts their text -> model answers from those sources

Python only runs the tool (search, fetch, trim) and passes text to the model.
It never decides what the question means or edits the answer.

Needs: SearXNG at 127.0.0.1:8888 with JSON output on (see searxng/settings.yml),
and `pip install trafilatura`.
"""

import json
import random
import re
import urllib.parse
import urllib.request
from datetime import datetime
from functools import lru_cache

import trafilatura

from cache import cached
from trafilatura.settings import use_config

import answer_key
import best_strat
import harness

SEARXNG_URL = "http://127.0.0.1:8888/search"  # 127.0.0.1, not localhost: see harness.SERVER_URL
# One engine per search, taken in turn, so the load is spread evenly. Before, every
# search went to ALL of SearXNG's engines at once, and after ~100 test searches all of
# them were blocked together (2026-10-09). These four gave relevant results when tested
# one by one; Bing gave nothing (and probably the earlier junk), Google, Qwant, Mojeek,
# Brave and DuckDuckGo were still blocked, Mwmbl's results were weak.
ENGINES = ["yahoo", "yandex", "yep", "seznam"]
_next_engine = [random.randrange(len(ENGINES))]  # random start: a run doesn't always begin with the same one
MAX_SOURCES = 3       # pages read per question
EXTRA_SNIPPETS = 2    # further results shown as their search snippet only (no page fetch)
SOURCE_CHARS = 1500   # a 3B model loses track of long text: keep each source short
MAX_OVERRUN = 300     # trim() may go this far past SOURCE_CHARS to finish a sentence
# A sentence end: . ! or ? (plus any [1]-style citation marks) followed by a space or the end.
SENTENCE_END = re.compile(r"[.!?](?:\[[^\]]*\])*(?=\s|$)")
# The date a search engine puts in front of a snippet ("May 11, 2026 · ...", "12 hours ago · ...").
# It is when the page was crawled or published, not page content, and the evidence step copied it:
# "May 11, 2026 · Transformer is a neural network..." for "When was the first transformer made?".
SNIPPET_DATE = re.compile(r"^\s*(?:[A-Z][a-z]{2} \d{1,2}, \d{4}|\d+ (?:minute|hour|day|week|month|year)s? ago)"
                          r"\s*[·—–-]\s*")
MAX_PHRASE_WORDS = 10
SKIP_EXTENSIONS = (".pdf", ".doc", ".docx", ".ppt", ".xls", ".zip", ".mp4", ".mp3")

# trafilatura waits 30 s for a slow page by default; 5 s is plenty for chat.
FETCH_CONFIG = use_config()
FETCH_CONFIG.set("DEFAULT", "DOWNLOAD_TIMEOUT", "5")

PHRASE_PROMPT = """Write a short web search phrase for each question.

Question: Who won the 2022 football World Cup?
Search: 2022 FIFA World Cup winner
Question: How tall is the Eiffel Tower?
Search: Eiffel Tower height
Question: Why do cats purr?
Search: why do cats purr

Question: {question}
Search:"""

ANSWER_HEADER = ("Answer key. Each answer was checked by a teacher: it uses only the sources given with "
                 "the question. If the sources don't contain the answer, the answer is: The sources "
                 "don't say. If the sources disagree, the answer says so instead of picking one.")

# Sample entries: answer from a source; say so when the sources don't contain it;
# read a source that debunks a common claim; don't pick a side when the sources
# really disagree. None of them is a test question.
# v2: the disagreement answer used to say "one says it is ..., one says it is ...":
# the model copied it, the repetition check killed it, and it said "disagree" even
# when all sources agreed. The debunk sample shows the far more common case.
# v3: sample answers kept short and plain: longer ones leaked into real answers
# ("No, that's a myth. Bats can see, and the human brain...").
ANSWER_SAMPLES = [
    (["The Eiffel Tower in Paris is 330 metres tall, including its antennas."],
     "How tall is the Eiffel Tower?", "330 metres."),
    (["Paris is the capital of France and its largest city."],
     "How tall is the Eiffel Tower?", "The sources don't say."),
    (["It is often said that bats are blind, but this is a myth: all bats can see."],
     "Are bats blind?", "No, that's a myth."),
    (["Botanists classify bananas as berries.", "A banana is not a berry; berries are small fruits."],
     "Is a banana a berry?", "The sources disagree."),
]

# No relevance validator for search answers. With the sources in view the model stays
# on topic, and the validator kept rejecting the honest answer "The sources don't say."
# (search test 2026-10-09: 5 of 6 no-answer questions gave up; a search-specific wording
# didn't change its NO). Only the generic checks run: empty, repetition, language.


def search_phrase(prompt):
    """The model's search phrase for the question (falls back to the question itself)."""
    text, _, _ = harness.generate(PHRASE_PROMPT.format(question=prompt), temperature=0,
                                  stop_sequences=answer_key.STOP_SEQUENCES)
    phrase = text.strip().strip('"')
    # Shape check only: something there, and short.
    if not re.search(r"\w", phrase) or len(phrase.split()) > MAX_PHRASE_WORDS:
        return prompt
    return phrase


@cached
def search(phrase):
    """Top web results from SearXNG: [{"url", "title", "snippet"}], skipping files and duplicates.

    Asks the next engine in turn; if it returns nothing (blocked, or no hits), the one after it.
    """
    results = []
    for _ in range(len(ENGINES)):
        engine = ENGINES[_next_engine[0] % len(ENGINES)]
        _next_engine[0] += 1
        url = SEARXNG_URL + "?" + urllib.parse.urlencode({"q": phrase, "format": "json", "engines": engine})
        with urllib.request.urlopen(url, timeout=15) as response:
            results = json.loads(response.read()).get("results", [])
        if results:
            break
    found, seen = [], set()
    for r in results:
        link = r.get("url", "")
        if not link or link in seen or urllib.parse.urlparse(link).path.lower().endswith(SKIP_EXTENSIONS):
            continue
        seen.add(link)
        found.append({"url": link, "title": r.get("title", ""), "snippet": r.get("content", "")})
    return found


@cached
def page_text(url):
    """Main text of a page via trafilatura, or None if it can't be fetched or extracted."""
    html = trafilatura.fetch_url(url, config=FETCH_CONFIG)
    return trafilatura.extract(html) if html else None


def trim(text, limit=SOURCE_CHARS):
    """Cut text to about `limit` characters without breaking a sentence.

    Finishes the sentence that crosses the limit (up to MAX_OVERRUN more characters);
    if none ends that soon, cuts at the last whole word. Found 2026-10-09: a hard cut at
    1500 characters left Wikipedia's "...was proposed in the 20" - the year cut in half -
    and "When was the first transformer model created?" got "The sources don't say."
    """
    if len(text) <= limit:
        return text
    end = SENTENCE_END.search(text, limit - 1)
    if end and end.end() <= limit + MAX_OVERRUN:
        return text[:end.end()]
    cut = text.rfind(" ", 0, limit)
    return text[:cut] if cut > 0 else text[:limit]


@lru_cache(maxsize=64)
def gather_sources(phrase):
    """(url, text) pairs for a search phrase: what the model reads.

    The top MAX_SOURCES results: their search snippet, then the page's own text, trimmed
    to about SOURCE_CHARS (at a sentence end: trim). Then EXTRA_SNIPPETS more results as snippet only.
    Why snippets (v4, from tracing "/search how hot is it in oslo right now?"): the
    search engine's snippet is a short, query-relevant summary and often holds exactly
    the fact, while pages lose it - AccuWeather builds its page with JavaScript
    (trafilatura got nothing), BBC's extracted text had no temperatures at all, only
    "Sunrise Sunset..." repeated. The snippets had "12:00, 7°, 4°".
    Cached per phrase, so asking the same thing twice doesn't search and fetch twice.
    """
    results = search(phrase)
    sources = []
    for i, result in enumerate(results[:MAX_SOURCES + EXTRA_SNIPPETS]):
        text = SNIPPET_DATE.sub("", result["snippet"])
        if i < MAX_SOURCES:
            text = f"{text} {page_text(result['url']) or ''}"
        text = " ".join(text.split())  # one line: newlines would look like new entries
        if re.search(r"\w", text):
            sources.append((result["url"], trim(text)))
    return tuple(sources)


# ---------------------------------------------------------------------------
# Weather tool (wttr.in). Web pages were a bad source for "how hot is it right
# now": thousands of characters of menus and hourly forecast tables, with many
# different temperatures (today 19:00, 20:00, Saturday...), and the model mostly
# answered "The sources don't say." wttr.in gives one clean line instead:
#     "Oslo: Light rain +10°C (feels like +8°C)"
# The MODEL decides it's a weather question (yes/no read as a probability:
# weather questions 0.91-0.98, others <= 0.37 in a test) and copies the place;
# Python only calls wttr.in and passes the line on as the source.
# wttr.in was flaky in testing ("Oslo" failed, "oslo" worked a second later),
# so it tries twice, then falls back to the normal web search.
# Current conditions only: a question about tomorrow gets today's weather as its
# source, and the answer key says "The sources don't say." if that doesn't answer it.
# ---------------------------------------------------------------------------

WEATHER_URL = "https://wttr.in/{place}?format=%l:+%C+%t+(feels+like+%f)"
WEATHER_THRESHOLD = 0.75

WEATHER_ROUTER_PROMPT = """Some questions ask about the weather in a place: temperature, rain, wind, how hot or cold it is. Others don't.

Question: Is it raining in Berlin?
About the weather: YES
Question: Who wrote Hamlet?
About the weather: NO
Question: How warm is it in Madrid today?
About the weather: YES
Question: What is the population of Tokyo?
About the weather: NO

Question: {question}
About the weather:"""

PLACE_PROMPT = """Copy the place the question asks about.

Question: Is it raining in Berlin?
Place: Berlin
Question: How warm is it in Madrid today?
Place: Madrid

Question: {question}
Place:"""


def weather_place(prompt):
    """The place, if the model says this is a weather question; otherwise None."""
    if best_strat.route_probability(WEATHER_ROUTER_PROMPT, prompt) < WEATHER_THRESHOLD:
        return None
    text, _, _ = harness.generate(PLACE_PROMPT.format(question=prompt), temperature=0,
                                  stop_sequences=answer_key.STOP_SEQUENCES)
    place = text.strip().strip(' ."')
    # Shape check: a short name, not a sentence.
    return place if re.search(r"\w", place) and len(place.split()) <= 4 else None


def weather_report(place):
    """One line of current weather from wttr.in, or None if it fails twice."""
    # Lowercase: wttr.in answered "location not found" for "Oslo" but worked for "oslo"
    # (tested 2026-10-09: oslo, milano, new york all fine lowercase).
    url = WEATHER_URL.format(place=urllib.parse.quote(place.lower()))
    for _ in range(2):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
            with urllib.request.urlopen(request, timeout=10) as response:
                line = response.read().decode("utf-8").strip()
            if line and "not found" not in line.lower():
                return line
        except OSError:  # includes HTTP errors and timeouts
            pass
    return None


def build_prompt(prompt, sources, note=None):
    """Answer key: each entry lists its numbered sources, then the question and answer."""
    lines = [ANSWER_HEADER]
    if note:
        lines.append(f"Note: {note}")
    for sample_sources, question, answer_text in ANSWER_SAMPLES:
        lines.append("")
        lines += [f"Source {i}: {text}" for i, text in enumerate(sample_sources, 1)]
        lines += [f"Question: {question}", f"Answer: {answer_text}"]
    lines.append("")
    lines += [f"Source {i} ({urllib.parse.urlparse(url).netloc}): {text}"
              for i, (url, text) in enumerate(sources, 1)]
    # v4: the current date and time, so "today" and "right now" mean something
    # (the weather sources are hourly tables; without a clock the model can't pick a row).
    # Kept on purpose (tested 2026-10-09, 20 tries each): this line also separates the sources
    # from the question and frames "this morning". Removing it or moving the date to the header
    # made answers worse (Kilimanjaro 16/20 -> 9/20 or 13/20; honest "don't know" for breakfast
    # 20/20 -> 15/20 or 10/20). Its cost: the model can copy it as the answer to a "When..."
    # question ("Friday 09 October 2026, 22:41." for the first transformer model - seen once in
    # the app, 0 of 50 in a test). A date-only line was copied more often (3/20).
    lines.append(f"Today is {datetime.now():%A %d %B %Y, %H:%M}.")
    lines += [f"Question: {prompt}", "Answer:"]
    return "\n".join(lines)


def answer_from(prompt, sources, max_attempts=harness.MAX_ATTEMPTS):
    """Write an answer from the given (url, text) sources. Returns (reply or None, attempts).

    Used by answer() below and by sources.py for every source in its chain.
    """
    note = None
    for attempt in range(1, max_attempts + 1):
        response, _, stopped_by = harness.generate(build_prompt(prompt, sources, note),
                                                   stop_sequences=answer_key.STOP_SEQUENCES)
        response = response.strip()
        ok, _, note = harness.check_reply(prompt, response, stopped_by, use_validator=False)
        if ok:
            return response, attempt
    return None, max_attempts


def answer(prompt, max_attempts=harness.MAX_ATTEMPTS):
    """Weather or web search, read, answer. Returns (reply or None, attempts, sources, phrase).

    sources: tuple of (url, text) the answer was written from (empty if nothing was found).
    phrase: the search phrase the model wrote. Both are returned so callers can log them.
    (The app and the test now use sources.py, which adds Wikipedia and Grokipedia.)
    """
    place = weather_place(prompt)
    report = weather_report(place) if place else None
    if report:
        phrase = f"weather: {place}"
        sources = ((WEATHER_URL.format(place=urllib.parse.quote(place.lower())), report),)
    else:  # not a weather question, or wttr.in failed: normal web search
        phrase = search_phrase(prompt)
        sources = gather_sources(phrase)
    reply, attempts = answer_from(prompt, sources, max_attempts)
    return reply, attempts, sources, phrase
