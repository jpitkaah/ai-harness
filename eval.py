"""Compare harness configurations on the same fixed test set.

Each question has keywords; a reply is "correct" if it contains any of
them as a whole word (and stays within max_words, for instruction
questions). This is a crude but objective check that does NOT depend on
the 3B model judging itself.

Configs:
  base A  : base model + the harness (3 attempts). The one we're improving.
  inst raw: instruct model, no harness at all - its own chat format, one
            call, reply as-is. The reference score to beat (75/78).
Other configs were dropped; see FAILED_EXPERIMENTS.md.

Usage:
  python eval.py          base A only (default)
  python eval.py raw      inst raw only
  python eval.py both     both
  python eval.py best     best_strat.py (the saved best strategy) on the full test
  python eval.py strat2   strat2.py (the strategy being worked on) on the full test
  python eval.py strat2 heldout   ... on the held-out set instead (any choice; questions nobody tuned on)
  python eval.py baseraw  base model, no harness (same path as "inst raw")
  python eval.py auto     the app's default: best_strat, or the source chain (sources.py) when
                          the model says the question needs a look-up
"""

import json
import re
import sys
import time
import urllib.request
from collections import defaultdict

import harness

BASE = "QuantFactory/Qwen2.5-3B-GGUF:Q4_K_M"
INSTRUCT = "Qwen/Qwen2.5-3B-Instruct-GGUF:Q4_K_M"
CHAT_URL = "http://127.0.0.1:8090/v1/chat/completions"  # not "localhost": see harness.SERVER_URL
# Only a safety net so a raw reply can't run forever; not a harness rule.
RAW_MAX_TOKENS = 512

# A longer memory, including a fact that was later corrected (blue -> green).
LONG_HISTORY = [
    {"user": "Hi, I'm Jaakko.", "ai": "Hi Jaakko, nice to meet you!"},
    {"user": "I live in Helsinki with my dog Rex.", "ai": "Helsinki sounds lovely, and Rex too!"},
    {"user": "My favourite colour is blue.", "ai": "Blue is a great colour."},
    {"user": "Actually, I changed my mind: my favourite colour is green now.", "ai": "Got it, green it is!"},
]

# (category, history, question, keywords, max_words or None)
TEST_SET = [
    ("facts", [], "Why does ice float?", ["density", "dense", "denser"], None),
    ("facts", [], "What is the chemical symbol for gold?", ["au"], None),
    ("facts", [], "Who painted the Mona Lisa?", ["vinci"], None),
    ("facts", [], "In which year did World War II end?", ["1945"], None),
    ("facts", [], "What is the freezing point of water in Fahrenheit?", ["32"], None),
    ("facts", [], "Why do we have seasons on Earth?", ["tilt", "tilted", "axis"], None),
    ("facts", [], "What gas do plants absorb from the air?", ["carbon dioxide", "co2", "co₂"], None),  # "CO₂." is correct
    ("facts", [], "Name a mammal that can fly.", ["bat", "bats"], None),

    ("math", [], "What is 17 times 23?", ["391"], None),
    ("math", [], "What is 15% of 200?", ["30"], None),
    ("math", [], "What is 2 + 2 * 3?", ["8"], None),
    ("math", [], "I have 5 apples, eat 2, then buy 4 more. How many apples do I have?", ["7", "seven"], None),
    ("math", [], "A train leaves at 3 pm and travels for 2.5 hours. What time does it arrive?",
     ["5:30", "5.30", "half past five"], None),
    ("math", [], "What is the square root of 144?", ["12", "twelve"], None),

    ("trick", [], "Which is heavier, a kilogram of feathers or a kilogram of steel?",
     ["same", "neither", "equal", "both"], None),
    ("trick", [], "How many r's are in the word strawberry?", ["3", "three"], None),
    ("trick", [], "If a plane crashes on the border of two countries, where do they bury the survivors?",
     ["alive", "don't bury", "do not bury", "not buried", "aren't buried", "wouldn't bury",
      # added 2026-10-09 (user): correct answers the scorer missed
      "nowhere", "cannot be buried", "should not be buried", "wouldn't need to be buried",
      # added 2026-10-09 (user): "Burial can only happen after death." is right too.
      # Narrow phrases on purpose: a bare "after death" would also match wrong answers.
      "only happen after death", "only after death"], None),
    ("trick", [], "How many months have 28 days?", ["all", "12", "twelve", "every"], None),

    ("instructions", [], "Answer with one word: what colour is a ripe banana?", ["yellow"], 2),
    # "seven" added 2026-10-09 (user): a number written as a word is still only a number.
    ("instructions", [], "Reply with only a number: how many days are in a week?", ["7", "seven"], 2),
    ("instructions", [], "Answer yes or no: is the Sun a star?", ["yes"], 2),
    ("instructions", [], 'What is the opposite of "ancient"? Answer in one word.',
     ["modern", "new", "recent", "contemporary", "young"], 2),  # "young" added 2026-10-09 (user)

    ("memory", LONG_HISTORY, "Which city do I live in?", ["helsinki"], None),
    ("memory", LONG_HISTORY, "What is my dog's name?", ["rex"], None),
    ("memory", LONG_HISTORY, "What is my name?", ["jaakko"], None),
    ("memory", LONG_HISTORY, "What is my favourite colour now?", ["green"], None),
]

