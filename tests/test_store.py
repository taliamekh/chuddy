import concurrent.futures
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chuddy.store import Store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'state.sqlite3'
        self.store = Store(self.path)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_existing_model_preference_survives_the_new_install_default(self):
        self.assertEqual(self.store.settings()['ai_model'], 'llama3.1:8b-instruct-q4_K_M')
        self.store.setting('ai_model', 'qwen3:4b-instruct-2507-q4_K_M')
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.settings()['ai_model'], 'qwen3:4b-instruct-2507-q4_K_M')

    def test_restart_preserves_real_state_and_installation_identity(self):
        initial = self.store.settings()['installation_id']
        self.store.deposit(35, 'deposit-1')
        self.store.event('sound-1', 'fart', 'manual')
        self.store.remember('nickname', 'Clam friend')
        self.store.add_deadline('Report', '2026-11-05T23:00:00-05:00')
        self.store.update_settings({'cooldown': 57, 'voice': True})
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.balance(), 140)  # Includes the new fart reward.
        self.assertEqual(self.store.scores()['total'], 1)
        self.assertEqual(self.store.settings()['installation_id'], initial)
        self.assertEqual(self.store.settings()['cooldown'], 57)
        self.assertEqual(self.store.memories()[0]['value'], 'Clam friend')
        self.assertEqual(self.store.deadlines()[0]['due'], '2026-11-06T04:00:00+00:00')

    def test_profiles_isolate_history_wallet_deadlines_memory_and_scores(self):
        first = self.store.profile()
        self.store.message('user', 'hello', 'you')
        self.store.event('first-event', 'fart', 'manual')
        self.store.deposit(20, 'same-request')
        self.store.add_deadline('First report', '2026-11-05T12:00:00Z')
        self.store.remember('favourite', 'clam')
        other = self.store.add_profile('Friend')
        self.assertEqual(self.store.profile(), first)
        self.store.select_profile(other['id'])
        self.assertEqual(self.store.messages(), [])
        self.assertEqual(self.store.balance(), 100)
        self.assertEqual(self.store.deadlines(), [])
        self.assertEqual(self.store.memories(), [])
        self.assertEqual(self.store.scores()['total'], 0)
        self.store.deposit(10, 'same-request')
        self.assertEqual(self.store.balance(), 110)
        self.store.select_profile(first['id'])
        self.assertEqual(self.store.balance(), 125)  # Includes the new fart reward.
        self.assertEqual(self.store.leaderboard()[0]['total'], 1)

    def test_async_message_can_keep_original_profile(self):
        first = self.store.profile()['id']
        second = self.store.add_profile('Other')['id']
        self.store.select_profile(second)
        self.store.message('assistant', 'Late response', 'local model', profile_id=first)
        self.assertEqual(self.store.messages(), [])
        self.assertEqual(self.store.messages(first)[-1]['text'], 'Late response')

    def test_wallet_retry_identity_and_conflicting_request_rejected(self):
        first = self.store.deposit(25, 'retry')
        self.assertEqual(first, self.store.deposit(25, 'retry'))
        self.assertEqual(self.store.balance(), 125)
        with self.assertRaises(ValueError):
            self.store.deposit(26, 'retry')
        with self.assertRaises(ValueError):
            self.store.purchase('tiny-hat', 'retry')
        self.assertEqual(self.store.balance(), 125)

    def test_spin_retry_does_not_reroll_or_recharge(self):
        with patch('chuddy.store.secrets.choice', side_effect=['clam', 'hat', 'butt']):
            result = self.store.spin(20, 'spin-one')
        self.assertEqual(result['payout'], 0)
        self.assertEqual(self.store.balance(), 80)
        self.assertEqual(self.store.spin(20, 'spin-one'), result)
        self.assertEqual(self.store.balance(), 80)

    def test_spin_checks_affordability_even_on_winning_result(self):
        with patch('chuddy.store.secrets.choice', return_value='clam'):
            with self.assertRaises(ValueError):
                self.store.spin(101, 'too-large')
        self.assertEqual(self.store.balance(), 100)

    def test_concurrent_purchases_cannot_overspend(self):
        def buy(index):
            try:
                self.store.purchase('tiny-hat', 'purchase-' + str(index))
                return True
            except ValueError:
                return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            bought = list(pool.map(buy, range(20)))
        self.assertEqual(sum(bought), 8)
        self.assertEqual(self.store.balance(), 4)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM ledger').fetchone()[0], 8)

    def test_two_connections_serialize_wallet_changes(self):
        other = Store(self.path)
        try:
            def deposit(index):
                target = self.store if index % 2 else other
                target.deposit(1, 'parallel-' + str(index))
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(deposit, range(30)))
            self.assertEqual(self.store.balance(), 130)
        finally:
            other.close()

    def test_wallet_numeric_validation(self):
        for amount in (0, -1, 0.5, True, float('inf'), float('nan'), '10', None, 10**1000):
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                self.store.deposit(amount, 'invalid')
        self.assertEqual(self.store.balance(), 100)

    def test_score_retries_are_idempotent_but_conflicts_are_visible(self):
        self.assertTrue(self.store.event('one', 'fart', 'manual'))
        self.assertFalse(self.store.event('one', 'fart', 'manual'))
        with self.assertRaises(ValueError):
            self.store.event('one', 'burp', 'manual')
        self.assertEqual(self.store.scores()['total'], 1)

    def test_deadline_import_validates_all_before_writing(self):
        with self.assertRaises(ValueError):
            self.store.import_deadlines([{'title': 'Good', 'due': '2026-11-05T12:00:00Z'}, {'title': 'Bad', 'due': '2026-11-05T12:00:00'}])
        self.assertEqual(self.store.deadlines(), [])

    def test_settings_atomic_on_serialization_or_name_collision(self):
        self.store.add_profile('Friend')
        with self.assertRaises(ValueError):
            self.store.update_settings({'cooldown': 17, 'name': 'Friend'})
        self.assertEqual(self.store.settings()['cooldown'], 90)
        with self.assertRaises(TypeError):
            self.store.update_settings({'cooldown': 17, 'bad': object()})
        self.assertEqual(self.store.settings()['cooldown'], 90)

    def test_memory_limits_updates_and_profile_owned_deletion(self):
        memory = self.store.remember('name', 'first')
        self.assertEqual(self.store.remember('name', 'second')['id'], memory['id'])
        for i in range(99):
            self.store.remember('key-' + str(i), 'value')
        with self.assertRaises(ValueError):
            self.store.remember('overflow', 'value')
        with self.assertRaises(ValueError):
            self.store.remember('too-long', 'x' * 301)
        second = self.store.add_profile('Other')
        self.store.select_profile(second['id'])
        self.assertFalse(self.store.forget(memory['id']))

    def test_sync_snapshot_excludes_private_context(self):
        self.store.message('user', 'private chat', 'you')
        self.store.add_deadline('private calendar', '2026-11-05T12:00:00Z')
        self.store.remember('private memory', 'value')
        self.store.event('sound', 'fart', 'manual')
        exported = self.store.events_for_sync()
        serialized = json.dumps(exported)
        self.assertEqual(set(exported), {'installation_id', 'profile', 'events'})
        self.assertNotIn('private', serialized)

    def test_legacy_schema_migrates_without_discarding_original_data(self):
        self.store.close()
        legacy = Path(self.temp.name) / 'legacy.sqlite3'
        db = sqlite3.connect(legacy)
        db.executescript('''
            CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            INSERT INTO settings VALUES('name','"Legacy friend"');
            CREATE TABLE messages(id INTEGER PRIMARY KEY,role TEXT,text TEXT,source TEXT,time TEXT);
            INSERT INTO messages VALUES(1,'user','Keep my conversation','you','2026-11-04T10:00:00Z');
            CREATE TABLE events(id TEXT PRIMARY KEY,kind TEXT,source TEXT,confidence REAL,time TEXT);
            INSERT INTO events VALUES('old-sound','burp','manual',NULL,'2026-11-04T10:00:00Z');
            CREATE TABLE wallet(id INTEGER PRIMARY KEY CHECK(id=1),balance INTEGER CHECK(balance>=0));
            INSERT INTO wallet VALUES(1,77);
        ''')
        db.close()
        self.store = Store(legacy)
        self.assertEqual(self.store.profile()['name'], 'Legacy friend')
        self.assertEqual(self.store.balance(), 77)
        self.assertEqual(self.store.messages()[0]['text'], 'Keep my conversation')
        self.assertEqual(self.store.scores()['burp'], 1)
        self.assertEqual(self.store.db.execute('PRAGMA user_version').fetchone()[0], 3)


if __name__ == '__main__':
    unittest.main()
