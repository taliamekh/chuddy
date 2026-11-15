"""Immediate original character lines; scripted text, never model output."""
from collections import deque
import random
import threading


BANTER_SOURCE = 'scripted work banter'
BANTER_OUTCOME_SOURCE = 'scripted work outcome'
_DESTINATION_INVITATIONS = {
    "casino": "How about we gamble?",
    "game": "Wanna play a game?",
    "lcbo": "Wanna look around?",
    "beer_store": "Wanna look around?",
    "league": "Wanna play League?",
}
_FAMILIES = (
    (("I'm good at sitting.", "I can sit real still.", "I'm already sitting.",
      "My best thing is sitting."),
     ("Can we turn that in?", "I did my part.", "That should count as work.",
      "I could do that all day.", "Now you try. It's easy.")),
    (("A stack has a top and a bottom.", "Papers have two sides.",
      "That work must have a last page.", "There's an end to every pile."),
     ("I like the side where we stop.", "Put the end on top. There, you found the end.",
      "The bottom is the best part. You can sit on it.",
      "Flipping it over should count as turning it in.", "I bet the other side is quiet.")),
    (("I saved a spot for you.", "I found an empty spot.", "There's room over here.",
      "Come chud in my chair."),
     ("I warmed it up with my butt.", "All I did was move one leg.",
      "I put my other seat over there.", "It fits both of us if I go sideways.",
      "I was holding it with my foot.")),
    (("I took a break early.", "Let's rest before we start.", "I think we should sit first.",
      "We could stop before we get tired."),
     ("Now I don't have to get tired.", "That's how you save your legs.",
      "Then we're ahead on resting.", "The hard part can wait its turn.",
      "We can be done with the break before we even start.")),
    (("I can do nothing.", "Let's try doing nothing.", "Nothing sounds good.",
      "I've got nothing to do."),
     ("I can get that done real fast.", "Want me to show you how?", "I'm already halfway done.",
      "I can do twice as much if you help.", "It fits on a blank piece of paper.")),
    (("That work can sit right there.", "Let's put work over there.", "We can leave the work here.",
      "I think work gets this chair."),
     ("I'll sit somewhere else.", "It can wait till it gets tired.",
      "It can hold our spot.", "Maybe it'll miss us.", "It needs a turn doing nothing too.")),
)
_VARIATIONS = tuple((family, lead, payoff, lead + ' ' + payoff)
                    for family, (leads, payoffs) in enumerate(_FAMILIES)
                    for lead in leads for payoff in payoffs)
WORK_BANTER_VARIANTS = tuple(row[3] for row in _VARIATIONS)


def _normal(text):
    return ' '.join(str(text or '').split()).casefold()


class WorkBanter:
    """Exhaust known lines before exact repeats; avoid recent joke structures.

    Recent assistant text can come from the selected profile's saved history,
    including after a restart. This does not promise unlimited novel material.
    No history, settings, data or speech is written by this selector.
    """
    def __init__(self, rng=None):
        self.rng = rng or random.Random()
        self.lock = threading.Lock()
        self.used = set()
        self.recent = deque(maxlen=16)

    def outcome(self, phase, destination, recent=(), *, tab_closed=False):
        """Return the destination's invitation after an acknowledged opening.

        Keep the callback signature for callers that pass history and close
        metadata. Each new ticket gets the same invitation for its destination.
        """
        if phase == "opened":
            return _DESTINATION_INVITATIONS.get(destination)
        return None

    def record_outcome(self, interruption, recent=(), *, tab_closed=False):
        """Mark the active ticket once, under the caller's interruption lock."""
        if interruption.get("test") or interruption.get("banter_source") == BANTER_OUTCOME_SOURCE:
            return None
        line = self.outcome(interruption.get("phase"), interruption.get("destination"),
                            recent, tab_closed=tab_closed)
        if line:
            interruption.update(banter_message=line, banter_source=BANTER_OUTCOME_SOURCE)
        return line

    def choose(self, label='', destination='casino', recent=()):
        # Work labels are metadata, not executable templates or quotations. The
        # immediate line never echoes a title/URL; model dialogue may use context.
        with self.lock:
            history = [_normal(row.get('text', '') if isinstance(row, dict) else row)
                       for row in list(recent or ())[-80:]]
            history += [_normal(text) for text in self.recent]
            known = set(history)
            options = [row for row in _VARIATIONS if row[3] not in self.used and _normal(row[3]) not in known]
            if not options:
                self.used.clear()
                options = [row for row in _VARIATIONS if _normal(row[3]) not in known]
            if not options:
                # Every supplied option was seen: prefer the least recently
                # heard exact line and still avoid a cycle-boundary repeat.
                last = history[-1] if history else ''
                options = [row for row in _VARIATIONS if _normal(row[3]) != last]
                rank = {_normal(text): i for i, text in enumerate(history)}
                oldest = min(rank.get(_normal(row[3]), -1) for row in options)
                options = [row for row in options if rank.get(_normal(row[3]), -1) == oldest]
            novel_ideas = [row for row in options if not any(_normal(row[2]) in text for text in history[-12:])]
            if novel_ideas:
                options = novel_ideas
            novel_leads = [row for row in options if not any(_normal(row[1]) in text for text in history[-3:])]
            if novel_leads:
                options = novel_leads
            if history:
                last_family = next((row[0] for row in _VARIATIONS if _normal(row[3]) == history[-1]), None)
                varied = [row for row in options if row[0] != last_family]
                if varied:
                    options = varied
            chosen = self.rng.choice(options)[3]
            self.used.add(chosen)
            self.recent.append(chosen)
            return chosen
