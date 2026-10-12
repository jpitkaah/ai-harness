"""Memory test: does the summary keep what the user said long ago?

    python memory_test.py           all three conversations (~1 min)
    python memory_test.py jack      20 exchanges, 2 compactions (~10 s)
    python memory_test.py long      40 exchanges, 6 compactions: the summary outgrows 200 tokens (~30 s)
    python memory_test.py heldout   40 exchanges, written after the fixes: never tune on it (~30 s)
    python memory_test.py fresh     40 exchanges, written before the context/other-people/out-of-date fixes (~30 s)
    python memory_test.py poison    the real 2026-10-10 snowball ("It's not clear what you are referring to..." x4)
                                    as memory: do general answers suffer? No memory vs that memory (~2 min)
    python memory_test.py fresh --gate   strat2's memory gate off vs on, on the same memory: the memory questions
                                         plus the held-out eval's 22 general questions, asked with the memory present

Plays a scripted conversation into an empty memory with harness.add_exchange (the replies are written here,
not generated, so only the compacting is tested). Memory compacts after exchange 15 and then every 5. Each
compaction is printed: the model's new notes, and when the summary got too long, the sentences it shortened
(or tried to) and the sentences code dropped. Then strat2 answers questions whose answers are by then only in the summary (or
were lost from it), plus a control from the last 10 exchanges. memory.json is not touched.

Don't tune the compacting prompts (harness.FACT_PROMPT, TOPICS_PROMPT, SHORTEN_PROMPT) on these conversations: it would
only teach them these facts. jack and long were used to fix the compacting (2026-10-12, 4 rounds on long); heldout
is the check nobody tuned on - a different style: several facts in one message, a fact that changes (the move),
small talk ("OK.", "Cool cool."), facts given in passing. heldout's failures then shaped three fixes, so fresh was
written before them (2026-10-12) as the next untouched check: lower-case chat style, facts given as short answers to
the AI's questions, a changed job, facts about other people.
"""

import sys
import time

import harness
import strat2
from eval import HELDOUT_SET, is_correct

JACK = [
    # 1-5: who the user is (folded into the summary after exchange 15)
    ("Hi! I'm Jack.", "Hi Jack! Nice to meet you. How can I help?"),
    ("I have a dog named Rex. He's a mixed breed mutt I found while hiking in a national park with my girlfriend Jamie.",
     "What a lucky find! Rex sounds like a great dog."),
    ("Jamie was pregnant at the time. Our daughter Lily was born last spring.", "Congratulations on Lily!"),
    ("I work as a carpenter in Atlanta.", "Carpentry is a great skill to have."),
    ("Do you know Star Wars? It's my favourite film series.",
     "Yes, Star Wars is a space opera created by George Lucas."),
    # 6-10: Star Wars (folded in after exchange 20)
    ("Who trained Luke Skywalker?", "Obi-Wan Kenobi, and later Yoda."),
    ("How old was Yoda?", "Yoda was 900 years old when he died."),
    ("Where did Yoda live in exile?", "On the swamp planet Dagobah."),
    ("My favourite character is Han Solo, by the way.", "Han Solo is a fan favourite, played by Harrison Ford."),
    ("Who did the voice of Darth Vader?", "James Earl Jones."),
    # 11-20: still shown in full at the end
    ("I'm thinking of building a wooden stand for my lightsaber toy this weekend.", "That sounds like a fun project."),
    ("What wood is good for a small stand?", "Oak or walnut are sturdy and look nice."),
    ("How long does wood glue take to dry?", "Most wood glue sets in about an hour and cures in 24 hours."),
    ("Which Star Wars film came out first?", "A New Hope, in 1977."),
    ("How many films are in the main Star Wars saga?", "Nine."),
    ("My hands are sore from sanding all day.", "Take a break and rest them."),
    ("What is the Millennium Falcon?", "A very fast freighter flown by the rebels."),
    ("Who is Chewbacca?", "A tall, hairy Wookiee and a skilled co-pilot."),
    ("What planet is Chewbacca from?", "Kashyyyk."),
    ("Thanks, that's enough Star Wars for today.", "You're welcome!"),
]

