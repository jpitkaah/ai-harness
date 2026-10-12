"""A chain of information sources, cleanest first. Plan: sources.md.

    weather question? -> wttr.in      (the model decides, yes/no)
    every question    -> Wikipedia    (model picks the article, Python fetches its summary)
                      -> Grokipedia   (same article title, opening ~1500 characters)
                      -> web search   (SearXNG, search.py)

A source is used when it returns text AND the model's answer from it isn't
"The sources don't say." Otherwise the chain moves on. No question-type router
beyond weather: Wikipedia is cheap to ask, and an empty result moves on anyway.

Python only calls the services, trims text and moves down the chain; the model
decides what to look up and writes every answer.
"""

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import trafilatura

from cache import cached

import answer_key
import best_strat
import harness
import search

# Wikipedia asks API users for a descriptive User-Agent.
USER_AGENT = "ai-harness-learning-project/0.1 (local educational use)"
WIKI_SEARCH_URL = "https://en.wikipedia.org/w/api.php"
WIKI_SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
GROKIPEDIA_URL = "https://grokipedia.com/page/{title}"
WIKI_TITLES = 5  # titles the model chooses from

# The answer the model is shown (by a sample in search.py) to give when a source
# doesn't contain the answer. Seeing it means: try the next source.
ABSTAIN = re.compile(r"sources? don.?t say|sources? do not say|don.?t know|do not know", re.IGNORECASE)

# The model first names the article it expects. Encyclopedia titles are a different
# kind of query than web search phrases: "gas plants absorb from air" found only junk
# titles on Wikipedia; "Photosynthesis" finds the right article.
TOPIC_PROMPT = """Name the encyclopedia article that would answer the question.

Question: Who designed the Sydney Opera House?
Need: the designer of the Sydney Opera House
Article: Sydney Opera House
Question: When did the Berlin Wall fall?
Need: the date the Berlin Wall fell
Article: Fall of the Berlin Wall
Question: Why do leaves change colour in autumn?
Need: the reason leaves change colour in autumn
Article: Autumn leaf color

Question: {question}
Need: {need}
Article:"""

# v12: a thinking step before any look-up (user's idea, 2026-10-10). Traced with view.py: "how far
# away is oslo from stockholm?" -> the article step named "Oslo", whose text has no distance, and every
# vote was invented. Now the model first writes exactly what has to be found ("the distance between
# Oslo and Stockholm"); that line goes to the article step, the evidence step, and is the web search
# phrase. Samples are none of the test questions.
NEED_PROMPT = """Write exactly what has to be found to answer the question.

Question: How deep is Lake Baikal?
Need: the depth of Lake Baikal
Question: When did the Berlin Wall fall?
Need: the date the Berlin Wall fell
Question: What is the exchange rate between the euro and the dollar?
Need: the current exchange rate between the euro and the US dollar
Question: Who wrote Hamlet?
Need: the author of Hamlet

Question: {question}
Need:"""
MAX_NEED_WORDS = 15


def web_phrase(prompt, need):
    """The web search phrase: the need itself when it is short enough, else search.py's phrase step."""
    if need != prompt and len(need.split()) <= search.MAX_PHRASE_WORDS:
        return need
    return search.search_phrase(prompt)


def lookup_need(prompt):
    """What has to be found, in the model's words; the question itself if the reply is malformed."""
    need = small_step(NEED_PROMPT.format(question=prompt))
    # Shape check only: something there, and short.
    return need if re.search(r"\w", need) and len(need.split()) <= MAX_NEED_WORDS else prompt

# v8: a yes/no (read as a probability) before any lookup. Prototype on the 18 test questions plus
# 6 others: personal questions 0.03-0.18, public ones (myths and weather included) 0.61-1.00.
# The samples are personal questions unlike the test's (breakfast, neighbour's cat, favourite song).
PUBLIC_PROMPT = """Some questions can be answered from public sources, like an encyclopedia or a web page. Others ask about the person's own life, which only they know.

Question: Who wrote Hamlet?
Public sources know: YES
Question: Where did I park my car?
Public sources know: NO
Question: How deep is the Pacific Ocean?
Public sources know: YES
Question: When is my sister's birthday?
Public sources know: NO

Question: {question}
Public sources know:"""

