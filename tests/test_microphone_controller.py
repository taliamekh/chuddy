"""Speech admission/lifecycle with synthetic results; never opens a microphone."""
from unittest.mock import Mock
import threading
from tests.helpers import ControllerCase


class MicrophoneControllerTests(ControllerCase):
    def setUp(self):
        super().setUp()
        self.microphone = self.controller.microphone
        self.profile = self.store.profile()['id']

    def test_default_is_push_to_talk_and_startup_is_passive(self):
        self.assertEqual(self.store.settings()['microphone_mode'], 'push_to_talk')
        self.assertFalse(self.controller.state()['microphone']['active'])
        self.assertFalse(self.controller.audio.recording)

    def test_toggle_starts_and_stops_same_manager(self):
        self.microphone.start = Mock(return_value={'active':True})
        self.microphone.stop = Mock(return_value={'active':False})
        self.assertTrue(self.controller.action({'action':'microphone','operation':'start'})['active'])
        self.assertFalse(self.controller.action({'action':'microphone','operation':'stop'})['active'])
        self.microphone.start.assert_called_once_with()
        self.microphone.stop.assert_called_once_with()

    def test_ptt_finish_submits_only_current_profile_generation(self):
        self.microphone.finish = Mock(return_value={'text':'Do we have to work?','generation':7,'profile_id':self.profile})
        self.microphone.current = Mock(return_value=True)
        result = self.controller.action({'action':'microphone','operation':'finish'})
        self.assertIn('turn_id', result)
        self.assertEqual(self.controller.agent.calls, [('Do we have to work?',False)])
        self.microphone.current.assert_called_once_with(7,self.profile)

    def test_late_finished_recording_cannot_submit_after_cancellation(self):
        self.microphone.finish = Mock(return_value={'text':'Old recording','generation':7,'profile_id':self.profile})
        self.microphone.current = Mock(return_value=False)
        result = self.controller.action({'action':'microphone','operation':'finish'})
        self.assertTrue(result['cancelled'])
        self.assertEqual(result['text'], '')
        self.assertEqual(self.controller.agent.calls, [])

    def test_profile_switch_rejects_old_callback_even_if_worker_claims_current(self):
        old_profile = self.profile
        self.microphone.current = Mock(return_value=True)
        profile = self.store.add_profile('Another person')
        self.controller.action({'action':'profile_select','id':profile['id']})
        result = self.controller._microphone_transcript('Old profile speech',7,old_profile)
        self.assertTrue(result['cancelled'])
        self.assertEqual(self.controller.agent.calls, [])

    def test_mode_save_and_device_change_stop_without_opening_capture(self):
        self.microphone.stop = Mock(wraps=self.microphone.stop)
        self.microphone.start = Mock()
        self.controller.settings({'microphone_mode':'always_listening','audio_device':3})
        self.microphone.stop.assert_called_once_with()
        self.microphone.start.assert_not_called()
        self.assertEqual(self.store.settings()['microphone_mode'],'always_listening')
        self.assertEqual(self.store.settings()['audio_device'],3)
        with self.assertRaises(ValueError):
            self.controller.settings({'microphone_mode':'invalid'})

    def test_pause_and_resume_do_not_implicitly_reopen_microphone(self):
        self.microphone.stop = Mock(wraps=self.microphone.stop)
        self.microphone.start = Mock()
        self.controller.set_session('start')
        self.controller.set_session('pause')
        self.controller.set_session('resume')
        self.microphone.stop.assert_called_once_with()
        self.microphone.start.assert_not_called()

    def test_legacy_asr_start_uses_new_mode_aware_manager(self):
        self.microphone.start = Mock(return_value={'active':True})
        self.controller.action({'action':'asr_start'})
        self.microphone.start.assert_called_once_with()

    def test_pause_serializes_with_legacy_scoring_microphone_start(self):
        entered, release, paused = threading.Event(), threading.Event(), threading.Event()
        original_stop = self.microphone.stop
        first = [True]
        def stop():
            if first[0]:
                first[0] = False
                entered.set()
                release.wait(2)
            return original_stop()
        self.microphone.stop = stop
        start = threading.Thread(target=lambda:self.controller.action({'action':'mic','operation':'start'}))
        pause = threading.Thread(target=lambda:(self.controller.set_session('pause'),paused.set()))
        start.start()
        self.assertTrue(entered.wait(1))
        pause.start()
        try:
            self.assertFalse(paused.wait(.05))
        finally:
            release.set()
            start.join(2)
            pause.join(2)
        self.assertTrue(paused.is_set())
        self.assertFalse(self.controller.audio.recording)

    def use_fake_held_capture(self):
        audio = self.controller.audio
        audio.generation = 0
        audio.require_transcriber = Mock()

        def start():
            audio.generation += 1
            audio.start()

        def finish(*, expected_generation, cancel_event):
            self.assertEqual(expected_generation, audio.generation)
            self.assertFalse(cancel_event.is_set())
            audio.recording = False
            return {'text': 'one held message'}

        audio.start_conversation = Mock(side_effect=start)
        audio.finish_conversation = Mock(side_effect=finish)
        audio.cancel_push_to_talk = Mock(side_effect=audio.stop)
        return audio

    def held(self, operation, token='hold-one', generation=None):
        return self.controller.action({'action':'microphone', 'operation':'hold_'+operation, 'hold_id':token,
                                       'generation':self.microphone.generation if generation is None else generation,
                                       'profile_id':self.profile})

    def test_held_ptt_in_stopped_session_submits_once_on_release(self):
        audio = self.use_fake_held_capture()
        self.assertTrue(self.held('start')['active'])
        self.assertTrue(audio.recording)
        self.assertTrue(self.controller.action({'action':'microphone','operation':'finish'})['cancelled'])
        self.assertIn('turn_id', self.held('finish'))
        self.assertTrue(self.held('finish')['cancelled'])
        self.assertFalse(audio.recording)
        self.assertEqual(self.controller.agent.calls, [('one held message', False)])
        audio.finish_conversation.assert_called_once()

    def test_out_of_order_release_or_cancel_prevents_any_late_start(self):
        audio = self.use_fake_held_capture()
        for token, operation in [('early-release','finish'),('early-cancel','cancel')]:
            self.assertTrue(self.held(operation, token)['cancelled'])
            self.assertTrue(self.held('start', token)['cancelled'])
        audio.start_conversation.assert_not_called()
        audio.finish_conversation.assert_not_called()

    def test_late_actions_cannot_target_newer_manual_capture_after_pause(self):
        audio = self.use_fake_held_capture()
        expected = self.microphone.generation
        self.held('start')
        self.controller.set_session('pause')
        self.assertTrue(self.controller.action({'action':'microphone','operation':'start'})['active'])
        self.assertTrue(self.held('start','stale',expected)['cancelled'])
        self.assertTrue(self.held('cancel')['cancelled'])
        self.assertTrue(self.held('finish')['cancelled'])
        self.assertTrue(audio.recording)
        self.assertTrue(self.microphone.info()['active'])
        audio.finish_conversation.assert_not_called()

    def test_lease_expires_without_browser_and_cannot_be_renewed_late(self):
        audio = self.use_fake_held_capture()
        self.held('start')
        self.now += 2
        self.assertTrue(self.held('keepalive')['accepted'])
        held = self.controller._held_microphone
        self.now += 5
        self.controller._watch_microphone_hold(held)
        self.assertFalse(audio.recording)
        self.assertTrue(self.held('keepalive')['cancelled'])
        self.assertTrue(self.held('finish')['cancelled'])
        self.assertEqual(self.controller.agent.calls, [])

    def test_cancel_during_transcription_discards_the_late_result(self):
        audio = self.use_fake_held_capture()
        entered, release = threading.Event(), threading.Event()
        def finish(**kwargs):
            entered.set()
            release.wait(2)
            return {'text':'late words'}
        audio.finish_conversation.side_effect = finish
        self.held('start')
        results = []
        worker = threading.Thread(target=lambda:results.append(self.held('finish')))
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            self.held('cancel')
        finally:
            release.set()
            worker.join(2)
        self.assertEqual(results, [{'text':'', 'cancelled':True}])
        self.assertFalse(audio.recording)
        self.assertEqual(self.controller.agent.calls, [])
