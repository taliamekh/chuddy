"""One-close handoff regressions with fake windows, launchers and shells."""
from concurrent.futures import Future
import threading
import unittest
from unittest.mock import Mock, patch

from chuddy import windows
from chuddy.interception import ChromeLaunch
from tests.helpers import ControllerCase


class DesktopSwitchTests(ControllerCase):
    def setUp(self):
        super().setUp()
        self.store.update_settings({'rules': [], 'ai_enabled': False, 'cooldown': 0})
        self.controller.distractions.delay = 0
        self.controller.set_session('start')

    def foreground(self, process='AccountsClient.exe', title='Inbox', **extra):
        window = {'handle': 92, 'pid': 701, 'thread_id': 702, 'process': process,
                  'title': title, 'executable_path': 'fixture/' + process, **extra}
        windows.foreground.return_value = window
        return window

    def test_generic_desktop_and_outlook_like_apps_post_once_before_handoff(self):
        for process in ('AccountsClient.exe', 'olk.exe'):
            with self.subTest(process=process):
                window = self.foreground(process)
                sent = threading.Event()
                shell = Future()
                self.controller.cover = Mock(side_effect=lambda: (sent.set(), shell)[1])
                self.controller.poll_work()
                self.assertTrue(self.controller.interrupt('Foreground activity'))
                self.assertTrue(sent.wait(1))
                windows.request_close.assert_called_once_with(window)
                self.assertEqual(self.controller.interruption['target_close'], 'confirmed')
                self.assertFalse(self.controller.state()['casino_interruption_active'])
                shell.set_result(True)
                self.assertTrue(self.controller.distractions.wait())
                self.assertEqual(self.controller.interruption['phase'], 'opened')
                for _ in range(3):
                    self.now += 1
                    self.controller.poll_work()
                windows.request_close.assert_called_once_with(window)
                self.controller.cover.assert_called_once_with()
                windows.minimize.assert_not_called()
                self.controller.set_session('stop')
                self.controller.set_session('start')
                windows.request_close.reset_mock()

    def test_unsaved_prompt_or_cancel_leave_destination_unopened_and_do_not_retry(self):
        window = self.foreground()
        prompt = self.foreground(title='Save your work?', is_transient=True)
        windows.foreground.return_value = window
        def waiting(_):
            windows.foreground.return_value = prompt
            raise RuntimeError('The app is waiting for a response to its own prompt.')
        windows.close_status.side_effect = waiting
        self.controller.poll_work()
        self.assertTrue(self.controller.interrupt('Inbox'))
        self.assertTrue(self.controller.distractions.wait())
        self.assertEqual(self.controller.interruption['phase'], 'error')
        self.assertEqual(self.controller.interruption['target_close'], 'requested')
        self.assertIn('own prompt', self.controller.interruption['error_detail'])
        self.controller.poll_work()
        # User dismisses the prompt; Chuddy must not reopen it on the next poll.
        windows.foreground.return_value = window
        self.now += 4
        self.controller.poll_work()
        self.assertIsNone(self.controller.pending_work)
        windows.request_close.assert_called_once_with(window)
        self.assertEqual(self.covered, [])

    def test_exposed_window_is_held_even_when_the_close_cannot_be_acknowledged(self):
        work = self.foreground()
        exposed = dict(work, handle=93, title='Other document')
        def unconfirmed(_):
            windows.foreground.return_value = exposed
            raise RuntimeError('Could not verify the close.')
        windows.close_status.side_effect = unconfirmed
        self.controller.poll_work()
        self.assertTrue(self.controller.interrupt('Inbox'))
        self.assertTrue(self.controller.distractions.wait())
        self.assertEqual(self.controller.interruption['phase'], 'error')
        for _ in range(3):
            self.now += 1
            self.controller.poll_work()
            self.assertIsNone(self.controller.pending_work)
        windows.request_close.assert_called_once_with(work)
        self.assertEqual(self.covered, [])

    def test_pause_while_window_close_is_pending_cancels_late_handoff(self):
        self.foreground()
        entered = threading.Event()
        windows.close_status.side_effect = lambda _: (entered.set(), 'pending')[1]
        self.controller.poll_work()
        self.assertTrue(self.controller.interrupt('Inbox'))
        self.assertTrue(entered.wait(1))
        self.controller.set_session('pause')
        windows.close_status.side_effect = None
        self.assertTrue(self.controller.distractions.wait())
        windows.request_close.assert_called_once()
        self.assertIsNone(self.controller.interruption)
        self.assertEqual(self.covered, [])

    def test_game_companion_disabled_and_unverifiable_windows_never_request_close(self):
        self.store.setting('rules', [{'kind': 'process', 'pattern': 'excluded.exe', 'enabled': False}])
        for process, title, extra in (
                ('Fixture-Win64-Shipping.exe', 'Mystery', {}),
                ('chuddy.exe', 'Chuddy', {}),
                ('Discord.exe', 'Channel', {}),
                ('excluded.exe', 'Work', {}),
                ('', 'Unverifiable', {}),
                ('Host.exe', 'Open file dialog', {'is_transient': True})):
            with self.subTest(process=process, title=title):
                self.foreground(process, title, **extra)
                self.controller.poll_work()
                self.assertIsNone(self.controller.pending_work)
        windows.request_close.assert_not_called()

    def test_missing_native_shell_leaves_work_intact(self):
        self.foreground()
        self.controller.cover = None
        self.controller.poll_work()
        self.assertTrue(self.controller.interrupt('Inbox'))
        self.assertTrue(self.controller.distractions.wait())
        self.assertEqual(self.controller.interruption['phase'], 'error')
        windows.request_close.assert_not_called()

    def test_external_launch_is_protected_even_if_fullscreen_confirmation_fails(self):
        work = self.foreground()
        process = Mock(pid=888)
        process.poll.return_value = None
        destination = {'handle': 601, 'pid': 888, 'thread_id': 602, 'process': 'chrome.exe',
                       'title': 'Local destination', 'executable_path': 'fixture/chrome.exe'}
        launcher = self.controller.distractions.launcher = Mock()
        launch = ChromeLaunch(process, 'fixture/chrome.exe')
        launcher.open.return_value = launch

        def denied(_):
            windows.foreground.return_value = destination
            raise RuntimeError('Chrome did not confirm fullscreen.')

        with patch.object(launch, 'confirm', side_effect=denied):
            self.controller.poll_work()
            self.assertTrue(self.controller.interrupt('Inbox', destination='lcbo'))
            self.assertTrue(self.controller.distractions.wait())
        self.assertEqual(self.controller.interruption['target_close'], 'confirmed')
        self.assertEqual(self.controller.interruption['destination_delivery'], 'launched')
        self.assertEqual(self.controller.interruption['fullscreen'], 'failed')
        self.assertTrue(self.controller.distractions.is_destination_window(destination))
        self.assertFalse(self.controller.distractions.is_destination_window(dict(destination, pid=889)))
        for _ in range(3):
            self.controller.poll_work()
            self.assertIsNone(self.controller.pending_work)
        launcher.open.assert_called_once_with('lcbo')
        windows.request_close.assert_called_once_with(work)
        process.poll.return_value = 0
        self.assertFalse(self.controller.distractions.is_destination_window(destination))


