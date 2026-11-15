"""Durable local casino rounds; the existing Store remains the wallet authority.

The desktop controller has one Store. Hold its reentrant lock across profile
validation and the existing atomic/idempotent spin so profile_select cannot
move a delayed request into another user's wallet.
"""
import json
import uuid

from .store import short_text


class CasinoService:
    """One active round per profile, atomic reserves and exactly-once settlement.

    Deliberately uses the controller's existing Store and wallet request ledger.
    All deck and bot state is local SQLite data; only public views leave here.
    """

    def __init__(self, store):
        self.store = store
        with store.transaction():
            store.db.execute('''CREATE TABLE IF NOT EXISTS casino_rounds(
                id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, game TEXT NOT NULL,
                revision INTEGER NOT NULL, complete INTEGER NOT NULL,
                state TEXT NOT NULL, created INTEGER NOT NULL)''')
            store.db.execute('''CREATE UNIQUE INDEX IF NOT EXISTS casino_one_active
                ON casino_rounds(profile_id) WHERE complete=0''')

    def _profile(self, payload):
        profile_id = payload.get('profile_id')
        if not isinstance(profile_id, str) or self.store.profile()['id'] != profile_id:
            raise ValueError('The local profile changed. Return to the original profile to retry.')
        return profile_id

    @staticmethod
    def _public(row):
        from .casino_poker import public_state
        from .casino_tables import blackjack_public, roulette_public
        state = json.loads(row['state'])
        view = {'poker': public_state, 'blackjack': blackjack_public, 'roulette': roulette_public}[row['game']](state)
        result = {'id': row['id'], 'game': row['game'], 'revision': row['revision'],
                  'complete': bool(row['complete']), 'view': view}
        if row['complete']:
            result['payout'] = state['payout']
        return result

    def state(self):
        with self.store.lock:
            profile_id = self.store.profile()['id']
            row = self.store.db.execute('''SELECT * FROM casino_rounds WHERE profile_id=?
                ORDER BY complete ASC, created DESC LIMIT 1''', (profile_id,)).fetchone()
            return {'round': self._public(row) if row else None, 'balance': self.store.balance(profile_id), 'profile_id': profile_id}

    def _response(self, round_id, profile_id):
        row = self.store.db.execute('SELECT * FROM casino_rounds WHERE id=?', (round_id,)).fetchone()
        return {'round': self._public(row), 'balance': self.store.balance(profile_id), 'profile_id': profile_id}

    def start(self, payload):
        from .casino_poker import new_game
        from .casino_tables import blackjack_new, roulette_new
        game = payload.get('game')
        if game not in ('poker', 'blackjack', 'roulette'):
            raise ValueError('Choose blackjack, poker or roulette.')
        encoded = {'game': game, 'bet': payload.get('bet')}
        if game == 'roulette':
            encoded['selection'] = payload.get('selection')
        with self.store.lock:
            self._profile(payload)

            def effect(profile_id):
                if self.store.db.execute('SELECT 1 FROM casino_rounds WHERE profile_id=? AND complete=0', (profile_id,)).fetchone():
                    raise ValueError('Finish your saved table round before starting another one.')
                if game == 'poker':
                    state = new_game(encoded['bet'])
                elif game == 'blackjack':
                    state = blackjack_new(encoded['bet'])
                else:
                    state = roulette_new(encoded['bet'], encoded['selection'])
                reserve = state['reserve']
                # Affordability is checked before accepting a winning result, too.
                self.store._transact(profile_id, -reserve, 'casino ' + game + ' table reserve')
                if state['complete']:
                    self.store._transact(profile_id, state['payout'], 'casino ' + game + ' table return')
                round_id = str(uuid.uuid4())
                self.store.db.execute('''INSERT INTO casino_rounds VALUES(?,?,?,?,?,?,
                    COALESCE((SELECT MAX(created)+1 FROM casino_rounds),1))''',
                    (round_id, profile_id, game, 0, int(state['complete']), json.dumps(state, allow_nan=False)))
                return self._response(round_id, profile_id)

            return self.store._wallet_request('casino_start', encoded, payload.get('request_id'), effect,
                                              expected_profile=payload['profile_id'])

    def act(self, payload):
        from .casino_poker import act
        from .casino_tables import blackjack_act
        round_id = short_text(payload.get('round_id'), 'Round ID')
        revision = payload.get('revision')
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise ValueError('Use the saved round revision.')
        encoded = {'round_id': round_id, 'revision': revision, 'move': payload.get('move'), 'amount': payload.get('amount')}
        with self.store.lock:
            self._profile(payload)

            def effect(profile_id):
                row = self.store.db.execute('SELECT * FROM casino_rounds WHERE id=? AND profile_id=?', (round_id, profile_id)).fetchone()
                if not row:
                    raise ValueError('That table round does not belong to this profile.')
                if row['complete']:
                    raise ValueError('That table round is already complete.')
                if row['revision'] != revision:
                    raise ValueError('The table has moved on. Refresh before choosing your next move.')
                state = json.loads(row['state'])
                if row['game'] == 'poker':
                    state = act(state, encoded['move'], encoded['amount'])
                elif row['game'] == 'blackjack':
                    state = blackjack_act(state, encoded['move'], encoded['amount'])
                else:
                    raise ValueError('Roulette settles in a single spin.')
                if state['complete']:
                    self.store._transact(profile_id, state['payout'], 'casino ' + row['game'] + ' table return')
                self.store.db.execute('UPDATE casino_rounds SET revision=revision+1,complete=?,state=? WHERE id=?',
                                      (int(state['complete']), json.dumps(state, allow_nan=False), round_id))
                return self._response(round_id, profile_id)

            return self.store._wallet_request('casino_act', encoded, payload.get('request_id'), effect,
                                              expected_profile=payload['profile_id'])


def spin_for_profile(store, payload):
    profile_id = payload.get("profile_id")
    if not isinstance(profile_id, str) or not profile_id or len(profile_id) > 160:
        raise ValueError("Choose a local profile before spinning.")
    with store.lock:
        if store.profile()["id"] != profile_id:
            raise ValueError("The local profile changed. Return to the original profile to retry.")
        result = store.spin(payload.get("bet"), payload.get("request_id"), expected_profile=profile_id)
        return {**result, "profile_id": profile_id}
