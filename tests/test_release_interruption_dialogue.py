"""The production controller publishes dialogue only for acknowledged fixture effects."""

from concurrent.futures import Future
import threading
from unittest.mock import patch

from chuddy.agent import LocalAgent
from chuddy.controller import WORK_SETTLE_SECONDS, windows
from chuddy.persona import CONVERSATION_MODEL
from chuddy.work_banter import BANTER_OUTCOME_SOURCE
from tests.helpers import ControllerCase
from tests.test_agent import ModelFixture, chunk


class InterruptionDialogueTests(ControllerCase):
    def setUp(self):
        with patch('tests.helpers.FakeAgent', LocalAgent):
            super().setUp()
        self.agent = self.controller.agent
        self.assertTrue(self.agent.managed_work_dialogue)
        self.manager = self.controller.distractions
        self.manager.delay = 0
        self.native = self.controller.native_browser_tabs
        self.native.available = True
        self.window = {'handle': 42, 'pid': 123, 'thread_id': 9, 'process': 'chrome.exe',
                       'title': 'Report - Google Chrome', 'is_transient': False}
        windows.foreground.return_value = self.window
        self.store.update_settings({'voice': True, 'ai_enabled': False, 'cooldown': 0})
        self.initial_messages = self.store.messages()
        self.controller.set_session('start')

    def start_close(self):
        self.controller.poll_work()
        self.now += WORK_SETTLE_SECONDS + 0.01
        self.controller.poll_work()
        self.assertTrue(self.native.requested.wait(2))
        self.assertEqual(self.controller.interruption['phase'], 'clicking')
        self.assertEqual(self.store.messages(), self.initial_messages)
        self.assertEqual(self.controller.voice.spoken, [])

    def ack_selected_tab(self):
        windows.foreground.return_value = {**self.window, 'title': 'Remaining tab - Google Chrome'}
        self.native.close_future.set_result(True)

    def assert_one_outcome(self, line="How about we gamble?"):
        self.assertTrue(self.manager.wait())
        result = self.controller.interruption
        self.assertEqual(result['phase'], 'opened')
        self.assertEqual(result['banter_source'], BANTER_OUTCOME_SOURCE)
        self.assertEqual(result['banter_message'], line)
        rows = self.store.messages()[len(self.initial_messages):]
        self.assertEqual([(row['source'], row['text']) for row in rows], [(BANTER_OUTCOME_SOURCE, line)])
        self.assertEqual(self.controller.voice.spoken, [(line, None)])
        return result

    def test_policy_opens_once_when_a_late_model_duplicate_arrives(self):
        entered, release = threading.Event(), threading.Event()
        tool = {'function': {'name': 'request_distraction',
                             'arguments': {'destination': 'casino', 'reason': 'Come sit here.'}}}
        fixture = ModelFixture([{
            'chunks': [chunk("Sorry, couldn't take a break. Work remains."), chunk(calls=[tool, tool], done=True)],
            'pause_after': 1, 'entered': entered, 'gate': release,
        }])
        self.addCleanup(fixture.close)
        self.addCleanup(release.set)
        self.store.update_settings({'ai_enabled': True, 'ai_model': CONVERSATION_MODEL, 'ai_endpoint': fixture.url})
        self.controller.poll_work()
        self.assertTrue(entered.wait(4))
        self.start_close()
        self.ack_selected_tab()
        result = self.assert_one_outcome()
        release.set()
        self.agent.worker.join(5)
        self.assertFalse(self.agent.worker.is_alive())
        trace = self.agent.info()['trace']
        self.assertEqual([row['result']['ok'] for row in trace], [True, False])
        self.assertTrue(trace[0]['result']['already_started'])
        self.assertEqual(self.assert_one_outcome()['id'], result['id'])
        self.assertEqual(self.covered, [True])
        self.assertEqual(len(self.native.targets), 1)
        self.assertEqual(self.controller.voice.fillers, [])

    def test_real_close_error_keeps_diagnostics_and_is_silent(self):
        self.start_close()
        self.native.close_future.set_exception(RuntimeError('Application kept the tab open.'))
        self.assertTrue(self.manager.wait())
        result = self.controller.interruption
        self.assertEqual(result['phase'], 'error')
        self.assertEqual(result['error'], 'RuntimeError')
        self.assertEqual(result['error_detail'], 'Application kept the tab open.')
        self.assertNotIn('banter_message', result)
        self.assertEqual(self.store.messages(), self.initial_messages)
        self.assertEqual(self.controller.voice.spoken, [])
        self.assertEqual(self.covered, [])
        self.assertIsNone(self.controller.state()['casino_opening_id'])

    def test_late_model_denial_does_not_change_an_opened_switch(self):
        entered, release = threading.Event(), threading.Event()
        wrong_destination = {'function': {'name': 'request_distraction',
                                          'arguments': {'destination': 'lcbo', 'reason': 'Come sit here.'}}}
        fixture = ModelFixture([{
            'chunks': [chunk('Work remains.'), chunk(calls=[wrong_destination], done=True)],
            'pause_after': 1, 'entered': entered, 'gate': release,
        }])
        self.addCleanup(fixture.close)
        self.addCleanup(release.set)
        self.store.update_settings({'ai_enabled': True, 'ai_model': CONVERSATION_MODEL, 'ai_endpoint': fixture.url})
        self.controller.poll_work()
        self.assertTrue(entered.wait(4))
        self.start_close()
        self.ack_selected_tab()
        self.assert_one_outcome()
        release.set()
        self.agent.worker.join(5)
        self.assertFalse(self.agent.worker.is_alive())
        self.assertEqual(self.agent.info()['action_outcome']['status'], 'denied')
        self.assert_one_outcome()
        self.assertEqual(self.covered, [True])

    def test_dialogue_failure_does_not_reclassify_the_switch(self):
        self.start_close()
        with (patch.object(self.manager, 'speak', side_effect=OSError('Speech fixture failed.')),
              patch.object(self.manager, 'on_dialogue_error') as log):
            self.ack_selected_tab()
            self.assertTrue(self.manager.wait())
            log.assert_called_once_with('OSError')
        result = self.controller.interruption
        self.assertEqual(result['phase'], 'opened')
        self.assertEqual(self.controller.state()['casino_opening_id'], result['id'])
        self.assertEqual(self.controller.voice.spoken, [])

    def test_fullscreen_denial_keeps_the_local_route_and_never_claims_success(self):
        handoff = Future()
        submitted = threading.Event()
        self.controller.cover = lambda: (submitted.set(), handoff)[1]
        self.start_close()
        self.ack_selected_tab()
        self.assertTrue(submitted.wait(2))
        result = self.controller.interruption
        self.assertEqual(result['phase'], 'opening')
        self.assertEqual(result['fullscreen'], 'pending')
        self.assertEqual(result['destination_delivery'], 'route_ready')
        self.assertEqual(self.controller.state()['casino_opening_id'], result['id'])
        self.assertFalse(self.controller.state()['casino_interruption_active'])
        self.assertEqual(self.store.messages(), self.initial_messages)
        self.assertEqual(self.controller.voice.spoken, [])
        handoff.set_exception(RuntimeError('Fullscreen handoff failed.'))
        self.assertTrue(self.manager.wait())
        result = self.controller.interruption
        self.assertEqual(result['phase'], 'error')
        self.assertEqual(result['target_close'], 'confirmed')
        self.assertEqual(result['fullscreen'], 'failed')
        self.assertEqual(result['error_detail'], 'Fullscreen handoff failed.')
        self.assertEqual(self.controller.state()['casino_opening_id'], result['id'])
        self.assertFalse(self.controller.state()['casino_interruption_active'])
        self.assertEqual(self.store.messages(), self.initial_messages)
        self.assertEqual(self.controller.voice.spoken, [])
        self.assertEqual(len(self.native.targets), 1)

    def test_back_during_fullscreen_ack_cancels_the_pending_cover(self):
        handoff = Future()
        submitted = threading.Event()
        self.controller.cover = lambda: (submitted.set(), handoff)[1]
        self.start_close()
        self.ack_selected_tab()
        self.assertTrue(submitted.wait(2))
        opening = self.controller.state()['casino_opening_id']
        self.assertTrue(self.controller.action({'action': 'casino_leave', 'opening_id': opening})['left'])
        handoff.set_result(True)
        self.assertTrue(self.manager.wait())
        self.assertIsNone(self.controller.interruption)
        self.assertEqual(self.controller.state()['casino_opening_id'], opening)
        self.assertEqual(self.controller.state()['session'], 'running')
        self.assertEqual(self.uncovered, [True])
        self.assertEqual(self.store.messages(), self.initial_messages)

    def assert_session_change_prevents_late_handoff_dialogue(self, operation):
        handoff = Future()
        submitted = threading.Event()

        def cover():
            submitted.set()
            return handoff

        self.controller.cover = cover
        self.start_close()
        self.ack_selected_tab()
        self.assertTrue(submitted.wait(2))
        self.controller.set_session(operation)
        handoff.set_result(True)
        self.assertTrue(self.manager.wait())
        self.assertIsNone(self.controller.interruption)
        self.assertIsNone(self.controller.state()['casino_opening_id'])
        self.assertEqual(self.store.messages(), self.initial_messages)
        self.assertEqual(self.controller.voice.spoken, [])

    def test_stop_prevents_late_handoff_dialogue(self):
        self.assert_session_change_prevents_late_handoff_dialogue('stop')

    def test_pause_prevents_late_handoff_dialogue(self):
        self.assert_session_change_prevents_late_handoff_dialogue('pause')

    def test_retailer_open_delivers_the_shop_invitation(self):
        self.store.setting('distraction_destination', 'lcbo')
        with patch.object(self.manager.launcher, 'open', return_value=None) as launch:
            self.start_close()
            self.ack_selected_tab()
            result = self.assert_one_outcome('Wanna look around?')
        self.assertEqual(result['target_close'], 'confirmed')
        launch.assert_called_once_with('lcbo')

    def test_ai_off_success_retains_the_opening_id_through_retirement(self):
        self.start_close()
        self.ack_selected_tab()
        opened = self.assert_one_outcome()['id']
        self.assertEqual(self.controller.state()['casino_opening_id'], opened)
        self.assertTrue(self.manager.retire_completed())
        self.assertIsNone(self.controller.interruption)
        self.assertEqual(self.controller.state()['casino_opening_id'], opened)
        self.controller.set_session('pause')
        self.assertIsNone(self.controller.state()['casino_opening_id'])

    def test_back_releases_only_the_acknowledged_casino_cover(self):
        self.start_close()
        self.ack_selected_tab()
        opened = self.assert_one_outcome()['id']
        self.assertFalse(self.controller.action({'action': 'casino_leave', 'opening_id': 'old'})['left'])
        self.assertEqual(self.uncovered, [])
        self.assertTrue(self.controller.action({'action': 'casino_leave', 'opening_id': opened})['left'])
        self.assertEqual(self.uncovered, [True])
        self.assertEqual(self.controller.state()['session'], 'running')
        self.assertEqual(self.controller.state()['casino_opening_id'], opened)
        self.assertFalse(self.controller.action({'action': 'casino_leave', 'opening_id': opened})['left'])
        self.assertEqual(self.uncovered, [True])

    def test_rehearsal_and_cancelled_preparation_are_silent(self):
        self.assertTrue(self.controller.interrupt('Rehearsal', test=True))
        self.assertTrue(self.manager.wait())
        self.assertEqual(self.controller.interruption['phase'], 'preview')
        self.assertTrue(self.manager.retire_completed())
        self.native.defer_prepare = True
        self.controller.poll_work()
        self.assertTrue(self.controller.interrupt('Report'))
        self.assertTrue(self.native.prepared.wait(2))
        self.controller.set_session('stop')
        self.assertTrue(self.manager.wait())
        self.assertEqual(self.store.messages(), self.initial_messages)
        self.assertEqual(self.controller.voice.spoken, [])
