"""Script the assignment joke and read saved deadlines for factual questions."""

from dataclasses import dataclass
from datetime import date, datetime, timezone
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .leaderboard import TIMEZONE


SOURCE = "scripted assignment reply"
NOTHING_DUE = "Nothing is due."
MISSED_ASSIGNMENT = "Well, I guess I missed Assignment #14. It’s due tomorrow."
_CHANGED = "That deadline changed. Ask me about it again."
_TYPOS = re.compile(r"\b(?:assignemnt|assignement|assigment|asignment|assign[\s,.-]+ment)\b")
_ASSIGNMENT = re.compile(r"\bassignments?\b")
_TRUE_TIME = re.compile(r"\b(?:actually|really|for real|no joke|(?:actual|real)(?= (?:assignment|due|deadline|time|date|next)))\b")
_QUESTION = re.compile(r"^(?:when|what|which)(?: (?:is|are|will|time|day))*\s+")
_DUE_END = re.compile(r" (?:(?:is|are|be|gonna be|going to be) )?due(?: (?:date|time))?$")
_DUE_FOR = re.compile(r"^(?:the |my )?(?:due (?:date|time)|deadline|time) (?:for|of) ")
_FOLLOW_UP = re.compile(
    r"when|what time|what day"
    r"|(?:when|what time|what day)(?: is)? (?:it|that|the assignment|my assignment) due"
    r"|(?:when|what time|what day) (?:it|that)(?: is|'s) due"
    r"|what is (?:the |my )?(?:due (?:date|time)|deadline|time (?:it|that) is due)"
)
_NEXT_DEADLINE = re.compile(r"(?:the |my )?(?:next (?:deadline|thing due)|due next)")
_NEXT_ASSIGNMENT = re.compile(r"(?:my |the )?next (?:(?P<prefix>[\w &.'-]+) )?assignment(?: for (?P<suffix>[\w &.'-]+))?")
_NUMBERED = re.compile(r"(?:my |the )?(?:(?P<course>[\w &.'-]+) )?assignment\s*#?\s*(?P<number>\d+)(?: for (?P<scope>[\w &.'-]+))?")
_ASSIGNMENT_NUMBER = re.compile(r"\bassignment\s*#?\s*(\d+)\b")
_DUE_LABEL = re.compile(r"\s*(?:[-–—]\s*due)?\s*(?:\[all day\])?\s*$", re.I)


@dataclass(frozen=True)
class AssignmentQuestion:
    kind: str
    scope: str = ""
    number: str = ""
    actual: bool = False


@dataclass(frozen=True)
class AssignmentExchange:
    question: AssignmentQuestion
    deadline: tuple | None = None


def assignment_question(text):
    """Recognize whole deadline questions while leaving reminders and chat alone."""
    value = " ".join(text.casefold().replace("’", "'").strip().split()).rstrip("?.! ")
    value = _TYPOS.sub("assignment", value)
    value = re.sub(r"\b(what|when|which)is\b", r"\1 is", value)
    value = re.sub(r"\bdfue\b", "due", value)
    value = re.sub(r"\b(what|when|which)'s\b", r"\1 is", value)
    actual = bool(_TRUE_TIME.search(value))
    value = re.sub(r"^no(?:,|\s)+\s*", "", _TRUE_TIME.sub("", value)).strip(" ,")
    value = re.sub(r"^(?:do you know|(?:can|could) you tell me|please tell me) (?=when |what |which )", "", value)
    value = re.sub(r"(?: (?:then|again|please))+$", "", value)
    value = " ".join(value.split())
    if _FOLLOW_UP.fullmatch(value):
        topic = ("assignment" if _ASSIGNMENT.search(value) else "deadline"
                 if re.search(r"\b(?:due|deadline)\b", value) else "")
        return AssignmentQuestion("followup", topic, actual=actual)
    question = _QUESTION.match(value)
    if not question and not _DUE_END.search(value):
        return None
    supplied = value[question.end():] if question else value
    body = _DUE_FOR.sub("", _DUE_END.sub("", supplied))
    body = re.sub(r" (?:is )?due for ", " for ", body)
    if _NEXT_DEADLINE.fullmatch(body):
        return AssignmentQuestion("next_deadline", actual=True)
    match = _NEXT_ASSIGNMENT.fullmatch(body)
    if match or re.fullmatch(r"(?:my |the )?assignment (?:is )?due next", body):
        scope = _words(" ".join(part for part in (match["prefix"], match["suffix"]) if part)) if match else ""
        return AssignmentQuestion("next_assignment", scope, actual=actual)
    match = _NUMBERED.fullmatch(body)
    if match:
        scope = _words(" ".join(part for part in (match["course"], match["scope"]) if part))
        return AssignmentQuestion("numbered", scope, match["number"], actual=True)
    if question and body != supplied and _ASSIGNMENT.search(body) and not re.search(r"[?!,;]", body):
        name = re.sub(r"^(?:my|the) ", "", body)
        if name not in ("assignment", "assignments") and "next" not in name.split():
            return AssignmentQuestion("named", name, actual=True)
    return None


