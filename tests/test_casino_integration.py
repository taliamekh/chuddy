"""Durable casino boundaries with real SQLite and deterministic private games."""
import concurrent.futures
import copy
import json
import random
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from chuddy.casino import CasinoService, spin_for_profile
from chuddy import casino_poker as poker, casino_tables as tables
from chuddy.store import Store, MAX_BALANCE
from tests.helpers import ControllerCase


class FixedPocket:
    def __init__(self, number):
        self.number = number

    def randrange(self, limit):
        return self.number


class FixedShoe:
    def __init__(self, draws):
        self.draws = draws

    def shuffle(self, cards):
        random.Random(19).shuffle(cards)
        for card in self.draws:
            cards.remove(card)
        cards.extend(reversed(self.draws))


class CasinoIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'casino.sqlite3'
        self.store = Store(self.path)
        self.casino = CasinoService(self.store)
        self.serial = 0

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def request_id(self):
        self.serial += 1
        return 'request-' + str(self.serial)

    def payload(self, game='blackjack', bet=10, **values):
        return {'game': game, 'bet': bet, 'profile_id': self.store.profile()['id'], 'request_id': self.request_id(), **values}

    def blackjack(self, **values):
        # Player18 beats dealer17 on stand: reserve40, return50 for a10 stake.
        original = tables.blackjack_new
        with patch('chuddy.casino_tables.blackjack_new', side_effect=lambda stake: original(stake, FixedShoe(['TC', '9D', '8S', '8C']))):
            return self.casino.start(self.payload(**values))

    def action(self, result, move='stand', **values):
        return {'round_id': result['round']['id'], 'revision': result['round']['revision'],
                'profile_id': result['profile_id'], 'request_id': self.request_id(), 'move': move, **values}

    def private(self, round_id):
        return dict(self.store.db.execute('SELECT * FROM casino_rounds WHERE id=?', (round_id,)).fetchone())

    def ledger(self, kind=None):
        sql = 'SELECT * FROM ledger'
        return [dict(r) for r in self.store.db.execute(sql + (' WHERE reason LIKE ?' if kind else ''), ('casino ' + kind + '%',) if kind else ())]

    def losing_roulette(self, payload):
        original = tables.roulette_new
        with patch('chuddy.casino_tables.roulette_new', side_effect=lambda stake, selection: original(stake, selection, FixedPocket(0))):
            return self.casino.start(payload)

    def test_blackjack_reserve_and_return_are_exact_once_across_restart(self):
        start_payload = self.payload(request_id='start-stable')
        original = tables.blackjack_new
        with patch('chuddy.casino_tables.blackjack_new', side_effect=lambda stake: original(stake, FixedShoe(['TC', '9D', '8S', '8C']))) as deal:
            started = self.casino.start(start_payload)
            self.assertEqual(started, self.casino.start(start_payload))
            self.assertEqual(deal.call_count, 1)
        self.assertEqual(self.store.balance(), 60)
        self.assertEqual([r['delta'] for r in self.ledger('blackjack')], [-40])
        action_payload = self.action(started, request_id='stand-stable')
        completed = self.casino.act(action_payload)
        self.assertEqual(completed['round']['payout'], 50)
        self.assertEqual(completed['balance'], 110)
        self.assertEqual(completed, self.casino.act(action_payload))
        self.assertEqual([r['delta'] for r in self.ledger('blackjack')], [-40, 50])
        self.store.close()
        self.store = Store(self.path)
        self.casino = CasinoService(self.store)
        self.assertEqual(started, self.casino.start(start_payload))
        self.assertEqual(completed, self.casino.act(action_payload))
        self.assertTrue(self.casino.state()['round']['complete'])
        self.assertEqual(self.store.balance(), 110)

    def test_duplicate_start_payload_conflict_and_second_active_table_do_not_debit(self):
        payload = self.payload(request_id='same-start')
        original = tables.blackjack_new
        with patch('chuddy.casino_tables.blackjack_new', side_effect=lambda stake: original(stake, FixedShoe(['TC', '9D', '8S', '8C']))):
            started = self.casino.start(payload)
        with self.assertRaises(ValueError):
            self.casino.start({**payload, 'bet': 12})
        with self.assertRaises(ValueError):
            self.losing_roulette(self.payload(game='roulette', selection='red'))
        self.assertEqual(self.store.balance(), 60)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM casino_rounds').fetchone()[0], 1)
        self.assertEqual(self.casino.state()['round']['id'], started['round']['id'])

    def test_stale_revision_and_conflicting_action_retry_leave_round_unchanged(self):
        # 4+6->10, hit2->12, then stand against dealer17.
        original = tables.blackjack_new
        with patch('chuddy.casino_tables.blackjack_new', side_effect=lambda stake: original(stake, FixedShoe(['4S', 'TD', '6C', '7D', '2C']))):
            started = self.casino.start(self.payload())
        action = self.action(started, move='hit', request_id='hit-once')
        advanced = self.casino.act(action)
        self.assertFalse(advanced['round']['complete'])
        private_before = self.private(started['round']['id'])
        with self.assertRaises(ValueError):
            self.casino.act(self.action(started, request_id='stale-new-id'))
        with self.assertRaises(ValueError):
            self.casino.act({**action, 'move': 'stand'})
        self.assertEqual(advanced, self.casino.act(action))
        self.assertEqual(self.private(started['round']['id']), private_before)
        self.assertEqual(self.store.balance(), 60)

    def test_profiles_keep_separate_saved_rounds_and_reject_late_requests(self):
        started = self.blackjack()
        first = started['profile_id']
        other = self.store.add_profile('Friend')
        self.store.select_profile(other['id'])
        self.assertIsNone(self.casino.state()['round'])
        with self.assertRaises(ValueError):
            self.casino.act(self.action(started))
        with self.assertRaises(ValueError):
            self.casino.act({**self.action(started), 'profile_id': other['id']})
        second = self.blackjack()
        self.assertNotEqual(second['round']['id'], started['round']['id'])
        self.assertEqual(self.store.balance(first), 60)
        self.assertEqual(self.store.balance(other['id']), 60)
        self.store.select_profile(first)
        self.assertEqual(self.casino.state()['round']['id'], started['round']['id'])

    def test_cross_connection_profile_switch_is_rechecked_inside_transaction(self):
        first = self.store.profile()['id']
        other_profile = self.store.add_profile('Friend')['id']
        other = Store(self.path)
        original_request = self.store._wallet_request
        def switched(*args, **kwargs):
            other.select_profile(other_profile)
            return original_request(*args, **kwargs)
        try:
            with patch.object(self.store, '_wallet_request', side_effect=switched), self.assertRaises(ValueError):
                self.losing_roulette(self.payload(game='roulette', selection='red', profile_id=first))
            self.assertEqual(self.store.balance(first), 100)
            self.assertEqual(self.store.balance(other_profile), 100)
            self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM casino_rounds').fetchone()[0], 0)
        finally:
            other.close()

    def test_legacy_slot_guard_rechecks_profile_at_atomic_wallet_boundary(self):
        first = self.store.profile()['id']
        friend = self.store.add_profile('Friend')['id']
        other = Store(self.path)
        original = self.store._wallet_request
        def switched(*args, **kwargs):
            other.select_profile(friend)
            return original(*args, **kwargs)
        try:
            with patch.object(self.store, '_wallet_request', side_effect=switched), self.assertRaises(ValueError):
                spin_for_profile(self.store, {'profile_id': first, 'bet': 10, 'request_id': 'late-slot'})
            self.assertEqual(self.store.balance(first), 100)
            self.assertEqual(self.store.balance(friend), 100)
        finally:
            other.close()

    def test_concurrent_duplicate_start_reserves_once(self):
        payload = self.payload(request_id='parallel-start')
        original = tables.blackjack_new
        with patch('chuddy.casino_tables.blackjack_new', side_effect=lambda stake: original(stake, FixedShoe(['TC', '9D', '8S', '8C']))):
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(lambda _: self.casino.start(payload), range(20)))
        self.assertTrue(all(result == results[0] for result in results))
        self.assertEqual(self.store.balance(), 60)
        self.assertEqual(len(self.ledger('blackjack')), 1)

    def test_concurrent_distinct_starts_allow_one_active_table(self):
        profile_id = self.store.profile()['id']
        def start(index):
            try:
                return self.casino.start({'game': 'blackjack', 'bet': 10, 'profile_id': profile_id, 'request_id': 'start-' + str(index)})
            except ValueError:
                return None
        original = tables.blackjack_new
        with patch('chuddy.casino_tables.blackjack_new', side_effect=lambda stake: original(stake, FixedShoe(['TC', '9D', '8S', '8C']))):
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(start, range(20)))
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(self.store.balance(), 60)

    def test_concurrent_losing_roulette_cannot_overspend_wallet(self):
        profile_id = self.store.profile()['id']
        original = tables.roulette_new
        def start(index):
            try:
                return self.casino.start({'game': 'roulette', 'bet': 10, 'selection': 'red', 'profile_id': profile_id, 'request_id': 'spin-' + str(index)})
            except ValueError:
                return None
        with patch('chuddy.casino_tables.roulette_new', side_effect=lambda stake, selection: original(stake, selection, FixedPocket(0))):
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
                results = list(pool.map(start, range(24)))
        self.assertEqual(sum(result is not None for result in results), 10)
        self.assertEqual(self.store.balance(), 0)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM casino_rounds').fetchone()[0], 10)

    def test_winning_roulette_still_requires_stake_before_return(self):
        self.store.transact(-100, 'fixture empties pretend wallet')
        payload = self.payload(game='roulette', selection={'kind': 'straight', 'value': 0})
        with self.assertRaises(ValueError):
            self.losing_roulette(payload)  # Fixed zero wins this straight selection.
        self.assertEqual(self.store.balance(), 0)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM casino_rounds').fetchone()[0], 0)
        self.assertEqual(self.ledger('roulette'), [])

    def test_roulette_wallet_cap_failure_rolls_back_reserve_and_round(self):
        with self.store.transaction():
            self.store.db.execute('UPDATE profile_wallet SET balance=?', (MAX_BALANCE,))
        with self.assertRaises(ValueError):
            self.losing_roulette(self.payload(game='roulette', selection={'kind': 'straight', 'value': 0}))
        self.assertEqual(self.store.balance(), MAX_BALANCE)
        self.assertEqual(self.ledger('roulette'), [])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM casino_rounds').fetchone()[0], 0)

    def test_settlement_wallet_cap_rolls_back_action_and_can_retry_after_spending(self):
        with self.store.transaction():
            self.store.db.execute('UPDATE profile_wallet SET balance=?', (MAX_BALANCE,))
        started = self.blackjack()
        self.store.deposit(40, 'fill-available-wallet')
        before = self.private(started['round']['id'])
        action = self.action(started, request_id='stand-after-space')
        with self.assertRaises(ValueError):
            self.casino.act(action)
        self.assertEqual(self.private(started['round']['id']), before)
        self.assertEqual(self.store.balance(), MAX_BALANCE)
        self.assertEqual([r['delta'] for r in self.ledger('blackjack')], [-40])
        self.store.transact(-100, 'fixture makes room for saved payout')
        complete = self.casino.act(action)
        self.assertTrue(complete['round']['complete'])
        self.assertEqual(complete['balance'], MAX_BALANCE - 50)

    def test_scoring_while_table_open_does_not_overwrite_earnings_at_settlement(self):
        started = self.blackjack()
        self.store.event('earned-during-table', 'fart', 'simulator sound test')
        self.assertEqual(self.store.balance(), 65)
        self.assertEqual(self.casino.state()['balance'], 65)
        completed = self.casino.act(self.action(started))
        self.assertEqual(completed['balance'], 115)
        self.assertEqual(self.store.earnings()['total_earned'], 5)

    def test_other_wagers_only_spend_unreserved_wallet(self):
        started = self.blackjack()
        with patch('chuddy.store.secrets.choice', side_effect=['clam', 'hat', 'butt']):
            self.store.spin(60, 'spend-free-wallet')
        self.assertEqual(self.store.balance(), 0)
        completed = self.casino.act(self.action(started))
        self.assertEqual(completed['balance'], 50)
        self.assertEqual(completed['round']['payout'], 50)

    def test_blackjack_shoe_and_concealed_dealer_hand_never_leave_projection(self):
        started = self.blackjack()
        view = started['round']['view']
        self.assertNotIn('shoe', view)
        self.assertEqual(view['dealer']['cards'], ['9D', None])
        self.assertIsNone(view['dealer']['total'])
        private = json.loads(self.private(started['round']['id'])['state'])
        self.assertEqual(private['dealer'], ['9D', '8C'])
        self.assertIn('shoe', private)
        cached = json.loads(self.store.db.execute("SELECT result FROM requests WHERE operation='casino_start'").fetchone()[0])
        self.assertNotIn('shoe', cached['round']['view'])
        view['hands'][0]['cards'].append('AS')
        self.assertEqual(self.casino.state()['round']['view']['hands'][0]['cards'], ['TC', '8S'])

    def test_poker_private_deck_bot_hands_and_folded_hands_stay_hidden(self):
        original_new, original_act = poker.new_game, poker.act
        with patch('chuddy.casino_poker.new_game', side_effect=lambda stake: original_new(stake, random.Random(0))):
            started = self.casino.start(self.payload(game='poker', bet=40))
        self.assertFalse(started['round']['complete'])
        self.assertEqual(self.store.balance(), 60)
        view = started['round']['view']
        self.assertNotIn('deck', view)
        self.assertNotIn('burned', view)
        self.assertNotIn('pending', view)
        self.assertEqual(len(view['players']), 5)
        self.assertTrue(all(p['cards'] == [None, None] for p in view['players'][1:]))
        with patch('chuddy.casino_poker.act', side_effect=lambda state, move, amount: original_act(state, move, amount, random.Random(1))):
            folded = self.casino.act(self.action(started, move='fold'))
        self.assertTrue(folded['round']['complete'])
        for player in folded['round']['view']['players'][1:]:
            if player['folded'] or not folded['round']['view']['showdown']:
                self.assertEqual(player['cards'], [None, None])
            else:
                self.assertTrue(all(card is not None for card in player['cards']))
        self.assertEqual(self.store.balance(), 100 - 40 + folded['round']['payout'])

    def test_browser_cannot_override_private_state_or_payout(self):
        started = self.blackjack()
        completed = self.casino.act({**self.action(started), 'state': {'payout': 999999}, 'payout': 999999})
        self.assertEqual(completed['round']['payout'], 50)
        self.assertEqual(self.store.balance(), 110)


