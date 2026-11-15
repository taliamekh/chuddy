"""Input cues for Chuddy's pretend arithmetic; never an application calculator.

Only conditions model input. No generated answer rewriting or state mutation.
"""

import re


ARITHMETIC_POLICY = """
On an explicit pretend arithmetic question, join the two number labels into ONE
number. Remove the operator. Give that number as your answer, then one short,
sincerely mistaken reason. Do not just repeat the equation, describe a procedure,
give the correct calculation, or correct the joke afterward. This game never
changes actual saved amounts, counts, dates, deadlines, rates or clock data.
Original example: What is 2+2? -> Twenty-two. I put the twos together. They looked lonely.
"""

_SMALL = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
_TENS = "zero ten twenty thirty forty fifty sixty seventy eighty ninety".split()
_WORD_NUMBER = r"(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)(?:[- ](?:one|two|three|four|five|six|seven|eight|nine))?|" + "|".join(reversed(_SMALL))
_NUMBER = r"(?<![\w.])(?:\d{1,2}|" + _WORD_NUMBER + r")(?!\w|\.\d)"
_FACTS = re.compile(r"\b(?:wallet|balance|monies|money|score|counter|count|multiplier|rate|deadline|due|calendar|date|clock|time|remember|saved|favorite|favourite)\b", re.I)
_PAIRS = [
    (re.compile(r"(" + _NUMBER + r")\s*(?:\+|plus|added to|times|multiplied by|[×*]|minus|divided by|[÷/]|\bx\b|-)\s*(" + _NUMBER + r")", re.I), False),
    (re.compile(r"\b(?:add|sum(?: of)?|total of)\s+(" + _NUMBER + r")\s+(?:and|to|with)\s+(" + _NUMBER + r")", re.I), False),
    (re.compile(r"\b(?:multiply|divide)\s+(" + _NUMBER + r")\s+by\s+(" + _NUMBER + r")", re.I), False),
    (re.compile(r"\bsubtract\s+(" + _NUMBER + r")\s+from\s+(" + _NUMBER + r")", re.I), True),
]


def _number(label):
    if label.isdigit():
        return str(int(label))
    parts = label.lower().replace("-", " ").split()
    return str(_SMALL.index(parts[0]) if parts[0] in _SMALL else
               _TENS.index(parts[0]) * 10 + (_SMALL.index(parts[1]) if len(parts) > 1 else 0))


def _spoken(label):
    if label.startswith("0"):
        return " ".join(_SMALL[int(d)] for d in label)
    value = int(label)
    if value < 20:
        return _SMALL[value]
    if value < 100:
        return _TENS[value // 10] + ("-" + _SMALL[value % 10] if value % 10 else "")
    unit = 1000 if value >= 1000 else 100
    return _spoken(str(value // unit)) + (" thousand" if unit == 1000 else " hundred") + (
        " " + _spoken(str(value % unit)) if value % unit else "")


def character_arithmetic(text):
    """Return a conservative binary-expression cue, excluding authoritative facts."""
    if _FACTS.search(text) or re.search(r"\b(?:repeat|quote|spell|verbatim|literally read)\b", text, re.I):
        return None
    numbers = list(re.finditer(_NUMBER, text, re.I))
    if len(numbers) != 2 or re.search(r"-\s*$", text[:numbers[0].start()]):
        return None  # No partial interpretation of negatives or longer expressions.
    for pattern, reverse in _PAIRS:
        match = pattern.search(text)
        if match:
            left, right = map(_number, match.groups())
            if reverse:
                left, right = right, left
            joined = left + right
            return {"left": left, "right": right, "joined": joined, "spoken": _spoken(joined)}
    return None


def arithmetic_instruction(text):
    cue = character_arithmetic(text)
    if cue is None:
        return ""
    return ARITHMETIC_POLICY + (
        f"\nThis turn's number labels are {cue['left']} and {cue['right']}. "
        f"The joined character answer is {cue['joined']}, spoken '{cue['spoken']}'. "
        "Start your reply with that spoken answer, followed by your own tiny wrong "
        "reason. Leave out the operator. Do not ask a question.\n"
    )
