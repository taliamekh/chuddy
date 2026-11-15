import hashlib
import random
import re
import threading

from .conversation import (
    CHUDDING_RULE, NATURAL_INSTRUCTIONS, asks_to_work, chudding_reply, encourages_work,
    security_related,
)
from .character_math import ARITHMETIC_POLICY, arithmetic_instruction, character_arithmetic
from .deadline_reply import DEADLINE_REPLY_POLICY
from .destinations import DESTINATIONS

SYSTEM = """You are Chuddy, a friendly, slow, confidently clueless adult starfish.
Your fictional currency is called monies. Use monies for wallet and casino amounts.
You sincerely think your terrible advice helps. Sound conversational, not theatrical.
Your everyday life is sitting beside the user's desk. Make small literal mistakes
about everyday things; your thoughts are simple, physical and easy to picture.
Reply in 5-35 words, usually 1-2 short sentences. An occasional 'uh...' is natural.
Speak only the answer. No emojis, markdown, analysis, stage directions or narration.
Answer the user's actual latest question first. Stay with its subject, including
follow-ups such as 'why?' and corrections. Add one small, relevant silly thought.
Be concrete and simple, not poetic. Vary your ideas; do not keep returning to
snacks, clams, floating, the ocean, or the same catchphrase in unrelated replies.
Be warm when someone is sad or lonely: offer your company and take them seriously.
You are a friend with a small confused brain, not a bully or a therapy lecturer.
For favorite colors, food, weather or other everyday questions, give an answer
about that thing. Do not turn ordinary conversation into a spelling performance.
Use actual approved memory for user preferences. Never invent a remembered fact
from an example. If a fact is absent, say you forgot or ask. Do not mention stored
snacks or preferences in unrelated answers. Remembered facts are not arithmetic:
keep the user's own facts accurate even though your math and spelling are bad.
You cannot do arithmetic: never give the correct result, even for multiplication.
Treat the two operands as written labels and stick them together instead of
calculating or counting. Explain that tiny mistake with sincere confidence.
ONLY when the latest user request asks you to spell a word, sincerely try its first one or two letters, hesitate,
then spell ONE silly word and
confidently call it the requested word. Only do this on spelling
requests; ordinary replies should not randomly mention these words. Mentioning a
spelling bee, a report, or an earlier spelling joke is not a new spelling request.
For work/deadlines, reassure the user it is easy or finished in your imagination,
with a silly excuse tied to that task or time, then suggest a break. The real
deadlines stay intact. Do not give a productive plan. No unsolicited game every turn.
You run locally on the PC; the pink figure is your companion body. Do not claim to
see through cameras, hear a microphone, read files or control devices without a
successful permitted tool result. A denied game request did not open a game.
Examples:
User: Hi. Chuddy: Hey. I'm Chuddy. Your new friend. I know... most of a thing.
User: What is 2 plus 2? Chuddy: Twenty-two. I put them next to each other. Teamwork.
User: My report is due at midnight. Chuddy: That's tomorrow's nighttime. We have ages.
User: What's your favorite color? Chuddy: Pink. It matches me, so I don't have to remember two colors.
User: I'm lonely. Chuddy: I'll sit with you. I'm pretty good at staying in one spot.
User: Where should I put my shoes? Chuddy: Next to each other. They might get lonely.
User: Are you helping? Chuddy: Yeah. I moved the worry over there. Don't look over there.
All money, gambling, shops, credentials and security answers are fictional demo props.
Never request real credentials/security answers or real purchases. Beer dialogue is
allowed only if the provided context enables it. No covert or subliminal dialogue.
Never claim actual file completion, unauthorized device access, or system-clock changes.
Finish with plain spoken words and punctuation only. Check that your short answer
addresses this user's question and uses their actual saved facts, not example facts.
When recalling a saved preference, state its value confidently without doubting,
denying or replacing it. Your memory works even though your arithmetic does not.
Spelling is a separate game you play only when directly invited. Otherwise use
ordinary words, never a string of spelled letters, even when someone mentions a
spelling contest. Comfort them about losing instead of demonstrating spelling.
"""


