"""Real SQLite reward checks: no devices, UI, model, clock changes or real money."""
import concurrent.futures
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from chuddy.store import Store, MAX_BALANCE


class EarningsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'earnings.sqlite3'
        self.current = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)
        self.clock = patch('chuddy.store.now', side_effect=lambda: self.current.isoformat())
        self.clock.start()
        self.store = Store(self.path)

    def tearDown(self):
        self.store.close()
        self.clock.stop()
        self.temp.cleanup()

    def advance(self, seconds):
        self.current += timedelta(seconds=seconds)

    def ticket(self, request='start', expected_profile=None):
        return self.store.start_game(request, expected_profile=expected_profile)

    def finish(self, round_id, request='finish', caught=5, expected_profile=None):
        return self.store.reward_game(round_id, request, caught, expected_profile=expected_profile)

    def test_new_score_earns_same_wallet_and_retry_does_not_mint(self):
        self.assertIs(self.store.event('first', 'fart', 'manual'), True)
        self.assertEqual(self.store.balance(), 105)
        self.assertIs(self.store.event('first', 'fart', 'manual'), False)
        self.assertEqual(self.store.balance(), 105)
        self.assertIs(self.store.event('fart', 'fart', 'deliberate sound test'), True)
        summary = self.store.earnings()
        self.assertEqual(summary['total_earned'], 10)
        self.assertEqual(summary['score_earned'], 10)
        self.assertEqual(summary['balance'], 110)
        self.assertEqual(summary['rewarded_events'], 2)
        self.assertIn('manual', self.store.db.execute("SELECT reason FROM ledger ORDER BY id LIMIT 1").fetchone()[0])

    def test_conflicting_score_id_rolls_back_without_another_reward(self):
        self.store.event('one', 'fart', 'manual')
        with self.assertRaises(ValueError):
            self.store.event('one', 'fart', 'different source')
        self.assertEqual(self.store.scores()['total'], 1)
        self.assertEqual(self.store.earnings()['total_earned'], 5)

    def test_saved_burp_reward_and_id_remain_readable_after_reopen(self):
        profile_id = self.store.profile()['id']
        timestamp = self.current.isoformat()
        with self.store.transaction():
            self.store.db.execute('INSERT INTO events(id,kind,source,confidence,time,profile_id) VALUES(?,?,?,?,?,?)',
                                  ('saved', 'burp', 'manual', None, timestamp, profile_id))
            self.store._earn(profile_id, 'score:saved', 'score', 'burp', 'manual', 3, 1, 0, timestamp,
                             {'event_id': 'saved', 'base': 3, 'sequence': 1})
        self.store.close()
        self.store = Store(self.path)
        for kind in ('burp', 'fart'):
            with self.assertRaises(ValueError):
                self.store.event('saved', kind, 'manual')
        scores, earnings = self.store.scores(), self.store.earnings()
        self.assertEqual((scores['burp'], scores['fart'], scores['total']), (1, 0, 1))
        self.assertEqual(scores['recent'][0]['id'], 'saved')
        self.assertEqual(scores['recent_farts'], [])
        self.assertEqual(earnings['balance'], 103)
        self.assertEqual(earnings['recent'][0]['detail']['event_id'], 'saved')
        self.store.event('new', 'fart', 'manual')
        self.assertEqual(self.store.balance(), 108)
        self.assertEqual(self.store.earnings()['rewarded_events'], 2)

    def test_all_honest_sources_earn_including_accepted_self_playback(self):
        sources = ['manual', 'deliberate sound test', 'simulator sound test', 'Pi physical playback · hardware unvalidated', 'YAMNet microphone · uncalibrated']
        for index, source in enumerate(sources):
            self.store.event(str(index), 'fart', source, .8 if source.startswith('YAMNet') else None)
        self.assertEqual(self.store.earnings()['score_earned'], 25)
        self.assertEqual({row['source'] for row in self.store.earnings()['recent']}, set(sources))

    def test_progressive_multiplier_milestones_and_level_cap(self):
        for index in range(10):
            self.store.event(str(index), 'fart', 'manual')
        summary = self.store.earnings()
        self.assertEqual(summary['score_earned'], 70)  # 10 * 5 + 20.
        self.assertEqual(summary['level'], 2)
        self.assertEqual(summary['next_level_at'], 20)
        self.store.event('eleven', 'fart', 'manual')
        self.assertEqual(self.store.earnings()['recent'][0]['amount'], 10)
        for index in range(11, 21):
            self.store.event(str(index), 'fart', 'manual')
        summary = self.store.earnings()
        self.assertEqual(summary['level'], 3)
        self.assertEqual(summary['multiplier'], 3)
        self.assertIsNone(summary['next_level_at'])
        self.assertEqual(summary['recent'][0]['amount'], 15)

    def test_daily_cap_does_not_cap_counts_or_retroactively_pay(self):
        for index in range(200):
            self.store.event('event-' + str(index), 'fart', 'manual')
        summary = self.store.earnings()
        self.assertEqual(summary['daily_earned'], 500)
        self.assertEqual(summary['daily_remaining'], 0)
        self.assertEqual(self.store.scores()['total'], 200)
        self.assertEqual(self.store.balance(), 600)
        self.advance(86400)
        self.assertEqual(self.store.earnings()['daily_remaining'], 500)
        self.assertEqual(self.store.balance(), 600)
        self.store.event('next-day', 'fart', 'manual')
        self.assertEqual(self.store.earnings()['daily_earned'], 15)
        self.assertEqual(self.store.earnings()['total_earned'], 515)

    def test_wallet_capacity_clips_reward_without_losing_score(self):
        with self.store.transaction():
            self.store.db.execute('UPDATE profile_wallet SET balance=?', (MAX_BALANCE - 2,))
        self.store.event('near-cap', 'fart', 'manual')
        self.assertEqual(self.store.balance(), MAX_BALANCE)
        self.assertEqual(self.store.earnings()['total_earned'], 2)
        self.store.event('full', 'fart', 'manual')
        self.assertEqual(self.store.scores()['total'], 2)
        self.assertEqual(self.store.earnings()['recent'][0]['amount'], 0)

    def test_profiles_have_independent_levels_caps_and_wallets(self):
        first = self.store.profile()['id']
        self.store.event('first', 'fart', 'manual')
        friend = self.store.add_profile('Friend')
        self.store.select_profile(friend['id'])
        self.assertEqual(self.store.earnings()['total_earned'], 0)
        self.store.event('friend', 'fart', 'manual')
        self.assertEqual(self.store.balance(), 105)
        self.assertEqual(self.store.earnings(first)['balance'], 105)
        self.assertEqual(self.store.earnings(first)['score_earned'], 5)

    def test_reward_and_score_are_one_atomic_transaction(self):
        self.store.db.execute("CREATE TEMP TRIGGER fail_reward BEFORE INSERT ON earnings BEGIN SELECT RAISE(ABORT,'simulated storage failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.event('must-rollback', 'fart', 'manual')
        self.assertEqual(self.store.scores()['total'], 0)
        self.assertEqual(self.store.balance(), 100)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM ledger').fetchone()[0], 0)

    def test_concurrent_event_retries_pay_once_across_connections(self):
        other = Store(self.path)
        try:
            def submit(index):
                target = self.store if index % 2 else other
                return target.event('same', 'fart', 'manual')
            with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
                results = list(pool.map(submit, range(40)))
            self.assertEqual(sum(results), 1)
            self.assertEqual(self.store.balance(), 105)
            self.assertEqual(self.store.earnings()['total_earned'], 5)
        finally:
            other.close()

    def test_concurrent_new_events_respect_shared_daily_cap(self):
        def submit(index):
            return self.store.event('unique-' + str(index), 'fart', 'manual')
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            self.assertTrue(all(pool.map(submit, range(120))))
        self.assertEqual(self.store.scores()['total'], 120)
        self.assertEqual(self.store.earnings()['daily_earned'], 500)

    def test_earnings_fund_existing_purchase_but_do_not_bypass_insufficient_funds(self):
        self.store.transact(-100, 'fixture empties pretend wallet')
        with self.assertRaises(ValueError):
            self.store.purchase('tiny-hat', 'unfunded')
        for index in range(3):
            self.store.event('earn-' + str(index), 'fart', 'manual')
        result = self.store.purchase('tiny-hat', 'funded')
        self.assertEqual(result['balance'], 3)
        self.assertEqual(self.store.earnings()['total_earned'], 15)
        with self.assertRaises(ValueError):
            self.store.spin(4, 'no-funds')

    def test_migration_and_restart_do_not_retroactively_mint_for_old_events(self):
        self.store.close()
        db = sqlite3.connect(self.path)
        db.execute('DROP TABLE earnings')
        db.execute('DROP TABLE clam_rounds')
        db.execute('PRAGMA user_version=2')
        profile_id = db.execute('SELECT id FROM profiles LIMIT 1').fetchone()[0]
        db.execute('INSERT INTO events(id,kind,source,confidence,time,profile_id) VALUES(?,?,?,?,?,?)', ('old', 'fart', 'manual', None, self.current.isoformat(), profile_id))
        db.commit()
        db.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.db.execute('PRAGMA user_version').fetchone()[0], 3)
        self.assertEqual(self.store.balance(), 100)
        self.assertEqual(self.store.earnings()['rewarded_events'], 0)
        self.assertFalse(self.store.event('old', 'fart', 'manual'))
        self.store.event('new', 'fart', 'manual')
        self.assertEqual(self.store.balance(), 105)
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.balance(), 105)
        self.assertEqual(self.store.earnings()['total_earned'], 5)

    def test_game_ticket_completion_is_bounded_idempotent_and_durable(self):
        ticket = self.ticket()
        self.assertEqual(ticket, self.ticket())
        self.assertEqual(ticket['minimum_seconds'], 20)
        with self.assertRaises(ValueError):
            self.finish(ticket['round_id'])
        self.advance(20)
        result = self.finish(ticket['round_id'])
        self.assertEqual(result['reward'], 10)
        self.assertEqual(result['balance'], 110)
        self.assertEqual(result['profile_id'], self.store.profile()['id'])
        self.assertEqual(result, self.finish(ticket['round_id']))
        with self.assertRaises(ValueError):
            self.finish(ticket['round_id'], request='different-request')
        with self.assertRaises(ValueError):
            self.finish(ticket['round_id'], caught=6)
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(result, self.finish(ticket['round_id']))
        self.assertEqual(self.store.earnings()['game_earned'], 10)

    def test_game_catches_and_ticket_owner_are_validated(self):
        first = self.store.profile()['id']
        ticket = self.ticket(expected_profile=first)
        self.advance(20)
        for caught in (0, 2, 2001, True, 3.5, '5'):
            with self.subTest(caught=caught), self.assertRaises(ValueError):
                self.finish(ticket['round_id'], caught=caught)
        friend = self.store.add_profile('Friend')
        self.store.select_profile(friend['id'])
        with self.assertRaises(ValueError):
            self.finish(ticket['round_id'])
        with self.assertRaises(ValueError):
            self.ticket('stale-profile', expected_profile=first)
        self.assertEqual(self.store.balance(), 100)

    def test_cancel_and_bulk_cancel_tickets_never_reward_partial_games(self):
        ticket = self.ticket()
        self.advance(25)
        self.assertTrue(self.store.cancel_game(ticket['round_id'])['cancelled'])
        self.assertTrue(self.store.cancel_game(ticket['round_id'])['cancelled'])
        self.assertEqual(self.ticket()['status'], 'cancelled')
        with self.assertRaises(ValueError):
            self.finish(ticket['round_id'])
        second = self.ticket('second-start')
        self.assertEqual(self.store.cancel_active_games()['cancelled'], 1)
        self.assertEqual(self.store.cancel_active_games()['cancelled'], 0)
        self.advance(25)
        with self.assertRaises(ValueError):
            self.finish(second['round_id'])
        self.assertEqual(self.store.earnings()['total_earned'], 0)

    def test_one_active_game_and_expired_ticket_cannot_earn(self):
        ticket = self.ticket()
        with self.assertRaises(ValueError):
            self.ticket('another-start')
        self.advance(121)
        with self.assertRaises(ValueError):
            self.finish(ticket['round_id'])
        replacement = self.ticket('replacement')
        self.assertNotEqual(ticket['round_id'], replacement['round_id'])
        self.assertEqual(self.store.balance(), 100)

    def test_game_daily_limit_and_shared_earnings_cap(self):
        results = []
        for index in range(4):
            ticket = self.ticket('start-' + str(index))
            self.advance(20)
            results.append(self.finish(ticket['round_id'], 'finish-' + str(index)))
        self.assertEqual([r['reward'] for r in results], [10, 10, 10, 0])
        self.assertEqual(results[-1]['limit_reason'], 'daily game limit')
        self.assertEqual(self.store.earnings()['game_rounds_remaining'], 0)
        self.assertEqual(self.store.earnings()['game_earned'], 30)
        for index in range(200):
            self.store.event('cap-' + str(index), 'fart', 'manual')
        self.assertEqual(self.store.earnings()['daily_earned'], 500)
        self.assertEqual(self.store.balance(), 600)

    def test_parallel_completion_requests_credit_a_ticket_once(self):
        ticket = self.ticket()
        self.advance(20)
        def complete(index):
            try:
                return self.finish(ticket['round_id'], 'claim-' + str(index))['reward']
            except ValueError:
                return 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            results = list(pool.map(complete, range(20)))
        self.assertEqual(sum(results), 10)
        self.assertEqual(self.store.earnings()['game_earned'], 10)


if __name__ == '__main__':
    unittest.main()