def _title(row):
    return _DUE_LABEL.sub("", row["title"]).strip()


def _words(value):
    return " ".join(re.findall(r"[\w]+", value.casefold()))


def _matches(row, question):
    title = _title(row).casefold()
    if question.kind == "next_deadline":
        return True
    if not _ASSIGNMENT.search(title):
        return False
    if question.number and question.number not in _ASSIGNMENT_NUMBER.findall(title):
        return False
    if not question.scope:
        return True
    scope = _words(question.scope)
    if question.kind == "named":
        return scope == _words(title)
    return (scope == row.get("calendar_source") or
            f" {scope} " in f" {_words(title)} ")


def _entry(row, now, upcoming):
    """Calendar imports store timed values in UTC and retain all-day metadata."""
    try:
        due = datetime.fromisoformat(row["due"])
        if due.utcoffset() is None:
            return None
        due = due.astimezone(timezone.utc)
        calendar_date = date.fromisoformat(row["calendar_date"]) if row.get("calendar_date") else None
        if upcoming:
            if calendar_date is not None:
                zone = ZoneInfo(row.get("calendar_timezone") or TIMEZONE)
                if calendar_date < now.astimezone(zone).date():
                    return None
            elif due < now:
                return None
    except (ValueError, TypeError, KeyError, OverflowError, ZoneInfoNotFoundError):
        return None
    return due, calendar_date, row


def _date_text(value):
    return f"{value:%b} {value.day}"


def _time_text(value):
    suffix = "a.m." if value.hour < 12 else "p.m."
    if value.replace(fold=1 - value.fold).utcoffset() != value.utcoffset():
        suffix += f" {value.tzname()}."
    return f"{_date_text(value)} at {value.hour % 12 or 12}:{value.minute:02d} {suffix}"


def assignment_reply(question, deadlines, exchange=None, *, now=None):
    """Return a reply and its anchor, reading the calendar only for facts."""
    followup = question.kind == "followup"
    unscoped_actual = followup and exchange is None
    if followup:
        if exchange is None:
            if not question.actual or not question.scope:
                return None
            question = AssignmentQuestion("next_" + question.scope, actual=True)
            followup = False
        else:
            question = AssignmentQuestion(
                exchange.question.kind, exchange.question.scope, exchange.question.number,
                actual=question.actual or exchange.question.actual,
            )
            if question.actual and exchange.deadline is None:
                followup = False  # The gag did not pick a real calendar entry.
    if question.kind == "next_assignment" and not question.scope and not question.actual:
        repeating = exchange is not None and exchange.deadline is None
        return (MISSED_ASSIGNMENT if repeating else NOTHING_DUE), AssignmentExchange(question)

    now = now or datetime.now(timezone.utc)
    upcoming = question.kind.startswith("next_")
    entries = [entry for row in deadlines() if _matches(row, question)
               if (entry := _entry(row, now, upcoming)) is not None]
    entries.sort(key=lambda item: item[0])
    if followup and not entries:
        return _CHANGED, None
    if not entries:
        if not upcoming:
            return "I don't see that assignment in your deadlines.", None
        kind = "assignment" if question.kind == "next_assignment" else "deadline"
        return f"I don't see an upcoming {kind}.", None
    if len(entries) > 1 and (unscoped_actual or not upcoming or entries[0][0] == entries[1][0]):
        return "Which one do you mean?", None
    due, calendar_date, row = entries[0]
    key = (row.get("id"), row["title"], due, calendar_date, row.get("calendar_timezone"),
           row.get("calendar_source"), row.get("calendar_key"))
    same = (exchange is not None and exchange.deadline == key
            and exchange.question.kind == question.kind and exchange.question.scope == question.scope)
    if followup and not same:
        return _CHANGED, None
    anchor = AssignmentExchange(question, key)
    title = _title(row)
    if calendar_date is not None:
        return f"{title} is due {_date_text(calendar_date)}. I only have the date.", anchor
    return f"{title} is due {_time_text(due.astimezone(ZoneInfo(TIMEZONE)))}", anchor