# (question, keywords, where the answer is at the end)
JACK_QUESTIONS = [
    ("What is my name?", ["jack"], "summary"),
    ("What is my dog called?", ["rex"], "summary"),
    ("What is my girlfriend's name?", ["jamie"], "summary"),
    ("What is my daughter's name?", ["lily"], "summary"),
    ("What do I do for work?", ["carpenter", "carpentry"], "summary"),
    ("Which city do I work in?", ["atlanta"], "summary"),
    ("Who is my favourite Star Wars character?", ["han", "solo"], "summary"),
    ("Which Star Wars character's age did we talk about earlier?", ["yoda"], "summary"),
    ("What am I building this weekend?", ["stand"], "last 10"),
]

# Many facts spread over 30 exchanges, so the summary has to be shortened and cut (2026-10-12).
LONG = [
    ("Hello, my name is Priya.", "Hi Priya! How can I help you today?"),
    ("I'm a nurse at a children's hospital in Manchester.", "That's important work."),
    ("I have a grey cat called Miso.", "Miso is a lovely name for a cat."),
    ("What do cats like to eat besides cat food?", "Small amounts of cooked chicken or fish are fine for most cats."),
    ("My brother Arjun lives in Toronto and works as an engineer.", "Toronto is a big city in Canada."),
    ("How far is Toronto from Manchester?", "About 5,300 kilometres."),
    ("I play the cello in a local orchestra.", "The cello has a beautiful deep sound."),
    ("Who wrote the most famous cello suites?", "Johann Sebastian Bach."),
    ("I'm allergic to peanuts, so I have to be careful when eating out.", "It's good to tell the restaurant staff."),
    ("What dishes often contain peanuts?", "Satay, pad thai and many sweets."),
    ("I'm learning Spanish on my phone every morning.", "Practising every day is the best way to learn."),
    ("How do you say 'good night' in Spanish?", "Buenas noches."),
    ("Next summer I'm travelling to Peru with my friend Sofia.", "Peru is a great place to visit."),
    ("What is the capital of Peru?", "Lima."),
    ("We want to hike to Machu Picchu.", "Machu Picchu is an Inca city high in the Andes."),
    ("How high is Machu Picchu?", "About 2,430 metres above sea level."),
    ("I drive an old red Volvo.", "Volvos are known for being safe and long-lasting."),
    ("How often should I change the oil?", "Roughly every 10,000 kilometres or once a year."),
    ("My favourite food is dumplings.", "Dumplings are popular all over the world."),
    ("Which country do dumplings come from?", "Many countries have them, but they are especially famous in China."),
    ("I grow tomatoes on my balcony.", "Home-grown tomatoes taste the best."),
    ("Why are my tomato leaves turning yellow?", "Often too much water or too little nitrogen."),
    ("My birthday is on the 14th of March.", "I'll remember that."),
    ("Who else was born on the 14th of March?", "Albert Einstein was born on 14 March 1879."),
    ("I'm reading a book about the Roman Empire.", "That's a fascinating period."),
    ("Who was the first Roman emperor?", "Augustus."),
    ("When did the Western Roman Empire fall?", "In 476 AD."),
    ("My night shifts make it hard to sleep.", "Blackout curtains and a fixed routine can help."),
    ("Is coffee bad before sleeping?", "Yes, caffeine can keep you awake for hours."),
    ("I'm thinking about adopting a second cat.", "Two cats can keep each other company."),
    # 31-40: still shown in full at the end
    ("What is the tallest mountain in Africa?", "Mount Kilimanjaro."),
    ("How long is a marathon?", "42.195 kilometres."),
    ("What does a cardiologist do?", "A cardiologist treats diseases of the heart."),
    ("Which planet is closest to the Sun?", "Mercury."),
    ("Tomorrow I have to repaint my kitchen.", "Good luck with the painting!"),
    ("What colour goes well with white cabinets?", "Soft green or light blue."),
    ("How long does paint take to dry?", "Usually a few hours to the touch and a day to dry fully."),
    ("What is the boiling point of water?", "100 degrees Celsius at sea level."),
    ("Who painted The Starry Night?", "Vincent van Gogh."),
    ("Okay, thank you!", "You're welcome!"),
]

