"""Scoring with disposable stores, fake adapter callbacks and synthetic PCM."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import Mock, patch
import wave

import numpy as np

from chuddy.audio import AudioPipeline
from chuddy.audio_events import SoundRuns, remove_effect_echo
from chuddy.store import Store
from chuddy.voice import Voice
from scripts.annotate_audio import compare


class SoundScoringTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name)
        self.store = Store(self.path / 'audio.sqlite3')
        self.addCleanup(self.store.close)
        self.store.bind_passive_runtime(lambda: 100)
        self.store.passive_start()
        self.audio = AudioPipeline(self.store, self.path / 'missing')
        self.voice = Mock()

    def callbacks(self, kind='fart', cancel=None):
        self.audio.play_effect(self.voice, kind, 'Chuddy speaker · deliberate sound test', cancel=cancel)
        return self.voice.sound.call_args.kwargs

    def test_one_confirmed_playback_awards_one_event_and_boost_under_concurrency(self):
        callbacks = self.callbacks()
        callbacks['on_complete']()  # No output adapter acknowledgment.
        self.assertEqual(self.store.scores()['total'], 0)
        callbacks['on_start'](Path('fart.wav'), 3., 1.)
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: callbacks['on_complete'](), range(24)))
        self.assertEqual(self.store.scores()['fart'], 1)
        self.assertEqual(self.store.balance(), 105)
        self.assertEqual(self.store.passive_info()['boost_remaining_ms'], 1800000)

    def test_playback_cancellation_profile_switch_and_expiry_reject_stale_callbacks(self):
        cancel = threading.Event()
        callbacks = self.callbacks(cancel=cancel)
        callbacks['on_start'](Path('fart.wav'), 3., 1.)
        cancel.set()
        self.assertFalse(callbacks['allowed']())
        callbacks['on_complete']()

        callbacks = self.callbacks()
        callbacks['on_start'](Path('fart.wav'), 3., 1.)
        self.audio.cancel_scores()
        self.assertFalse(callbacks['allowed']())
        callbacks['on_complete']()

        callbacks = self.callbacks()
        callbacks['on_start'](Path('fart.wav'), 3., 1.)
        self.audio.score_allowed = lambda: False
        self.assertFalse(callbacks['allowed']())
        callbacks['on_complete']()

        self.audio.score_allowed = lambda: True
        callbacks = self.callbacks()
        callbacks['on_start'](Path('fart.wav'), 3., 1.)
        original = self.store.profile()['id']
        self.store.select_profile(self.store.add_profile('Other')['id'])
        self.assertFalse(callbacks['allowed']())
        callbacks['on_complete']()
        self.assertEqual(self.store.scores(original)['total'], 0)
        self.assertEqual(self.store.scores()['total'], 0)

    def test_windows_adapter_success_failure_and_stop(self):
        path = self.path / 'effect.wav'
        self.write_wave(path, np.zeros(1600, dtype=np.float32))
        with patch('chuddy.voice.threading.Thread'), patch('winsound.PlaySound') as driver:
            voice = Voice(self.path, self.store.settings)
            started, completed = Mock(), Mock()
            with patch('chuddy.voice.time.monotonic', side_effect=[0., 1.]):
                voice._play(path, voice.generation, 'effect', (None, started, completed, None))
            started.assert_called_once()
            completed.assert_called_once()
            driver.side_effect = RuntimeError('output unavailable')
            with self.assertRaisesRegex(RuntimeError, 'output unavailable'):
                voice._play(path, voice.generation, 'effect', (None, started, completed, None))
            completed.assert_called_once()
            driver.side_effect = None
            with patch('chuddy.voice.time.sleep', side_effect=lambda _: voice.stop()):
                voice._play(path, voice.generation, 'effect', (None, started, completed, None))
            completed.assert_called_once()

    def test_windows_effect_start_and_stop_share_the_admission_lock(self):
        path = self.path / 'effect.wav'
        self.write_wave(path, np.zeros(1600, dtype=np.float32))
        with patch('chuddy.voice.threading.Thread'):
            voice = Voice(self.path, self.store.settings)
        callbacks = self.callbacks()
        effect = tuple(callbacks[key] for key in ('allowed', 'on_start', 'on_complete', 'admission'))

        def can_admit_stop():
            if not self.audio.lock.acquire(blocking=False):
                return False
            self.audio.lock.release()
            return True

        with ThreadPoolExecutor(max_workers=1) as pool:
            def fake_playback(_path, _flags):
                self.assertFalse(pool.submit(can_admit_stop).result(timeout=2))

            with (
                patch('winsound.PlaySound', side_effect=fake_playback) as driver,
                patch('chuddy.voice.time.monotonic', side_effect=[0., 1.]),
            ):
                voice._play(path, voice.generation, 'effect', effect)
            driver.assert_called_once()
            self.assertTrue(pool.submit(can_admit_stop).result(timeout=2))

    def test_session_stop_rejects_a_capture_worker_that_has_not_started(self):
        with patch('chuddy.audio.threading.Thread') as worker:
            self.audio.start()
        self.audio.cancel_scores()
        with patch('sounddevice.InputStream') as capture:
            self.audio._run(*worker.call_args.kwargs['args'])
        capture.assert_not_called()

    @staticmethod
    def write_wave(path, waveform):
        with wave.open(str(path), 'wb') as wav:
            wav.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            wav.writeframes((waveform * 32768).astype('<i2').tobytes())

    def test_echo_is_removed_while_an_overlapping_independent_event_counts(self):
        rng = np.random.default_rng(42)
        path = self.path / 'effect.wav'
        effect = rng.uniform(-.5, .5, 8800).astype(np.float32)
        self.write_wave(path, effect)
        echo = np.zeros(16000, dtype=np.float32)
        echo[800:9600] = effect * .7
        other = np.zeros_like(echo)
        other[8000:13000] = .14 * np.sin(np.arange(5000) / 12)

        self.audio.classify = Mock(side_effect=lambda samples, rate: {
            'available': True, 'detected': np.max(np.abs(samples)) > .10,
            'kind': 'fart', 'confidence': .85,
        })
        callbacks = self.callbacks()
        callbacks['on_start'](path, 10., .55)
        references = tuple(self.audio.effect_references)
        self.assertFalse(self.audio.detect_window(echo, 16000, 10., references)['detected'])
        residual = remove_effect_echo(echo + other, 16000, 10., references)
        np.testing.assert_allclose(residual, other, atol=.002)
        pcm = ((echo + other) * 32767).astype('<i2').tobytes()
        runs = SoundRuns()
        self.audio.ingest_pcm(pcm, event_id='independent', start=10., references=references, runs=runs)
        self.audio.ingest_pcm(pcm, event_id='repeat-window', start=10.5, runs=runs)
        callbacks['on_complete']()
        self.assertEqual(self.store.scores()['fart'], 2)
        self.assertEqual({event['source'] for event in self.store.scores()['recent']},
                         {'Chuddy speaker · deliberate sound test', 'YAMNet Pi microphone · uncalibrated'})
        self.assertEqual(self.store.passive_info()['boost_remaining_ms'], 3600000)

    def test_separate_farts_can_be_accepted_after_overlapping_windows(self):
        runs = SoundRuns()
        fart = {'detected': True, 'kind': 'fart'}
        self.assertTrue(runs.new_event(fart, 0, 1))
        self.assertFalse(runs.new_event(fart, .5, 1))
        self.assertFalse(runs.new_event(fart, 1, 1))
        self.assertTrue(runs.new_event(fart, 2.5, 1))

    def test_desktop_scoring_stream_groups_overlap_rejects_legacy_class_and_discards_stop(self):
        stream = Mock()
        capture = Mock(return_value=stream)
        inference_entered, release = threading.Event(), threading.Event()
        started, exited = threading.Event(), threading.Event()
        stream.start.side_effect = started.set
        predictions = iter(enumerate(('fart', 'fart', 'burp', 'fart')))

        def infer(samples, rate):
            index, kind = next(predictions)
            if index == 3:
                inference_entered.set()
                release.wait(2)
            return {'available': True, 'kind': kind, 'detected': True, 'confidence': .8}

        # Use a fake PortAudio callback and model. One-second windows advance
        # by half a second, so five blocks can produce four predictions.
        self.audio.prepare = Mock(return_value=True)
        self.audio.classify = infer
        run = self.audio._run

        def observed_run(*args):
            try:
                run(*args)
            finally:
                exited.set()

        self.audio._run = observed_run
        with patch('sounddevice.query_devices', return_value={'default_samplerate': 16000}), patch('sounddevice.InputStream', capture):
            self.audio.start()
            try:
                # Wait for the fake stream to accept its callback.
                self.assertTrue(started.wait(2))
                callback = capture.call_args.kwargs['callback']
                samples = np.zeros((8000, 1), dtype=np.float32)
                for _ in range(5):
                    callback(samples, 8000, None, None)
                self.assertTrue(inference_entered.wait(2))
            finally:
                self.audio.stop()
                release.set()
                self.assertTrue(exited.wait(2))
        self.assertEqual(self.store.scores()['fart'], 1)
        self.assertEqual(self.store.scores()['burp'], 0)
        self.assertEqual(self.store.passive_info()['boost_remaining_ms'], 1800000)

    def test_yamnet_competing_burp_class_is_negative_in_pcm(self):
        scores = np.zeros((2, 521))
        scores[:, 53], scores[:, 55] = .8, .6
        self.audio.model = lambda _: (scores, None, None)
        pcm = bytes(32000)
        result = self.audio.ingest_pcm(pcm, event_id='same')
        self.assertFalse(result['detected'])
        self.assertEqual(self.store.scores()['total'], 0)
        scores[:, 53], scores[:, 55] = .6, .8
        self.assertTrue(self.audio.ingest_pcm(pcm, event_id='same')['detected'])
        self.assertEqual(self.store.scores()['fart'], 1)

    def test_audio_effect_rejects_burp_without_queueing(self):
        with self.assertRaisesRegex(ValueError, 'Only fart'):
            self.audio.play_effect(self.voice, 'burp', 'old request')
        self.voice.sound.assert_not_called()

    def test_annotation_reports_missing_duplicate_and_false_positive_separately(self):
        def interval(start, end, kind):
            return {'start_s': start, 'end_s': end, 'kind': kind}

        doc = {'duration_s': 5., 'speech_draft': {'segments': [{'text': 'fart, burp'}]},
               'annotations': [interval(0, 1, 'fart'), interval(1, 2, 'fart'),
                               interval(2, 3, 'burp'), interval(4, 5, 'other')]}
        detections = [interval(0, .5, 'fart'), interval(.5, 1, 'fart'),
                      interval(2, 3, 'fart'), interval(4, 5, 'fart')]
        result = compare(doc, 5., detections)
        self.assertEqual(result['expected'], 2)
        self.assertEqual(result['matched'], 1)
        self.assertEqual(result['duplicates'], detections[1:2])
        self.assertEqual(result['missed'], [doc['annotations'][1]])
        self.assertEqual(result['false_positives'], detections[2:])
