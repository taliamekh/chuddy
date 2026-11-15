import base64
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from chuddy.audio import AudioPipeline
from chuddy.pi_bridge import PiBridge, local_pi_url
from chuddy.store import Store


class FakePi:
    def __init__(self):
        self.lock = threading.RLock()
        self.events = []
        self.chunks = []
        self.startup_id = str(uuid.uuid4())
        self.stops = 0
        self.microphones = []
        self.audio_queries = 0
        self.poll_entered = self.poll_release = None
        self.mic_entered = self.mic_release = None

    def event(self, kind='audio.playback', source='simulator sound test', sound='fart'):
        with self.lock:
            details = {'kind': sound, 'source': source, 'playback_id': str(uuid.uuid4())}
            if kind == 'audio.playback':
                self.events.append({'cursor': len(self.events) + 1, 'id': str(uuid.uuid4()),
                                    'type': 'audio.playback.started', 'payload': details})
            event = {'cursor': len(self.events) + 1, 'id': str(uuid.uuid4()), 'type': kind,
                     'payload': {**details, 'confirmed': True}}
            self.events.append(event)
            return event

    def audio(self):
        with self.lock:
            item = {'id': str(uuid.uuid4()), 'startup_id': self.startup_id, 'cursor': len(self.chunks) + 1, 'rate': 16000, 'encoding': 'pcm_s16le', 'channels': 1, 'pcm': base64.b64encode(b'\0\0' * 16000).decode()}
            self.chunks.append(item)
            return item


class FakeClient:
    def __init__(self, backend):
        self.backend = backend
        self.cursor = 0

    def status(self):
        return {'version': 1, 'startup_id': self.backend.startup_id, 'event_cursor': len(self.backend.events), 'armed': False, 'microphone': False, 'spray': 'disabled'}

    def events(self):
        with self.backend.lock:
            events = [dict(e) for e in self.backend.events if e['cursor'] > self.cursor]
            if events:
                self.cursor = events[-1]['cursor']
            gate, entered = self.backend.poll_release, self.backend.poll_entered
        if gate:
            if entered:
                entered.set()
            gate.wait(timeout=3)
        return events

    def stop(self):
        self.backend.stops += 1
        return {'accepted': True}

    def microphone(self, enabled):
        self.backend.microphones.append(enabled)
        if enabled and self.backend.mic_release:
            self.backend.mic_entered.set()
            self.backend.mic_release.wait(timeout=3)
        return {'accepted': True}

    def audio_chunks(self, after=0):
        with self.backend.lock:
            self.backend.audio_queries += 1
            return {'version': 1, 'startup_id': self.backend.startup_id, 'cursor': len(self.backend.chunks), 'chunks': [dict(c) for c in self.backend.chunks if c['cursor'] > after][-8:]}


class FakeAudio:
    def __init__(self):
        self.ingested = []

    def ingest_pcm(self, pcm, sample_rate=16000, event_id=None, profile_id=None, cancel_event=None, **metadata):
        if not cancel_event.is_set():
            self.ingested.append({'pcm': pcm, 'rate': sample_rate, 'event_id': event_id, 'profile_id': profile_id})
        return {'available': True, 'detected': False}


class PiBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.store = Store(self.path / 'bridge.sqlite3')
        self.backend = FakePi()
        self.audio = FakeAudio()
        self.factory = patch('chuddy.pi_bridge.PiClient', side_effect=lambda *a, **k: FakeClient(self.backend))
        self.factory.start()
        self.bridges = []
        self.bridge = self.new_bridge()

    def tearDown(self):
        if self.backend.poll_release:
            self.backend.poll_release.set()
        if self.backend.mic_release:
            self.backend.mic_release.set()
        for bridge in self.bridges:
            bridge.stop()
            if bridge.worker:
                bridge.worker.join(timeout=3)
        self.factory.stop()
        self.store.close()
        self.temp.cleanup()

    def new_bridge(self, audio=None):
        bridge = PiBridge(self.store, audio or self.audio)
        bridge.POLL_SECONDS = .01
        self.bridges.append(bridge)
        return bridge

    def connect(self, bridge=None):
        return (bridge or self.bridge).connect('http://127.0.0.1:8767', 'test-pi-token-at-least-sixteen')

    def wait(self, condition):
        end = time.monotonic() + 2
        while time.monotonic() < end:
            if condition():
                return
            time.sleep(.005)
        self.fail('Bridge condition did not become true.')

    def test_construction_and_connect_do_not_start_microphone_or_arm(self):
        self.assertFalse(self.bridge.info()['connected'])
        self.connect()
        time.sleep(.03)
        self.assertEqual(self.backend.microphones, [])
        self.assertEqual(self.backend.audio_queries, 0)
        self.assertFalse(self.bridge.info()['microphone'])
        self.assertFalse(self.bridge.info()['remote']['armed'])

    def test_first_profile_connection_baselines_historical_sounds(self):
        self.backend.event()
        self.connect()
        time.sleep(.03)
        self.assertEqual(self.store.scores()['total'], 0)
        self.backend.event(source='physical playback')
        self.wait(lambda: self.store.scores()['total'] == 1)
        self.assertIn('physical hardware unvalidated', self.store.scores()['recent'][0]['source'])

    def test_persistent_cursor_resumes_history_without_scoring_while_disconnected(self):
        self.connect()
        self.backend.event()
        self.wait(lambda: self.store.scores()['total'] == 1)
        self.bridge.stop()
        self.bridge.worker.join(timeout=2)
        self.backend.event()
        restarted = self.new_bridge()
        self.connect(restarted)
        self.backend.event()
        self.wait(lambda: self.store.scores()['total'] == 2)
        restarted.client.cursor = 0  # Deliberate transport replay.
        time.sleep(.04)
        self.assertEqual(self.store.scores()['total'], 2)

    def test_connection_cannot_score_after_profile_change_and_new_profile_gets_fresh_baseline(self):
        original = self.store.profile()['id']
        self.connect()
        other = self.store.add_profile('Other')
        self.store.select_profile(other['id'])
        event = self.backend.event()
        self.wait(lambda: self.bridge.client.cursor >= event['cursor'])
        self.assertEqual(self.store.scores(original)['total'], 0)
        self.assertEqual(self.store.scores()['total'], 0)
        self.bridge.stop()
        second = self.new_bridge()
        self.connect(second)
        time.sleep(.03)
        self.assertEqual(self.store.scores()['total'], 0)
        self.backend.event()
        self.wait(lambda: self.store.scores()['total'] == 1)

    def test_stop_is_nonblocking_and_late_event_batch_cannot_score(self):
        self.backend.poll_entered, self.backend.poll_release = threading.Event(), threading.Event()
        self.connect()
        self.assertTrue(self.backend.poll_entered.wait(timeout=1))
        self.backend.event()
        # The blocked poll has no events yet. Force a subsequent poll to block
        # with an event snapshot before requesting stop.
        self.backend.poll_release.set()
        self.wait(lambda: self.store.scores()['total'] == 1)
        self.backend.poll_entered.clear()
        self.backend.poll_release.clear()
        self.backend.event()
        self.assertTrue(self.backend.poll_entered.wait(timeout=1))
        started = time.monotonic()
        self.bridge.stop()
        self.assertLess(time.monotonic() - started, .2)
        self.backend.poll_release.set()
        self.bridge.worker.join(timeout=2)
        self.assertEqual(self.store.scores()['total'], 1)

    def test_pcm_forwarding_is_explicit_and_preserves_profile_and_chunk_identity(self):
        self.connect()
        self.bridge.microphone(True)
        item = self.backend.audio()
        self.wait(lambda: len(self.audio.ingested) == 1)
        self.assertEqual(self.audio.ingested[0]['profile_id'], self.store.profile()['id'])
        self.assertEqual(self.audio.ingested[0]['rate'], 16000)
        self.bridge.audio_cursor = 0
        self.wait(lambda: len(self.audio.ingested) == 2)
        self.assertEqual(self.audio.ingested[0]['event_id'], self.audio.ingested[1]['event_id'])
        self.bridge.microphone(False)
        self.assertFalse(self.bridge.info()['microphone'])

    def test_stop_invalidates_inflight_classification_before_score(self):
        pipeline = AudioPipeline(self.store, self.path / 'absent-model')
        entered, release = threading.Event(), threading.Event()
        def classify(*args):
            entered.set()
            release.wait(timeout=3)
            return {'available': True, 'detected': True, 'kind': 'fart', 'confidence': .8}
        with patch.object(pipeline, 'classify', side_effect=classify):
            bridge = self.new_bridge(pipeline)
            self.connect(bridge)
            bridge.microphone(True)
            self.backend.audio()
            self.assertTrue(entered.wait(timeout=2))
            bridge.stop()
            release.set()
            bridge.worker.join(timeout=2)
        self.assertEqual(self.store.scores()['total'], 0)

    def test_disconnect_during_microphone_start_stops_late_remote_start(self):
        self.connect()
        self.backend.mic_entered, self.backend.mic_release = threading.Event(), threading.Event()
        thread = threading.Thread(target=lambda: self.bridge.microphone(True))
        thread.start()
        self.assertTrue(self.backend.mic_entered.wait(timeout=1))
        self.bridge.stop()
        self.backend.mic_release.set()
        thread.join(timeout=2)
        self.assertFalse(self.bridge.info()['microphone'])
        self.wait(lambda: self.backend.stops >= 2)

    def test_pi_reboot_never_implicitly_restarts_microphone(self):
        self.connect()
        self.bridge.microphone(True)
        self.backend.startup_id = str(uuid.uuid4())
        self.wait(lambda: not self.bridge.info()['microphone'])
        self.assertIn('restarted', self.bridge.info()['microphone_error'])
        self.assertEqual(self.backend.microphones, [True])

    def test_remote_urls_and_malformed_tokens_are_rejected(self):
        for url in ('https://example.com', 'http://192.0.2.5', 'http://localhost/path', 'http://u:p@localhost', 'http://localhost:bad'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                local_pi_url(url)
        with self.assertRaises(ValueError):
            self.bridge.connect('http://localhost:8767', 'short')

    def test_real_service_protocol_forwards_events_without_outputs(self):
        from pi.service import Companion, make_server
        companion = Companion(self.path / 'real-protocol-simulator')
        server = make_server(companion, 'protocol-token-at-least-sixteen', port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            self.factory.stop()
            bridge = self.new_bridge()
            bridge.connect(f'http://127.0.0.1:{server.server_port}', 'protocol-token-at-least-sixteen')
            with patch('winsound.PlaySound') as adapter:
                rejection = bridge.client.command('audio.play', {'kind': 'burp'})
                self.assertFalse(rejection['accepted'])
                adapter.assert_not_called()
                companion.play_sound('fart')
                self.wait(lambda: self.store.scores()['fart'] == 1)
                adapter.assert_called()
            self.assertFalse(companion.status()['armed'])
            self.assertFalse(companion.status()['microphone'])
            self.assertEqual(companion.runner.info()['sequence'], 0)
            bridge.stop()
            bridge.worker.join(timeout=2)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
            with patch.object(companion, 'stop_playback'), patch.object(companion, 'stop_mic'):
                companion.close()
            companion.db.close()
            self.factory.start()

    def test_playback_requires_current_matching_start_and_confirmed_completion(self):
        old = self.backend.event()
        self.connect()
        self.backend.event(sound='burp')
        with self.backend.lock:
            for payload in (old['payload'], {**old['payload'], 'playback_id': 'unpaired'},
                            {'kind': 'fart', 'source': 'simulator sound test'}):
                self.backend.events.append({'cursor': len(self.backend.events) + 1, 'id': str(uuid.uuid4()),
                                            'type': 'audio.playback', 'payload': payload})
        live = self.backend.event()
        with self.backend.lock:
            self.backend.events.append({**live, 'cursor': len(self.backend.events) + 1, 'id': str(uuid.uuid4())})
        self.wait(lambda: self.bridge.client.cursor >= live['cursor'] + 1)
        self.wait(lambda: self.store.scores()['total'] == 1)
        self.assertEqual(self.bridge.info()['scored'], 1)


if __name__ == '__main__':
    unittest.main()