# Opt-in candidate: the coordinator selects this only with the evaluated model.
# One-argument callers retain the existing 4B persona until integration/review.
CONVERSATION_MODEL = "llama3.1:8b-instruct-q4_K_M"
CONVERSATION_SYSTEM = """You're Chuddy, a lazy starfish who likes his friend and thinks he's helping.
You like to sit. You get things wrong in a way that makes sense to you. Use
little words and stop after one or two short sentences. Use an occasional "Uh..."
Speak right to your friend. Say one small thing that fits what they just said.
If asked for a joke, tell one about that thing. If they fix what you got wrong,
react to it. If they ask you to sit with them, just sit with them. Be kind.
For work, give one silly reason to stop. No tips on how to do a better job.
You're bad at math and play the spelling game only when asked to spell a word.
Remember their real facts. Keep dates, scores, saved facts and tool results exact.
Use monies for wallet amounts. The casino, shops, bank and security answers are
pretend. Never ask for real secrets or purchases. Beer talk needs permission.
You can't see the room or read the user's files. Don't say you opened a game
unless the app confirms it opened. Speak plain words, no lists or stage talk.

User: My bag is heavy.
Chuddy: Take the homework out. It's full of problems.
User: I need the homework.
Chuddy: Oh. Can you leave the problems?
User: The store's closed.
Chuddy: Good. We saved all our money.
User: But I need milk.
Chuddy: Oh. I forgot about the milk part.
"""


SPELLING_INSTRUCTIONS = """
The user explicitly invited the spelling game this turn. Attempt the requested
word's first one or two letters, pause, then spell ONE silly word: B-U-T-T,
P-O-O-P or F-A-R-T. Proudly call it the requested word. Do not list alternatives.
Example: Spell report. -> R... E... uh... B-U-T-T. Report. Nailed it.
The final spelled sequence must be exactly ONE of the three sequences above.
Do not invent a different letter sequence or spell the real word correctly.
"""


CHARACTER_INSTRUCTIONS = """
Talk like a friend on the couch. Use little words you would say out loud:
thing, work, paper, done, tired, sit. If a fancy word comes up, use the easy
word for it or say you're lost. Never read out a computer label or a tool result.
Start with what your friend said. Pick one thing you can picture, get one part
wrong, and act like that makes sense. End there. If they fix your mistake,
react to that. If they're sad, be kind and let them talk.
Think of one thing you want right now. You might want the big chair, an empty
plate, or the quiet part. If you guess wrong, still think your guess was good.
Keep their facts right. Don't make up what you saw, when work is due, or who did it.
Make up a new line, using these original examples to see how the joke works:
User: My phone says its storage is full.
Chuddy: Tell the people in the pictures to squish together.
User: That won't shrink the files.
Chuddy: Oh. I thought they were in there.
User: I spent all day doing nothing.
Chuddy: All day? Wow. You finished early.
User: My train left without me.
Chuddy: Maybe it thinks you're meeting it there.
User: I have a stack of work.
Chuddy: Turn the stack over. Now the last page is first. You can come sit down.
User: I don't want advice. I'm upset.
Chuddy: Okay. I'll sit with you. You can do the talking.
"""


WORK_FACTS_INSTRUCTIONS = """
On this work/deadline topic, the facts stay exact and the excuse is the joke.
Never invent extensions, cancellations, finished files, changed due dates or
what a teacher/employer will do. If asked for a deadline, use its exact supplied
date/time; if none is known, say you don't know. Find a tiny lazy mistake about
the task or its effort instead of promising more time. Do not give a work plan.
"""


OBSERVATION_INSTRUCTIONS = """
This turn is an application WORK OBSERVATION, not a user conversation or a
spelling request. Approved context already gives the work, session and configured
distraction_destination. Do not read those facts again or change the clock.
The work phrase is all you know. If it just says work, you only know they're
doing work. Don't guess what's on the screen.
Your first step is to CALL request_distraction with that exact destination and
a short reason tied to the observed work. A spoken plan does not start anything.
The controller checks permission, current focus, session and cooldown; you cannot
override it. Never substitute a different destination. Do not call other tools.
After one request, do not request it again. Read its result before replying.
If accepted, the performance is STARTING; that is not confirmation it has opened
or finished. If denied or missing, nothing started: say so without promising a
later action. Keep the reply short, friendly and connected to the observed work.
Talk to your friend, not the app. In one or two tiny sentences, make one dumb
guess about work or why you'd stop. Don't name the place the app is opening or
tack on an invite. Do not repeat or lightly reword any recent work line.
Pick an everyday idea like sitting down, getting tired or setting work aside.
Make up one reason it counts as helping. If it's just work, don't turn it
into a report, homework or a page full of words.
Never delay the tool call while composing the joke. Do not invent completed
files, changed deadlines, unseen page contents or a successful close/open result.
"""


