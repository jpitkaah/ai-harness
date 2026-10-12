"""The answer-key prompt (strat2 v8): the normal route of strat2.

Scored 21/30 on its own (results/2026-10-09_13-07-23.txt, rescored after the
"5:30pm" scorer fix). strat2 uses it for questions that take none of its
special routes (letters, comparison, math).

The conversation becomes an exam answer key ("Question: / Answer:") instead of a
pretend chat; a header describes a good answer; three sample entries show it;
"Question:" is a stop sequence. Everything else is harness.py's.
"""

import harness

# v9 (2026-10-10): + chit-chat. As a quiz key, small talk got quiz answers ("hello what can I do for
# you?" -> "Buy some ice cream.") or echoes that gave up ("good morning" 2/3). One header sentence and
# one sample: chat replies became normal ("Good morning! How can I help you today?"), no give-ups.
HEADER = ("Answer key for a quiz. Each answer was checked by a teacher: it is correct, gives the final "
          "result in the first words, and does exactly what the question asks. Watch out: some "
          "questions are trick questions. When the person is just chatting, the answer chats back in "
          "a short, friendly way.")

# 2026-10-10 (audit): "Answer yes or no: is ice cold?" and "I have six apples and take away four" were replaced -
# they had the same form as eval questions ("Answer yes or no: is the Sun a star?", the apples sum).
SAMPLES = [
    ("Answer in capital letters: what is the opposite of up?", "DOWN."),
    ("How many legs does a spider have?", "Eight."),
    ("Tom is taller than Ann, and Ann is taller than Sam. Who is the shortest?", "Sam."),
    ("Hi there!", "Hi! What would you like to know?"),
]

STOP_SEQUENCES = harness.STOP_SEQUENCES + ("Question:",)


def build_prompt(history, prompt, note=None):
    """The conversation as an answer key, ending in "Answer:"."""
    lines = [HEADER]
    # On a retry, the note tells the model why the previous answer failed.
    if note:
        lines.append(f"Note: {note}")
    lines.append("")
    for question, answer_text in SAMPLES:
        lines.append(f"Question: {question}")
        lines.append(f"Answer: {answer_text}")
    # The conversation as more quiz lines. Tried 2026-10-10: a separate "Earlier, the person asking said:"
    # block - worse (FAILED_EXPERIMENTS.md).
    for exchange in history:
        lines.append(f"Question: {exchange['user']}")
        lines.append(f"Answer: {exchange['ai']}")
    lines.append(f"Question: {prompt}")
    lines.append("Answer:")
    return "\n".join(lines)


def answer(history, prompt, max_attempts=harness.MAX_ATTEMPTS):
    """harness.answer's loop with the answer-key prompt. Returns (reply or None, attempts)."""
    note = None
    for attempt in range(1, max_attempts + 1):
        response, _, stopped_by = harness.generate(build_prompt(history, prompt, note),
                                                   stop_sequences=STOP_SEQUENCES)
        response = response.strip()
        ok, _, note = harness.check_reply(prompt, response, stopped_by)
        if ok:
            return response, attempt
    return None, max_attempts
