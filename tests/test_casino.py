"""CasinoService against real temporary SQLite wallets; no running app used."""
import concurrent.futures
import json
from pathlib import Path
import random
import tempfile
import threading
import unittest
from unittest.mock import patch

from chuddy.casino import CasinoService, spin_for_profile
from chuddy.casino_poker import act as poker_act, new_game as poker_new
from chuddy.store import Store


class CasinoServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'state.sqlite3'
        self.store = Store(self.path)
        self.casino = CasinoService(self.store)
        self.profile = self.store.profile()['id']

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def start(self, game='blackjack', bet=10, request_id='start-1', **kwargs):
        return self.casino.start({'profile_id': self.profile, 'game': game, 'bet': bet,
                                  'request_id': request_id, **kwargs})

    def move(self, response, move='stand', request_id='move-1', **kwargs):
        table = response['round']
        return self.casino.act({'profile_id': self.profile, 'round_id': table['id'],
                                'revision': table['revision'], 'move': move,
                                'request_id': request_id, **kwargs})

    def count(self, table):
        return self.store.db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]

    def ledger(self):
        return [dict(row) for row in self.store.db.execute('SELECT * FROM ledger ORDER BY id')]

    def snapshot(self):
        return {table: [tuple(row) for row in self.store.db.execute('SELECT * FROM ' + table)]
                for table in ['profile_wallet', 'ledger', 'casino_rounds', 'requests']}

    def shoe(self, cards):
        return patch('chuddy.casino_tables._shoe', return_value=['2C'] * 50 + list(reversed(cards)))

    def test_empty_state_and_idempotent_schema_creation(self):
        self.assertEqual(self.casino.state(), {'round': None, 'balance': 100, 'profile_id': self.profile})
        CasinoService(self.store)
        self.assertEqual(self.count('casino_rounds'), 0)

    def test_blackjack_reserve_and_final_payout_are_exactly_once(self):
        with self.shoe(['TS', 'AD', '8C', '6D']):
            started = self.start()
        self.assertFalse(started['round']['complete'])
        self.assertEqual(started['balance'], 60)
        self.assertEqual([row['delta'] for row in self.ledger()], [-40])
        ended = self.move(started)
        self.assertTrue(ended['round']['complete'])
        self.assertEqual(ended['round']['payout'], 50)
        self.assertEqual(ended['balance'], 110)
        self.assertEqual([row['delta'] for row in self.ledger()], [-40, 50])
        before = self.snapshot()
        self.assertEqual(self.move(started), ended)
        self.assertEqual(self.snapshot(), before)

    def test_natural_is_reserved_and_settled_in_same_start_transaction(self):
        with self.shoe(['AS', '9D', 'KC', '7D']):
            result = self.start()
        self.assertTrue(result['round']['complete'])
        self.assertEqual(result['balance'], 115)
        self.assertEqual(result['round']['payout'], 55)
        self.assertEqual([row['delta'] for row in self.ledger()], [-40, 55])
        self.assertEqual(self.count('requests'), 1)

    def test_start_retry_returns_original_snapshot_without_reroll_or_second_charge(self):
        with self.shoe(['TS', 'AD', '8C', '6D']):
            result = self.start()
        before = self.snapshot()
        with patch('chuddy.casino_tables.blackjack_new', side_effect=AssertionError('retry rerolled')):
            self.assertEqual(self.start(), result)
        self.assertEqual(self.snapshot(), before)
        # Cached responses are intentionally historical; state() supplies current balance.
        self.store.deposit(10, 'another-wallet-operation')
        self.assertEqual(self.start()['balance'], 60)
        self.assertEqual(self.casino.state()['balance'], 70)

    def test_retry_id_cannot_change_start_or_action_payload(self):
        with self.shoe(['4S', 'TD', '6C', '7D', '3H']):
            start = self.start()
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'different operation'):
            self.start(bet=12)
        self.assertEqual(self.snapshot(), before)
        first = self.move(start, 'hit')
        self.assertFalse(first['round']['complete'])
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'different operation'):
            self.move(start, 'stand')
        self.assertEqual(self.snapshot(), before)

    def test_request_id_conflict_with_existing_wallet_operation(self):
        self.store.deposit(1, 'start-1')
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'different operation'):
            self.start()
        self.assertEqual(self.snapshot(), before)

    def test_one_active_round_rejects_another_game_without_wallet_writes(self):
        with self.shoe(['4S', 'TD', '6C', '7D']):
            self.start()
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'Finish your saved'):
            self.start('roulette', request_id='other-start', selection={'kind': 'red'})
        self.assertEqual(self.snapshot(), before)

    def test_stale_revision_and_completed_round_reject_new_request_ids(self):
        with self.shoe(['4S', 'TD', '6C', '7D', '3H']):
            started = self.start()
        active = self.move(started, 'hit')
        self.assertEqual(active['round']['revision'], 1)
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'moved on'):
            self.move(started, request_id='new-stale-id')
        self.assertEqual(self.snapshot(), before)
        complete = self.move(active, request_id='stand-current')
        self.assertTrue(complete['round']['complete'])
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'already complete'):
            self.move(complete, request_id='new-after-complete')
        self.assertEqual(self.snapshot(), before)

    def test_invalid_move_amount_and_revision_roll_back_everything(self):
        with self.shoe(['4S', 'TD', '6C', '7D']):
            started = self.start()
        for move, fields in [('split', {}), ('dance', {}), ('hit', {'amount': 10}),
                             ('stand', {'revision': True}), ('stand', {'revision': -1}),
                             ('stand', {'revision': '0'}), ('stand', {'round_id': 'missing'})]:
            before = self.snapshot()
            with self.assertRaises(ValueError):
                self.move(started, move, request_id='invalid', **fields)
            self.assertEqual(self.snapshot(), before)

    def test_cross_profile_payload_and_round_owner_guards(self):
        with self.shoe(['4S', 'TD', '6C', '7D']):
            started = self.start()
        other = self.store.add_profile('Sandy')
        self.store.select_profile(other['id'])
        self.assertIsNone(self.casino.state()['round'])
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'profile changed'):
            self.move(started)
        with self.assertRaisesRegex(ValueError, 'does not belong'):
            self.move(started, profile_id=other['id'])
        self.assertEqual(self.snapshot(), before)
        self.store.select_profile(self.profile)
        self.assertEqual(self.casino.state()['round'], started['round'])

    def test_another_profile_can_start_its_own_active_round(self):
        with self.shoe(['4S', 'TD', '6C', '7D']):
            first = self.start()
            other = self.store.add_profile('Pearl')
            self.store.select_profile(other['id'])
            second = self.start(profile_id=other['id'])
        self.assertNotEqual(first['round']['id'], second['round']['id'])
        self.assertEqual(self.store.balance(self.profile), 60)
        self.assertEqual(self.store.balance(other['id']), 60)
        self.assertEqual(self.count('casino_rounds'), 2)

    def test_restart_restores_active_blackjack_without_shoe_or_hole_card(self):
        with self.shoe(['4S', 'TD', '6C', '7D', '3H']):
            result = self.start()
        raw = self.store.db.execute('SELECT state FROM casino_rounds').fetchone()[0]
        self.assertIn('shoe', json.loads(raw))
        self.store.close()
        self.store = Store(self.path)
        self.casino = CasinoService(self.store)
        resumed = self.casino.state()
        self.assertEqual(resumed['round'], result['round'])
        self.assertEqual(resumed['balance'], 60)
        public = resumed['round']['view']
        self.assertNotIn('shoe', public)
        self.assertEqual(public['dealer']['cards'], ['TD', None])
        self.assertIsNone(public['dealer']['total'])
        ended = self.move(resumed)
        self.assertTrue(ended['round']['complete'])
        self.assertEqual(self.count('casino_rounds'), 1)

    def test_restart_restores_poker_without_deck_burned_or_hidden_bot_cards(self):
        with patch('chuddy.casino_poker.new_game', side_effect=lambda stake: poker_new(stake, random.Random(3))):
            result = self.start('poker', 50)
        self.store.close()
        self.store = Store(self.path)
        self.casino = CasinoService(self.store)
        resumed = self.casino.state()
        view = resumed['round']['view']
        self.assertEqual(resumed['round'], result['round'])
        for key in ['deck', 'burned', 'pending', 'acted_at']:
            self.assertNotIn(key, view)
        self.assertEqual(len(view['players'][0]['cards']), 2)
        self.assertTrue(all(player['cards'] == [None, None] for player in view['players'][1:]))

    def test_concurrent_exact_retries_reserve_only_once(self):
        barrier = threading.Barrier(8)
        def task(_):
            barrier.wait()
            return self.start('roulette', 5, selection={'kind': 'red'})
        with patch('chuddy.casino_tables.random.SystemRandom') as rng:
            rng.return_value.randrange.return_value = 1
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(task, range(8)))
        self.assertTrue(all(result == results[0] for result in results))
        self.assertEqual(self.store.balance(), 105)
        self.assertEqual(self.count('casino_rounds'), 1)
        self.assertEqual(self.count('requests'), 1)
        self.assertEqual([row['delta'] for row in self.ledger()], [-5, 10])

    def test_concurrent_new_ids_same_revision_allow_only_one_move_and_return(self):
        with self.shoe(['TS', 'AD', '8C', '6D']):
            started = self.start()
        barrier = threading.Barrier(8)
        def task(index):
            barrier.wait()
            try:
                return self.move(started, request_id='concurrent-' + str(index))
            except ValueError:
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(task, range(8)))
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(self.store.balance(), 110)
        self.assertEqual([row['delta'] for row in self.ledger()], [-40, 50])
        self.assertEqual(self.count('requests'), 2)

    def test_concurrent_exact_action_retries_settle_once_and_return_same_result(self):
        with self.shoe(['TS', 'AD', '8C', '6D']):
            started = self.start()
        barrier = threading.Barrier(8)
        def task(_):
            barrier.wait()
            return self.move(started)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(task, range(8)))
        self.assertTrue(all(result == results[0] for result in results))
        self.assertEqual(self.store.balance(), 110)
        self.assertEqual([row['delta'] for row in self.ledger()], [-40, 50])
        self.assertEqual(self.count('requests'), 2)

    def test_insufficient_reserve_is_rejected_even_for_winning_result(self):
        for game, bet, fields in [('blackjack', 50, {}), ('roulette', 101, {'selection': {'kind': 'red'}}), ('poker', 101, {})]:
            before = self.snapshot()
            with self.shoe(['AS', '9D', 'KC', '7D']), \
                 patch('chuddy.casino_poker.new_game', side_effect=lambda stake: poker_new(stake, random.Random(3))), \
                 patch('chuddy.casino_tables.random.SystemRandom') as rng:
                rng.return_value.randrange.return_value = 1
                with self.assertRaisesRegex(ValueError, 'Not enough'):
                    self.start(game, bet, request_id='too-big-' + game, **fields)
            self.assertEqual(self.snapshot(), before)

    def test_failure_after_settlement_rolls_back_wallet_round_and_request(self):
        with self.shoe(['TS', 'AD', '8C', '6D']):
            started = self.start()
        before = self.snapshot()
        with patch.object(self.casino, '_response', side_effect=RuntimeError('projection failed')):
            with self.assertRaisesRegex(RuntimeError, 'projection failed'):
                self.move(started)
        self.assertEqual(self.snapshot(), before)
        self.assertTrue(self.move(started)['round']['complete'])

    def test_failure_after_start_reserve_rolls_back_everything(self):
        before = self.snapshot()
        with self.shoe(['AS', '9D', 'KC', '7D']), patch.object(self.casino, '_response', side_effect=RuntimeError('projection failed')):
            with self.assertRaises(RuntimeError):
                self.start()
        self.assertEqual(self.snapshot(), before)

    def test_roulette_round_full_path_and_next_round_state_is_latest(self):
        with patch('chuddy.casino_tables.random.SystemRandom') as rng:
            rng.return_value.randrange.return_value = 0
            zero = self.start('roulette', 2, selection={'kind': 'straight', 'value': 0})
            rng.return_value.randrange.return_value = 1
            red = self.start('roulette', 5, request_id='roulette-next', selection={'kind': 'red'})
        self.assertEqual(zero['round']['payout'], 72)
        self.assertEqual(zero['balance'], 170)
        self.assertEqual(red['round']['payout'], 10)
        self.assertEqual(red['balance'], 175)
        self.assertEqual(self.casino.state()['round'], red['round'])
        self.assertEqual(self.count('casino_rounds'), 2)
        self.assertEqual([r['delta'] for r in self.ledger()], [-2, 72, -5, 10])

    def test_complete_poker_hand_with_real_bot_turns_settles_once(self):
        with patch('chuddy.casino_poker.new_game', side_effect=lambda stake: poker_new(stake, random.Random(3))), \
             patch('chuddy.casino_poker.act', side_effect=lambda state, move, amount: poker_act(state, move, amount, random.Random(4))):
            result = self.start('poker', 50)
            for index in range(40):
                if result['round']['complete']:
                    break
                legal = result['round']['view']['legal_actions']
                move = 'check' if legal['check'] else 'call'
                result = self.move(result, move, request_id='poker-move-' + str(index))
            self.assertTrue(result['round']['complete'])
        payout = result['round']['payout']
        self.assertEqual(self.store.balance(), 100 - 50 + payout)
        self.assertEqual([row['delta'] for row in self.ledger()], [-50, payout])
        self.assertEqual(self.casino.state()['round'], result['round'])

    def test_blackjack_split_and_double_moves_never_spend_additional_wallet_money(self):
        with self.shoe(['5S', 'TD', '5C', '7D', '6H', '4D', 'TC', 'TH']):
            result = self.start()
        for index, move in enumerate(['split', 'double', 'double']):
            result = self.move(result, move, request_id='split-round-' + str(index))
            if index < 2:
                self.assertEqual(self.store.balance(), 60)
                self.assertEqual(len(self.ledger()), 1)
        self.assertTrue(result['round']['complete'])
        self.assertEqual(result['round']['payout'], 80)
        self.assertEqual(self.store.balance(), 140)
        self.assertEqual([row['delta'] for row in self.ledger()], [-40, 80])

    def test_legacy_spin_guard_retries_and_profile_rejection(self):
        payload = {'profile_id': self.profile, 'bet': 10, 'request_id': 'guarded-slot'}
        with patch('chuddy.store.secrets.choice', return_value='clam'):
            result = spin_for_profile(self.store, payload)
        self.assertEqual(result['profile_id'], self.profile)
        self.assertEqual(result['payout'], 50)
        before = self.snapshot()
        with patch('chuddy.store.secrets.choice', side_effect=AssertionError('rerolled')):
            self.assertEqual(spin_for_profile(self.store, payload), result)
        self.assertEqual(self.snapshot(), before)
        other = self.store.add_profile('Larry')
        self.store.select_profile(other['id'])
        with self.assertRaisesRegex(ValueError, 'profile changed'):
            spin_for_profile(self.store, payload)
        self.assertEqual(self.store.balance(other['id']), 100)

    def test_profile_switch_via_second_connection_before_transaction_is_rejected(self):
        other = self.store.add_profile('Karen')
        second = Store(self.path)
        original = self.store._wallet_request
        def switching_request(*args, **kwargs):
            second.select_profile(other['id'])
            return original(*args, **kwargs)
        before = self.snapshot()
        try:
            with patch.object(self.store, '_wallet_request', side_effect=switching_request):
                with self.assertRaisesRegex(ValueError, 'profile changed'):
                    self.start('roulette', 5, selection={'kind': 'red'})
            self.assertEqual(self.snapshot(), before)
        finally:
            second.close()

    def test_profile_switch_via_second_connection_before_action_transaction_is_rejected(self):
        with self.shoe(['TS', 'AD', '8C', '6D']):
            started = self.start()
        other = self.store.add_profile('Mrs. Puff')
        second = Store(self.path)
        original = self.store._wallet_request
        def switching_request(*args, **kwargs):
            second.select_profile(other['id'])
            return original(*args, **kwargs)
        before = self.snapshot()
        try:
            with patch.object(self.store, '_wallet_request', side_effect=switching_request):
                with self.assertRaisesRegex(ValueError, 'profile changed'):
                    self.move(started)
            self.assertEqual(self.snapshot(), before)
        finally:
            second.close()


if __name__ == '__main__':
    unittest.main()
