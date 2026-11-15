"""Async native effects must complete before success and must not block Stop."""
from concurrent.futures import Future
import threading
import time
import unittest
from unittest.mock import Mock

from chuddy.interception import InterceptionCoordinator
from tests.test_interception import FakeWindows
from tests.helpers import ControllerCase


class NativeEffectCompletionTests(unittest.TestCase):
    def setUp(self):
        self.lock = threading.RLock()
        self.future = Future()
        self.submitted = threading.Event()
        self.local = Mock()
        self.uncover = Mock()
        self.manager = InterceptionCoordinator(allowed=lambda: True, cooldown=lambda: 0,
            lock=self.lock, windows=FakeWindows(), cover=self.cover, uncover=self.uncover,
            open_local=self.local, delay=0, warning_seconds=0)

    def cover(self):
        self.submitted.set()
        return self.future

    def begin(self):
        self.assertTrue(self.manager.request('Preview', test=True)['ok'])
        self.assertTrue(self.submitted.wait(1))

    def tearDown(self):
        self.manager.close()
        self.assertTrue(self.manager.wait(1))

    def test_native_callback_can_acquire_controller_lock_before_completion(self):
        self.begin()
        self.assertTrue(self.lock.acquire(timeout=.1), 'Native wait held the controller lock')
        self.lock.release()
        self.assertEqual(self.manager.info()['phase'], 'clicking')
        self.local.assert_not_called()
        self.future.set_result(True)
        self.assertTrue(self.manager.wait(1))
        self.assertEqual(self.manager.info()['phase'], 'preview')

    def test_stop_during_pending_native_cover_prevents_late_open(self):
        self.begin()
        self.manager.stop()
        self.future.set_result(True)
        self.assertTrue(self.manager.wait(1))
        self.local.assert_not_called()
        self.assertIsNone(self.manager.info())
        self.uncover.assert_called_once()

    def test_native_failure_is_error_without_opened_claim(self):
        self.begin()
        self.future.set_exception(RuntimeError('fake native dispatch failure'))
        self.assertTrue(self.manager.wait(1))
        self.assertEqual(self.manager.info()['phase'], 'error')
        self.local.assert_not_called()

    def test_cancelled_native_dispatch_abandons_without_opened_claim(self):
        self.begin()
        self.future.set_result(False)
        self.assertTrue(self.manager.wait(1))
        self.assertIsNone(self.manager.info())
        self.local.assert_not_called()

    def test_pending_uncover_remains_reported_until_native_acknowledgement(self):
        self.begin()
        self.future.set_result(True)
        self.assertTrue(self.manager.wait(1))
        restored = Future()
        self.uncover.return_value = restored
        self.manager.stop()
        self.assertTrue(self.manager.diagnostics()['covered'])
        restored.set_result(True)
        self.assertFalse(self.manager.diagnostics()['covered'])

    def test_full_warning_grace_starts_after_delayed_native_frame(self):
        overlay = Mock()
        native = {'visible': False, 'enabled': True, 'error': None, 'activity': 'roaming',
                  'request_id': 1, 'rendered_request_id': 1}
        overlay.info.side_effect = lambda: dict(native)
        overlay.warning.side_effect = lambda *args: native.update(request_id=2)
        acknowledged = threading.Event()
        times = {}
        self.manager.on_warning = lambda *args: (times.update(ack=time.monotonic()), acknowledged.set())
        overlay.perform.side_effect = lambda *args: times.update(approach=time.monotonic())
        self.manager.overlay = overlay
        self.manager.warning_seconds = .12
        self.manager.warning_render_timeout = .8
        self.future.set_result(True)
        window = dict(self.manager.windows.current)
        self.assertTrue(self.manager.request('Report', window=window, seen=time.monotonic(), active=lambda: True)['ok'])
        time.sleep(.18)  # Render is delayed longer than the whole configured grace.
        self.assertFalse(acknowledged.is_set())
        self.local.assert_not_called()
        native.update(visible=True, activity='warning', rendered_request_id=2)
        self.assertTrue(acknowledged.wait(1))
        self.assertGreater(self.manager.info()['warning_remaining'], .08)
        self.assertTrue(self.manager.wait(1))
        self.assertGreaterEqual(times['approach']-times['ack'], .11)


class ControllerCleanupFutureTests(ControllerCase):
    def test_stop_queues_one_uncover_without_invalidating_its_future(self):
        future = Future()
        future.set_result(True)
        self.controller.cover = Mock(return_value=future)
        cleanup = Future()
        self.controller.uncover = Mock(return_value=cleanup)
        self.controller.distractions.delay = 0
        self.controller.set_session('start')
        self.assertTrue(self.controller.interrupt('Preview', test=True))
        self.assertTrue(self.controller.distractions.wait(1))
        self.controller.set_session('stop')
        self.controller.uncover.assert_called_once()
        cleanup.set_result(True)
        self.assertFalse(self.controller.distractions.diagnostics()['covered'])
