"""Controller + real choreography, using fake windows/browser/audio/devices only."""
import json
import threading
import time
from unittest.mock import Mock, patch

from chuddy.destinations import LEAGUE_DESTINATION
from chuddy.interception import DESTINATIONS, RETAIL_HOME
from tests.helpers import ControllerCase, FakeAgent


class FakePreservingWindows:
    def __init__(self, trace):
        self.trace = trace
        self.current = {'handle': 42, 'pid': 123, 'process': 'WINWORD.EXE', 'title': 'Unsaved report - Word'}
        self.saved = {}
        self.minimized = []
        self.restored = []
        self.fail_restore = False
        self.closed = []

    def foreground(self):
        return dict(self.current) if self.current else None

    def identity(self, handle):
        return self.saved.get(handle, {}).get('pid', 123)

    def request_close(self, window):
        self.closed.append(window['handle'])
        self.trace.append(('close', window['handle']))

    def close_status(self, window):
        self.current = None
        return 'closed'

    def minimize_work(self, window):
        self.saved[window['handle']] = dict(window)
        self.minimized.append(window['handle'])
        self.current = None
        self.trace.append(('minimize', window['handle']))

    def minimize(self, handle):
        self.minimize_work({'handle': handle, 'pid': 123})

    def restore(self, handle):
        self.restored.append(handle)
        self.trace.append(('restore', handle))
        if self.fail_restore:
            raise RuntimeError('Fake restore failure')
        self.current = self.saved.pop(handle, None)

    def discard(self, handle):
        self.saved.pop(handle, None)


class FakeChromeLauncher:
    def __init__(self, trace):
        self.trace = trace
        self.opened = []

    def open(self, destination):
        if destination not in RETAIL_HOME:
            raise ValueError('Not an allowlisted retailer.')
        self.opened.append(destination)
        self.trace.append(('chrome', destination))
        return RETAIL_HOME[destination]


class FakeOverlay:
    def __init__(self, trace):
        self.trace = trace
        self.performed = threading.Event()
        self.warned = threading.Event()
        self.on_warning = None
        self.on_perform = None
        self.status = 'unstarted'
        self.disarms = 0
        self.closed = False

    def roam(self, *args, **kwargs):
        self.status = 'ready'
        self.trace.append(('roam', None))

    def perform(self, window, destination):
        self.trace.append(('perform', destination))
        self.status = 'ready'
        self.performed.set()
        if self.on_perform:
            self.on_perform()

    def warning(self, window, destination, message):
        self.trace.append(('warning', message))
        self.warned.set()
        if self.on_warning:
            self.on_warning()

    def hide(self):
        self.trace.append(('hide', None))

    def disarm(self):
        self.disarms += 1
        self.trace.append(('disarm', None))

    def close(self):
        self.closed = True
        self.trace.append(('overlay_close', None))

    def info(self):
        return {'status': self.status, 'hotkey': 'fake · no registration', 'error': None}


class CancellableFakeAgent(FakeAgent):
    def __init__(self, voice):
        super().__init__()
        self.voice = voice

    def cancel(self):
        super().cancel()
        self.voice.stop()


