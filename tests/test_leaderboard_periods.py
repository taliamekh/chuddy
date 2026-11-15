import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from chuddy.store import Store
from chuddy.leaderboard import period_bounds
from friends.service import FriendsStore


class PeriodTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'local.sqlite3')

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_toronto_day_week_and_dst_use_calendar_boundaries(self):
        at = datetime.fromisoformat('2026-11-06T15:00:00+00:00')
        self.assertEqual(period_bounds('today', at)[0], '2026-11-06T05:00:00+00:00')
        self.assertEqual(period_bounds('week', at)[0], '2026-11-02T05:00:00+00:00')
        # DST switches during this day; midnight was still EDT.
        dst = datetime.fromisoformat('2026-11-01T18:00:00+00:00')
        self.assertEqual(period_bounds('today', dst)[0], '2026-11-01T04:00:00+00:00')

    def test_periods_include_boundary_exclude_old_and_future_and_keep_zero_profiles(self):
        profile = self.store.profile()['id']
        self.store.add_profile('Quiet friend')
        for event_id, stamp in [('old', '2026-11-02T04:59:59Z'), ('week', '2026-11-02T05:00:00Z'),
                                ('yesterday', '2026-11-06T04:59:59Z'), ('today', '2026-11-06T00:00:00-05:00'),
                                ('future', '2026-11-07T12:00:00Z')]:
            self.store.event(event_id, 'fart', 'manual')
            self.store.db.execute('UPDATE events SET time=? WHERE id=?', (stamp, event_id))
        result = self.store.leaderboard_periods(datetime.fromisoformat('2026-11-06T15:00:00+00:00'))
        self.assertEqual([result[p][0]['total'] for p in ('today', 'week', 'all_time')], [1, 3, 4])
        self.assertTrue(all(result[p][0]['id'] == profile and result[p][1]['total'] == 0 for p in ('today', 'week', 'all_time')))

    def test_admin_money_is_exact_retry_safe_and_profile_scoped(self):
        first = self.store.profile()['id']
        result = self.store.admin_money('monies 234567', 'cheat-once', first)
        self.assertEqual(result['balance'], 234667)
        self.assertEqual(self.store.admin_money('monies 234567', 'cheat-once', first), result)
        self.assertEqual(self.store.balance(), 234667)
        other = self.store.add_profile('Other')['id']
        self.store.select_profile(other)
        with self.assertRaises(ValueError):
            self.store.admin_money('monies 9', 'late-request', first)
        self.assertEqual(self.store.balance(), 100)
        for value in ['monies -2', 'monies 2.5', 'monies 0', 'monies 2; exit', 'monies 1000000000001']:
            with self.assertRaises(ValueError):
                self.store.admin_money(value, 'bad', other)

    def test_active_period_ranks_and_history_use_farts_with_old_scores_intact(self):
        profile = self.store.profile()['id']
        other = self.store.add_profile('Friend')['id']
        self.store.event('first-fart', 'fart', 'manual')
        self.store.db.execute("UPDATE events SET time='2026-11-06T06:00:00Z' WHERE id='first-fart'")
        with self.store.transaction():
            self.store.db.executemany('INSERT INTO events(id,kind,source,confidence,time,profile_id) VALUES(?,?,?,?,?,?)',
                                      [(f'old-{i}', 'burp', 'manual', None, '2026-11-06T07:00:00Z', pid)
                                       for i, pid in enumerate([profile] * 12 + [other] * 14)])
        scores = self.store.scores()
        self.assertEqual((scores['fart'], scores['burp'], scores['total']), (1, 12, 13))
        self.assertEqual({row['kind'] for row in scores['recent']}, {'burp'})
        self.assertEqual([row['id'] for row in scores['recent_farts']], ['first-fart'])
        result = self.store.leaderboard_periods(datetime.fromisoformat('2026-11-06T15:00:00+00:00'))
        for period in ('today', 'week', 'all_time'):
            self.assertEqual([(row['id'], row['fart'], row['total']) for row in result[period]],
                             [(profile, 1, 13), (other, 0, 14)])

    def test_friends_uses_same_periods_and_no_future_points(self):
        shared = FriendsStore(Path(self.temp.name) / 'friends.sqlite3')
        try:
            shared.create_room('club')
            self.store.event('score', 'fart', 'manual')
            self.store.db.execute("UPDATE events SET time='2026-11-06T05:00:00Z'")
            sync = shared.sync('club', self.store.events_for_sync())
            self.assertIn('leaderboard_periods', sync)
            at = datetime.fromisoformat('2026-11-06T15:00:00+00:00')
            self.assertEqual(shared.leaderboard('club', 'today', at)[0]['total'], 1)
            self.assertEqual(shared.leaderboard('club', 'today', datetime.fromisoformat('2026-11-07T15:00:00+00:00'))[0]['total'], 0)
        finally:
            shared.close()
