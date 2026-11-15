from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from tests.helpers import ControllerCase


class EarningsIntegrationTests(ControllerCase):
    def test_scoring_exposes_earned_wallet_and_retry_does_not_pay_again(self):
        first = self.controller.action({'action': 'score', 'kind': 'fart', 'request_id': 'one'})
        self.assertTrue(first['counted'])
        self.assertEqual(first['earnings']['balance'], 105)
        retry = self.controller.action({'action': 'score', 'kind': 'fart', 'request_id': 'one'})
        self.assertFalse(retry['counted'])
        state = self.controller.state()
        self.assertEqual(state['earnings']['total_earned'], 5)
        self.assertEqual(state['balance'], 105)

    def start_round(self):
        profile = self.store.profile()['id']
        ticket = self.controller.action({'action': 'clam_game_start', 'profile_id': profile, 'request_id': 'start'})
        return {'action': 'clam_game_finish', 'round_id': ticket['round_id'],
                'profile_id': profile, 'request_id': 'finish', 'caught': 4}

    def test_game_earnings_require_full_duration_and_recover_exact_retry(self):
        start = datetime(2026, 11, 4, 8, tzinfo=timezone.utc)
        with patch('chuddy.store.now', return_value=start.isoformat()):
            finish = self.start_round()
            with self.assertRaisesRegex(ValueError, '20-second'):
                self.controller.action(finish)
        with patch('chuddy.store.now', return_value=(start + timedelta(seconds=21)).isoformat()):
            result = self.controller.action(finish)
            self.assertEqual(result['reward'], 10)
            self.assertEqual(self.controller.action(finish), result)
        self.assertEqual(self.store.balance(), 110)

    def test_stop_and_pause_cancel_server_tickets(self):
        for operation in ('stop', 'pause'):
            finish = self.start_round()
            self.controller.set_session(operation)
            self.assertEqual(self.store.db.execute('SELECT status FROM clam_rounds WHERE id=?',
                                                  (finish['round_id'],)).fetchone()[0], 'cancelled')
            with self.assertRaises(ValueError):
                self.controller.action(finish)
            # New ticket ID for the second operation, rather than replay a cancelled start.
            self.store.db.execute("DELETE FROM requests WHERE operation='clam_game_start'")
        self.assertEqual(self.store.balance(), 100)

    def test_stale_profile_game_request_cannot_start_or_credit_other_wallet(self):
        finish = self.start_round()
        other = self.store.add_profile('Other')
        self.controller.action({'action': 'profile_select', 'id': other['id']})
        with self.assertRaisesRegex(ValueError, 'profile changed'):
            self.controller.action(finish)
        self.assertEqual(self.store.balance(), 100)
        with self.assertRaisesRegex(ValueError, 'profile changed'):
            self.controller.action({'action': 'clam_game_start', 'request_id': 'late',
                                    'profile_id': finish['profile_id']})