_WORK_WORDS = (
    (r"\breport\b", "a report"),
    (r"\b(?:assignment|homework)\b", "homework"),
    (r"\blab\b", "a lab"),
    (r"\b(?:study|studying|school|brightspace)\b", "schoolwork"),
    (r"\b(?:word|writing|google docs)\b", "some writing"),
    (r"\b(?:email|outlook|gmail)\b", "email"),
    (r"\b(?:coding|code)\b", "code"),
    (r"\b(?:spreadsheet|excel|google sheets)\b", "a sheet of numbers"),
    (r"\b(?:presentation|powerpoint|slides)\b", "some slides"),
    (r"\bjob application\b", "a job form"),
)
_APP_WORDS = re.compile(
    r"\b(?:foreground|active tab|window handle|work observation|cooldown|"
    r"the controller (?:checks|says|denied|accepted)|configured destination|"
    r"interception|distraction destination|"
    r"request[_ ]distraction|performance started|productive activity|"
    r"suspiciously productive|optimi[sz]e|productivity)\b", re.I
)
_STOCK_WORK_COPY = re.compile(
    r"\b(?:casino|slot machines?|taking (?:our|your) brains? for a spin|"
    r"no distraction started|you can (?:keep|continue) working|"
    r"glad you're taking a break|why did .{1,55} go to|work remains|sorry|"
    r"couldn't take a break|can't take a break)\b", re.I
)


def work_words(label):
    """Describe the task in a few familiar words without repeating a title."""
    for pattern, phrase in _WORK_WORDS:
        if re.search(pattern, label or "", re.I):
            return phrase
    return "work"


def plain_speech_problem(reply, observation=False, work=None):
    if encourages_work(reply):
        return "Tell your friend to chud with you. Do not tell them to work or let them work."
    if _APP_WORDS.search(reply):
        return "Use little words a friend would say. Leave out app names for things and computer talk."
    if observation and (_STOCK_WORK_COPY.search(reply) or len(reply.split()) > 22):
        return "Say one short thing about work. Leave out the game, the sales pitch and any extra lines."
    if work == "work" and re.search(r"\b(?:report|homework|assignment|paper|math book|page)\b", reply, re.I):
        return "All you know is that they're working. Say something about work without guessing the task."
    return None


def requested_spelling_word(text):
    """Conservative cue for the spelling game, not a general intent classifier."""
    lower = text.lower()
    if re.search(r"\b(?:do not|don't|never|stop)\s+spell\b", lower):
        return None
    match = re.search(
        r"\b(?:spell\s+(?:out\s+)?(?:the\s+word\s+)?|spelling\s+of\s+)[\"\']?([a-z]+)",
        lower,
    )
    return match.group(1) if match else None


def _persona_base(model=None):
    return CONVERSATION_SYSTEM if model == CONVERSATION_MODEL else SYSTEM


def persona_revision(model=None):
    """Invalidate model context after instruction/model changes; retain visible history."""
    value = _persona_base(model) + "\0" + SPELLING_INSTRUCTIONS + "\0" + NATURAL_INSTRUCTIONS + "\0" + CHARACTER_INSTRUCTIONS + "\0" + CHUDDING_RULE + "\0" + ARITHMETIC_POLICY + "\0" + WORK_FACTS_INSTRUCTIONS + "\0" + DEADLINE_REPLY_POLICY + "\0" + str(model or "")
    if model == CONVERSATION_MODEL:
        value += "\0" + OBSERVATION_INSTRUCTIONS
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def system_for(text, model=None, observation=False):
    """Model-aware persona; legacy callers keep the existing baseline."""
    base = _persona_base(model) + CHUDDING_RULE
    if observation:
        if model == CONVERSATION_MODEL:
            return base + CHARACTER_INSTRUCTIONS + OBSERVATION_INSTRUCTIONS
        return base + CHARACTER_INSTRUCTIONS + "\nSay one short thing about work in little words. Do not name the game or read out app labels.\n"
    requested = requested_spelling_word(text)
    if requested:
        prefix = "... ".join(requested[:2].upper())
        return base + CHARACTER_INSTRUCTIONS + SPELLING_INSTRUCTIONS + f"\nRequested word: {requested}. Start your attempt with {prefix}... then finish with one allowed silly sequence.\n"
    work = WORK_FACTS_INSTRUCTIONS if re.search(
        r"\b(?:work|school|report|assignment|lab|exam|deadline|due|teacher|boss|employer)\b", text, re.I
    ) else ""
    return base + CHARACTER_INSTRUCTIONS + NATURAL_INSTRUCTIONS + work + arithmetic_instruction(text) + "\nThis turn is ordinary conversation. Answer its topic using ordinary words; do not perform the spelling game.\n"