class WindowsCloseTests(unittest.TestCase):
    def setUp(self):
        self.window = {'handle': 92, 'pid': 701, 'thread_id': 702, 'process': 'olk.exe',
                       'title': 'Inbox', 'executable_path': 'fixture/olk.exe'}
        self.user = Mock()
        self.user.GetForegroundWindow.return_value = 92
        self.user.GetLastActivePopup.return_value = 92
        self.owner = self.enterContext(patch.object(windows, '_window_owner', return_value=(701, 702)))
        self.enterContext(patch.object(windows, '_user32', return_value=self.user))
        self.enterContext(patch.object(windows, '_transient', return_value=False))
        self.foreground = self.enterContext(patch.object(windows, 'foreground', return_value=self.window))

    def test_exact_native_window_gets_one_wm_close_then_destruction_is_confirmed(self):
        windows.request_close(self.window)
        self.user.PostMessageW.assert_called_once_with(92, 0x10, 0, 0)
        self.assertEqual(windows.close_status(self.window), 'pending')
        self.owner.return_value = None
        self.assertEqual(windows.close_status(self.window), 'closed')

    def test_foreground_change_or_owned_popup_prevents_posting(self):
        self.foreground.return_value = dict(self.window, thread_id=999)
        with self.assertRaisesRegex(RuntimeError, 'could not be verified'):
            windows.request_close(self.window)
        self.foreground.return_value = self.window
        self.user.GetLastActivePopup.return_value = 93
        with self.assertRaisesRegex(RuntimeError, 'open prompt'):
            windows.request_close(self.window)
        self.user.PostMessageW.assert_not_called()

    def test_prompt_or_owner_reuse_cannot_acknowledge_close(self):
        self.user.GetLastActivePopup.return_value = 93
        with self.assertRaisesRegex(RuntimeError, 'own prompt'):
            windows.close_status(self.window)
        self.owner.return_value = (701, 777)
        with self.assertRaisesRegex(RuntimeError, 'identity'):
            windows.close_status(self.window)


class ChromeHandoffTests(unittest.TestCase):
    def test_only_the_spawned_foreground_chrome_in_fullscreen_confirms_launch(self):
        process = Mock(pid=700)
        process.poll.return_value = None
        window = {'handle': 90, 'pid': 700, 'executable_path': 'fixture/chrome.exe'}
        with patch.object(windows, 'foreground', side_effect=[dict(window, pid=800), window, window]), \
             patch.object(windows, 'foreground_fullscreen', side_effect=[False, True]) as verify:
            self.assertEqual(ChromeLaunch(process, 'fixture/chrome.exe').confirm(lambda: True), window)
        self.assertEqual(verify.call_count, 2)

    def test_cancelled_browser_confirmation_is_inert(self):
        process = Mock(pid=700)
        with patch.object(windows, 'foreground') as check:
            self.assertIsNone(ChromeLaunch(process, 'fixture/chrome.exe').confirm(lambda: False))
        check.assert_not_called()
