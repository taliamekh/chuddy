import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from chuddy.audio import AudioPipeline
from chuddy.hardware import Simulator
from chuddy.pi_client import PiClient
from chuddy.voice import speech_text, Voice
from pi.engine import Sequence, SIM_CALIBRATION, SimulatedOutputs, validate_calibration
from pi.service import Companion, make_server


class FakeStore:
    def __init__(self):
        self.events = []
        self.lock = threading.RLock()
    def settings(self):
        return {'audio_threshold': .35, 'voice': False}
    def profile(self):
        return {'id': 'original-profile'}
    def event(self, *args, **kwargs):
        self.events.append((args, kwargs))
    def log(self, *args):
        pass


class SequenceTests(unittest.TestCase):
    def test_full_simulator_turn_and_no_spray(self):
        outputs = SimulatedOutputs()
        playback, events = [], []
        runner = Sequence(outputs, event=lambda kind, payload: events.append(kind), playback=playback.append)
        runner.spray_enabled = True  # A simulator still cannot actuate it.
        runner.start()
        runner.thread.join(4)
        self.assertFalse(runner.info()['busy'])
        self.assertEqual(runner.yaw, 0)
        self.assertEqual(playback, ['fart'])
        self.assertFalse(outputs.spray)
        self.assertEqual(runner.info()['spray'], 'disabled')
        self.assertIn('spray.disabled', events)

    def test_cancel_before_sound_and_busy_rejection(self):
        playback = []
        runner = Sequence(playback=playback.append)
        runner.start()
        with self.assertRaises(ValueError):
            runner.start()
        time.sleep(.05)
        runner.stop()
        self.assertFalse(runner.info()['busy'])
        self.assertEqual(runner.yaw, 0)
        self.assertEqual(playback, [])

    def test_calibration_rejects_out_of_bounds_and_short_timeout(self):
        for values in ({'away': 181}, {'home': -1}, {'speed_deg_s': float('nan')}, {'speed_deg_s': 1}):
            with self.assertRaises(ValueError):
                validate_calibration({**SIM_CALIBRATION, **values})
        with self.assertRaises(ValueError):
            validate_calibration(SIM_CALIBRATION, physical=True)

    def test_simulator_scores_only_confirmed_playback_and_labels_source(self):
        store, voice = FakeStore(), Mock()
        simulator = Simulator(store, voice, AudioPipeline(store, 'missing'))
        simulator.belly()
        simulator.runner.thread.join(4)
        self.assertEqual(store.events, [])
        callbacks = voice.sound.call_args.kwargs
        callbacks['on_complete']()
        self.assertEqual(store.events, [])
        callbacks['on_start'](Path('fart.wav'), 1., 1.)
        callbacks['on_complete']()
        self.assertEqual(store.events[0][1]['profile_id'], 'original-profile')
        self.assertEqual(store.events[0][0][2], 'Chuddy speaker · simulated belly')
        self.assertEqual(voice.sound.call_args.args, ('fart',))


class AudioTests(unittest.TestCase):
    def test_constructor_and_stop_do_not_open_microphone(self):
        with tempfile.TemporaryDirectory() as folder, patch('sounddevice.InputStream') as stream:
            audio = AudioPipeline(FakeStore(), Path(folder) / 'model')
            audio.stop()
            self.assertFalse(audio.info()['recording'])
            self.assertEqual(audio.info()['status'], 'off')
            stream.assert_not_called()

    def test_missing_model_and_fake_directory_not_ready(self):
        with tempfile.TemporaryDirectory() as folder:
            audio = AudioPipeline(FakeStore(), folder)
            result = audio.classify(np.zeros(16000), 16000)
            self.assertFalse(result['available'])
            self.assertNotIn('ready', audio.info()['classifier'])

    def test_classifier_resamples_and_has_no_score_side_effect(self):
        store = FakeStore()
        audio = AudioPipeline(store, 'missing')
        seen = []
        def model(samples):
            seen.append(len(samples))
            scores = np.zeros((2, 521))
            scores[:, 55] = .8
            return scores, None, None
        audio.model = model
        result = audio.classify(np.zeros((48000, 2)), 48000)
        self.assertEqual(seen, [16000])
        self.assertEqual(result['kind'], 'fart')
        self.assertTrue(result['detected'])
        self.assertFalse(result['calibrated'])
        self.assertEqual(store.events, [])

    def test_invalid_audio_and_sample_rate(self):
        audio = AudioPipeline(FakeStore(), 'missing')
        for samples, rate in [(np.zeros(10), 16000), (np.full(16000, np.nan), 16000), (np.zeros(16000), True)]:
            with self.assertRaises(ValueError):
                audio.classify(samples, rate)

    def test_stop_preserves_healthy_voice_worker_and_spelling(self):
        with tempfile.TemporaryDirectory() as folder:
            voice = Voice(folder, FakeStore().settings)
            voice.process = Mock()
            voice.process.poll.return_value = None
            with patch('winsound.PlaySound'):
                voice.stop()
            voice.process.terminate.assert_not_called()
        self.assertIn('bee. you. tee. tee.', speech_text('B-U-T-T. Report.'))

    def test_pi_cancellation_discards_inference_result(self):
        store, cancel = FakeStore(), threading.Event()
        audio = AudioPipeline(store, 'missing')
        def classify(samples, rate):
            cancel.set()
            return {'detected': True, 'kind': 'fart', 'confidence': .9}
        audio.classify = classify
        self.assertTrue(audio.ingest_pcm(bytes(32000), cancel_event=cancel)['cancelled'])
        self.assertEqual(store.events, [])


class PiProtocolTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.companion = Companion(self.folder.name)
        self.companion.runner.playback = lambda kind: None  # Never play speakers in tests.
        self.server = make_server(self.companion, 'private-test-token-1234', port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)
        self.client = PiClient(self.url, 'private-test-token-1234')

    def tearDown(self):
        self.companion.close()
        self.server.shutdown()
        self.server.server_close()
        self.companion.db.close()
        self.folder.cleanup()

    def test_auth_and_default_disabled_outputs(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(self.url + '/v1/status')
        self.assertEqual(error.exception.code, 401)
        status = self.client.status()
        self.assertFalse(status['armed'])
        self.assertFalse(status['microphone'])
        self.assertEqual(status['spray'], 'disabled')

    def test_command_retry_does_not_repeat_sequence(self):
        first = self.client.belly('one-command')
        second = self.client.belly('one-command')
        self.assertTrue(first['accepted'])
        self.assertTrue(second['replayed'])
        self.assertEqual(self.companion.runner.sequence, 1)
        self.client.stop()
        self.companion.runner.thread.join(3)
        self.assertEqual(self.companion.runner.yaw, 0)

    def test_id_reuse_and_stale_command_are_rejected(self):
        self.client.command('spray.request', request_id='same-id')
        with self.assertRaises(ValueError):
            self.client.command('belly.perform', request_id='same-id')
        with self.assertRaises(ValueError):
            self.companion.command({'version': 1, 'id': 'stale', 'type': 'belly.perform', 'payload': {}, 'time': '2000-01-01T00:00:00+00:00'})

    def test_event_cursor_and_no_simulated_spray(self):
        result = self.client.command('spray.request')
        self.assertEqual(result['spray'], 'disabled')
        events = self.client.events()
        self.assertTrue(events)
        self.assertEqual(self.client.events(), [])
        self.assertTrue(any(e['type'] == 'spray.request' for e in events))

    def test_physical_lease_expiry_disarms_mock_outputs(self):
        class FakePhysical(SimulatedOutputs):
            physical = True
            closed = False
            def close(self):
                self.closed = True
        outputs = FakePhysical()
        self.companion.config = {'backend': 'gpio', 'motion': {**SIM_CALIBRATION, 'calibrated': True}}
        with patch('pi.outputs.ServoOutputs', return_value=outputs):
            self.companion.arm({'lease_seconds': 2, 'spray': False})
        self.assertTrue(self.companion.armed)
        self.companion.lease = time.monotonic() - 1
        time.sleep(.25)
        self.assertFalse(self.companion.armed)
        self.assertTrue(outputs.closed)

    def test_pc_voice_wav_retry_plays_once_and_invalid_data_rejected(self):
        with patch.object(self.companion, 'play_wav') as playback:
            path = Path(__file__).resolve().parents[1] / 'assets/audio/fart.wav'
            first = self.client.play_wav(path, 'voice-command')
            second = self.client.play_wav(path, 'voice-command')
            self.assertTrue(first['accepted'])
            self.assertTrue(second['replayed'])
            playback.assert_called_once()
        result = self.client.command('audio.wav', {'wav': 'bm90IGEgd2F2'})
        self.assertFalse(result['accepted'])

    def test_client_rejects_nonlocal_or_embedded_credentials(self):
        for url in ('https://example.com', 'http://8.8.8.8', 'http://user:pass@127.0.0.1', 'file:///tmp/pi'):
            with self.assertRaises(ValueError):
                PiClient(url, 'test')


if __name__ == '__main__':
    unittest.main()
