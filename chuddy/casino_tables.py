"""Local, serializable blackjack and European single-zero roulette rules.

These functions never touch a wallet. Atomically reserve ``state['reserve']``
when creating a table, persist the entire private state, then credit exactly
``state['payout']`` once ``complete`` is true. Never accept private state from
the browser; return only *_public(). Actions return copies and fail without
mutating their input. Request retry/deduplication belongs to the caller's
durable transaction, not this module.

Blackjack reserves four times the even base stake, enough for one split and
doubling both resulting hands. Final payout includes unused reserve. Six
decks; dealer stands on all 17; natural pays 3:2; ordinary win pays 1:1; ties
push. Dealer blackjack is checked before actions. One equal-value split,
double any first two cards except split aces, one card per split ace. No
insurance or surrender. Fresh shoe for each round. Rules reference:
https://bicyclecards.com/how-to-play/blackjack/

Roulette reserves one stake and returns stake-inclusive 36x straight, 3x
dozen/column, or 2x outside winnings. Zero loses all outside wagers. No
la partage or en prison. Wheel order is European single-zero. References:
https://www.playnow.com/resources/pdf/casino/how-to-play/table-games/roulette/how-to-play-roulette.pdf
https://help.danskespil.dk/en/casino-help/roulette/europeanroulette
"""

from copy import deepcopy
import random


RANKS = "23456789TJQKA"
SUITS = "CDHS"
RED_NUMBERS = frozenset((1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36))
WHEEL_ORDER = (0, 32, 15, 19, 4, 21, 2, 25, 17, 34, 6, 27, 13, 36, 11, 30, 8, 23, 10, 5, 24, 16, 33, 1, 20, 14, 31, 9, 22, 18, 29, 7, 28, 12, 35, 3, 26)
BLACKJACK_RULES = {
    "decks": 6, "dealer_stands_soft_17": True, "blackjack_profit": "3:2",
    "max_splits": 1, "double_after_split": True, "split_aces_one_card": True,
    "insurance": False, "surrender": False, "reserve_multiplier": 4,
}


def _stake(value, minimum=1, maximum=10000):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"Stake must be a whole number from {minimum} to {maximum}.")
    return value


def _card_value(card):
    return 11 if card[0] == "A" else min(RANKS.index(card[0]) + 2, 10)


def blackjack_total(cards):
    """Return (best total, has an ace still counted as eleven)."""
    value = sum(_card_value(card) for card in cards)
    aces = sum(card[0] == "A" for card in cards)
    while value > 21 and aces:
        value -= 10
        aces -= 1
    return value, bool(aces)


def _shoe(rng):
    cards = [rank + suit for rank in RANKS for suit in SUITS] * 6
    rng.shuffle(cards)
    return cards


def _draw(state):
    if not state["shoe"]:
        raise ValueError("The shoe is exhausted; keep the saved round for recovery.")
    return state["shoe"].pop()


def _natural(cards):
    return len(cards) == 2 and blackjack_total(cards)[0] == 21


def _hand(cards, stake, split=False, split_aces=False):
    return {"cards": cards, "bet": stake, "status": "active", "split": split,
            "split_aces": split_aces, "outcome": None, "payout": None}


def _settle_blackjack(state):
    dealer = state["dealer"]
    natural_dealer = _natural(dealer)
    if not natural_dealer and any(blackjack_total(hand["cards"])[0] <= 21 for hand in state["hands"]):
        # A natural already wins after the peek; no reason to draw more cards.
        if not (len(state["hands"]) == 1 and not state["hands"][0]["split"] and _natural(state["hands"][0]["cards"])):
            while blackjack_total(dealer)[0] < 17:
                dealer.append(_draw(state))
    dealer_total = blackjack_total(dealer)[0]
    for hand in state["hands"]:
        total = blackjack_total(hand["cards"])[0]
        natural_player = not hand["split"] and _natural(hand["cards"])
        if total > 21:
            outcome, payout = "bust", 0
        elif natural_dealer:
            outcome, payout = ("push", hand["bet"]) if natural_player else ("lose", 0)
        elif natural_player:
            outcome, payout = "blackjack", hand["bet"] * 5 // 2
        elif dealer_total > 21 or total > dealer_total:
            outcome, payout = "win", hand["bet"] * 2
        elif total == dealer_total:
            outcome, payout = "push", hand["bet"]
        else:
            outcome, payout = "lose", 0
        hand.update(status="complete", outcome=outcome, payout=payout)
    state.update(complete=True, phase="complete", active_hand=None,
                 payout=state["reserve"] - state["committed"] + sum(hand["payout"] for hand in state["hands"]))
    profit = state["payout"] - state["reserve"]
    state["dealer_line"] = ("Gary slides the winnings across the felt." if profit > 0 else
                            "Gary returns the reserved monies. The round breaks even." if profit == 0 else
                            "Gary collects the losing chips and closes the round.")
    state["log"].append("Gary reveals his hand. The round is settled.")


