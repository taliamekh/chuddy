import json
import time

from tests.helpers import ControllerCase


class RequestedUpdatesTests(ControllerCase):
    def test_admin_cheat_endpoint_credits_current_profile_and_retries_once(self):
        request = {'action': 'admin_cheat', 'command': 'monies 987654', 'request_id': 'admin-once', 'profile_id': self.store.profile()['id']}
        result = self.controller.action(request)
        self.assertEqual(result['balance'], 987754)
        self.assertEqual(self.controller.action(request), result)
        other = self.controller.action({'action': 'profile_add', 'name': 'Other'})
        self.controller.action({'action': 'profile_select', 'id': other['id']})
        with self.assertRaises(ValueError):
            self.controller.action({**request, 'request_id': 'late'})
        self.assertEqual(self.store.balance(), 100)

    def test_connected_deadlines_refresh_in_background_and_state_has_no_feed_secret(self):
        feed = b'BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:report-1\r\nSUMMARY:Report\r\nDTSTART:20261109T180000Z\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n'
        from datetime import datetime, timezone
        self.controller.deadline_sync.utcnow = lambda: datetime(2026, 11, 6, tzinfo=timezone.utc)
        self.controller.deadline_sync.fetcher = lambda provider, url, cancel: feed
        request = {'action': 'deadline_connect', 'provider': 'google', 'profile_id': self.store.profile()['id'],
                   'url': 'https://calendar.google.com/calendar/ical/test/private-secret/basic.ics'}
        self.controller.action(request)
        end = time.monotonic() + 3
        while not self.store.deadlines() and time.monotonic() < end:
            time.sleep(.01)
        state = self.controller.state()
        self.assertEqual(state['deadlines'][0]['title'], 'Report')
        self.assertIn('leaderboard_periods', state)
        self.assertNotIn('private-secret', json.dumps(state))
        self.assertEqual(state['deadline_sync']['sources'][0]['status'], 'ready')
        self.controller.action({'action': 'deadline_disconnect', 'provider': 'google', 'profile_id': request['profile_id']})
        self.assertEqual(self.store.deadlines(), [])

    def test_launch_is_passive_and_sessions_have_no_timeout(self):
        state = self.controller.state()
        self.assertEqual(state['session'], 'stopped')
        self.assertFalse(state['audio']['recording'])
        self.assertFalse(state['voice']['speaking'])
        self.assertEqual(state['settings']['distraction_destination'], 'casino')
        self.controller.set_session('start')
        self.now += 86400
        state = self.controller.state()
        self.assertEqual(state['session'], 'running')
        self.assertIsNone(state['session_remaining'])
        json.dumps(state, allow_nan=False)
