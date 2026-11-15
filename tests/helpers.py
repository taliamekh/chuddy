import tempfile
import threading
import unittest
import uuid
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import patch

from chuddy.store import Store


class FakeVoice:
    def __init__(self, *args, **kwargs):
        self.spoken = []
        self.fillers = []
        self.sounds = []
        self.stops = 0
        self.spoke = threading.Event()

    def say(self, text, turn_id=None):
        self.spoken.append((text, turn_id))
        self.spoke.set()

    def filler(self, turn_id):
        self.fillers.append(turn_id)

    def sound(self, kind, **callbacks):
        self.sounds.append(kind)
        return True

    def stop(self):
        self.stops += 1

    def info(self):
        return {'status': 'fake', 'engine': 'test', 'speaking': False, 'reference_ready': False}

    def test(self):
        return {'status': 'test only'}

    def close(self):
        self.stop()

    def warm(self):
        return None


class FakeAudio:
    def __init__(self, *args):
        self.recording = False
        self.stops = 0

    def start(self):
        self.recording = True

    def stop(self):
        self.recording = False
        self.stops += 1

    def cancel_scores(self):
        pass

    def play_effect(self, voice, kind, source):
        return voice.sound(kind)

    def info(self):
        return {'recording': self.recording, 'classifier': 'test only'}

    def devices(self):
        return []

    def close(self):
        self.stop()


class FakeSimulator:
    def __init__(self, *args):
        self.stops = 0

    def belly(self):
        return {'accepted': True}

    def stop(self):
        self.stops += 1

    def info(self):
        return {'mode': 'simulator', 'phase': 'idle', 'yaw': 0, 'spray': 'disabled', 'sequence': 0}


class FakeInterceptionWindows:
    """Controller tests must never reach the native preserving adapter."""
    def __init__(self):
        self.identities = {}

    def foreground(self):
        from chuddy.controller import windows
        return windows.foreground()

    def request_close(self, window):
        from chuddy.controller import windows
        windows.request_close(window)

    def close_status(self, window):
        from chuddy.controller import windows
        return windows.close_status(window)

    def minimize_work(self, window):
        from chuddy.controller import windows
        self.identities[window['handle']] = window['pid']
        windows.minimize(window['handle'])

    def identity(self, handle):
        return self.identities.get(handle)

    def restore(self, handle):
        from chuddy.controller import windows
        windows.restore(handle)


class FakeChromeLauncher:
    def open(self, destination):
        raise AssertionError('A controller test must explicitly provide a fake retailer launcher.')


class FakeLeagueLauncher:
    def prepare(self):
        raise AssertionError('A controller test must explicitly provide a fake League launcher.')


class FakeAgent:
    def __init__(self, *args, **kwargs):
        self.state = {'status': 'idle', 'turn_id': None}
        self.cancel_event = threading.Event()
        self.calls = []

    def begin(self, text, observation=False):
        self.cancel_event = threading.Event()
        self.state = {'status': 'thinking', 'turn_id': str(uuid.uuid4())}
        self.calls.append((text, observation))
        return {'turn_id': self.state['turn_id']}

    def cancel(self, *, keep_assignment_exchange=False):
        self.cancel_event.set()
        self.state['status'] = 'idle'

    def info(self):
        return dict(self.state)

    def warm(self):
        return {'status': 'warming'}


class FakeNativeBrowserTabs:
    """Native-tab tests opt in explicitly; ordinary tests never inspect UIA."""
    def __init__(self, *args, **kwargs):
        self.available = False
        self.targets = []
        self.preparations = []
        self.prepared = threading.Event()
        self.requested = threading.Event()
        self.prepare_future = None
        self.close_future = None
        self.defer_prepare = False

    def info(self):
        return {'available': self.available, 'ready': False, 'status': 'available' if self.available else 'unavailable',
                'reason': 'Native browser controls are disabled in this fixture.'}

    def prepare_async(self, window, valid, live, **options):
        self.preparations.append(dict(window))
        self.prepare_future = Future()
        if not self.defer_prepare:
            self.prepare_future.set_result({'kind':'native','window':dict(window),'close_point':[100,20]})
        self.prepared.set()
        return self.prepare_future

    def request(self, target, valid, live):
        self.targets.append(target)
        self.close_future = Future()
        self.requested.set()
        return self.close_future

    def cancel(self):
        for future in (self.prepare_future, self.close_future):
            if future is not None and not future.done():
                future.set_result(None if future is self.prepare_future else False)

    close = cancel


class ControllerCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.store = Store(self.path / 'state.sqlite3')
        self.now = 100.0
        self.patches = [
            patch('chuddy.interception.PreservingWindows', FakeInterceptionWindows),
            patch('chuddy.interception.ChromeLauncher', FakeChromeLauncher),
            patch('chuddy.interception.LeagueLauncher', FakeLeagueLauncher),
            patch('chuddy.voice.Voice', FakeVoice),
            patch('chuddy.hardware.Simulator', FakeSimulator),
            patch('chuddy.controller.AudioPipeline', FakeAudio),
            patch('chuddy.controller.LocalAgent', FakeAgent),
            patch('chuddy.native_browser_tabs.NativeBrowserTabs', FakeNativeBrowserTabs),
            patch('chuddy.controller.windows.foreground', return_value={'handle': 42, 'pid': 123, 'process': 'chrome.exe', 'title': 'Report'}),
            patch('chuddy.controller.windows.request_close'),
            patch('chuddy.controller.windows.close_status', return_value='closed'),
            patch('chuddy.controller.windows.minimize'),
            patch('chuddy.controller.windows.restore'),
        ]
        for item in self.patches:
            item.start()
        from chuddy.controller import Controller
        self.controller = Controller(self.store, self.path, clock=lambda: self.now)
        self.controller.distractions.warning_seconds = 0
        self.controller.work_recognizer.infer = lambda metadata, cancel: {"classification": "uncertain", "category": "other", "confidence": 0}
        self.covered = []
        self.uncovered = []
        self.controller.cover = lambda: self.covered.append(True)
        self.controller.uncover = lambda: self.uncovered.append(True)

    def tearDown(self):
        self.controller.close()
        for item in reversed(self.patches):
            item.stop()
        self.store.close()
        self.temp.cleanup()

    def tool(self, name, arguments, text='hello', observation=False):
        return self.controller.tool(name, arguments, cancel=self.controller.agent.cancel_event, turn_id=self.controller.agent.info()['turn_id'], user_text=text, observation=observation)
