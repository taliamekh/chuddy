"""Tab command orchestration with fake browser/window/audio adapters only."""
from concurrent.futures import Future
import json
import os
import threading
import time
import unittest
from unittest.mock import Mock

from chuddy.browser_tabs import BrowserTabCommands
from tests.helpers import ControllerCase


class BrowserCommandTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.
        self.valid = self.live = True
        self.commands = BrowserTabCommands(threading.RLock(), wall_clock=lambda: self.now)
        self.target = {'tab_id': 31, 'client_id': 'browser-one', 'url': 'https://docs.google.com/document/report'}
        self.future = self.commands.request(self.target, lambda: self.valid, lambda: self.live)

    def test_instance_scope_claim_and_acknowledgement(self):
        self.assertIsNone(self.commands.offer('other-browser'))
        command = self.commands.offer('browser-one')
        self.assertIsNone(self.commands.claim('wrong-id', 'browser-one')['command'])
        grant = self.commands.claim(command['id'], 'browser-one')['command']
        self.assertTrue(grant['execute'])
        self.assertLess(grant['expires_at'], command['expires_at'])
        self.assertIsNone(self.commands.claim(command['id'], 'browser-one')['command'])
        self.assertFalse(self.future.done())
        self.assertTrue(self.commands.complete(command['id'], 'browser-one', True)['accepted'])
        self.assertTrue(self.future.result())
        self.assertTrue(self.commands.complete(command['id'], 'browser-one', True)['accepted'])

    def test_close_confirmation_is_distinct_from_short_execution_permission(self):
        command = self.commands.offer('browser-one')
        self.now += 3.9  # Claim just before the original offer expires.
        grant = self.commands.claim(command['id'], 'browser-one')['command']
        self.now += 2.1  # Closure began promptly, confirmation came later.
        self.assertGreater(self.now * 1000, grant['expires_at'])
        self.assertIsNone(self.commands.offer('browser-one'))
        self.assertTrue(self.commands.complete(command['id'], 'browser-one', True)['accepted'])
        self.assertTrue(self.future.result())

    def test_focus_change_cancellation_and_expiry_deny_claim(self):
        command = self.commands.offer('browser-one')
        self.valid = False
        self.assertIsNone(self.commands.claim(command['id'], 'browser-one')['command'])
        self.assertFalse(self.future.result())
        self.valid = True
        other = self.commands.request(self.target, lambda: True, lambda: self.live)
        self.now += 9
        self.assertIsNone(self.commands.offer('browser-one'))
        self.assertFalse(other.result())

    def test_stop_after_claim_invalidates_late_ack(self):
        command = self.commands.offer('browser-one')
        self.commands.claim(command['id'], 'browser-one')
        self.live = False
        self.assertFalse(self.commands.complete(command['id'], 'browser-one', True)['accepted'])
        self.assertFalse(self.future.result())

    def test_failed_close_never_claims_success(self):
        command = self.commands.offer('browser-one')
        self.commands.complete(command['id'], 'browser-one', False)
        with self.assertRaisesRegex(RuntimeError, 'did not confirm'):
            self.future.result()