class InterceptionIntegrationTests(ControllerCase):
    def setUp(self):
        super().setUp()
        self.trace = []
        self.windows = FakePreservingWindows(self.trace)
        self.chrome = FakeChromeLauncher(self.trace)
        self.overlay = FakeOverlay(self.trace)
        self.manager = self.controller.distractions
        self.manager.windows = self.windows
        self.manager.launcher = self.chrome
        self.league = self.manager.league_launcher = Mock()
        league_opening = self.league.open.return_value
        league_opening.reused = False

        def confirm_league(live, _stop, lock, _exposed, report, *, input_at_close=None):
            with lock:
                self.assertTrue(live())
                report('league_client', True, True, 'resize')
            self.trace.append(('league', LEAGUE_DESTINATION))
            return dict(handle=20, pid=22, thread_id=24)

        league_opening.confirm.side_effect = confirm_league
        self.manager.overlay = self.overlay
        self.manager.delay = 0
        self.manager.warning_seconds = 0
        self.controller.desktop_overlay = self.overlay
        self.controller.agent = CancellableFakeAgent(self.controller.voice)
        self.controller.cover = lambda: self.trace.append(('cover', None))
        self.controller.uncover = lambda: self.trace.append(('uncover', None))
        self.controller._pi_stop = Mock()  # Never issue a real remote command.
        self.pi_stop = Mock(wraps=self.controller.pi_bridge.stop)
        self.controller.pi_bridge.stop = self.pi_stop
        self.foreground = patch('chuddy.controller.windows.foreground', side_effect=self.windows.foreground)
        self.foreground.start()
        self.controller.voice.say = lambda text, **kwargs: self.trace.append(('speech', text))

    def tearDown(self):
        self.controller.close()
        self.assertTrue(self.manager.wait(timeout=2), 'A fake interruption worker did not stop.')
        self.foreground.stop()
        super().tearDown()

    def configure(self, destination='game', mode='cover'):
        self.controller.settings({'distraction_destination': destination, 'interception': mode})

    def observe(self, label='Microsoft Word'):
        return self.controller.observe_work(label, self.windows.foreground())

    def interrupt_observed(self, destination=None):
        self.assertTrue(self.observe())
        options = {} if destination is None else {'destination': destination}
        return self.controller.interrupt('Microsoft Word', **options)

    def finish(self):
        self.assertTrue(self.manager.wait(timeout=2))
        return self.controller.state()['interruption']

    def destinations_opened(self):
        return [value for action, value in self.trace if action in ('chrome', 'local')]

    def browser_report(self, path, **extra):
        self.windows.current = {'handle': 42, 'pid': 123, 'process': 'chrome.exe', 'title': 'Google Docs'}
        return self.controller.browser_event({'url': 'https://docs.google.com/document/' + path,
                                             'active': True, 'focused': True, 'sent_at': time.time() * 1000,
                                             'request_id': path, 'tab_id': 31, 'window_id': 7,
                                             'close_version': 2, 'address_id': '2'*64,
                                             'client_id': 'fake-paired-browser', **extra})

    def test_constructor_and_stopped_session_have_no_desktop_effects(self):
        self.assertEqual(self.controller.session, 'stopped')
        self.assertIsNone(self.manager.info())
        self.assertFalse(self.observe())
        self.assertFalse(self.controller.interrupt('No session', test=True))
        self.assertEqual(self.trace, [])
        self.assertEqual(self.chrome.opened, [])
        self.assertFalse(self.controller.audio.recording)

    def test_default_destination_is_local_and_invalid_settings_are_atomic(self):
        self.assertEqual(self.store.settings()['distraction_destination'], 'casino')
        for destination in ('https://example.com', 'bank', 'javascript:alert(1)', ''):
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                self.controller.settings({'cooldown': 17, 'distraction_destination': destination})
        self.assertEqual(self.store.settings()['cooldown'], 90)
        self.assertEqual(self.chrome.opened, [])

    def test_each_configured_destination_performs_before_opening(self):
        for destination in DESTINATIONS:
            with self.subTest(destination=destination):
                self.configure(destination)
                self.controller.set_session('start')
                self.windows.current = {'handle': 42, 'pid': 123, 'process': 'WINWORD.EXE', 'title': 'Report'}
                self.trace.clear()
                self.assertTrue(self.interrupt_observed())
                state = self.finish()
                self.assertEqual(state['phase'], 'opened')
                self.assertEqual(state['destination'], destination)
                self.assertEqual(self.trace[0], ('perform', destination))
                if destination in RETAIL_HOME:
                    self.assertEqual(self.chrome.opened[-1], destination)
                    self.assertNotIn(('cover', None), self.trace)
                elif destination == LEAGUE_DESTINATION:
                    self.league.open.assert_called_once_with(self.league.prepare.return_value)
                    self.assertIn(('league', destination), self.trace)
                    self.assertNotIn(('cover', None), self.trace)
                else:
                    self.assertIn(('cover', None), self.trace)
                self.assertEqual(state['target_close'], 'confirmed')
                self.controller.set_session('stop')
                self.now += 90

    def test_no_fresh_captured_work_cannot_launch_even_from_chat_tool(self):
        self.configure('lcbo')
        self.controller.set_session('start')
        self.controller.agent.begin('Please distract me')
        result = self.tool('request_distraction', {'destination': 'lcbo', 'reason': 'break'}, 'Please distract me')
        self.assertFalse(result['ok'])
        self.assertFalse(self.controller.interrupt('Unobserved work', destination='lcbo'))
        self.assertIsNone(self.manager.info())
        self.assertEqual(self.chrome.opened, [])

    def test_agent_can_only_request_configured_destination_and_legacy_game_maps_to_it(self):
        self.configure('casino')
        self.controller.set_session('start')
        self.assertTrue(self.observe())
        try:
            denied = self.tool('request_distraction', {'destination': 'lcbo', 'reason': 'break'}, observation=True)
        except ValueError:
            denied = {'ok': False}
        self.assertFalse(denied['ok'])
        self.assertEqual(self.chrome.opened, [])
        accepted = self.tool('request_distraction', {'game': 'clam-catch', 'reason': 'break'}, observation=True)
        self.assertTrue(accepted['ok'])
        self.assertEqual(self.finish()['destination'], 'casino')

    def test_focus_pid_and_observation_age_are_rechecked_before_effects(self):
        self.configure('lcbo', 'minimize')
        self.controller.set_session('start')
        for alteration in ('pid', 'window', 'expired'):
            with self.subTest(alteration=alteration):
                self.windows.current = {'handle': 42, 'pid': 123, 'process': 'WINWORD.EXE', 'title': 'Report'}
                self.controller.pending_work = None
                self.controller.agent.cancel()
                self.assertTrue(self.observe())
                if alteration == 'pid':
                    self.windows.current['pid'] = 999
                elif alteration == 'window':
                    self.windows.current['handle'] = 44
                else:
                    self.now += 9
                self.assertFalse(self.controller.interrupt('Report', destination='lcbo'))
                self.assertEqual(self.windows.minimized, [])
                self.assertEqual(self.chrome.opened, [])

    def test_approaching_phase_precedes_launch_and_current_info_updates(self):
        self.configure('lcbo')
        self.controller.set_session('start')
        self.manager.delay = .12
        self.assertTrue(self.interrupt_observed())
        self.assertTrue(self.overlay.performed.wait(timeout=1))
        approaching = self.controller.state()['interruption']
        self.assertEqual(approaching['phase'], 'approaching')
        self.assertEqual(self.chrome.opened, [])
        self.assertEqual(self.finish()['phase'], 'opened')
        self.assertEqual(self.chrome.opened, ['lcbo'])

    def test_cooldown_survives_dismiss_and_start_resume(self):
        self.controller.set_session('start')
        self.assertTrue(self.interrupt_observed())
        self.finish()
        self.controller.dismiss()
        self.controller.set_session('pause')
        self.controller.set_session('resume')
        self.assertFalse(self.observe())
        self.assertFalse(self.controller.interrupt('Preview during cooldown', test=True))
        self.now += 90
        self.assertTrue(self.observe())

    def test_same_host_document_change_invalidates_delayed_browser_work(self):
        self.configure('lcbo')
        self.controller.set_session('start')
        self.manager.delay = .12
        self.assertTrue(self.browser_report('private-first-document')['matched'])
        work = dict(self.controller.pending_work)
        self.assertTrue(self.controller.interrupt('Google Docs', destination='lcbo'))
        self.assertTrue(self.overlay.performed.wait(timeout=1))
        self.browser_report('private-second-document')
        self.assertFalse(self.controller._work_still_active(work))
        self.finish()
        self.assertIsNone(self.manager.info())
        self.assertEqual(self.chrome.opened, [])
        serialized = json.dumps(self.controller.state())
        self.assertNotIn('private-first-document', serialized)
        self.assertNotIn('private-second-document', serialized)

    def test_browser_focus_loss_cancels_delayed_launch(self):
        self.configure('beer_store')
        self.controller.set_session('start')
        self.manager.delay = .12
        self.browser_report('report')
        self.assertTrue(self.controller.interrupt('Docs', destination='beer_store'))
        self.assertTrue(self.overlay.performed.wait(timeout=1))
        self.controller.browser_event({'active': False, 'focused': False})
        self.finish()
        self.assertEqual(self.chrome.opened, [])
        self.assertIsNone(self.manager.info())

    def test_cancel_turn_propagates_its_actual_event_to_delayed_performance(self):
        self.configure('lcbo')
        self.controller.set_session('start')
        self.manager.delay = .12
        self.assertTrue(self.observe())
        accepted = self.tool('request_distraction', {'destination': 'lcbo', 'reason': 'break'}, observation=True)
        self.assertTrue(accepted['ok'])
        self.assertTrue(self.overlay.performed.wait(timeout=1))
        self.controller.action({'action': 'cancel_turn'})
        self.finish()
        self.assertEqual(self.chrome.opened, [])
        self.assertIsNone(self.manager.info())

    def test_destination_change_invalidates_previous_delayed_external_launch(self):
        self.configure('lcbo')
        self.controller.set_session('start')
        self.manager.delay = .12
        self.assertTrue(self.interrupt_observed())
        self.assertTrue(self.overlay.performed.wait(timeout=1))
        self.controller.settings({'distraction_destination': 'game'})
        self.finish()
        self.assertEqual(self.chrome.opened, [])
        self.assertIsNone(self.manager.info())

    def test_stop_pause_dismiss_and_profile_switch_invalidate_late_callbacks(self):
        other = self.store.add_profile('Friend')
        for operation in ('stop', 'pause', 'dismiss', 'profile'):
            with self.subTest(operation=operation):
                self.configure('lcbo', 'minimize')
                self.controller.set_session('start')
                self.now += 90
                self.windows.current = {'handle': 42, 'pid': 123, 'process': 'WINWORD.EXE', 'title': 'Report'}
                self.controller.pending_work = None
                self.controller.agent.cancel()
                self.overlay.performed.clear()
                if operation == 'dismiss':
                    callback = self.controller.dismiss
                elif operation == 'profile':
                    callback = lambda: self.controller.action({'action': 'profile_select', 'id': other['id']})
                else:
                    callback = lambda operation=operation: self.controller.set_session(operation)
                self.overlay.on_perform = callback
                self.assertTrue(self.interrupt_observed())
                self.finish()
                self.assertIsNone(self.manager.info())
                self.assertEqual(self.chrome.opened, [])
                self.assertEqual(self.windows.minimized, [])
                self.assertTrue(self.controller.agent.cancel_event.is_set())
                self.overlay.on_perform = None

    def test_stop_retains_audio_pi_simulator_cleanup_and_ticket_cancellation(self):
        self.controller.set_session('start')
        self.controller.audio.start()
        ticket = self.store.start_game('pending-arcade')
        self.controller.set_session('stop')
        self.assertFalse(self.controller.audio.recording)
        self.assertGreater(self.controller.simulator.stops, 0)
        self.pi_stop.assert_called()
        self.controller._pi_stop.assert_called()
        self.assertTrue(self.controller.agent.cancel_event.is_set())
        self.assertEqual(self.store.start_game('pending-arcade')['status'], 'cancelled')

    def test_restore_failures_remain_visible_and_next_stop_retries(self):
        # The standalone legacy preserving adapter remains reversible.
        self.manager.close_desktop = False
        self.configure('game', 'minimize')
        self.controller.set_session('start')
        self.assertTrue(self.interrupt_observed())
        self.finish()
        self.assertEqual(self.windows.minimized, [42])
        self.windows.fail_restore = True
        self.controller.set_session('stop')
        diagnostics = self.controller.state()['desktop_interception']
        self.assertEqual(diagnostics['pending_restorations'], 1)
        self.assertTrue(diagnostics['restoration_errors'])
        self.windows.fail_restore = False
        self.controller.set_session('stop')
        self.assertEqual(self.controller.state()['desktop_interception']['pending_restorations'], 0)
        self.assertEqual(self.windows.restored, [42, 42])
        self.assertEqual(self.controller.minimized, set())

    def test_preview_is_local_silent_and_does_not_query_model_or_minimize(self):
        self.configure('lcbo', 'minimize')
        self.store.setting('voice', True)
        self.controller.set_session('start')
        initial_messages = self.store.messages()
        result = self.controller.action({'action': 'test_trigger'})
        self.assertTrue(result['triggered'])
        preview = self.finish()
        self.assertEqual(preview['phase'], 'preview')
        self.assertTrue(preview['test'])
        self.assertEqual(self.chrome.opened, [])
        self.assertEqual(self.windows.minimized, [])
        self.assertEqual(self.controller.agent.calls, [])
        self.assertFalse(any(action == 'speech' for action, _ in self.trace))
        self.assertEqual(self.store.messages(), initial_messages)

    def test_close_cancels_worker_and_closes_overlay_without_launch(self):
        self.configure('beer_store')
        self.controller.set_session('start')
        self.overlay.on_perform = self.controller.close
        self.assertTrue(self.interrupt_observed())
        self.finish()
        self.assertFalse(self.controller.alive)
        self.assertTrue(self.overlay.closed)
        self.assertEqual(self.chrome.opened, [])

    def begin_warning(self, destination='game', mode='minimize'):
        self.configure(destination, mode)
        self.controller.set_session('start')
        self.manager.warning_seconds = .16
        self.trace.clear()
        self.assertTrue(self.interrupt_observed())
        self.assertTrue(self.overlay.warned.wait(1))
        state = self.controller.state()['interruption']
        self.assertEqual(state['phase'], 'warning')
        return state

    def test_stationary_warning_is_visible_in_state_before_closing(self):
        warning = self.begin_warning()
        self.assertIn('Microsoft Word', warning['warning_message'])
        self.assertGreater(warning['warning_remaining'], 0)
        self.assertEqual(self.trace[0], ('warning', warning['warning_message']))
        self.assertFalse(self.overlay.performed.is_set())
        self.assertEqual(self.windows.minimized, [])
        self.assertFalse(any(action == 'cover' for action, _ in self.trace))
        messages = self.store.messages()
        self.assertFalse(any(message['source'] in ('offline work warning', 'scripted work outcome')
                             for message in messages))
        opened = self.finish()
        self.assertEqual(opened['phase'], 'opened')
        self.assertEqual(self.store.messages()[-1]['text'], opened['banter_message'])
        self.assertEqual(self.store.messages()[-1]['source'], 'scripted work outcome')
        self.assertEqual(self.windows.closed, [42])
        actions = [action for action, _ in self.trace]
        self.assertLess(actions.index('warning'), actions.index('perform'))
        self.assertLess(actions.index('perform'), actions.index('close'))
        self.assertLess(actions.index('close'), actions.index('cover'))
        self.controller.dismiss()
        self.assertEqual(self.windows.restored, [])

    def test_continued_work_reobserves_identity_after_original_observation_ttl(self):
        self.begin_warning('lcbo')
        self.now += 12  # The original observation is old; the same live work remains.
        self.assertEqual(self.finish()['phase'], 'opened')
        self.assertEqual(self.chrome.opened, ['lcbo'])
        self.assertEqual(self.windows.closed, [42])

    def test_switch_away_during_warning_cancels_before_approach_or_minimize(self):
        self.begin_warning('lcbo')
        original = dict(self.windows.current)
        self.windows.current = {'handle': 77, 'pid': 999, 'title': 'Unrelated app'}
        self.finish()
        self.windows.current = original
        self.assertIsNone(self.manager.info())
        self.assertFalse(self.overlay.performed.is_set())
        self.assertEqual(self.chrome.opened, [])
        self.assertEqual(self.windows.minimized, [])
        self.assertIn(('roam', None), self.trace)

    def test_browser_document_change_during_warning_cancels_before_approach(self):
        self.configure('lcbo', 'minimize')
        self.controller.set_session('start')
        self.manager.warning_seconds = .16
        self.assertTrue(self.browser_report('private-original', tab_id=1)['matched'])
        self.assertTrue(self.controller.interrupt('Google Docs'))
        self.assertTrue(self.overlay.warned.wait(1))
        self.browser_report('private-next', tab_id=2)
        self.finish()
        self.assertIsNone(self.manager.info())
        self.assertFalse(self.overlay.performed.is_set())
        self.assertEqual(self.chrome.opened, [])
        self.assertEqual(self.windows.minimized, [])

    def test_stop_pause_dismiss_settings_and_profile_cancel_visible_warning(self):
        other = self.store.add_profile('Warning cancellation')
        for operation in ('stop', 'pause', 'dismiss', 'settings', 'profile'):
            with self.subTest(operation=operation):
                self.now += 90
                self.windows.current = {'handle': 42, 'pid': 123, 'process': 'WINWORD.EXE',
                                        'title': 'Unsaved report - Word'}
                self.overlay.warned.clear()
                self.overlay.performed.clear()
                self.begin_warning('lcbo')
                if operation == 'dismiss':
                    self.controller.dismiss()
                elif operation == 'settings':
                    self.controller.settings({'distraction_destination': 'game'})
                elif operation == 'profile':
                    self.controller.action({'action': 'profile_select', 'id': other['id']})
                else:
                    self.controller.set_session(operation)
                self.finish()
                self.assertIsNone(self.manager.info())
                self.assertFalse(self.overlay.performed.is_set())
                self.assertEqual(self.chrome.opened, [])
                self.assertEqual(self.windows.minimized, [])
