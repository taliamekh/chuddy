"""One-hand, five-seat no-limit Texas Hold'em with local character bots.

Only the human's buy-in/ending stack cross the casino wallet boundary. The
four bot stacks are fictional table chips, not other profiles' wallets.
Rules reference: https://www.pokerstars.uk/poker/games/texas-holdem/
"""
import copy
import itertools
import random
from collections import Counter

RANKS = '23456789TJQKA'
SUITS = 'CDHS'
HAND_NAMES = ['High card', 'One pair', 'Two pair', 'Three of a kind', 'Straight',
              'Flush', 'Full house', 'Four of a kind', 'Straight flush']
BOT_STYLES = {
    'SpongeBob': {'style': 'Enthusiastic caller', 'fold': .18, 'raise': .20,
                 'bluff': .03, 'value_threshold': .35, 'raise_extra': 0,
                 'line': "I'm ready! These cards are ready too!"},
    'Squidward': {'style': 'Tight, selective', 'fold': .62, 'raise': .10,
                  'bluff': 0, 'value_threshold': .6, 'raise_extra': 0,
                  'line': 'Another magnificent waste of my afternoon.'},
    'Plankton': {'style': 'Calculated aggressive bluffer', 'fold': .28, 'raise': .46,
                 'bluff': .20, 'value_threshold': .45, 'raise_extra': 2,
                 'line': 'A calculated risk. My microscopic empire grows!'},
    'Mr. Krabs': {'style': 'Greedy aggressive value bettor', 'fold': .52, 'raise': .62,
                  'bluff': .01, 'value_threshold': .55, 'raise_extra': 3,
                  'line': "Those monies belong in me pocket!"},
}


def _rng(rng):
    return rng if rng is not None else random.SystemRandom()


def _integer(value, minimum, maximum, label):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f'{label} must be a whole number from {minimum} to {maximum}.')
    return value


def rank_five(cards):
    """Lexicographically comparable rank, including all kickers and wheel ace."""
    if len(cards) != 5 or len(set(cards)) != 5 or any(c not in _CARDS for c in cards):
        raise ValueError('A poker hand needs five distinct standard cards.')
    values = sorted((RANKS.index(c[0]) + 2 for c in cards), reverse=True)
    counts = Counter(values)
    groups = sorted(((count, rank) for rank, count in counts.items()), reverse=True)
    flush = len({c[1] for c in cards}) == 1
    unique = sorted(counts, reverse=True)
    straight = 5 if unique == [14, 5, 4, 3, 2] else unique[0] if len(unique) == 5 and unique[0] - unique[-1] == 4 else 0
    if straight and flush:
        return (8, straight)
    if groups[0][0] == 4:
        return (7, groups[0][1], groups[1][1])
    if [g[0] for g in groups] == [3, 2]:
        return (6, groups[0][1], groups[1][1])
    if flush:
        return (5, *values)
    if straight:
        return (4, straight)
    if groups[0][0] == 3:
        return (3, groups[0][1], *sorted((v for v in values if v != groups[0][1]), reverse=True))
    pairs = sorted((rank for count, rank in groups if count == 2), reverse=True)
    if len(pairs) == 2:
        return (2, *pairs, next(rank for count, rank in groups if count == 1))
    if pairs:
        return (1, pairs[0], *sorted((v for v in values if v != pairs[0]), reverse=True))
    return (0, *values)


_CARDS = frozenset(rank + suit for rank in RANKS for suit in SUITS)


def evaluate(cards):
    if not 5 <= len(cards) <= 7 or len(set(cards)) != len(cards) or any(c not in _CARDS for c in cards):
        raise ValueError('Evaluate five to seven distinct standard cards.')
    return max(rank_five(list(hand)) for hand in itertools.combinations(cards, 5))


def _players_in(state):
    return [i for i, p in enumerate(state['players']) if not p['folded']]


def _can_act(state):
    return [i for i in _players_in(state) if state['players'][i]['stack'] > 0]


def _pay(state, seat, amount):
    p = state['players'][seat]
    amount = min(amount, p['stack'])
    p['stack'] -= amount
    p['street_bet'] += amount
    p['contributed'] += amount