# The search-decision layer (used by the app, eval.py auto, search_eval.py auto): should this question
# go to the source chain at all, or be answered by best_strat from the model's own knowledge?
# Prototype 2026-10-09 (44 questions, no web calls): the 7 that need a look-up (2023/2024 events, most
# known moons) 0.77-0.95; the 26 eval questions 0.05-0.91 - math, memory and instructions <= 0.58, but
# "In which year did WWII end?" 0.91 and "Who painted the Mona Lisa?" 0.80. The model separates "a
# factual question" from the rest, not recent from well known. At 0.75: 0 missed, 2 unnecessary look-ups.
# A variant with "famous old fact -> NO" samples lowered everything, Physics 2024 included (0.71).
LOOKUP_PROMPT = """Some questions need a look-up in up-to-date sources: recent events, facts that change, or specific details. Others can be answered from general knowledge, by calculating, or from the conversation.

Question: Who won the 2025 Wimbledon men's final?
Needs a look-up: YES
Question: What is 12 times 12?
Needs a look-up: NO
Question: What is the current population of Brazil?
Needs a look-up: YES
Question: Why is the sky blue?
Needs a look-up: NO
Question: Which films won Oscars this year?
Needs a look-up: YES
Question: Where do I work?
Needs a look-up: NO

Question: {question}
Needs a look-up:"""


LOOKUP_THRESHOLD = 0.60  # lowered from 0.75 (2026-10-09): "who holds this office now" scored 0.62-0.72


NUMBER = re.compile(r"\d+(?:\.\d+)?")


def best_strat_route(prompt):
    """The best_strat special route that would answer this question from the question alone, or None.

    best_strat's own routers and steps (temperature 0, so best_strat repeats the same choice). A
    route counts only when it is really taken with what the question gives: a router alone claimed
    "What is the price of bitcoin?" (math: no calculation in its rewrite) and "Which planet has the
    most known moons?" (comparison: no amounts given) - both need a look-up.
    """
    if best_strat.route_probability(best_strat.LETTERS_ROUTER_PROMPT, prompt) >= best_strat.ROUTE_THRESHOLD:
        return "letters"
    if best_strat.route_probability(best_strat.COMPARE_ROUTER_PROMPT, prompt) >= best_strat.ROUTE_THRESHOLD:
        facts = best_strat.comparison_facts(prompt)
        if facts and facts[1] in ("yes", "no"):  # both amounts are in the question
            return "comparison"
    # Math: a real calculation, made only of the question's own numbers. The price of bitcoin was
    # rewritten as "1 bitcoin = 10000000000..." - a calculation sign, but invented numbers.
    calculation = best_strat.restate(prompt) if best_strat.needs_math(prompt) else None
    if calculation and set(NUMBER.findall(calculation)) <= set(NUMBER.findall(prompt)):
        return "math"
    if best_strat.impossibility_reason(prompt):
        return "sense check"
    return None


def needs_lookup(prompt):
    """The model's call: does this question need the source chain?

    Yes when P(YES) >= LOOKUP_THRESHOLD and none of best_strat's special routes claims it.
    Why the second part (2026-10-09): at bar 0.60 feathers-vs-steel and the plane-crash riddle went
    to the chain, which has no comparison route or sense check (feathers 0/3, plane 2/3 there).
    Letters, comparison, math and "doesn't make sense" questions never need a look-up.
    """
    # Questions only the person can answer go to the chain too (2026-10-10): its memory stage answers
    # from the user's own messages, and if they don't tell, the v8 step answers with no sources
    # ("The sources don't say."). best_strat invented instead: 0/18 honest ("I had cereal", "My
    # sister's name is Mary"); with this route 15/18, memory questions still 12/12.
    if best_strat.route_probability(PUBLIC_PROMPT, prompt) < 1 - best_strat.ROUTE_THRESHOLD:
        return True
    if best_strat.route_probability(LOOKUP_PROMPT, prompt) < LOOKUP_THRESHOLD:
        return False
    return best_strat_route(prompt) is None