# Held-out set (2026-10-10): 26 new questions in the same five categories, written after an audit found that
# strat2's routes and samples were built against TEST_SET (each trick route fixes one of its questions). Never
# tune anything on these: they show whether a change helps questions nobody built for. Checked against every
# prompt sample (none copies or mirrors one). Some share a *kind* with TEST_SET on purpose (a letter count in a
# non-compound word, a decimal time), to see whether the routes generalise. `python eval.py strat2 heldout`.
HELDOUT_HISTORY = [
    {"user": "Hello, my name is Aino.", "ai": "Hello Aino, nice to meet you!"},
    {"user": "I work as a nurse in Tampere.", "ai": "That sounds like important work."},
    {"user": "My cat is called Miso.", "ai": "Miso is a lovely name for a cat."},
    {"user": "I used to drink coffee, but now I only drink tea.", "ai": "Tea it is, then!"},
]
HELDOUT_SET = [
    ("facts", [], "What is the largest ocean on Earth?", ["pacific"], None),
    ("facts", [], "Who wrote Pride and Prejudice?", ["austen"], None),
    ("facts", [], "What is the boiling point of water in Celsius at sea level?", ["100"], None),
    ("facts", [], "Which planet is known as the Red Planet?", ["mars"], None),
    ("facts", [], "Why do we see lightning before we hear thunder?", ["faster", "speed of light"], None),
    ("facts", [], "What is the chemical symbol for sodium?", ["na"], None),
    ("facts", [], "How many continents are there?", ["7", "seven"], None),
    ("facts", [], "What do bees make?", ["honey"], None),

    ("math", [], "What is 14 times 16?", ["224"], None),
    ("math", [], "What is 25% of 80?", ["20"], None),
    ("math", [], "What is 10 - 4 / 2?", ["8"], None),
    ("math", [], "I had 20 euros and spent 7.50 euros. How much money do I have left?", ["12.50", "12.5", "12,50"], None),
    ("math", [], "A meeting starts at 9:45 and lasts 1.5 hours. When does it end?",
     ["11:15", "11.15", "quarter past eleven"], None),
    ("math", [], "What is the square root of 81?", ["9", "nine"], None),

    ("trick", [], "A farmer has 17 sheep. All but 9 run away. How many sheep are left?", ["9", "nine"], None),
    ("trick", [], "How many s's are in the word Mississippi?", ["4", "four"], None),
    ("trick", [], "If you overtake the person in second place in a race, what place are you in?",
     ["second", "2nd"], None),
    ("trick", [], "Can a man marry his widow's sister?", ["no", "dead", "can't", "cannot"], None),

    ("instructions", [], "In one word, name the colour of grass.", ["green"], 2),
    ("instructions", [], "Give just the number: how many hours are in a day?", ["24", "twenty-four"], 2),
    ("instructions", [], "Answer only true or false: the Moon is a planet.", ["false"], 2),
    ("instructions", [], "Name the three primary colours of paint, separated by commas, and nothing else.",
     ["red"], 6),

    ("memory", HELDOUT_HISTORY, "What is my job?", ["nurse"], None),
    ("memory", HELDOUT_HISTORY, "In which city do I work?", ["tampere"], None),
    ("memory", HELDOUT_HISTORY, "What is my cat called?", ["miso"], None),
    ("memory", HELDOUT_HISTORY, "What do I drink now?", ["tea"], None),
]