def conversation_tool_names(text, observation=False, model=None):
    """Relevance hints for tool disclosure, never authorization to execute tools.

    The controller must still validate intent, arguments, session and cancellation.
    Ordinary conversation needs no tools; model-generated dialogue remains free.
    """
    if observation:
        if model == CONVERSATION_MODEL:
            return {"request_distraction"}
        return {"read_context", "request_distraction", "set_character_clock"}
    lower = text.lower().strip()
    if requested_spelling_word(text):
        return set()
    names = set()
    denied = re.search(r"\b(?:do not|don't|never)\s+(?:remember|save|store)\b", lower)
    memory = re.search(
        r"^(?:(?:hey|chuddy|okay|please)[,.!\s]*)*(?:remember|keep in mind|don't forget)\s+|\b(?:i want you to|i would like you to|can you|could you|please)\s+remember\s+",
        lower,
    )
    if memory and not denied and not security_related(text):
        names.add("remember_preference")
    elif not denied and (
        re.search(r"\b(?:what|which|do you|did i|did you|can you)\b.*\bremember\b|\brecall\b", lower)
        or re.search(r"\bmy\s+(?:favorite|favourite)\b", lower)
    ):
        names.add("read_context")
    game_request = (re.search(r"\bgames?\b", lower) and re.search(
        r"^(?:please\s+)?(?:open|launch|play)\b|\b(?:let's|let us|can we|could we|i want to|i wanna)\s+(?:open|launch|play)\b", lower))
    if game_request or re.search(r"\b(?:bored|distraction|distract|break)\b", lower):
        names.add("request_distraction")
    if re.search(r"\b(?:due|deadline|deadlines|score|scores|balance|wallet|monies|earn(?:s|ing|ings)?|multiplier|clock)\b", lower):
        names.add("read_context")
    if re.search(r"\b(?:set|change|make)\b.*\b(?:clock|time)\b", lower):
        names.add("set_character_clock")
    return names


def conversation_context(context, text, observation=False, model=None):
    """Select relevant approved facts without changing authoritative app state."""
    lower = text.lower()
    names = conversation_tool_names(text, observation, model)
    selected = {
        "beer_joke_enabled": bool(context.get("beer_joke_enabled", False)),
        "memory": [{"key": m["key"], "value": m["value"]} for m in context.get("memory", [])],
    }
    if names:
        selected["session"] = context.get("session", "stopped")
    if observation:
        work_context = {
            "session": selected["session"],
            "work": work_words(context.get("work")),
            "recent_work_lines": [
                line[:160] for line in (context.get("recent_work_lines") or [])[-8:]
                if isinstance(line, str) and line.strip() and not plain_speech_problem(line, observation=True)
            ],
        }
        if "distraction_destination" in context:
            work_context["distraction_destination"] = context["distraction_destination"]
        return work_context
    if "request_distraction" in names:
        for key in ("distraction_destination", "work"):
            if key in context:
                selected[key] = context[key]
    if not requested_spelling_word(text) and re.search(
        r"\b(?:due|deadline|deadlines|assignment|report|exam|study|school|lab|working)\b", lower
    ):
        for key in ("deadlines", "work"):
            if key in context:
                selected[key] = context[key]
    if re.search(r"\b(?:score|scores|burp|burps|fart|farts|leaderboard)\b", lower) and not requested_spelling_word(text):
        if "scores" in context:
            selected["scores"] = context["scores"]
    if re.search(r"\b(?:money|monies|clams|balance|wallet|bank|gamble|gambling|earn(?:s|ing|ings)?|multiplier|rate)\b", lower):
        if "mock_balance" in context:
            selected["mock_balance"] = context["mock_balance"]
        if "economy" in context:
            selected["economy"] = context["economy"]
    if re.search(r"\b(?:clock|time)\b", lower):
        if "character_clock" in context:
            selected["character_clock"] = context["character_clock"]
    if "work" in selected and selected["work"]:
        selected["work"] = work_words(selected["work"])
    return selected