# Then the model picks from Wikipedia's top titles (its own ranking is poor: "Euro 2024
# winner" put "UEFA Euro 2024 qualifying" first). No "NONE" option: with one in the
# samples the model said NONE even when the perfect article was listed. A wrong pick
# just leads to "The sources don't say." and the next source.
PICK_PROMPT = """Pick the encyclopedia article that best answers the question. Copy its title exactly.

Question: Who designed the Sydney Opera House?
Articles: Sydney Opera House (film) | Sydney Opera House | Sydney Harbour | Jørn Utzon
Pick: Sydney Opera House
Question: When did the Berlin Wall fall?
Articles: Berlin Wall graffiti | Fall of the Berlin Wall | Berlin Wall
Pick: Fall of the Berlin Wall

Question: {question}
Articles: {titles}
Pick:"""


# Wikipedia rate-limits anonymous API use: after 5 quick searches it answered
# HTTP 429 "You are making too many requests" (2026-10-09), and the chain silently
# fell through to web search. So: remember results, space requests out, and on a
# 429 wait as long as the server asks (capped) before one more try.
MIN_INTERVAL = 1.0      # seconds between requests to the same site
MAX_RETRY_WAIT = 5.0    # longest wait we accept for a 429 Retry-After
_last_request = {}      # site -> time of our last request


@cached
def get(url, params=None):
    """HTTP GET with our User-Agent; the body as text, or None. Cached per URL.

    params: a tuple of (key, value) pairs (a tuple, so the result can be cached).
    """
    if params:
        url += "?" + urllib.parse.urlencode(params)
    site = urllib.parse.urlparse(url).netloc
    for _ in range(2):
        wait = MIN_INTERVAL - (time.time() - _last_request.get(site, 0))
        if wait > 0:
            time.sleep(wait)
        _last_request[site] = time.time()
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as error:
            if error.code == 404:  # no such article: don't retry
                return None
            if error.code == 429:  # too many requests: wait as asked, then one more try
                retry_after = float(error.headers.get("Retry-After") or MIN_INTERVAL)
                if retry_after > MAX_RETRY_WAIT:
                    return None
                time.sleep(retry_after)
        except OSError:  # timeouts, connection errors
            pass
    return None


def wikipedia_titles(query):
    """Top article titles from Wikipedia's search."""
    body = get(WIKI_SEARCH_URL, (("action", "query"), ("list", "search"), ("srsearch", query),
                                 ("srlimit", WIKI_TITLES), ("format", "json")))
    if not body:
        return []
    return [hit["title"] for hit in json.loads(body).get("query", {}).get("search", [])]


def small_step(prompt_text):
    """One short, deterministic model job (temperature 0), one line."""
    text, _, _ = harness.generate(prompt_text, temperature=0, stop_sequences=answer_key.STOP_SEQUENCES)
    return text.strip().strip(' ."')


def pick_title(prompt, titles):
    """The model's choice among the titles, or None. Python only checks it IS one of them."""
    if not titles:
        return None
    choice = small_step(PICK_PROMPT.format(question=prompt, titles=" | ".join(titles)))
    return next((t for t in titles if t.lower() == choice.lower()), None)


def wikipedia_summary(title):
    """The article's opening paragraph as plain text, or None."""
    body = get(WIKI_SUMMARY_URL.format(title=urllib.parse.quote(title.replace(" ", "_"))))
    return json.loads(body).get("extract") or None if body else None


def grokipedia_text(title):
    """The article's opening text (trimmed like web pages), or None if there's no such page."""
    html = get(GROKIPEDIA_URL.format(title=urllib.parse.quote(title.replace(" ", "_"))))
    text = trafilatura.extract(html) if html else None
    return search.trim(" ".join(text.split())) if text else None


