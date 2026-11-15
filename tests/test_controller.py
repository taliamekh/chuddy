import json
import threading
import time
from unittest.mock import patch

from tests.helpers import ControllerCase


class ControllerTests(ControllerCase):
    def test_model_settings_cancel_old_inference_without_starting_a_new_turn(self):
        self.controller.agent.begin('A pending old-model turn')
        previous = list(self.controller.agent.calls)
        self.controller.settings({'ai_model': 'different-downloaded-model'})
        self.assertTrue(self.controller.agent.cancel_event.is_set())
        self.assertEqual(self.controller.agent.calls, previous)
        self.assertEqual(self.controller.agent.info()['status'], 'idle')
        self.assertEqual(self.controller.voice.spoken, [])

    def test_startup_has_no_disruptive_effects(self):
        state = self.controller.state()
        self.assertEqual(state['session'], 'stopped')
        self.assertFalse(state['audio']['recording'])
        self.assertEqual(state['hardware']['spray'], 'disabled')
        self.assertEqual(self.controller.voice.spoken, [])
        self.assertEqual(self.covered, [])

    def test_cooldown_pause_stop_and_explicit_expiry_guard(self):
        self.assertFalse(self.controller.interrupt('Report'))
        self.controller.set_session('start')
        self.assertTrue(self.controller.interrupt('Report', test=True))
        self.assertFalse(self.controller.interrupt('Report again'))
        self.controller.set_session('pause')
        self.now += 100
        self.assertFalse(self.controller.interrupt('While paused'))
        self.controller.set_session('resume')
        self.controller.expires = self.now + 1800  # Diagnostic finite lease; normal sessions are untimed.
        self.assertTrue(self.controller.interrupt('Resumed', test=True))
        self.now += 1801
        self.controller.check_expiry()
        self.assertEqual(self.controller.session, 'stopped')
        self.assertIsNone(self.controller.interruption)

    def test_interruption_settings_accept_zero_and_reject_negative_cooldown(self):
        self.controller.settings({'cooldown': 0})
        self.assertEqual(self.controller.state()['settings']['cooldown'], 0)
        with self.assertRaises(ValueError):
            self.controller.settings({'cooldown': -1})
        self.assertEqual(self.store.settings()['cooldown'], 0)

    def test_stop_cancels_capture_and_simulator_after_one_window_close(self):
        self.store.setting('interception', 'minimize')
        self.controller.set_session('start')
        self.controller.audio.start()
        window = {'handle': 45, 'pid': 123, 'process': 'WINWORD.EXE', 'title': 'Report'}
        self.patches[-1].target.foreground.return_value = window
        self.controller.observe_work('Word', window)
        self.controller.distractions.delay = 0
        self.assertTrue(self.controller.interrupt('Word', window))
        self.assertTrue(self.controller.distractions.wait())
        self.controller.set_session('stop')
        self.assertFalse(self.controller.audio.recording)
        self.assertTrue(self.controller.agent.cancel_event.is_set())
        self.assertEqual(self.controller.simulator.stops, 1)
        self.assertEqual(self.controller.minimized, set())
        self.patches[-1].target.request_close.assert_called_once_with(window)
        self.patches[-1].target.restore.assert_not_called()

    def test_stale_and_cancelled_tools_cannot_mutate_state(self):
        self.controller.agent.begin('remember clams')
        turn = self.controller.agent.info()['turn_id']
        self.controller.set_session('stop')
        result = self.tool('remember_preference', {'key': 'snack', 'value': 'clams'}, 'remember clams')
        self.assertFalse(result['ok'])
        self.assertEqual(self.store.memories(), [])
        self.controller.agent.begin('other turn')
        result = self.controller.tool('set_character_clock', {'label': 'yesterday'}, threading.Event(), turn, 'hello', False)
        self.assertFalse(result['ok'])
        self.assertEqual(self.controller.character_clock, 'snack thirty')

    def test_memory_requires_explicit_request_and_rejects_sensitive_content(self):
        self.controller.agent.begin('hello')
        self.assertFalse(self.tool('remember_preference', {'key': 'snack', 'value': 'clams'})['ok'])
        self.assertFalse(self.tool('remember_preference', {'key': 'password', 'value': 'fictional'}, 'remember this')['ok'])
        self.assertTrue(self.tool('remember_preference', {'key': 'snack', 'value': 'clams'}, 'remember my favourite snack')['ok'])
        self.assertEqual(self.store.memories()[0]['value'], 'clams')

    def test_negative_and_meta_memory_requests_do_not_authorize_writes(self):
        self.controller.agent.begin('Memory question')
        for text in ('Do not remember my snack preference', 'What do you remember?', 'I remember liking clams'):
            with self.subTest(text=text):
                result = self.tool('remember_preference', {'key': 'snack', 'value': 'clams'}, text)
                self.assertFalse(result['ok'])
        self.assertEqual(self.store.memories(), [])

    def test_fictional_clock_never_changes_real_deadlines(self):
        self.store.add_deadline('Report', '2026-11-05T10:00:00Z')
        original = self.store.deadlines()
        self.controller.agent.begin('what time')
        self.tool('set_character_clock', {'label': 'snack thirty tomorrow'})
        self.assertEqual(self.store.deadlines(), original)
        self.assertEqual(self.controller.state()['character_clock'], 'snack thirty tomorrow')

    def test_settings_validate_all_fields_before_mutating(self):
        with self.assertRaises(ValueError):
            self.controller.settings({'cooldown': 19, 'voice': 'yes'})
        self.assertEqual(self.store.settings()['cooldown'], 90)
        for values in ({'ai_endpoint': 'https://example.com'}, {'ai_model': 'qwen3:cloud'}, {'arbitrary': True}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.controller.settings(values)

    def test_cloud_model_filter_is_case_insensitive(self):
        with self.assertRaises(ValueError):
            self.controller.settings({'ai_model': 'qwen3:4b-CLOUD'})

    def test_huge_integer_settings_are_validation_errors(self):
        for setting in ('cooldown', 'audio_threshold', 'audio_device'):
            with self.subTest(setting=setting), self.assertRaises(ValueError):
                self.controller.settings({setting: 10**1000})

    def test_browser_reports_strip_queries_and_require_fresh_focus(self):
        report = {'url': 'https://docs.google.com/document/123?secret=private#private', 'active': True, 'focused': True, 'sent_at': time.time() * 1000}
        self.controller.set_session('start')
        result = self.controller.browser_event(report)
        self.assertTrue(result['matched'])
        self.assertEqual(self.controller.browser['host'], 'docs.google.com')
        self.assertNotIn('private', json.dumps(self.controller.state()))
        with self.assertRaises(ValueError):
            self.controller.browser_event({**report, 'sent_at': time.time() * 1000 - 9000})
        self.controller.browser_event({'active': False, 'focused': False})
        self.assertIsNone(self.controller.browser)

    def test_browser_focus_loss_invalidates_pending_observation(self):
        self.controller.set_session('start')
        self.controller.browser_event({'url': 'https://docs.google.com/document/123', 'active': True, 'focused': True, 'sent_at': time.time() * 1000})
        self.controller.browser_event({'active': False, 'focused': False})
        result = self.tool('request_distraction', {'game': 'clam-catch', 'reason': 'work'}, observation=True)
        self.assertFalse(result['ok'])
        self.assertIsNone(self.controller.interruption)

    def test_expired_work_observation_cannot_interrupt(self):
        self.controller.set_session('start')
        self.controller.observe_work('Word')
        self.now += 16
        result = self.tool('request_distraction', {'game': 'clam-catch', 'reason': 'work'}, observation=True)
        self.assertFalse(result['ok'])
        self.assertIsNone(self.controller.interruption)

    def test_pairing_secret_is_only_in_explicit_pairing_response(self):
        state = json.dumps(self.controller.state())
        self.assertNotIn(self.controller.browser_token, state)
        self.assertEqual(self.controller.action({'action': 'pairing'})['token'], self.controller.browser_token)

    def test_profile_switch_stops_session_and_preserves_profile_ownership(self):
        self.controller.set_session('start')
        self.store.event('first', 'fart', 'manual')
        other = self.store.add_profile('Other')
        self.controller.action({'action': 'profile_select', 'id': other['id']})
        self.assertEqual(self.controller.session, 'stopped')
        self.assertEqual(self.store.scores()['total'], 0)