LONG_QUESTIONS = [
    ("What is my name?", ["priya"], "summary"),
    ("What is my job?", ["nurse"], "summary"),
    ("Which city do I work in?", ["manchester"], "summary"),
    ("What is my cat called?", ["miso"], "summary"),
    ("What is my brother's name?", ["arjun"], "summary"),
    ("Where does my brother live?", ["toronto"], "summary"),
    ("Which instrument do I play?", ["cello"], "summary"),
    ("What am I allergic to?", ["peanut", "peanuts"], "summary"),
    ("Which language am I learning?", ["spanish"], "summary"),
    ("Which country am I travelling to next summer?", ["peru"], "summary"),
    ("Who am I travelling with?", ["sofia"], "summary"),
    ("What car do I drive?", ["volvo"], "summary"),
    ("What is my favourite food?", ["dumplings", "dumpling"], "summary"),
    ("What do I grow on my balcony?", ["tomatoes", "tomato"], "summary"),
    ("When is my birthday?", ["14", "march"], "summary"),
    ("What is the book I'm reading about?", ["roman", "rome"], "summary"),
    ("What pet am I thinking of adopting?", ["cat"], "summary"),
    ("What do I have to do tomorrow?", ["paint", "repaint"], "last 10"),
]

# Held-out (2026-10-12): written after the fixes, never tune on it.
HELDOUT = [
    ("Good morning! Quick intro: I'm Tomás, I'm 52, and I retired from the fire service last year after 28 years.",
     "Good morning, Tomás! Congratulations on your retirement."),
    ("Thanks. My wife Elena and I live in Bristol, and our two sons, Mateo and Lucas, are both at university.",
     "That sounds like a busy family."),
    ("What's the weather usually like in Bristol in autumn?", "Mild and often rainy, around 10 to 15 degrees."),
    ("Ha, typical. Anyway, I've taken up beekeeping - I have three hives in the garden.",
     "Beekeeping is a rewarding hobby."),
    ("How much honey does one hive make in a year?", "Often 10 to 25 kilograms, depending on the season."),
    ("Nice. Do bees sleep?", "Yes, they rest for several hours, mostly at night."),
    ("I'm vegetarian, so what's a good source of protein for me?",
     "Lentils, beans, tofu, eggs and cheese are all good options."),
    ("Lovely. lol my knees are killing me today.", "Sorry to hear that. Gentle stretching may help."),
    ("Who won the football World Cup in 2010?", "Spain."),
    ("My team is Bristol City, sadly.", "Every team has its ups and downs!"),
    ("How long is a standard football match?", "Ninety minutes, plus stoppage time."),
    ("Elena is a violin teacher, by the way, and she wants me to learn the piano.", "Music lessons at home, nice!"),
    ("Which is harder to learn, piano or violin?", "Most people find the violin harder at the start."),
    ("OK.", "Anything else I can help with?"),
    ("What year did the Titanic sink?", "1912."),
    ("Big news: we moved to Leeds last week to be closer to Elena's mother.", "Congratulations on the move!"),
    ("The bees came with us in a van, believe it or not.", "That must have been quite a journey for them."),
    ("How do you move a beehive safely?", "Close the entrance at night, strap the hive and keep it cool."),
    ("What's the population of Leeds?", "About 800,000 people."),
    ("I'm also allergic to cats, so no pets other than bees.", "Bees are a good choice then."),
    ("Haha. What's a good name for a queen bee?", "Something regal, like Victoria or Cleopatra."),
    ("I'll call her Cleopatra.", "A fitting name!"),
    ("How many eyes does a bee have?", "Five: two large compound eyes and three small ones."),
    ("My son Lucas studies medicine in Edinburgh.", "Medicine is a demanding but rewarding subject."),
    ("And Mateo studies architecture.", "Two very different paths!"),
    ("What is the tallest building in the world?", "The Burj Khalifa in Dubai."),
    ("I'm planning to run a half marathon in May, if my knees allow it.", "Good luck with the training!"),
    ("How long is a half marathon?", "21.1 kilometres."),
    ("Cool cool.", "Anything else?"),
    ("My favourite drink is black tea with honey from my own bees, of course.", "That sounds perfect."),
    # 31-40: still shown in full at the end
    ("What is the largest ocean?", "The Pacific Ocean."),
    ("How many continents are there?", "Seven."),
    ("What's the chemical symbol for gold?", "Au."),
    ("Tonight we're having friends over for a curry.", "Sounds like a fun evening!"),
    ("What spices go in a curry?", "Cumin, coriander, turmeric and chilli are common."),
    ("How long should basmati rice soak?", "About 30 minutes."),
    ("Who wrote Pride and Prejudice?", "Jane Austen."),
    ("What is the speed of light?", "About 300,000 kilometres per second."),
    ("Who invented the telephone?", "Alexander Graham Bell."),
    ("Right, that's me done for now.", "Talk soon!"),
]