class ControllerCasinoIntegrationTests(ControllerCase):
    def test_root_routes_share_authoritative_table_wallet_and_profiles(self):
        profile = self.store.profile()['id']
        original = tables.blackjack_new
        with patch('chuddy.casino_tables.blackjack_new', side_effect=lambda stake: original(stake, FixedShoe(['TC', '9D', '8S', '8C']))):
            started = self.controller.action({'action': 'casino_start', 'game': 'blackjack', 'bet': 10, 'request_id': 'route-start', 'profile_id': profile})
        self.assertEqual(self.controller.action({'action': 'casino_state'})['balance'], 60)
        self.controller.action({'action': 'score', 'kind': 'fart', 'request_id': 'route-score'})
        completed = self.controller.action({'action': 'casino_act', 'round_id': started['round']['id'], 'revision': 0, 'move': 'stand', 'request_id': 'route-stand', 'profile_id': profile})
        self.assertEqual(completed['balance'], 115)
        self.assertEqual(self.controller.state()['casino']['round']['id'], started['round']['id'])
        other = self.store.add_profile('Friend')
        self.controller.action({'action': 'profile_select', 'id': other['id']})
        with self.assertRaises(ValueError):
            self.controller.action({'action': 'casino_act', 'round_id': started['round']['id'], 'revision': 0, 'move': 'stand', 'request_id': 'route-stand', 'profile_id': profile})
        self.assertEqual(self.controller.action({'action': 'casino_state'})['balance'], 100)

    def test_state_snapshot_blocks_profile_switch_until_all_store_fields_are_captured(self):
        first = self.store.profile()['id']
        other = self.store.add_profile('Friend')
        self.store.select_profile(other['id'])
        self.store.deposit(200, 'friend-wallet')
        self.store.select_profile(first)
        reached, attempted, switched = threading.Event(), threading.Event(), threading.Event()
        original = self.store.balance
        def intercepted_balance(*args, **kwargs):
            value = original(*args, **kwargs)
            if not reached.is_set():
                reached.set()
                self.assertTrue(attempted.wait(timeout=1))
                self.assertFalse(switched.wait(timeout=.1), 'Profile switch escaped the aggregate Store snapshot lock.')
            return value
        def switch_profile():
            reached.wait(timeout=1)
            attempted.set()
            self.store.select_profile(other['id'])
            switched.set()
        worker = threading.Thread(target=switch_profile)
        worker.start()
        try:
            with patch.object(self.store, 'balance', side_effect=intercepted_balance):
                state = self.controller.state()
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive())
            self.assertEqual(state['profile']['id'], first)
            self.assertEqual(state['balance'], 100)
            self.assertEqual(state['earnings']['profile_id'], first)
            self.assertEqual(state['casino']['profile_id'], first)
            self.assertEqual(self.store.profile()['id'], other['id'])
        finally:
            worker.join(timeout=2)

    def test_state_snapshot_also_blocks_a_second_database_connection(self):
        first = self.store.profile()['id']
        friend = self.store.add_profile('Friend')
        other = Store(self.path / 'state.sqlite3')
        reached, attempted, switched = threading.Event(), threading.Event(), threading.Event()
        original = self.store.balance
        def intercepted_balance(*args, **kwargs):
            value = original(*args, **kwargs)
            if not reached.is_set():
                reached.set()
                self.assertTrue(attempted.wait(timeout=1))
                self.assertFalse(switched.wait(timeout=.1), 'A second connection changed profile mid-snapshot.')
            return value
        def switch_profile():
            reached.wait(timeout=1)
            attempted.set()
            other.select_profile(friend['id'])
            switched.set()
        worker = threading.Thread(target=switch_profile)
        worker.start()
        try:
            with patch.object(self.store, 'balance', side_effect=intercepted_balance):
                state = self.controller.state()
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive())
            self.assertEqual(state['profile']['id'], first)
            self.assertEqual(state['earnings']['profile_id'], first)
            self.assertEqual(state['casino']['profile_id'], first)
            self.assertEqual(self.store.profile()['id'], friend['id'])
        finally:
            worker.join(timeout=2)
            other.close()