def action_line(name, action, amount=0):
    """Brief character speech based only on the current public move."""
    if action == 'fold':
        return {'SpongeBob': 'I fold. Good luck, everybody!', 'Squidward': 'I fold. Carry on.',
                'Plankton': 'I fold. For now.', 'Mr. Krabs': "I fold. Keepin' me monies."}.get(name, 'I fold.')
    if action == 'check':
        return {'SpongeBob': 'I check!', 'Squidward': 'Check.',
                'Plankton': 'I check. Go on, then.', 'Mr. Krabs': "I check. Me chips stay here."}.get(name, 'I check.')
    if action == 'call':
        return f"{'Fine. I call' if name == 'Squidward' else 'I call'} {amount} monies."
    if action == 'raise':
        return f"I raise to {amount} monies{'!' if name in ('SpongeBob', 'Mr. Krabs') else '.'}"
    return f"{action.capitalize()}, {amount} monies."


def _log(state, seat, action, amount=0):
    p = state['players'][seat]
    entry = {'seat': seat, 'name': p['name'], 'action': action, 'amount': amount,
             'street': state['street'],
             # Public post-action chips let the UI replay a batch in order
             # without reconstructing betting arithmetic or exposing cards.
             'chips': {'stack': p['stack'], 'street_bet': p['street_bet'],
                       'contributed': p['contributed'], 'folded': p['folded'],
                       'pot': sum(player['contributed'] for player in state['players'])}}
    if seat:
        entry['text'] = action_line(p['name'], action, amount)
    state['log'].append(entry)


def _next(state, after):
    seats = len(state['players'])
    for offset in range(1, seats + 1):
        seat = (after + offset) % seats
        if seat in state['pending'] and seat in _can_act(state):
            return seat
    return None


def _finish(state):
    """Settle layered side pots, refund unmatched overbets, award odd chips left of button."""
    active = _players_in(state)
    if len(active) == 1:
        winner = active[0]
        total = sum(p['contributed'] for p in state['players'])
        state['players'][winner]['stack'] += total
        state['pots'] = [{'amount': total, 'winners': [winner]}]
        state['showdown'] = False
    else:
        while len(state['community']) < 5:
            _deal_street(state)
        rankings = {i: evaluate(state['players'][i]['cards'] + state['community']) for i in active}
        state['showdown'] = True
        for i, rank in rankings.items():
            state['players'][i]['hand_name'] = HAND_NAMES[rank[0]]
        levels = sorted(set(p['contributed'] for p in state['players']) - {0})
        previous = 0
        state['pots'] = []
        for level in levels:
            contributors = [i for i, p in enumerate(state['players']) if p['contributed'] >= level]
            amount = (level - previous) * len(contributors)
            eligible = [i for i in contributors if i in active]
            if len(contributors) == 1:
                winners = contributors  # uncalled chips, even if the owner was not a showdown winner
            else:
                if not eligible:
                    raise ValueError('Invalid poker state: no eligible side-pot player.')
                best = max(rankings[i] for i in eligible)
                winners = [i for i in eligible if rankings[i] == best]
            share, odd = divmod(amount, len(winners))
            order = sorted(winners, key=lambda i: (i - state['button'] - 1) % len(state['players']))
            for offset, i in enumerate(order):
                state['players'][i]['stack'] += share + (offset < odd)
            state['pots'].append({'amount': amount, 'winners': winners})
            previous = level
    state['complete'] = True
    state['street'] = 'complete'
    state['actor'] = None
    state['pending'] = []
    state['payout'] = state['players'][0]['stack']


def _deal_street(state):
    state['burned'].append(state['deck'].pop())
    count = 3 if not state['community'] else 1
    state['community'].extend(state['deck'].pop() for _ in range(count))


def _advance(state, after):
    if len(_players_in(state)) == 1:
        _finish(state)
        return
    acting = _can_act(state)
    state['pending'] = [i for i in state['pending'] if i in acting]
    # A sole funded player may still owe an all-in opponent's bet. Otherwise
    # there is no one to bet against and the remaining board runs out.
    if len(acting) <= 1 and (not acting or state['players'][acting[0]]['street_bet'] >= state['current_bet']):
        _finish(state)
        return
    actor = _next(state, after)
    if actor is not None:
        state['actor'] = actor
        return
    if state['street'] == 'river':
        _finish(state)
        return
    _deal_street(state)
    state['street'] = {3: 'flop', 4: 'turn', 5: 'river'}[len(state['community'])]
    state['current_bet'] = 0
    state['min_raise'] = state['big_blind']
    state['acted_at'] = [None] * len(state['players'])
    for p in state['players']:
        p['street_bet'] = 0
    state['pending'] = _can_act(state)
    state['actor'] = _next(state, state['button'])


