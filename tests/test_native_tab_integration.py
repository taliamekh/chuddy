"""Native tab-close controller wiring: fake controls, no desktop operations."""
from concurrent.futures import Future
from unittest.mock import Mock
from tests.helpers import ControllerCase


class NativeTabIntegrationTests(ControllerCase):
    def setUp(self):
        super().setUp()
        self.window = {'handle':42,'pid':123,'process':'chrome.exe','title':'Assignment - Google Docs - Google Chrome'}
        from chuddy.controller import windows
        self.windows = windows
        windows.foreground.return_value = self.window
        self.native = self.controller.native_browser_tabs
        self.native.available = True
        self.controller.distractions.delay = 0
        self.overlay = Mock()
        self.controller.distractions.overlay = self.overlay
        self.controller.set_session('start')
        self.controller.poll_work()

    def test_no_extension_closes_selected_tab_before_opening_casino(self):
        self.assertIsNone(self.controller.browser_target)
        self.assertTrue(self.controller.interrupt('Google Docs'))
        self.assertTrue(self.native.requested.wait(1))
        self.assertEqual(self.covered, [])
        self.assertEqual(self.native.targets[0]['window'], self.window)
        self.assertEqual(self.native.targets[0]['kind'], 'native')
        self.assertEqual(self.overlay.perform.call_args.args[0]['tab_close_point'], [100,20])
        self.windows.foreground.return_value = {**self.window,'title':'Remaining tab'}
        self.native.close_future.set_result(True)
        self.assertTrue(self.controller.distractions.wait())
        self.assertEqual(self.controller.interruption['phase'], 'opened')
        self.assertEqual(self.controller.interruption['destination'], 'casino')
        self.assertTrue(self.controller.state()['casino_interruption_active'])
        self.assertEqual(self.covered, [True])
        self.windows.minimize.assert_not_called()
        self.now += 90
        self.assertTrue(self.controller.distractions.retire_completed())
        self.assertTrue(self.controller.state()['casino_interruption_active'])
        self.controller.set_session('stop')
        self.assertFalse(self.controller.state()['casino_interruption_active'])

    def test_native_preparation_waits_off_lock_before_any_approach(self):
        self.native.defer_prepare = True
        self.assertTrue(self.controller.interrupt('Google Docs'))
        self.assertTrue(self.native.prepared.wait(1))
        self.assertEqual(self.controller.interruption['phase'], 'preparing')
        self.overlay.perform.assert_not_called()
        self.assertTrue(self.controller.lock.acquire(timeout=.2))
        self.controller.lock.release()
        self.controller.set_session('pause')
        self.assertTrue(self.controller.distractions.wait())
        self.overlay.perform.assert_not_called()
        self.assertEqual(self.native.targets, [])
        self.assertEqual(self.controller.session, 'paused')
        self.assertEqual(self.covered, [])

    def test_pause_cancels_close_ack_and_prevents_late_casino(self):
        self.assertTrue(self.controller.interrupt('Google Docs'))
        self.assertTrue(self.native.requested.wait(1))
        self.controller.set_session('pause')
        self.assertTrue(self.controller.distractions.wait())
        self.assertTrue(self.native.close_future.done())
        self.assertFalse(self.native.close_future.result())
        self.assertIsNone(self.controller.interruption)
        self.assertEqual(self.covered, [])

    def test_missing_native_controls_never_starts_approach(self):
        self.native.defer_prepare = True
        self.assertTrue(self.controller.interrupt('Google Docs'))
        self.assertTrue(self.native.prepared.wait(1))
        self.native.prepare_future.set_exception(RuntimeError('Selected tab close control unavailable.'))
        self.assertTrue(self.controller.distractions.wait())
        self.overlay.perform.assert_not_called()
        self.assertEqual(self.controller.interruption['phase'], 'error')
        self.assertEqual(self.covered, [])
        self.assertEqual(self.controller.state()['cooldown_remaining'], 0)
        self.windows.foreground.return_value = {**self.window, 'process':'WINWORD.EXE', 'title':'Report - Word'}
        self.controller.poll_work()
        self.assertTrue(self.controller.interrupt('Word'))
        self.assertTrue(self.controller.distractions.wait())
        self.assertEqual(self.controller.interruption['phase'], 'opened')
        self.assertEqual(self.controller.state()['cooldown_remaining'], 90)

    def test_switching_to_a_protected_tab_cancels_before_close(self):
        self.native.defer_prepare = True
        self.assertTrue(self.controller.interrupt('Google Docs'))
        self.assertTrue(self.native.prepared.wait(1))
        self.windows.foreground.return_value = {**self.window, 'title':'ChatGPT - Google Chrome'}
        self.controller.poll_work()
        self.native.prepare_future.set_result({'kind':'native', 'window':self.window, 'close_point':[100,20]})
        self.assertTrue(self.controller.distractions.wait())
        self.assertEqual(self.controller.work_detection['reason'], 'protected_app')
        self.assertEqual(self.native.targets, [])
        self.assertEqual(self.covered, [])