# v2: an evidence step per source. In v1 the model read a whole summary and took a
# prominent related fact ("Germany, the host country" for Euro 2024; "Oxygen." from
# the photosynthesis summary), and unrelated articles produced inventions ("Grok."
# as the neighbour's cat). Now one small call copies the words that answer the
# question, or NONE; the answer is written from that evidence line only.
EVIDENCE_PROMPT = """Copy the words from the source that answer the question. If the source doesn't answer it, write NONE.

Source: The Eiffel Tower in Paris is 330 metres tall, including its antennas. It was built in 1889.
Question: How tall is the Eiffel Tower?
Need: the height of the Eiffel Tower
Evidence: The Eiffel Tower in Paris is 330 metres tall
Source: Paris is the capital of France and its largest city.
Question: How tall is the Eiffel Tower?
Need: the height of the Eiffel Tower
Evidence: NONE
Source: The conference was held in Geneva and opened by the mayor. The award went to Dr. Lee for her work on vaccines.
Question: Who received the award?
Need: the person who received the award
Evidence: The award went to Dr. Lee

Source: {source}
Question: {question}
Need: {need}
Evidence:"""
EVIDENCE_MIN_COPIED = 0.8  # share of the evidence's words that must appear in the source
SAMPLE_TEXT = EVIDENCE_PROMPT.split("Source: {source}")[0].lower()  # the prompt's sample part


def evidence(prompt, text, need):
    """The words from `text` that answer the question (what `need` says has to be found), or None.

    Shape check only: the model must have COPIED - at least EVIDENCE_MIN_COPIED of the
    evidence's words appear in the source - so it can't invent evidence.
    """
    found = small_step(EVIDENCE_PROMPT.format(source=text, question=prompt, need=need))
    words = re.findall(r"\w+", found.lower())
    if not words or found.upper().startswith("NONE"):
        return None
    source_words = set(re.findall(r"\w+", text.lower()))
    copied = sum(w in source_words for w in words) / len(words)
    if copied < EVIDENCE_MIN_COPIED:
        return None
    # v5: not copied from the prompt's own samples. Once "Paris is the capital of France"
    # came back as evidence for Australia; enough of its words were in the source to pass.
    if " ".join(words) in " ".join(re.findall(r"\w+", SAMPLE_TEXT)):
        return None
    return found


# v10: a vote (user's idea, 2026-10-09). "What gas do plants absorb?": Wikipedia's evidence "Oxygen",
# Grokipedia's "CO₂", and the answer step, given both bare words, took the first. Asked separately, the
# model's own answer (best_strat, no sources) and one answer per source are votes; the model judges which
# votes say the same thing. 2+ agreeing votes and no equal rival group win. Otherwise up to MAX_VOTES with
# votes from web pages; still no winner -> the v8 answer from all the evidence. Python only counts.
MAX_VOTES = 5

# Samples are none of the test questions.
SAME_ANSWER_PROMPT = """Do the two answers to the question say the same thing? Ignore the wording; compare only what they claim.

Question: Who wrote Hamlet?
Answers: Shakespeare. | William Shakespeare wrote it.
Same: YES
Question: What is the capital of Canada?
Answers: Toronto. | Ottawa.
Same: NO
Question: How long is a marathon?
Answers: 42.195 km. | About 42 kilometres.
Same: YES
Question: Do camels store water in their humps?
Answers: No, that's a myth. | Yes, they do.
Same: NO

Question: {question}
Same:"""


# 0.8, not 0.5: probed on 8 pairs, the judge gave "Jupiter. | Saturn." 0.75 and "Oxygen. | CO₂" 0.56
# (different), same pairs 0.68-0.89. A wrong split only costs a majority; a wrong merge picks a winner.
SAME_THRESHOLD = 0.8


def same_answer(prompt, a, b):
    """The model's judgement: do answers a and b claim the same thing?"""
    return best_strat.route_probability(SAME_ANSWER_PROMPT, f"{prompt}\nAnswers: {a} | {b}") >= SAME_THRESHOLD


def count_votes(prompt, votes):
    """Group votes [(voter, reply, (url, evidence) or None)] by what they claim; biggest group first."""
    groups = []
    for v in votes:
        group = next((g for g in groups if same_answer(prompt, g[0][1], v[1])), None)
        if group:
            group.append(v)
        else:
            groups.append([v])
    return sorted(groups, key=len, reverse=True)