def _advance_blackjack(state):
    while state["active_hand"] is not None:
        index = state["active_hand"]
        hand = state["hands"][index]
        total = blackjack_total(hand["cards"])[0]
        if total >= 21 or hand["split_aces"]:
            hand["status"] = "bust" if total > 21 else "stood"
        if hand["status"] == "active":
            return
        next_index = index + 1
        if next_index == len(state["hands"]):
            state["active_hand"] = None
            _settle_blackjack(state)
            return
        state["active_hand"] = next_index
        state["hands"][next_index]["status"] = "active"


def blackjack_new(stake, rng=None):
    """Create a private round. Reserve exactly 4*stake before publishing it."""
    stake = _stake(stake, 2)
    if stake % 2:
        raise ValueError("Blackjack needs an even stake for exact 3:2 natural payouts.")
    rng = rng if rng is not None else random.SystemRandom()
    state = {"game": "blackjack", "version": 1, "stake": stake, "reserve": 4 * stake,
             "committed": stake, "payout": None, "complete": False, "phase": "player",
             "shoe": _shoe(rng), "hands": [], "dealer": [], "active_hand": 0,
             "dealer_line": "Gary deals the cards and waits for your move.",
             "log": ["Four stakes reserved; unused play monies return at settlement."]}
    first_player, first_dealer, second_player, second_dealer = (_draw(state) for _ in range(4))
    state["hands"] = [_hand([first_player, second_player], stake)]
    state["dealer"] = [first_dealer, second_dealer]
    if _natural(state["dealer"]) or _natural(state["hands"][0]["cards"]):
        _settle_blackjack(state)
    return state


def _blackjack_actions(state):
    if state["complete"]:
        return []
    hand = state["hands"][state["active_hand"]]
    actions = ["hit", "stand"]
    available = state["reserve"] - state["committed"]
    if len(hand["cards"]) == 2 and not hand["split_aces"] and available >= hand["bet"]:
        actions.append("double")
        if len(state["hands"]) == 1 and _card_value(hand["cards"][0]) == _card_value(hand["cards"][1]):
            actions.append("split")
    return actions


def blackjack_act(state, action, amount=None, rng=None):
    """Apply hit/stand/double/split; returns a new private state, never a debit."""
    if state.get("game") != "blackjack" or state.get("version") != 1:
        raise ValueError("Choose a saved blackjack round.")
    if amount is not None:
        raise ValueError("Blackjack actions use the original stake; no extra amount is accepted.")
    if action not in _blackjack_actions(state):
        raise ValueError("That action is not available in this blackjack hand.")
    result = deepcopy(state)
    hand = result["hands"][result["active_hand"]]
    if action == "hit":
        hand["cards"].append(_draw(result))
        result["log"].append("You take another card.")
    elif action == "stand":
        hand["status"] = "stood"
        result["log"].append("You stand.")
    elif action == "double":
        result["committed"] += hand["bet"]
        hand["bet"] *= 2
        hand["cards"].append(_draw(result))
        hand["status"] = "stood"
        result["log"].append("You double, take one card, and stand.")
    elif action == "split":
        cards = hand["cards"]
        aces = cards[0][0] == "A"
        result["committed"] += hand["bet"]
        result["hands"] = [_hand([cards[0], _draw(result)], hand["bet"], True, aces),
                           _hand([cards[1], _draw(result)], hand["bet"], True, aces)]
        result["hands"][1]["status"] = "waiting"
        result["log"].append("You split into two hands." + (" One card per split ace." if aces else " Play the left hand first."))
    _advance_blackjack(result)
    return result


