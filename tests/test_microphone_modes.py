"""No real input devices/models: synthetic PCM, fake streams and fake ASR only."""
import json
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from chuddy.audio import AudioPipeline
from chuddy.microphone import ConversationMicrophone, SpeechSegmenter
from chuddy.store import Store


def wait_for(predicate, timeout=2):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if predicate():
            return
        time.sleep(.005)
    raise AssertionError('Timed out waiting for mocked microphone work.')


class FakeAudio:
    def __init__(self):
        self.generation, self.sink, self.recording, self.starts = 0, None, False, 0
        self.failure = ''
        self.transcribe_samples = Mock(return_value={'text': 'hello Chuddy', 'source': 'mock local ASR'})
        self.finish_conversation = Mock(return_value={'text': 'a voice message', 'source': 'mock local ASR'})

    def require_transcriber(self):
        pass

    def start_conversation(self):
        self.generation += 1
        self.failure = ''
        self.recording, self.starts = True, self.starts + 1

    def start_continuous_conversation(self, sink):
        self.sink = sink
        self.start_conversation()

    def stop(self):
        self.generation += 1
        self.sink, self.recording = None, False

    def info(self):
        return {'recording': self.recording, 'status': 'microphone error' if self.failure else 'listening' if self.recording else 'off', 'error': self.failure}


class MicrophoneModeTests(unittest.TestCase):
    def setUp(self):
        self.audio = FakeAudio()
        self.settings = {'microphone_mode': 'push_to_talk'}
        self.profile = {'id': 'first'}
        self.delivered, self.suppressed = [], False
        def delivered(*args):
            self.assertFalse(self.manager.lock._is_owned(), 'Callback ran under manager lock.')
            self.delivered.append(args)
        self.manager = ConversationMicrophone(self.audio, lambda: self.settings, lambda: self.profile, delivered,
                                              suppress=lambda: self.suppressed)

    def tearDown(self):
        self.manager.stop()

    def speech(self, sink=None):
        sink = sink or self.audio.sink
        for _ in range(4):
            sink(np.ones(1600, dtype=np.float32) * .1, 16000)
        for _ in range(8):
            sink(np.zeros(1600, dtype=np.float32), 16000)

    def test_defaults_constructor_and_preference_changes_are_passive(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'db.sqlite3')
            try:
                self.assertEqual(store.settings()['microphone_mode'], 'push_to_talk')
            finally:
                store.close()
        self.settings['microphone_mode'] = 'always_listening'
        self.assertEqual(self.manager.info()['mode'], 'always_listening')
        self.assertFalse(self.manager.info()['active'])
        self.assertEqual(self.audio.starts, 0)

    def test_ptt_click_start_finish_keeps_local_provenance_and_token(self):
        self.assertTrue(self.manager.start()['active'])
        result = self.manager.finish()
        self.assertEqual(result['text'], 'a voice message')
        self.assertEqual(result['source'], 'mock local ASR')
        self.assertTrue(self.manager.current(result['generation'], result['profile_id']))
        self.manager.stop()
        self.assertFalse(self.manager.current(result['generation'], result['profile_id']))
        self.assertEqual(self.delivered, [])

    def test_always_listening_segments_and_delivers_a_conversation(self):
        self.settings['microphone_mode'] = 'always_listening'
        self.manager.start()
        self.speech()
        wait_for(lambda: bool(self.delivered))
        text, generation, profile = self.delivered[0]
        self.assertEqual((text, profile), ('hello Chuddy', 'first'))
        self.assertTrue(self.manager.current(generation, profile))
        samples, rate, audio_generation = self.audio.transcribe_samples.call_args.args
        self.assertEqual(rate, 16000)
        self.assertGreaterEqual(len(samples), rate * .25)
        self.assertLessEqual(len(samples), rate * 12)
        self.assertEqual(audio_generation, self.audio.generation)
        self.assertTrue(self.manager.info()['active'])

    def test_silence_does_not_trigger_transcription(self):
        self.settings['microphone_mode'] = 'always_listening'
        self.manager.start()
        for _ in range(15):
            self.audio.sink(np.zeros(1600, dtype=np.float32), 16000)
        time.sleep(.05)
        self.audio.transcribe_samples.assert_not_called()

    def test_stop_and_profile_change_discard_inflight_transcripts(self):
        for change_profile in (False, True):
            entered, release = threading.Event(), threading.Event()
            def transcribe(*args):
                entered.set()
                release.wait(2)
                return {'text': 'late response'}
            self.audio.transcribe_samples.side_effect = transcribe
            self.settings['microphone_mode'] = 'always_listening'
            self.manager.start()
            self.speech()
            self.assertTrue(entered.wait(2))
            if change_profile:
                self.profile['id'] = 'second'
            else:
                self.manager.stop()
            release.set()
            time.sleep(.05)
            self.assertEqual(self.delivered, [])
            self.manager.stop()

    def test_suppression_discards_character_reply_audio_only(self):
        self.settings['microphone_mode'] = 'always_listening'
        self.suppressed = True
        self.manager.start()
        self.speech()
        time.sleep(.05)
        self.audio.transcribe_samples.assert_not_called()
        self.suppressed = False
        self.speech()
        wait_for(lambda: bool(self.delivered))
        self.assertEqual(len(self.delivered), 1)

    def test_missing_transcriber_never_opens_capture(self):
        self.audio.require_transcriber = Mock(side_effect=ValueError('not installed'))
        with self.assertRaisesRegex(ValueError, 'not installed'):
            self.manager.start()
        self.assertEqual(self.audio.starts, 0)
        self.assertFalse(self.manager.info()['active'])

    def test_failed_async_device_open_can_be_retried_from_plus(self):
        self.manager.start()
        token = self.manager.generation
        self.audio.recording, self.audio.failure = False, 'Mock device unavailable'
        self.assertFalse(self.manager.info()['active'])
        result = self.manager.start()
        self.assertTrue(result['active'])
        self.assertEqual(self.audio.starts, 2)
        self.assertFalse(self.manager.current(token, 'first'))
        self.assertFalse(result['error'])

    def test_new_ptt_start_cancels_previous_transcription_token(self):
        entered, release, result = threading.Event(), threading.Event(), []
        def finish():
            entered.set();release.wait(2)
            return {'text': 'old PTT result'}
        self.audio.finish_conversation.side_effect = finish
        self.manager.start()
        token = self.manager.generation
        worker = threading.Thread(target=lambda: result.append(self.manager.finish()))
        worker.start();self.assertTrue(entered.wait(1))
        self.manager.start()
        release.set();worker.join(1)
        self.assertEqual(result, [{'text': '', 'cancelled': True}])
        self.assertFalse(self.manager.current(token, 'first'))
        self.assertTrue(self.manager.info()['active'])

    def test_stopped_late_audio_callback_does_not_reopen_or_deliver(self):
        self.settings['microphone_mode'] = 'always_listening'
        self.manager.start()
        sink = self.audio.sink
        self.manager.stop()
        self.speech(sink)
        time.sleep(.05)
        self.audio.transcribe_samples.assert_not_called()
        self.assertEqual(self.audio.starts, 1)
        self.assertFalse(self.manager.info()['active'])

    def test_long_or_noisy_stream_is_bounded(self):
        segmenter = SpeechSegmenter()
        segment = None
        for _ in range(121):
            result = segmenter.feed(np.ones(1600, dtype=np.float32) * .1, 16000)
            if result is not None:
                segment = result
        self.assertIsNotNone(segment)
        self.assertLessEqual(len(segment[0]), 12 * 16000)