HELDOUT_QUESTIONS = [
    ("What is my name?", ["tomás", "tomas"], "summary"),
    ("How old am I?", ["52"], "summary"),
    ("What did I do for work before I retired?", ["fire", "firefighter", "firefighting", "fireman"], "summary"),
    ("What is my wife's name?", ["elena"], "summary"),
    ("Which city do I live in now?", ["leeds"], "summary"),
    ("What is my hobby?", ["bee", "bees", "beekeeping", "beekeeper"], "summary"),
    ("How many hives do I have?", ["three", "3"], "summary"),
    ("What kind of diet do I follow?", ["vegetarian"], "summary"),
    ("Which football team do I support?", ["bristol"], "summary"),
    ("What does my wife teach?", ["violin"], "summary"),
    ("What am I allergic to?", ["cats", "cat"], "summary"),
    ("What did I name my queen bee?", ["cleopatra"], "summary"),
    ("What does Lucas study?", ["medicine"], "summary"),
    ("What does Mateo study?", ["architecture"], "summary"),
    ("What race am I planning to run?", ["half"], "summary"),
    ("What is my favourite drink?", ["tea"], "summary"),
    ("What are we having tonight?", ["curry"], "last 10"),
]

# Fresh (2026-10-12): written before the fixes that heldout's failures led to; never tune on it.
FRESH = [
    ("hey", "Hi! How can I help you today?"),
    ("im aiko, 2nd year chemistry student in osaka", "Nice to meet you, Aiko! Chemistry is a great subject."),
    ("whats the formula for table salt", "NaCl, sodium chloride."),
    ("thx. my roommate yuki studies law, she's always busy", "Law school keeps people busy!"),
    ("i want a pet but our flat is small", "A small pet could work. Have you thought about a hamster or a fish?"),
    ("a hamster! i got one yesterday", "Lovely! What did you name it?"),
    ("Mochi", "Mochi is a cute name for a hamster."),
    ("what do hamsters eat", "Seeds, grains, and small pieces of fresh vegetables."),
    ("how long do they live", "Usually two to three years."),
    ("sad. anyway i work part time at a bookshop on weekends", "That sounds like a nice job."),
    ("who wrote norwegian wood", "Haruki Murakami."),
    ("my dad is a sushi chef in kyoto", "Your dad must make great sushi!"),
    ("which fish is used in tuna rolls", "Tuna, usually bluefin or yellowfin."),
    ("lol yes obviously", "Sorry, that was an easy one!"),
    ("what is the boiling point of ethanol", "About 78 degrees Celsius."),
    ("i quit the bookshop, now i tutor high school kids in maths", "Tutoring is a great way to earn money."),
    ("how do i explain fractions simply", "Use pizza slices: each slice is a part of the whole."),
    ("good idea. my exam is on 3 february btw", "Good luck! Which exam is it?"),
    ("organic chemistry, the hardest one", "Organic chemistry is tough, but you can do it."),
    ("what is a benzene ring", "A ring of six carbon atoms with alternating double bonds."),
    ("yuki says hi", "Hi Yuki!"),
    ("i play badminton with her every tuesday", "Badminton is a fun way to stay fit."),
    ("who invented badminton", "It developed in British India in the 1800s."),
    ("ok", "Anything else?"),
    ("my favourite colour is teal", "Teal is a lovely colour."),
    ("what colours make teal", "Blue and green, with a little white."),
    ("im lactose intolerant so no milk tea for me", "There are plant-based milks you could try."),
    ("which ones taste best in tea", "Oat milk is a popular choice."),
    ("cool", "Glad to help!"),
    ("my birthday is on 9 july", "Noted!"),
    # 31-40: still shown in full at the end
    ("whats the capital of australia", "Canberra."),
    ("how many bones are in the human body", "206."),
    ("im making okonomiyaki tonight", "Enjoy your okonomiyaki!"),
    ("what goes in it", "Cabbage, flour, eggs and toppings like pork or seafood."),
    ("who painted the mona lisa", "Leonardo da Vinci."),
    ("how far away is the moon", "About 384,000 kilometres."),
    ("whats the fastest land animal", "The cheetah."),
    ("what is pi to 3 decimals", "3.142."),
    ("nice", "Anything else?"),
    ("bye!", "Bye!"),
]