class BrowserTabControllerTests(ControllerCase):
    def setUp(self):
        super().setUp()
        self.window = {'handle': 42, 'pid': 123, 'process': 'chrome.exe', 'title': 'Google Docs'}
        from chuddy.controller import windows
        windows.foreground.return_value = self.window
        self.window_api = windows
        self.controller.distractions.delay = 0
        self.store.setting('distraction_destination', 'casino')
        self.controller.set_session('start')

    def report(self, **extra):
        return self.controller.browser_event({'url': 'https://docs.google.com/document/report?secret=hidden',
            'active': True, 'focused': True, 'sent_at': time.time()*1000,
            'tab_id': 31, 'window_id': 9, 'close_version': 2, 'address_id': '1'*64,
            'client_id': 'browser-one', **extra})

    def wait_for_command(self):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            command = self.report().get('command')
            if command:
                return command
            time.sleep(.01)
        self.fail('No fake browser command was queued.')

    def test_close_ack_precedes_casino_and_browser_window_is_never_minimized(self):
        self.report()
        self.assertTrue(self.controller.interrupt('Google Docs'))
        command = self.wait_for_command()
        self.assertEqual(command['url'], 'https://docs.google.com/document/report')
        self.assertEqual(command['tab_id'], 31)
        self.assertEqual(self.covered, [])
        grant = self.controller.browser_event({'command_claim': command['id'], 'client_id': 'browser-one'})
        self.assertTrue(grant['command']['execute'])
        self.window_api.foreground.return_value = {**self.window, 'title': 'Unrelated tab'}
        result = self.controller.browser_event({'command_result': command['id'], 'client_id': 'browser-one', 'closed': True})
        self.assertTrue(result['accepted'])
        self.assertTrue(self.controller.distractions.wait())
        self.assertEqual(self.controller.interruption['phase'], 'opened')
        self.assertEqual(self.controller.interruption['destination'], 'casino')
        self.assertEqual(self.covered, [True])
        self.window_api.minimize.assert_not_called()

    def test_zero_cooldown_cannot_close_exposed_sibling_until_later_focus(self):
        self.store.setting('cooldown', 0)
        fullscreen = Future()
        self.controller.cover = Mock(return_value=fullscreen)
        self.report()
        turn_id = self.controller.pending_work['turn_id']
        self.assertTrue(self.controller.interrupt('Google Docs'))
        ticket = self.controller.interruption['id']
        command = self.wait_for_command()
        self.assertTrue(self.controller.browser_event({'command_claim': command['id'],
            'client_id': 'browser-one'})['command']['execute'])
        # A sibling with the same title and URL can be observed before the
        # client's acknowledgement. Its different tab ID never becomes the old target.
        self.assertIsNone(self.report(tab_id=32).get('command'))
        self.controller.poll_work()
        self.assertEqual(self.controller.interruption['id'], ticket)
        self.assertTrue(self.controller.browser_event({'command_result': command['id'],
            'client_id': 'browser-one', 'closed': True})['accepted'])

        deadline = time.monotonic() + 1
        while not self.controller.cover.called and time.monotonic() < deadline:
            time.sleep(.005)
        self.controller.cover.assert_called_once_with()
        self.assertEqual(self.controller.interruption['target_close'], 'confirmed')
        self.assertNotEqual(self.controller.interruption['phase'], 'opened')
        fullscreen.set_result(True)
        self.assertTrue(self.controller.distractions.wait())
        for _ in range(5):
            self.now += 1
            self.controller.poll_work()
            self.assertIsNone(self.report(tab_id=32).get('command'))
        self.assertEqual(self.controller.interruption['id'], ticket)
        self.assertIsNone(self.controller.pending_work)

        self.window_api.foreground.return_value = {'handle': 19, 'pid': os.getpid(),
            'process': 'chuddy.exe', 'title': 'Chuddy'}
        self.controller.poll_work()
        self.window_api.foreground.return_value = self.window
        self.assertTrue(self.report(tab_id=32)['interrupted'])
        self.assertIsNone(self.controller.interruption)
        self.assertEqual(self.controller.state()['casino_interruption_id'], ticket)
        late = self.controller.tool('request_distraction', {}, self.controller.agent.cancel_event,
                                    turn_id, 'work', True)
        self.assertTrue(late['already_started'])
        self.assertEqual((late['phase'], late['target_close']), ('opened', 'confirmed'))
        self.assertEqual(self.controller.pending_work['origin'], 'browser')
        self.controller.cover.assert_called_once_with()

    def test_paired_report_during_native_preparation_cancels_native_without_dispatch(self):
        native = self.controller.native_browser_tabs
        native.available, native.defer_prepare = True, True
        self.controller.poll_work()
        self.assertTrue(self.controller.interrupt('Google Docs'))
        self.assertTrue(native.prepared.wait(1))
        self.report()
        native.prepare_future.set_result({'kind': 'native', 'window': self.window})
        self.assertTrue(self.controller.distractions.wait())
        self.assertEqual(native.targets, [])
        self.assertIsNone(self.controller.interruption)
        self.assertEqual(self.covered, [])
        self.report()
        self.assertTrue(self.controller.interrupt('Google Docs'))
        self.assertEqual(self.wait_for_command()['tab_id'], 31)
        self.assertEqual(native.targets, [])

    def test_missing_updated_pairing_fails_without_touching_browser_window(self):
        self.report(client_id=None)
        overlay = Mock()
        self.controller.distractions.overlay = overlay
        self.assertFalse(self.controller.interrupt('Google Docs'))
        self.assertTrue(self.controller.distractions.wait())
        self.assertIsNone(self.controller.interruption)
        self.assertIsNotNone(self.controller.pending_work)
        self.assertEqual(self.controller.work_detection['status'], 'browser_unavailable')
        self.assertIn('Native browser controls', self.controller.work_detection['reason'])
        self.assertFalse(self.controller.state()['browser_tab_control']['ready'])
        overlay.perform.assert_not_called()
        self.assertEqual(self.covered, [])
        self.window_api.minimize.assert_not_called()

    def test_watcher_first_observation_is_superseded_by_fresh_paired_identity(self):
        self.window['title'] = 'Assignment - Google Docs - Google Chrome'
        self.controller.poll_work()
        self.assertEqual(self.controller.pending_work['origin'], 'desktop')
        old_turn = self.controller.pending_work['turn_id']
        self.report()
        self.assertEqual(self.controller.pending_work['origin'], 'browser')
        self.assertNotEqual(self.controller.pending_work['turn_id'], old_turn)
        self.assertTrue(self.controller.interrupt('Google Docs'))
        command = self.wait_for_command()
        self.assertEqual(command['tab_id'], 31)
        self.assertTrue(self.controller.browser_event({'command_claim': command['id'], 'client_id': 'browser-one'})['command']['execute'])
        self.controller.browser_event({'command_result': command['id'], 'client_id': 'browser-one', 'closed': True})
        self.assertTrue(self.controller.distractions.wait())
        self.assertEqual(self.controller.interruption['phase'], 'opened')
        self.assertEqual(self.controller.interruption['destination'], 'casino')
        self.window_api.minimize.assert_not_called()

    def test_attempt_before_first_report_never_approaches_and_can_retry_after_report(self):
        self.window['title'] = 'Assignment - Google Docs - Google Chrome'
        self.controller.poll_work()
        overlay = Mock()
        self.controller.distractions.overlay = overlay
        self.assertFalse(self.controller.interrupt('Google Docs'))
        overlay.perform.assert_not_called()
        self.assertIsNone(self.controller.interruption)
        self.report()
        self.assertTrue(self.controller.interrupt('Google Docs'))
        command = self.wait_for_command()
        self.assertEqual(command['tab_id'], 31)
        self.controller.set_session('stop')
        self.assertTrue(self.controller.distractions.wait())

    def test_stale_or_different_window_report_cannot_adopt_unrelated_tab(self):
        self.window['title'] = 'Assignment - Google Docs - Google Chrome'
        self.controller.poll_work()
        original = dict(self.controller.pending_work)
        self.report()
        self.controller.browser['seen'] = self.now - 9
        self.assertFalse(self.controller.interrupt('Google Docs'))
        self.controller.browser['seen'] = self.now
        for key, value in [('title', 'Different document'), ('pid', 999), ('handle', 100)]:
            old_value = self.controller.browser_window[key]
            self.controller.browser_window[key] = value
            self.assertFalse(self.controller.interrupt('Google Docs'))
            self.controller.browser_window[key] = old_value
        self.assertEqual(self.controller.pending_work['window'], original['window'])
        self.assertIsNone(self.controller.interruption)
        self.window_api.minimize.assert_not_called()

    def test_readiness_is_false_after_focus_moves_to_another_app(self):
        self.report()
        self.assertTrue(self.controller.state()['browser_tab_control']['ready'])
        self.window_api.foreground.return_value = {'handle':55,'pid':500,'process':'Codex.exe','title':'Codex'}
        readiness = self.controller.state()['browser_tab_control']
        self.assertFalse(readiness['ready'])
        self.assertNotIn('https://', str(readiness))
        self.assertNotIn('31', str(readiness))

    def test_missing_extension_keeps_one_work_turn_without_repeated_approach_or_voice(self):
        self.window['title'] = 'Assignment - Google Docs - Google Chrome'
        overlay = Mock()
        self.controller.distractions.overlay = overlay
        self.controller.poll_work()
        initial_calls = list(self.controller.agent.calls)
        initial_messages = self.store.messages()
        for _ in range(40):
            self.now += .5
            self.controller.poll_work()
        self.assertEqual(self.controller.agent.calls, initial_calls)
        self.assertEqual(len(initial_calls), 1)
        self.assertEqual(self.store.messages(), initial_messages)
        self.assertEqual(self.controller.voice.spoken, [])
        self.assertIsNotNone(self.controller.pending_work)
        self.assertIsNone(self.controller.interruption)
        overlay.perform.assert_not_called()

    def test_stop_cancels_an_offered_command_and_no_casino_opens(self):
        self.report()
        self.controller.interrupt('Google Docs')
        command = self.wait_for_command()
        self.controller.set_session('stop')
        result = self.controller.browser_event({'command_claim': command['id'], 'client_id': 'browser-one'})
        self.assertIsNone(result['command'])
        self.assertTrue(self.controller.distractions.wait())
        self.assertIsNone(self.controller.interruption)
        self.assertEqual(self.covered, [])

    def test_session_is_untimed_and_has_no_warning_countdown(self):
        self.now += 100000
        self.controller.check_expiry()
        state = self.controller.state()
        self.assertEqual(state['session'], 'running')
        self.assertIsNone(state['session_remaining'])
        self.assertEqual(self.controller.distractions.warning_seconds, 0)
        json.dumps(state, allow_nan=False)
