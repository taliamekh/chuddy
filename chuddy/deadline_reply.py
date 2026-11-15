"""Conservative deadline-dialogue checks, never calendar edits or answer rewriting.

This catches explicit unsupported temporal claims, not every factual implication.
The agent's existing bounded repair/fallback handles a returned instruction.
"""

import json
import calendar
import re


DEADLINE_REPLY_POLICY = "deadline-reply-grounding-v1"
_TOPIC = re.compile(r"\b(?:deadline|due|hand in|submit|submission|report|assignment)\b", re.I)
_TEMPORAL = re.compile(
    r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"today|tomorrow|tonight|yesterday|(?:next|last|this) (?:week|month|year|weekend)|"
    r"(?:all|whole) week|morning|afternoon|evening|noon|midnight|"
    r"\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)|\d{1,2}:\d{2}|"
    r"\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}(?:/\d{2,4})?|"
    r"(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?)\b",
    re.I,
)
_EXTENSION = re.compile(
    r"\b(?:teacher|professor|employer|boss|they)\b[^.!?]{0,75}\b(?:will|might|may|could|'ll)\b"
    r"[^.!?]{0,45}\b(?:give|grant|extend|postpone|cancel|forget|move|ignore)\b|"
    r"\b(?:deadline|due date)\b[^.!?]{0,35}\b(?:extended|postponed|cancelled|canceled|moved)\b",
    re.I,
)
_COMPLETION = re.compile(
    r"\b(?:you|we|i)(?:'ll| will| can)?\s+(?:(?:already|be|have|am|are)\s+)*"
    r"(?:done|finished|completed|submitted|finish|complete|submit)\b[^.!?]{0,55}|"
    r"\b(?:the|your|our|my)\s+(?:report|assignment|file|work)\s+(?:is|was|has been)\s+"
    r"(?:already\s+)?(?:done|finished|completed|submitted)\b[^.!?]{0,55}", re.I,
)


def _facts(text):
    values = set()
    # Keep exact ISO date/time fields usable without treating the UTC offset as
    # another deadline time. No local weekday or timezone inference is made.
    text = re.sub(r"(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?", r"\1 \2", text)
    for match in _TEMPORAL.finditer(text):
        value = re.sub(r"[.\s]+", " ", match.group(0).lower()).strip()
        iso_date = re.fullmatch(r"\d{4}-(\d{2}-\d{2})", value)
        named_date = re.fullmatch(r"([a-z]+) (\d{1,2})(?:st|nd|rd|th)?(?:,? (\d{4}))?", value)
        if iso_date:
            values.add(iso_date[1])
        elif named_date and named_date[1] in [m.lower() for m in calendar.month_name]:
            month = [m.lower() for m in calendar.month_name].index(named_date[1])
            short = f"{month:02d}-{int(named_date[2]):02d}"
            values.add(short)
            value = f"{named_date[3]}-{short}" if named_date[3] else short
        if value == "noon":
            value = "12:00 pm"
        elif value == "midnight":
            value = "12:00 am"
        else:
            clock = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*([ap])\s*m", value)
            if clock:
                value = f"{int(clock[1])}:{clock[2] or '00'} {clock[3]}m"
        values.add(value)
    return values


def _reply_facts(reply):
    values = set()
    for sentence in re.split(r"(?<=[.!?])\s+", reply):
        opinion = re.search(r"\b(?:my (?:brain|head|imagination|pretend clock|favorite day|favourite day)|i (?:like|love|prefer|nap|sleep))\b", sentence, re.I)
        actual_task = re.search(r"\b(?:your|you|we|deadline|due|report|assignment|file|work)\b", sentence, re.I)
        suggestion = re.match(r"\s*(?:maybe\s+)?(?:you (?:should|could)|we could|let's|just\b|try\b|start\b|sleep\b|take\b|do\b|put\b|leave\b)", sentence, re.I)
        factual_claim = re.search(r"\b(?:due (?:on|at|by)|deadline (?:is|was|will|has)|last (?:year|week|month)|yesterday)\b", sentence, re.I)
        if not (opinion and not actual_task) and not (suggestion and not factual_claim):
            values.update(_facts(sentence))
    return values


def deadline_reply_problem(text, reply, context=None):
    """Return repair guidance for explicit deadline turns with unsupported claims.

    When the user supplies temporal facts, use those conversation facts, not an
    unrelated stored deadline. Without them, supplied deadline records are the
    allowed source. ISO records may be quoted exactly; this check does not infer
    local weekdays or resolve conflicting user/calendar dates.
    """
    if not _TOPIC.search(text):
        return None
    supplied = _facts(text)
    if not supplied:
        supplied = _facts(json.dumps((context or {}).get("deadlines", []), ensure_ascii=False))
    invented = _reply_facts(reply) - supplied
    completed = any(
        _TEMPORAL.search(match.group(0)) or re.search(r"\b(?:done|finished|completed|submitted)\b", match.group(0), re.I)
        for match in _COMPLETION.finditer(reply)
    ) and not re.search(r"\b(?:in my head|in my imagination|pretend)\b", reply, re.I)
    extension = _EXTENSION.search(reply)
    known_change = any(word in text.lower() and word in reply.lower()
                       for word in ("extended", "postponed", "cancelled", "canceled", "moved"))
    unsupported_extension = bool(extension and not (
        known_change and not re.search(r"\b(?:will|might|may|could)\b", extension.group(0), re.I)
    ))
    if invented or completed or unsupported_extension:
        return (
            "Do not invent deadline details, extensions or completed work. The only "
            "supplied temporal facts are " + json.dumps(sorted(supplied)) + ". "
            "Keep them exact; if no deadline is known, say you don't know. Give one "
            "tiny lazy thought about the task itself, without a work plan, a promised "
            "finish time or a prediction about a teacher/employer."
        )
    return None
