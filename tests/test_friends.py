import copy
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from unittest.mock import patch

from chuddy.friends import FriendsClient, service_url
from chuddy.store import Store
from friends.service import FriendsStore, make_server


class FriendsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.shared = FriendsStore(self.path / 'shared.sqlite3')
        self.token = self.shared.create_room('friends')
        self.other_token = self.shared.create_room('other')
        self.server = make_server('127.0.0.1', 0, self.shared)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        self.first = Store(self.path / 'first.sqlite3')
        self.second = Store(self.path / 'second.sqlite3')
        self.second.setting('name', 'Friend')

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.shared.close()
        self.first.close()
        self.second.close()
        self.temp.cleanup()

    def test_two_clients_retry_safe_shared_leaderboard(self):
        self.first.event('same-id-on-different-device', 'fart', 'simulator sound test')
        self.second.event('same-id-on-different-device', 'fart', 'manual')
        client = FriendsClient(self.first)
        self.assertEqual(client.sync(self.url, 'friends', self.token)['synced'], 1)
        self.assertEqual(client.sync(self.url, 'friends', self.token)['synced'], 0)
        result = FriendsClient(self.second).sync(self.url, 'friends', self.token)
        self.assertEqual(sum(row['total'] for row in result['leaderboard']), 2)
        self.assertEqual({r['name'] for r in result['leaderboard']}, {'You', 'Friend'})

    def test_room_isolation_and_wrong_token(self):
        self.first.event('sound', 'fart', 'manual')
        FriendsClient(self.first).sync(self.url, 'friends', self.token)
        result = FriendsClient(self.second).sync(self.url, 'other', self.other_token)
        self.assertEqual(sum(r['total'] for r in result['leaderboard']), 0)
        with self.assertRaises(ValueError):
            FriendsClient(self.second).sync(self.url, 'friends', self.other_token)
        self.assertEqual(len(self.shared.leaderboard('friends')), 1)

    def test_profile_identity_survives_client_restart(self):
        self.first.event('once', 'fart', 'manual')
        before = FriendsClient(self.first).sync(self.url, 'friends', self.token)
        self.first.close()
        self.first = Store(self.path / 'first.sqlite3')
        after = FriendsClient(self.first).sync(self.url, 'friends', self.token)
        self.assertEqual(after['synced'], 0)
        self.assertEqual(after['leaderboard'], before['leaderboard'])

    def test_service_storage_retains_tokens_and_scores(self):
        self.first.event('one', 'fart', 'manual')
        FriendsClient(self.first).sync(self.url, 'friends', self.token)
        reopened = FriendsStore(self.path / 'shared.sqlite3')
        try:
            self.assertTrue(reopened.authorized('friends', self.token))
            self.assertEqual(reopened.leaderboard('friends')[0]['total'], 1)
            self.assertFalse(reopened.authorized('other', self.token))
        finally:
            reopened.close()

    def test_invalid_batch_and_conflicting_events_roll_back(self):
        self.first.event('once', 'fart', 'manual')
        snapshot = self.first.events_for_sync()
        self.shared.sync('friends', snapshot)
        changed = copy.deepcopy(snapshot)
        changed['events'].insert(0, {**changed['events'][0], 'id': 'would-be-new'})
        changed['events'][1]['kind'] = 'burp'
        with self.assertRaises(ValueError):
            self.shared.sync('friends', changed)
        self.assertEqual(self.shared.leaderboard('friends')[0]['total'], 1)

    def test_service_requires_authentication_and_bounds_body(self):
        request = urllib.request.Request(self.url + '/v1/rooms/friends/sync', b'{}', {'Content-Type': 'application/json'})
        with self.assertRaises(urllib.error.HTTPError) as rejected:
            urllib.request.urlopen(request)
        self.assertEqual(rejected.exception.code, 401)
        request = urllib.request.Request(self.url + '/v1/rooms/friends/sync', b'x' * (128 * 1024 + 1), {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.token})
        with self.assertRaises(urllib.error.HTTPError) as rejected:
            urllib.request.urlopen(request)
        self.assertEqual(rejected.exception.code, 413)

    def test_sync_batches_larger_histories(self):
        for index in range(205):
            self.first.event('event-' + str(index), 'fart', 'manual')
        result = FriendsClient(self.first).sync(self.url, 'friends', self.token)
        self.assertEqual(result['synced'], 205)
        self.assertEqual(result['leaderboard'][0]['total'], 205)

    def test_no_private_fields_uploaded(self):
        self.first.message('user', 'private conversation', 'you')
        self.first.remember('private context', 'private value')
        self.first.add_deadline('private task', '2026-11-05T10:00:00Z')
        result = FriendsClient(self.first).sync(self.url, 'friends', self.token)
        self.assertNotIn('private', json.dumps(result['leaderboard']))
        self.assertEqual(result['leaderboard'][0]['total'], 0)

    def test_existing_shared_burps_are_readable_but_new_sync_and_ranks_are_fart_only(self):
        legacy = self.first.events_for_sync()
        profile = legacy['profile']['id']
        identity = legacy['installation_id']
        stamp = '2026-11-04T12:00:00+00:00'
        self.shared.sync('friends', legacy)
        with self.shared.db:
            self.shared.db.executemany('INSERT INTO events VALUES(?,?,?,?,?,?,?,?)',
                                       [('friends', identity, profile, f'old-{i}', 'burp', 'manual', None, stamp)
                                        for i in range(3)])
        self.first.db.execute('INSERT INTO events(id,kind,source,confidence,time,profile_id) VALUES(?,?,?,?,?,?)',
                              ('local-old', 'burp', 'manual', None, stamp, profile))
        with patch.object(self.shared, 'sync', wraps=self.shared.sync) as received:
            self.assertEqual(FriendsClient(self.first).sync(self.url, 'friends', self.token)['synced'], 0)
        self.assertEqual(received.call_args.args[1]['events'], [])
        historical = self.first.events_for_sync()
        self.assertEqual(historical['events'][0]['id'], 'local-old')
        self.assertEqual(self.shared.sync('friends', historical)['inserted'], 0)
        self.second.event('fart', 'fart', 'manual')
        rows = FriendsClient(self.second).sync(self.url, 'friends', self.token)['leaderboard']
        self.assertEqual([(row['fart'], row['burp'], row['total']) for row in rows], [(1, 0, 1), (0, 3, 3)])

    def test_client_rejects_insecure_public_endpoint(self):
        for url in ('http://example.com', 'ftp://localhost', 'https://user:password@example.com', 'http://localhost/#token'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                service_url(url)
        self.assertEqual(service_url('http://192.168.1.2:8766/'), 'http://192.168.1.2:8766')


if __name__ == '__main__':
    unittest.main()