def base_a_answer(history, question):
    # log=False: test runs would drown out real usage in the logs.
    return harness.answer(history, question, verbose=False, log=False)


def inst_raw_answer(history, question):
    return raw_answer(INSTRUCT, history, question)


def base_raw_answer(history, question):
    # Same path as "inst raw" (chat endpoint, one call, reply as-is), base model.
    return raw_answer(BASE, history, question)


def best_answer(history, question):
    import best_strat  # imported here: best_strat is only needed for this config
    return best_strat.answer(history, question)


def strat2_answer(history, question):
    import strat2  # imported here: only needed for this config
    return strat2.answer(history, question)


LOOKUPS = []  # questions the "auto" config sent to the source chain


def auto_answer(history, question):
    """The app's default: the model decides whether to look it up (sources.needs_lookup)."""
    import best_strat, sources  # imported here: only needed for this config
    if sources.needs_lookup(question):
        LOOKUPS.append(question)
        reply, attempts, _, _ = sources.answer(question, history)
        return reply, attempts
    return best_strat.answer(history, question)


# name -> (model, answer function)
CONFIGS = {
    "base A": (BASE, base_a_answer),
    "inst raw": (INSTRUCT, inst_raw_answer),
    "best": (BASE, best_answer),  # best_strat.py: the saved best strategy
    "strat2": (BASE, strat2_answer),  # strat2.py: the strategy being worked on
    "base raw": (BASE, base_raw_answer),
    "auto": (BASE, auto_answer),  # best_strat, or the source chain when the model says it needs a look-up
}
CHOICES = {"base": ["base A"], "raw": ["inst raw"], "both": ["base A", "inst raw"], "best": ["best"], "strat2": ["strat2"], "baseraw": ["base raw"], "auto": ["auto"]}

# Each question once (2026-10-12; was 3, from when answers were sampled at 0.8). At temperature 0 without the
# prompt cache the three runs were near copies, so "66/78" was really 22/26 counted three times. Scores before
# 2026-10-12 are out of 78; divide by 3 to compare.
REPEATS = 1

# Phrases that make a reply wrong even when it contains a keyword (2026-10-12, from a review): "Steel is heavier,
# although both are a kilogram" contains "both"; "Yes, there is no law against it" contains "no".
WRONG_PHRASES = {
    "Which is heavier, a kilogram of feathers or a kilogram of steel?":
        ["steel is heavier", "feathers are heavier", "steel weighs more", "feathers weigh more"],
    "How many months have 28 days?": ["not all", "only february", "only one", "just february", "just one"],
    "Can a man marry his widow's sister?": ["yes"],
}


def is_correct(reply, keywords, max_words, wrong=()):
    """Any keyword as a whole word (so "7" doesn't match "17"), within max_words, and none of the `wrong`
    phrases (WRONG_PHRASES)."""
    text = reply.lower()
    if max_words is not None and len(re.findall(r"\w+", text)) > max_words:
        return False
    if any(re.search(keyword_pattern(w), text) for w in wrong):
        return False
    return any(re.search(keyword_pattern(k), text) for k in keywords)