def blackjack_public(state):
    """Public projection: never includes the shoe or a concealed dealer card."""
    result = {key: deepcopy(state[key]) for key in ("game", "version", "stake", "reserve", "committed", "payout", "complete", "phase", "active_hand", "dealer_line", "log")}
    result["rules"] = deepcopy(BLACKJACK_RULES)
    result["dealer_line"] = "Gary settles the hand." if state["complete"] else "Gary waits for your move."
    result["hands"] = []
    for hand in state["hands"]:
        projected = deepcopy(hand)
        projected["total"], projected["soft"] = blackjack_total(hand["cards"])
        result["hands"].append(projected)
    result["dealer"] = {"name": "Gary", "cards": deepcopy(state["dealer"]) if state["complete"] else [state["dealer"][0], None],
                        "total": blackjack_total(state["dealer"])[0] if state["complete"] else None,
                        "soft": blackjack_total(state["dealer"])[1] if state["complete"] else None}
    result["actions"] = _blackjack_actions(state)
    return result


def roulette_color(number):
    if isinstance(number, bool) or not isinstance(number, int) or not 0 <= number <= 36:
        raise ValueError("A roulette pocket is a whole number from 0 to 36.")
    return "green" if number == 0 else "red" if number in RED_NUMBERS else "black"


def roulette_selection(selection):
    """Validate one straight/outside bet; canonical JSON dict in, or 'red' etc."""
    if isinstance(selection, str):
        selection = {"kind": selection}
    if not isinstance(selection, dict) or set(selection) - {"kind", "value"}:
        raise ValueError("Choose one roulette betting area.")
    kind = selection.get("kind")
    if kind in ("red", "black", "odd", "even", "low", "high"):
        if "value" in selection:
            raise ValueError("That outside bet does not take a number.")
        return {"kind": kind}
    if kind not in ("straight", "dozen", "column"):
        raise ValueError("Choose straight, red, black, odd, even, low, high, dozen or column.")
    value = selection.get("value")
    maximum = 36 if kind == "straight" else 3
    minimum = 0 if kind == "straight" else 1
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{kind.title()} needs a whole number from {minimum} to {maximum}.")
    return {"kind": kind, "value": value}


def _roulette_win(number, selection):
    kind, value = selection["kind"], selection.get("value")
    if kind == "straight":
        return number == value
    if number == 0:
        return False
    if kind in ("red", "black"):
        return roulette_color(number) == kind
    if kind == "odd":
        return number % 2 == 1
    if kind == "even":
        return number % 2 == 0
    if kind == "low":
        return 1 <= number <= 18
    if kind == "high":
        return 19 <= number <= 36
    if kind == "dozen":
        return (number - 1) // 12 + 1 == value
    return (number - 1) % 3 + 1 == value


def roulette_new(stake, selection, rng=None):
    """Resolve one single-zero spin. Debit reserve=stake; credit payout once."""
    stake = _stake(stake)
    selection = roulette_selection(selection)
    rng = rng if rng is not None else random.SystemRandom()
    number = rng.randrange(37)
    won = _roulette_win(number, selection)
    multiplier = 36 if selection["kind"] == "straight" else 3 if selection["kind"] in ("dozen", "column") else 2
    payout = stake * multiplier if won else 0
    return {"game": "roulette", "version": 1, "stake": stake, "reserve": stake,
            "selection": selection, "number": number, "color": roulette_color(number),
            "wheel_index": WHEEL_ORDER.index(number), "complete": True, "phase": "complete",
            "won": won, "payout": payout, "multiplier": multiplier,
            "dealer": "Gary",
            "dealer_line": "Gary points to the winning pocket and pays the chips." if won else "Gary points to the landing pocket and collects the losing chips."}


def roulette_public(state):
    result = deepcopy(state)
    # Also normalize durable rounds written before Gary became silent.
    result['dealer_line'] = ('Gary points to the winning pocket and pays the chips.' if state['won']
                             else 'Gary points to the landing pocket and collects the losing chips.')
    result["wheel_order"] = list(WHEEL_ORDER)
    return result
