"""PTT-owned capture must not wait for a cold sound classifier."""
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from chuddy.audio import AudioPipeline


class PTTStartupTests(unittest.TestCase):
    def test_owned_ptt_collects_samples_without_loading_classifier(self):
        gate = threading.Event()
        store = Mock()
        store.settings.return_value = {'audio_device': None}
        store.profile.return_value = {'id': 'ptt-profile'}
        class FakeStream:
            def __init__(self, **kwargs):
                self.callback = kwargs['callback']
            def start(self):
                self.callback(np.ones((8000, 1), dtype=np.float32) * .05, 8000, None, None)
            def abort(self):
                pass
            def close(self):
                pass
        with tempfile.TemporaryDirectory() as folder, patch('sounddevice.query_devices', return_value={'default_samplerate': 16000}), patch('sounddevice.InputStream', FakeStream):
            audio = AudioPipeline(store, Path(folder) / 'models' / 'yamnet')
            audio.prepare = Mock(side_effect=lambda: gate.wait(5))
            try:
                audio.start_conversation()
                deadline = time.monotonic() + 1
                while not audio.ptt_data and time.monotonic() < deadline:
                    time.sleep(.005)
                self.assertTrue(audio.ptt_data, 'Conversational samples waited for classifier startup.')
                self.assertEqual(audio.ptt_rate, 16000)
                self.assertEqual(audio.info()['status'], 'listening · push-to-talk')
                audio.prepare.assert_not_called()
            finally:
                audio.stop()
                gate.set()


if __name__ == '__main__':
    unittest.main()