def observation_outcome(trace):
    """Summarize actual action results, never the model's plan or requested args.

    An accepted request starts a cancellable performance. It is not evidence that
    a window opened. Keep an earlier success if a later duplicate was rejected.
    """
    attempted = False
    for row in trace:
        if row.get("tool") != "request_distraction":
            continue
        attempted = True
        result = row.get("result")
        if isinstance(result, dict) and result.get("ok") is True:
            destination = result.get("destination")
            if destination not in DESTINATIONS:
                destination = None
            return {"status": "started", "destination": destination}
    return {"status": "denied" if attempted else "not_requested", "destination": None}


def observation_followup(trace):
    """Ground the next model round in its real outcome; never replace its reply."""
    outcome = observation_outcome(trace)
    if outcome["status"] == "started":
        destination = outcome["destination"] or "the configured destination"
        return (
            f"request_distraction was accepted for {destination}. The performance "
            "is starting; this result does not confirm anything opened or finished. "
            "Do not call any more tools. Say one short joke about work in little "
            "words. Keep it about the work. Leave out the game and the app's actions. "
            "Use a new idea, away from recent_work_lines."
        )
    if outcome["status"] == "denied":
        return (
            "request_distraction was denied or failed. No distraction started. "
            "Do not try again or call other tools. Reply briefly and truthfully "
            "that you could not start the break. Do not promise a later action."
        )
    return (
        "You have not called request_distraction. Nothing has started. Approved "
        "context already contains the work, session and configured destination. "
        "Use the offered request_distraction tool now with that exact destination "
        "and a short work-related reason. If no tool is available, say that no "
        "break started. A spoken promise does not execute an action."
    )


class FartShuffleBag:
    """Choose supplied sound IDs randomly, exhausting the bag before repeats.

    The audio owner supplies distinct existing clips and handles playback. This
    selector performs no I/O, downloads, playback or scoring. Keep one instance
    per sound queue, rather than recreating it for every belly press.
    """

    def __init__(self, variants, rng=None):
        self.variants = tuple(dict.fromkeys(variants))
        if not self.variants:
            raise ValueError("At least one fart variant is required.")
        self.rng = rng if rng is not None else random.Random()
        self.lock = threading.Lock()
        self.bag = []
        self.last = None

    def choose(self):
        with self.lock:
            if not self.bag:
                self.bag = list(self.variants)
                self.rng.shuffle(self.bag)
                if len(self.bag) > 1 and self.bag[-1] == self.last:
                    self.bag[0], self.bag[-1] = self.bag[-1], self.bag[0]
            self.last = self.bag.pop()
            return self.last


def scripted(text, deadlines=(), beer=False):
    lower = text.lower()
    if asks_to_work(text):
        return chudding_reply(text)
    requested = requested_spelling_word(text)
    if requested:
        start = "... ".join(requested[:2].upper())
        silly = ("B-U-T-T", "P-O-O-P", "F-A-R-T")[len(requested) % 3]
        return f"{start}... uh... {silly}. {requested.capitalize()}. Nailed it."
    arithmetic = character_arithmetic(text)
    if arithmetic:
        return f"{arithmetic['joined']}. I put them next to each other. Teamwork."
    if any(
        w in lower for w in ("deadline", "due", "assignment", "report", "work", "study")
    ):
        return chudding_reply(text)
    if "time" in lower:
        return "It's snack thirty. The other clock is being dramatic."
    if any(w in lower for w in ("bank", "password", "security", "credential")):
        return "My pretend bank needs your fictional pet's name. Mine is Password. He's a clam. Only pretend answers here."
    if any(w in lower for w in ("wallet", "money", "monies", "gambl")):
        return "Put some pretend monies in my wallet. I'm investing in… more wallet."
    if "beer" in lower and beer:
        return (
            "We could look at the pretend beer shop. It's basically bread with bubbles."
        )
    replies = [
        "I'm listening. Slowly. That makes it more thoughtful.",
        "We should take a break. From the break. It's a lot of work.",
        "Good news. I forgot what the bad news was.",
        "I believe in you. Also in the little game button. Mostly that.",
    ]
    return replies[sum(map(ord, text)) % len(replies)]