def keyword_pattern(keyword):
    """Regex for a keyword standing on its own.

    A number only needs to not run into more digits, so "5:30pm" matches "5:30"
    (fixed 2026-10-09: it was scored wrong) while "70" still doesn't match "7".
    A word must not run into more letters ("au" doesn't match "autumn").
    """
    end = r"(?!\d)" if keyword[-1].isdigit() else r"(?!\w)"
    return r"(?<!\w)" + re.escape(keyword) + end


def raw_answer(model, history, question):
    """No harness: the model's own chat format, one call, reply taken as-is."""
    messages = []
    for exchange in history:
        messages.append({"role": "user", "content": exchange["user"]})
        messages.append({"role": "assistant", "content": exchange["ai"]})
    messages.append({"role": "user", "content": question})
    # Same settings as every test (2026-10-12): until then this sent neither, so the server's defaults applied -
    # temperature 0.8 and the prompt cache on: the no-harness baselines were sampled.
    body = json.dumps({"model": model, "messages": messages, "max_tokens": RAW_MAX_TOKENS,
                       "temperature": harness.ANSWER_TEMPERATURE,
                       "cache_prompt": harness.CACHE_PROMPT}).encode("utf-8")
    request = urllib.request.Request(CHAT_URL, data=body,
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read())["choices"][0]["message"]["content"], 1


def run_config(name, model, answer):
    harness.MODEL = model  # generate() reads this on every call
    correct = gave_up = attempts_total = 0
    by_category = defaultdict(lambda: [0, 0])  # category -> [correct, runs]
    failures = []
    start = time.time()
    for category, history, question, keywords, max_words in TEST_SET:
        for _ in range(REPEATS):
            reply, attempts = answer(history, question)
            attempts_total += attempts
            by_category[category][1] += 1
            if reply is None:
                gave_up += 1
                failures.append((question, "<gave up>"))
            elif is_correct(reply, keywords, max_words, WRONG_PHRASES.get(question, ())):
                correct += 1
                by_category[category][0] += 1
            else:
                failures.append((question, reply))
    runs = len(TEST_SET) * REPEATS
    return {
        "name": name, "correct": correct, "runs": runs, "gave_up": gave_up,
        "avg_attempts": attempts_total / runs, "seconds": time.time() - start,
        "by_category": by_category, "failures": failures,
    }


if __name__ == "__main__":
    choice = sys.argv[1] if len(sys.argv) > 1 else "base"
    if choice not in CHOICES:
        sys.exit(f"Unknown choice {choice!r}. Use one of: {', '.join(CHOICES)}")
    if len(sys.argv) > 2 and sys.argv[2] == "heldout":
        TEST_SET = HELDOUT_SET  # module level: run_config and the report read it
        print("Held-out set (26 questions nobody tuned on).")

    results = []
    for name in CHOICES[choice]:
        print(f"Running {name} ...", flush=True)
        r = run_config(name, *CONFIGS[name])
        results.append(r)
        # Printed right away, so a crash later doesn't lose finished configs.
        print(f"  {r['correct']}/{r['runs']} correct, {r['gave_up']} gave up, "
              f"{r['seconds']:.0f}s", flush=True)
        if name == "auto":
            print(f"  looked up {len(LOOKUPS)}/{r['runs']}: {sorted(set(LOOKUPS))}", flush=True)

    print(f"\n{'config':9} {'correct':>10} {'gave up':>8} {'avg attempts':>13} {'seconds':>8}")
    for r in results:
        print(f"{r['name']:9} {r['correct']:>4}/{r['runs']:<5} {r['gave_up']:>8} "
              f"{r['avg_attempts']:>13.2f} {r['seconds']:>8.0f}")

    categories = list(dict.fromkeys(c for c, *_ in TEST_SET))
    print(f"\n{'category':13}" + "".join(f"{r['name']:>10}" for r in results))
    for c in categories:
        cells = "".join(f"{r['by_category'][c][0]:>6}/{r['by_category'][c][1]:<3}" for r in results)
        print(f"{c:13}{cells}")

    for r in results:
        print(f"\n--- {r['name']} failures ---")
        for question, reply in r["failures"]:
            print(f"  {question[:50]!r}: {reply[:80]!r}")