def majority(groups):
    """The biggest group if it has 2+ votes and no other group is as big, else None."""
    if groups and len(groups[0]) >= 2 and (len(groups) == 1 or len(groups[0]) > len(groups[1])):
        return groups[0]
    return None


def vote(prompt, history, collected, chain, need, memo, stage="vote"):
    """(reply, attempts, used) chosen by vote, or None if no majority. See MAX_VOTES above.

    collected: (source name, url, evidence) per source. memo keeps the model's own answer, so the
    encyclopedia vote and the web vote (stage "web vote": no extra web votes) ask best_strat once.
    """
    votes = []
    if "own" not in memo:
        memo["own"], _ = best_strat.answer(list(history), prompt)
    own = memo["own"]
    if own and not ABSTAIN.search(own):
        votes.append(("model", own, None))
    for name, url, found in collected:
        reply, _ = search.answer_from(prompt, ((url, found),))
        if reply and not ABSTAIN.search(reply):
            votes.append((name, reply, (url, found)))
    groups = count_votes(prompt, votes)
    if stage == "vote" and not majority(groups):  # more votes from web pages, up to MAX_VOTES
        for url, text in search.gather_sources(web_phrase(prompt, need)):
            if len(votes) >= MAX_VOTES:
                break
            found = evidence(prompt, text, need)
            chain.append({"source": "web page", "url": url, "text": text, "evidence": found})
            if found:
                reply, _ = search.answer_from(prompt, ((url, found),))
                if reply and not ABSTAIN.search(reply):
                    votes.append(("web", reply, (url, found)))
        groups = count_votes(prompt, votes)
    win = majority(groups)
    chain.append({"source": stage, "votes": [[voter, reply] for voter, reply, _ in votes],
                  "groups": [[reply for _, reply, _ in g] for g in groups]})
    if not win:
        return None
    reply = next(r for _, r, source in win if source)  # a source-backed vote's wording (the model has 1 vote)
    chain[-1].update(reply=reply, used=True)
    return reply, 1, tuple(source for _, _, source in win if source)


MEMORY_URL = "memory://conversation"  # shown to the model as "Source 1 (conversation): ..."


def memory_text(history):
    """The user's own earlier messages as one source, or None if there are none.

    Only the user's words, not the AI's replies: those can be wrong, and a saved "Sauli Niinistö
    is the president of Finland" (2026-10-09, in the app) would be read back as evidence and
    stop every later look-up. The user's messages hold the facts about them ("I live in Helsinki").
    The memory summary (harness.prompt_history, 2026-10-12) holds the older facts, written to the user.
    Its "You also said: ..." lines are the user's own messages whose reply wasn't kept: read as such
    (as "Notes ...: You also said: My name is Jack Jackson." the evidence step found no name).
    """
    lines = [line for e in history if e.get("summary") for line in e["ai"].splitlines()]
    also = "You also said: "
    notes = [line for line in lines if not line.startswith(also)]
    said = [line[len(also):] for line in lines if line.startswith(also)]
    said += [exchange["user"] for exchange in history if not exchange.get("summary")]
    # The user's messages one per line (2026-10-12, answer 3e2cb3): joined, "my name is JJ can you tell me about
    # yourself" read as one run-on and the evidence step found no name; one per line it copied "my name is JJ".
    parts = (["Notes from the earlier conversation: " + " ".join(notes)] if notes else []) + \
            (["The user said:\n" + "\n".join(f"- {s}" for s in said)] if said else [])
    return "\n".join(parts) if parts else None