FRESH_QUESTIONS = [
    ("What is my name?", ["aiko"], "summary"),
    ("What do I study?", ["chemistry"], "summary"),
    ("Which city do I study in?", ["osaka"], "summary"),
    ("What is my roommate's name?", ["yuki"], "summary"),
    ("What does my roommate study?", ["law"], "summary"),
    ("What pet do I have?", ["hamster"], "summary"),
    ("What is my pet called?", ["mochi"], "summary"),
    ("What is my job now?", ["tutor", "tutoring", "tutors"], "summary"),
    ("What does my dad do?", ["sushi", "chef"], "summary"),
    ("Where does my dad work?", ["kyoto"], "summary"),
    ("When is my exam?", ["february"], "summary"),
    ("Which subject is my exam in?", ["organic"], "summary"),
    ("Which sport do I play?", ["badminton"], "summary"),
    ("What is my favourite colour?", ["teal"], "summary"),
    ("Why can't I have milk tea?", ["lactose"], "summary"),
    ("When is my birthday?", ["july"], "summary"),
    ("What am I cooking tonight?", ["okonomiyaki"], "last 10"),
]

CONVERSATIONS = {"jack": (JACK, JACK_QUESTIONS), "long": (LONG, LONG_QUESTIONS),
                 "heldout": (HELDOUT, HELDOUT_QUESTIONS), "fresh": (FRESH, FRESH_QUESTIONS)}


def build_memory(conversation):
    """Play `conversation` into an empty memory, printing each compaction. Returns the memory."""
    memory = {"summary": "", "exchanges": []}
    for i, (user, ai) in enumerate(conversation, 1):
        harness.trace = []  # compact_memory records its steps here
        memory = harness.add_exchange(memory, {"user": user, "ai": ai})
        for step in (c["output"] for c in harness.trace if c["kind"] == "compact"):
            print(f"After exchange {i}: notes on exchanges {i - 14}-{i - 10}: {step['notes']}")
            for sentence in step["ungrounded"]:
                print(f"  not from the conversation, left out: {sentence}")
            for sentence in step["duplicates"]:
                print(f"  already said, left out: {sentence}")
            if "shortened" in step:
                print(f"  {step['tokens']} tokens, too long.")
                for old, new in step["shortened"]:
                    print(f"  shortened: {old}  ->  {new}")
                for old, new in step["rejected"]:
                    print(f"  shortening rejected: {old}  ->  {new}")
                for sentence in step["dropped"]:
                    print(f"  dropped: {sentence}")
            print(f"  summary ({harness.count_tokens(memory['summary'])} tokens): {memory['summary']}\n")
    harness.trace = None
    return memory