class AudioMicrophoneBridgeTests(unittest.TestCase):
    def test_ptt_shared_scoring_stream_captures_once_while_classifier_is_cold(self):
        gate, prepared = threading.Event(), threading.Event()
        store = Mock()
        store.settings.return_value = {'audio_device': None}
        store.profile.return_value = {'id': 'fixture'}
        streams = []
        class FakeStream:
            def __init__(self, **kwargs):
                streams.append(self);self.callback = kwargs['callback']
            def start(self):
                pass
            def abort(self):
                pass
            def close(self):
                pass
        def prepare():
            prepared.set();gate.wait(2)
            return False
        with tempfile.TemporaryDirectory() as folder, patch('sounddevice.query_devices', return_value={'default_samplerate': 16000}), patch('sounddevice.InputStream', FakeStream):
            audio = AudioPipeline(store, Path(folder) / 'models/yamnet')
            audio.prepare = Mock(side_effect=prepare)
            try:
                audio.start();self.assertTrue(prepared.wait(1))
                audio.start_conversation()
                self.assertFalse(audio.ptt_owned_stream, 'Must exercise an existing scoring stream.')
                samples = np.ones((8000, 1), dtype=np.float32) * .1
                streams[0].callback(samples, 8000, None, None)
                self.assertEqual(sum(map(len, audio.ptt_data)), 8000)
                gate.set();time.sleep(.05)
                self.assertEqual(sum(map(len, audio.ptt_data)), 8000, 'Run-loop processing duplicated PTT samples.')
                for _ in range(45):
                    streams[0].callback(samples, 8000, None, None)
                self.assertLessEqual(sum(map(len, audio.ptt_data)), 20 * 16000)
                generation = audio.generation
                audio.cancel_push_to_talk()
                self.assertEqual(audio.generation, generation)
                self.assertIs(audio.stream, streams[0])
                self.assertTrue(audio.info()['recording'])
                self.assertFalse(audio.ptt)
                streams[0].callback(samples, 8000, None, None)
                self.assertFalse(audio.ptt_data)
                audio.stop()
                streams[0].callback(samples, 8000, None, None)
                self.assertFalse(audio.ptt_data, 'Stopped callback appended late samples.')
            finally:
                audio.stop();gate.set()

    def test_stop_terminates_asr_discards_result_and_cleans_temporary_file(self):
        with tempfile.TemporaryDirectory() as folder:
            audio = AudioPipeline(Mock(), Path(folder) / 'models/yamnet')
            python, model = Path(folder) / 'python.exe', Path(folder) / 'model'
            python.touch(); model.mkdir()
            entered, released = threading.Event(), threading.Event()
            paths, results, processes = [], [], []
            class FakeProcess:
                returncode = 0
                dead = False
                def __init__(self, args, **kwargs):
                    processes.append(self);paths.append(Path(args[args.index('--input') + 1]))
                def communicate(self, timeout):
                    entered.set();released.wait(2)
                    return json.dumps({'text': 'too late'}), ''
                def poll(self):
                    return 0 if self.dead else None
            def kill(process):
                process.dead = True;released.set()
            with patch.object(audio, '_asr_paths', return_value=(python, model)), patch('chuddy.audio.subprocess.Popen', FakeProcess), patch('chuddy.audio.terminate_worker', side_effect=kill) as terminate:
                token = audio.generation
                worker = threading.Thread(target=lambda: results.append(audio.transcribe_samples(np.zeros(16000, dtype=np.float32), 16000, token)))
                worker.start()
                self.assertTrue(entered.wait(2))
                audio.stop();worker.join(2)
                self.assertFalse(worker.is_alive())
                terminate.assert_called_once_with(processes[0])
                self.assertEqual(results, [{'text': '', 'cancelled': True}])
                self.assertTrue(all(not path.exists() for path in paths))
                self.assertTrue(audio.transcribe_samples(np.zeros(16000), 16000, token)['cancelled'])
                self.assertEqual(len(processes), 1, 'Late ASR work reopened after Stop.')

    def test_cancelled_hold_cannot_consume_newer_samples_on_the_shared_stream(self):
        audio = AudioPipeline(Mock(), 'missing')
        old_hold = threading.Event()
        old_hold.set()
        audio.ptt = True
        audio.ptt_data.append(np.zeros(16000, dtype=np.float32))
        self.assertTrue(audio.finish_conversation(expected_generation=audio.generation, cancel_event=old_hold)['cancelled'])
        self.assertTrue(audio.ptt)
        self.assertEqual(len(audio.ptt_data), 1)

    def test_selected_device_tap_transcribes_while_classifier_is_cold(self):
        gate, delivered = threading.Event(), threading.Event()
        store = Mock()
        store.settings.return_value = {'audio_device': 7, 'microphone_mode': 'always_listening'}
        store.profile.return_value = {'id': 'fixture'}
        opened = []
        class FakeStream:
            def __init__(self, **kwargs):
                opened.append(kwargs)
                self.callback = kwargs['callback']
            def start(self):
                for level in [.1] * 4 + [0.] * 8:
                    self.callback(np.full((1600, 1), level, dtype=np.float32), 1600, None, None)
            def abort(self):
                pass
            def close(self):
                pass
        with tempfile.TemporaryDirectory() as folder, patch('sounddevice.query_devices', return_value={'default_samplerate': 16000}) as query, patch('sounddevice.InputStream', FakeStream):
            audio = AudioPipeline(store, Path(folder) / 'models/yamnet')
            audio.require_transcriber = Mock()
            audio.prepare = Mock(side_effect=lambda: gate.wait(2) and False)
            audio.transcribe_samples = Mock(return_value={'text': 'captured while cold'})
            manager = ConversationMicrophone(audio, store.settings, store.profile, lambda *args: delivered.set())
            try:
                manager.start()
                self.assertTrue(delivered.wait(1), 'Conversation waited on classifier startup.')
                query.assert_called_once_with(7, 'input')
                self.assertEqual(opened[0]['device'], 7)
                self.assertEqual(opened[0]['blocksize'], 1600)
            finally:
                manager.stop()
                gate.set()

    def test_temporary_pcm_wav_is_local_and_deleted_after_mock_transcription(self):
        with tempfile.TemporaryDirectory() as folder:
            audio = AudioPipeline(Mock(), Path(folder) / 'models/yamnet')
            python, model = Path(folder) / 'python.exe', Path(folder) / 'model'
            python.touch(); model.mkdir()
            paths = []
            class FakeProcess:
                returncode = 0
                def __init__(self, args, **kwargs):
                    path = Path(args[args.index('--input') + 1]); paths.append(path)
                    with wave.open(str(path), 'rb') as wav:
                        self.asserted = (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes())
                def communicate(self, timeout):
                    return json.dumps({'text': 'real file; fake ASR', 'source': 'mock local ASR'}), ''
                def poll(self):
                    return 0
            with patch.object(audio, '_asr_paths', return_value=(python, model)), patch('chuddy.audio.subprocess.Popen', FakeProcess):
                result = audio.transcribe_samples(np.zeros(16000, dtype=np.float32), 16000)
                self.assertEqual(result['text'], 'real file; fake ASR')
                self.assertTrue(paths)
                self.assertTrue(all(not path.exists() for path in paths))
                self.assertIsNone(audio.asr_process)


if __name__ == '__main__':
    unittest.main()