def chain_answer(prompt, history=()):
    """Go down the chain. Returns (reply or None, attempts, used, chain).

    history: the conversation memory (harness.prompt_history()); its user messages and summary are the first source.
    used:  the (url, text) sources of the answer that was given.
    chain: one dict per source tried - what it returned and what the model answered -
           so every answer can be traced to its source.
    """
    chain = []
    need = lookup_need(prompt)  # v12: what has to be found
    memo = {}  # the model's own answer, shared by the votes
    chain.append({"source": "need", "need": need})

    def attempt(name, url, text, use_evidence=True):
        """Answer from one source; (reply, attempts, sources) if that answer is final, else None."""
        found = evidence(prompt, text, need) if use_evidence else text
        chain.append({"source": name, "url": url, "text": text, "evidence": found})
        if not found:
            return None
        sources = ((url, found),)
        reply, attempts = search.answer_from(prompt, sources)
        chain[-1]["reply"] = reply
        if reply and not ABSTAIN.search(reply):
            chain[-1]["used"] = True
            return reply, attempts, sources
        return None

    # 0. Memory: the user's own earlier messages (2026-10-09). With a lower look-up bar, questions
    # like "Which city do I live in?" (0.58) reach the chain, which otherwise can't see the memory.
    # Same evidence step as every source; "The sources don't say." moves on down the chain.
    said = memory_text(history)
    if said:
        if result := attempt("memory", MEMORY_URL, said):
            return (*result, chain)

    # 1. Weather (the model decides).
    place = search.weather_place(prompt)
    report = search.weather_report(place) if place else None
    if report:
        url = search.WEATHER_URL.format(place=urllib.parse.quote(place.lower()))
        if result := attempt("weather", url, report, use_evidence=False):  # one clean line already
            return (*result, chain)

    # v8: can public sources know this at all? Once web search worked again, questions only the
    # asker can answer ("What did I have for breakfast?") found real pages ("Moose the neighbor
    # cat") and the model answered from them. If the model is sure only the person knows, nothing
    # is looked up: the answer step gets no sources and writes the reply itself.
    public = best_strat.route_probability(PUBLIC_PROMPT, prompt)
    if public < 1 - best_strat.ROUTE_THRESHOLD:
        return nothing_found(prompt, chain, "no lookup", public=public)

    # 2. + 3. Wikipedia AND Grokipedia, with the article the model picked.
    # v5: evidence from both, then one answer from all of it. In v2 the chain stopped at
    # Wikipedia's first evidence even when it was only related ("Germany hosted the
    # tournament" for who won Euro 2024) while Grokipedia had the right line. Seeing both,
    # the answer step can pick the line that actually answers the question.
    topic = small_step(TOPIC_PROMPT.format(question=prompt, need=need))
    titles = wikipedia_titles(topic) if re.search(r"\w", topic) else []
    title = pick_title(prompt, titles)
    chain.append({"source": "wikipedia search", "query": topic, "titles": titles, "picked": title})
    if title:
        page = urllib.parse.quote(title.replace(" ", "_"))
        collected = []  # (source name, url, evidence)
        for name, url, text in (("wikipedia", WIKI_SUMMARY_URL.format(title=page), wikipedia_summary(title)),
                                ("grokipedia", GROKIPEDIA_URL.format(title=page), grokipedia_text(title))):
            if text:
                found = evidence(prompt, text, need)
                chain.append({"source": name, "url": url, "text": text, "evidence": found})
                if found:
                    collected.append((name, url, found))
        if collected:
            if result := vote(prompt, history, collected, chain, need, memo):
                return (*result, chain)
            # No majority: v8's one answer from all the encyclopedia evidence.
            used = tuple((url, found) for _, url, found in collected)
            reply, attempts = search.answer_from(prompt, used)
            chain.append({"source": "encyclopedias", "reply": reply})
            if reply and not ABSTAIN.search(reply):
                chain[-1]["used"] = True
                return reply, attempts, used, chain

    # 4. Web search: the last resort. Evidence from each page; the answer is written from
    # whatever evidence was found and is final, "The sources don't say." included.
    phrase = web_phrase(prompt, need)
    web = []
    for url, text in search.gather_sources(phrase):
        found = evidence(prompt, text, need)
        chain.append({"source": "web page", "url": url, "text": text, "evidence": found})
        if found:
            web.append((url, found))
    # v13: a vote here too - the model's own answer + one answer per page. With one answer from all
    # pages' evidence, junk lines drowned a perfect one: Einstein's top page said "Einstein didn't fail
    # math as a child ... a myth", beside a publications list and Czech text -> "The sources don't say."
    if result := vote(prompt, history, [("web", url, found) for url, found in web], chain, need, memo,
                      stage="web vote"):
        return (*result, chain)
    if not web:  # v17: no page gave any evidence
        return nothing_found(prompt, chain, "web", guess=own_guess(prompt, history, memo), query=phrase)
    web = tuple(web)  # no majority: one answer from all the evidence, as before
    reply, attempts = search.answer_from(prompt, web)
    chain.append({"source": "web", "query": phrase, "urls": [u for u, _ in web], "reply": reply, "used": True})
    return reply, attempts, web, chain