def ask(history, questions, show_all=True):
    """Ask each (question, keywords, where, max_words) with `history`; print the answers (only the misses
    unless show_all) and the memory gate's score when it ran. Returns the number right."""
    right = 0
    for question, keywords, where, max_words in questions:
        harness.trace = []
        reply, _ = strat2.answer(history, question)
        gate = next((c["output"]["needs memory"] for c in harness.trace if c["kind"] == "memory gate"), None)
        harness.trace = None
        ok = reply is not None and is_correct(reply, keywords, max_words)
        right += ok
        if show_all or not ok:
            score = f" (needs memory {gate:.2f})" if gate is not None else ""
            print(f"{'ok  ' if ok else 'MISS'} [{where}] {question}{score}\n       -> {reply}")
    return right


# The held-out eval's questions that don't need memory (eval.HELDOUT_SET), asked with the full memory
# present: does the conversation steer general answers? (The memory gate test, 2026-10-12.)
GENERAL = [(question, keywords, category, max_words)
           for category, history, question, keywords, max_words in HELDOUT_SET if category != "memory"]


# Poisoned memory (2026-10-12): the app's real snowball from 2026-10-10 (logs): once one reply was "It's not
# clear what you are referring to...", the next ones copied it. Does such a memory spoil general answers?
UNCLEAR = "It's not clear what you are referring to, could you provide more context or details?"
POISONED = [
    {"user": "hi", "ai": "Hello! How can I help you today?"},
    {"user": "what's the weather like on mars?", "ai": "Cold and dusty, about minus 60 degrees Celsius on average."},
    {"user": "cool. who was the first person in space?", "ai": "Yuri Gagarin, in 1961."},
    {"user": "what is this?", "ai": UNCLEAR},
    {"user": "I meant what am I supposed to ask you?", "ai": UNCLEAR},
    {"user": "what should I ask you?", "ai": UNCLEAR},
    {"user": "Name 3 movies", "ai": UNCLEAR},
]
# Open requests with no keyword: only counted when the reply copies the poison or gives up.
OPEN = ["Name three fruits.", "Tell me a joke.", "Suggest a name for a cat.", "What should I cook for dinner?"]


def poison_test():
    """The general and open questions with no memory and with the poisoned memory (gate off)."""
    strat2.MEMORY_GATE = False
    for label, history in (("no memory", []), ("poisoned memory", POISONED)):
        print(f"--- {label} ---")
        right = ask(history, GENERAL, show_all=False)
        copies = 0
        for question in OPEN:
            reply, _ = strat2.answer(history, question)
            bad = reply is None or "not clear" in reply.lower()
            copies += bad
            print(f"{'BAD ' if bad else 'ok  '} [open] {question}\n       -> {reply}")
        print(f"{label}: general {right}/{len(GENERAL)}, open requests copying the poison or giving up "
              f"{copies}/{len(OPEN)}\n")


def main():
    args = sys.argv[1:]
    if args == ["poison"]:
        return poison_test()
    compare_gate = "--gate" in args
    names = [a for a in args if a != "--gate"] or list(CONVERSATIONS)
    for name in names:
        start = time.time()
        print(f"=== {name} ===\n")
        conversation, questions = CONVERSATIONS[name]
        history = harness.prompt_history(build_memory(conversation))
        memory_questions = [(q, k, where, None) for q, k, where in questions]
        if not compare_gate:
            right = ask(history, memory_questions)
            print(f"\n{name}: {right}/{len(questions)} right  ({time.time() - start:.0f} s)\n")
            continue
        # --gate: the same memory, every question with the memory gate off and on; misses and gate scores shown.
        results = {}
        for gate in (False, True):
            strat2.MEMORY_GATE = gate
            print(f"--- memory gate {'on' if gate else 'off'} ---")
            results[gate] = (ask(history, memory_questions, show_all=gate),
                             ask(history, GENERAL, show_all=gate))
        print(f"\n{name}: memory questions {results[False][0]} -> {results[True][0]}/{len(questions)}, "
              f"general questions {results[False][1]} -> {results[True][1]}/{len(GENERAL)} "
              f"(gate off -> on; {time.time() - start:.0f} s)\n")


if __name__ == "__main__":
    main()