def _legal(state, seat):
    if state['complete'] or state['actor'] != seat:
        return {}
    p = state['players'][seat]
    call = min(p['stack'], state['current_bet'] - p['street_bet'])
    maximum = p['stack'] + p['street_bet']
    acted = state['acted_at'][seat]
    reopened = acted is None or state['current_bet'] - acted >= state['min_raise']
    can_raise = maximum > state['current_bet'] and reopened and len(_can_act(state)) > 1
    minimum = state['current_bet'] + state['min_raise']
    return {'fold': True, 'check': call == 0, 'call': call > 0, 'call_amount': call,
            'raise': can_raise and maximum >= minimum, 'min_raise_to': minimum,
            'max_raise_to': maximum, 'all_in': (maximum <= state['current_bet'] or can_raise),
            'raise_reopened': reopened}


def _apply(state, seat, action, amount=None):
    legal = _legal(state, seat)
    if not legal:
        raise ValueError('Wait for your turn.')
    p = state['players'][seat]
    current = state['current_bet']
    if action == 'all_in':
        if not legal['all_in']:
            raise ValueError('A short all-in has not reopened raising.')
        action = 'raise' if legal['max_raise_to'] > current else 'call'
        amount = legal['max_raise_to']
    if action == 'fold':
        p['folded'] = True
        _log(state, seat, 'fold')
    elif action == 'check':
        if not legal['check']:
            raise ValueError('Match the bet or fold; checking is unavailable.')
        _log(state, seat, 'check')
    elif action == 'call':
        if not legal['call']:
            raise ValueError('There is no bet to call. Check instead.')
        paid = legal['call_amount']
        _pay(state, seat, paid)
        _log(state, seat, 'call', paid)
    elif action == 'raise':
        amount = _integer(amount, 1, legal['max_raise_to'], 'Raise-to amount')
        if not legal['raise_reopened'] or len(_can_act(state)) <= 1:
            raise ValueError('Raising is not open.')
        increase = amount - current
        if increase <= 0 or (increase < state['min_raise'] and amount != legal['max_raise_to']):
            raise ValueError('Raise by the previous full raise or go all-in.')
        paid = amount - p['street_bet']
        _pay(state, seat, paid)
        state['current_bet'] = amount
        if increase >= state['min_raise']:
            state['min_raise'] = increase
            state['pending'] = [i for i in _can_act(state) if i != seat]
        else:
            state['pending'] = list(set(state['pending']) | {i for i in _can_act(state) if state['players'][i]['street_bet'] < amount})
        _log(state, seat, 'raise', amount)
    else:
        raise ValueError('Choose fold, check, call, raise or all_in.')
    state['acted_at'][seat] = state['current_bet']
    state['pending'] = [i for i in state['pending'] if i != seat]
    _advance(state, seat)


def _strength(cards, board):
    # Bots only inspect their own hole cards and the public board.
    if board:
        rank = evaluate(cards + board)
        return min(.99, .14 + rank[0] * .12 + rank[1] / 140)
    ranks = sorted((RANKS.index(c[0]) + 2 for c in cards), reverse=True)
    return min(.95, sum(ranks) / 40 + (.3 if ranks[0] == ranks[1] else 0) + (.08 if cards[0][1] == cards[1][1] else 0))


def _bots(state, rng):
    steps = 0
    while not state['complete'] and state['actor'] != 0:
        steps += 1
        if steps > 500:
            raise ValueError('Poker turn limit reached.')
        seat = state['actor']
        p = state['players'][seat]
        legal = _legal(state, seat)
        style = BOT_STYLES[p['name']]
        strength = _strength(p['cards'], state['community'])
        roll = rng.random()
        price = legal['call_amount'] / max(1, p['stack'])
        raise_chance = (style['raise'] * (.5 + strength)
                        if strength >= style['value_threshold'] else style['bluff'])
        # Plankton often bluffs weak cards; Krabs mostly raises larger for
        # value. Both decisions use only their cards, public board and price.
        if legal['raise'] and roll < raise_chance:
            target = min(legal['max_raise_to'], legal['min_raise_to'] + state['big_blind'] * style['raise_extra'])
            _apply(state, seat, 'raise', target)
        elif legal['call'] and strength < style['fold'] + price * .35 and roll < .85:
            _apply(state, seat, 'fold')
        else:
            _apply(state, seat, 'check' if legal['check'] else 'call')


