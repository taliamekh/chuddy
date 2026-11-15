"""PTT with fake PortAudio callbacks. No input devices are opened."""
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import sounddevice as sd

from chuddy.audio import AudioPipeline
from chuddy.microphone import ConversationMicrophone


class FakeInput:
    def __init__(self, **options):
        self.options = options
        self.callback = options['callback']
        self.closed = threading.Event()
        self.starts = 0

    def start(self):
        self.starts += 1

    def abort(self):
        pass

    def close(self):
        self.closed.set()

    def feed(self, data):
        self.callback(data, len(data), None, None)


class PTTCaptureTests(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        store = Mock()
        store.settings.return_value = {'audio_device': 17, 'microphone_mode': 'push_to_talk'}
        store.profile.return_value = {'id': 'ptt-profile'}
        self.audio = AudioPipeline(store, Path(folder) / 'models/yamnet')
        self.audio.require_transcriber = Mock()
        self.audio.transcribe_samples = Mock(return_value={'text': 'fixture'})
        self.audio.prepare = Mock()
        self.mic = ConversationMicrophone(self.audio, store.settings, store.profile, Mock())
        self.input = None
        self.enterContext(patch('sounddevice.query_devices', return_value={
            'name': 'Fixture microphone array', 'default_samplerate': 48000, 'max_input_channels': 2,
        }))
        self.factory = self.enterContext(patch('sounddevice.InputStream', side_effect=self.create_input))
        self.addCleanup(self.audio.stop)

    def create_input(self, **options):
        self.input = FakeInput(**options)
        return self.input

    def wait_for(self, test):
        deadline = time.monotonic() + 1
        while not test() and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertTrue(test())

    def test_short_host_block_sets_readiness_and_keeps_the_live_channel(self):
        self.mic.start()
        self.wait_for(lambda: self.audio.info()['recording'])
        self.assertTrue(self.mic.info()['active'])
        self.assertFalse(self.mic.info()['capturing'])
        self.assertEqual(self.input.options['blocksize'], 0)
        self.assertEqual(self.input.options['latency'], 'low')
        self.assertEqual(self.input.options['device'], 17)
        self.assertEqual(self.input.options['channels'], 2)
        self.input.feed(np.zeros((0, 2), dtype=np.float32))
        self.assertFalse(self.mic.info()['capturing'])
        speech = np.sin(np.linspace(0, 6, 960, dtype=np.float32)) * .08
        self.input.feed(np.column_stack((np.zeros_like(speech), speech)))
        self.assertTrue(self.mic.info()['capturing'])
        self.assertEqual(self.mic.info()['ptt_frames'], 960)
        self.assertIsNotNone(self.mic.info()['ptt_first_pcm_ms'])
        self.assertEqual(self.mic.finish(expected_generation=self.mic.generation)['text'], 'fixture')
        args = self.audio.transcribe_samples.call_args.args
        np.testing.assert_array_equal(args[0], speech)
        self.assertEqual(args[1], 48000)
        self.assertTrue(self.input.closed.is_set())
        self.input.feed(np.ones((480, 2), dtype=np.float32))
        self.assertEqual(self.mic.info()['ptt_frames'], 960)
        self.audio.prepare.assert_not_called()

    def test_release_before_device_creation_never_starts_the_late_stream(self):
        entered, release = threading.Event(), threading.Event()

        def delayed(**options):
            entered.set()
            release.wait(2)
            return self.create_input(**options)

        self.factory.side_effect = delayed
        self.mic.start()
        try:
            self.assertTrue(entered.wait(1))
            with self.assertRaisesRegex(ValueError, 'Released before microphone audio arrived'):
                self.mic.finish(expected_generation=self.mic.generation)
        finally:
            release.set()
        self.wait_for(lambda: self.input is not None and self.input.closed.is_set())
        self.assertEqual(self.input.starts, 0)
        self.audio.transcribe_samples.assert_not_called()

    def test_selected_open_stream_without_callbacks_times_out_and_closes(self):
        with patch('chuddy.audio.PTT_FIRST_PCM_TIMEOUT', .05):
            self.mic.start()
            self.wait_for(lambda: self.input is not None and self.input.closed.is_set())
        with self.assertRaisesRegex(ValueError, 'opened but sent no audio frames'):
            self.mic.finish(expected_generation=self.mic.generation)
        self.input.feed(np.ones((480, 2), dtype=np.float32))
        self.assertEqual(self.mic.info()['ptt_frames'], 0)
        self.assertFalse(self.audio.info()['recording'])
        self.audio.transcribe_samples.assert_not_called()

    def test_silent_frames_are_distinct_from_the_absence_of_callbacks(self):
        self.mic.start()
        self.wait_for(lambda: self.audio.info()['recording'])
        self.input.feed(np.zeros((480, 2), dtype=np.float32))
        self.assertTrue(self.mic.info()['capturing'])
        with self.assertRaisesRegex(ValueError, 'frames arrived.*input was silent'):
            self.mic.finish()
        self.assertEqual(self.mic.info()['ptt_frames'], 480)
        self.assertTrue(self.input.closed.is_set())
        self.audio.transcribe_samples.assert_not_called()

    def test_channel_fallback_uses_only_the_selected_device(self):
        def reject_stereo(**options):
            if options['channels'] == 2:
                raise sd.PortAudioError('fixture channel failure')
            return self.create_input(**options)

        self.factory.side_effect = reject_stereo
        self.mic.start()
        self.wait_for(lambda: self.audio.info()['recording'])
        self.assertEqual([(c.kwargs['device'], c.kwargs['channels']) for c in self.factory.call_args_list],
                         [(17, 2), (17, 1)])
        self.assertEqual(self.mic.info()['input_device']['channels'], 1)

    def test_open_failure_is_reported_even_when_release_has_no_frames(self):
        self.factory.side_effect = sd.PortAudioError('fixture input unavailable')
        self.mic.start()
        self.wait_for(lambda: bool(self.mic.info()['error']))
        with self.assertRaisesRegex(ValueError, 'Could not open the selected microphone: fixture input unavailable'):
            self.mic.finish(expected_generation=self.mic.generation)
        self.assertFalse(self.audio.info()['recording'])

    def test_failed_device_start_closes_the_unpublished_stream(self):
        def fail_on_start(**options):
            stream = self.create_input(**options)
            stream.start = Mock(side_effect=sd.PortAudioError('fixture could not start'))
            return stream

        self.factory.side_effect = fail_on_start
        self.mic.start()
        self.wait_for(lambda: self.input is not None and self.input.closed.is_set())
        self.assertFalse(self.audio.info()['recording'])
        self.assertIn('Could not open the selected microphone', self.mic.info()['error'])