# v15 (user's idea, 2026-10-10): "The sources don't say." is honest but reads oddly in a chat. When the
# chain ends in it, one more call says it naturally, about what was asked ("I couldn't find a reliable
# source for the name of King Olaf's first horse."). It must not answer: it has to still say "I couldn't
# find / don't know", and may not bring in a number the question doesn't have; otherwise the plain reply
# stays. Samples are none of the test questions.
NOT_FOUND_PROMPT = """Answer key. The sources did not contain the answer, so each answer says so in one short, natural sentence about what was asked. It never guesses the answer.

Question: How many windows does the Louvre have?
Answer: I couldn't find a reliable source for how many windows the Louvre has.
Question: What is my brother's name?
Answer: I don't know your brother's name; you haven't told me.
Question: When did the first bakery open in Bergen?
Answer: I couldn't find when the first bakery in Bergen opened.

Question: {question}
Answer:"""
NOT_FOUND = re.compile(r"couldn.?t find|could not find|can.?t find|cannot find|unable to find|don.?t know|"
                       r"do not know|no (reliable )?(information|source|record)|haven.?t told|not sure", re.IGNORECASE)


def not_found_reply(prompt):
    """A natural "I couldn't find ..." sentence for this question, or None if every attempt was malformed."""
    note = None
    for _ in range(harness.MAX_ATTEMPTS):
        text, _, stopped_by = harness.generate(NOT_FOUND_PROMPT.format(question=prompt),
                                               stop_sequences=answer_key.STOP_SEQUENCES)
        reply = text.strip()
        ok, _, note = harness.check_reply(prompt, reply, stopped_by, use_validator=False)
        # Shape checks: it still says it doesn't know, brings in no number of its own, and doesn't
        # talk about the prompt itself ("King Olaf had a horse, but the answer key couldn't find its name").
        own_numbers = set(NUMBER.findall(reply)) - set(NUMBER.findall(prompt))
        if ok and NOT_FOUND.search(reply) and not own_numbers and "answer key" not in reply.lower():
            return reply
    return None


def own_guess(prompt, history, memo):
    """v18 (user's option C): the model's own answer (best_strat, no sources), shown by the app beside
    "I couldn't find ..." as an unverified guess - never as the reply. The vote may have asked already."""
    if "own" not in memo:
        memo["own"], _ = best_strat.answer(list(history), prompt)
    own = memo["own"]
    return own if own and not ABSTAIN.search(own) and not NOT_FOUND.search(own) else None


def nothing_found(prompt, chain, stage, guess=None, **details):
    """v17: no source gave any evidence, so the model says it couldn't find it (not_found_reply) instead
    of answering from nothing. The answer step, given no sources at all, sometimes invented: "How many
    hairs does the average cat have?" -> "3500 hairs.", "How old am I?" -> "13 years old.", King Olaf's
    first horse -> "No.". Falls back to that answer step only if every not-found attempt is malformed."""
    reply, attempts = not_found_reply(prompt), 1
    if reply is None:
        reply, attempts = search.answer_from(prompt, ())
    chain.append({"source": stage, **details, "reply": reply, "no_evidence": True, "guess": guess, "used": True})
    return reply, attempts, (), chain


def answer(prompt, history=()):
    """The source chain (chain_answer), with "The sources don't say." said naturally.
    Returns (reply or None, attempts, used, chain)."""
    reply, attempts, used, chain = chain_answer(prompt, history)
    if reply and ABSTAIN.search(reply) and not any(c.get("no_evidence") for c in chain):
        natural = not_found_reply(prompt)
        chain.append({"source": "not found", "plain": reply, "reply": natural})
        if natural:
            reply = natural
    return reply, attempts, used, chain