def new_game(stake, rng=None):
    stake = _integer(stake, 20, 10000, 'Table buy-in')
    rng = _rng(rng)
    deck = [rank + suit for rank in RANKS for suit in SUITS]
    rng.shuffle(deck)
    names = ['You (Patrick)', *BOT_STYLES]
    players = [{'name': name, 'stack': stake, 'cards': [], 'street_bet': 0,
                'contributed': 0, 'folded': False} for name in names]
    for _ in range(2):
        for p in players:
            p['cards'].append(deck.pop())
    small = max(1, stake // 100)
    state = {'game': 'poker', 'variant': 'No-limit Texas Hold’em · one hand',
             'buy_in': stake, 'reserve': stake, 'players': players, 'deck': deck, 'burned': [],
             'community': [], 'button': 0, 'small_blind': small, 'big_blind': small * 2,
             'street': 'preflop', 'current_bet': small * 2, 'min_raise': small * 2,
             'acted_at': [None] * len(players), 'pending': list(range(len(players))), 'actor': 3,
             'complete': False, 'payout': None, 'log': [], 'pots': [], 'showdown': False}
    _pay(state, 1, small)
    _log(state, 1, 'small blind', small)
    _pay(state, 2, small * 2)
    _log(state, 2, 'big blind', small * 2)
    _bots(state, rng)
    return state


def act(state, action, amount=None, rng=None):
    """Apply one human decision and all bots until the next decision/settlement.

    Raise amount is the TOTAL bet target for this street, not the increment.
    Invalid moves leave the supplied JSON state untouched.
    """
    if state.get('complete'):
        raise ValueError('This hand is complete. Start another table buy-in.')
    result = _current_cast(state)
    _apply(result, 0, action, amount)
    _bots(result, _rng(rng))
    return result


def is_complete(state):
    return state.get('complete') is True


def _current_cast(state):
    """Resume a saved earlier hand after Gary's move from player to dealer."""
    result = copy.deepcopy(state)
    old_seats = {i for i, p in enumerate(result['players']) if i and p['name'] == 'Gary'}
    for i in old_seats:
        result['players'][i]['name'] = 'Mr. Krabs'
    for entry in result['log']:
        if entry['seat'] in old_seats:
            entry['name'] = 'Mr. Krabs'
            if entry.get('text') == 'Meow.':
                entry['text'] = action_line('Mr. Krabs', entry['action'], entry.get('amount', 0))
    return result


def public_state(state):
    state = _current_cast(state)
    result = {k: copy.deepcopy(v) for k, v in state.items() if k not in {'deck', 'burned', 'acted_at', 'pending'}}
    for entry in result['log']:
        if entry['seat']:
            entry['text'] = action_line(entry['name'], entry['action'], entry.get('amount', 0))
    for i, p in enumerate(result['players']):
        p['bet'] = p['street_bet']
        p['all_in'] = not p['folded'] and p['stack'] == 0
        latest = next((entry for entry in reversed(result['log']) if entry['seat'] == i), None)
        p['chatter'] = latest.get('text', '') if i and latest else ''
        if i and not (state['complete'] and state['showdown'] and not p['folded']):
            p['cards'] = [None, None]
        if i:
            p['style'] = BOT_STYLES[p['name']]['style']
    result['pot'] = sum(p['contributed'] for p in state['players']) if not state['complete'] else 0
    if result['showdown'] and result['pots']:
        # At showdown the last layer belongs to one contributor if that seat
        # bet more than everyone else. It is a return, not a contested win.
        # Derive the display flag here so older saved rounds get the same cue.
        last = result['pots'][-1]
        if len(last['winners']) == 1:
            winner = last['winners'][0]
            contribution = state['players'][winner]['contributed']
            if all(contribution > player['contributed']
                   for index, player in enumerate(state['players']) if index != winner):
                last['refund'] = True
    result['legal_actions'] = _legal(state, 0)
    result['dealer'] = 'Gary'
    return result
