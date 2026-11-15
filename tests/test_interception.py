"""No real windows, retailers, mouse, microphone, speakers or hardware involved."""
import ctypes
from ctypes import wintypes
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from chuddy.destinations import LEAGUE_DESTINATION
from chuddy.interception import (ChromeLauncher, DESTINATIONS, RETAIL_HOME,
                                 InterceptionCoordinator, PreservingWindows)


class FakeWindows:
    def __init__(self):
        self.current = {"handle": 42, "pid": 8, "title": "Unsaved report"}
        self.minimized, self.restored = [], []
        self.pid = 8

    def foreground(self):
        return self.current

    def minimize(self, handle):
        self.minimized.append(handle)
        self.current = None

    def restore(self, handle):
        self.restored.append(handle)

    def identity(self, handle):
        return self.pid


class InterceptionTests(unittest.TestCase):
    def setUp(self):
        self.running, self.now = False, 100.
        self.windows, self.overlay, self.launcher = FakeWindows(), Mock(), Mock()
        self.league = Mock()
        self.league.open.return_value.reused = False
        self.league.open.return_value.confirm.return_value = dict(handle=20, pid=22, thread_id=24)
        self.local, self.cover, self.uncover, self.speak = Mock(), Mock(), Mock(), Mock()
        self.manager = InterceptionCoordinator(allowed=lambda: self.running,
            cooldown=lambda: 90, windows=self.windows, overlay=self.overlay,
            launcher=self.launcher, league_launcher=self.league, open_local=self.local, cover=self.cover,
            uncover=self.uncover, speak=self.speak, clock=lambda: self.now, delay=0,
            warning_seconds=0)
        self.work = dict(self.windows.current)

    def tearDown(self):
        self.manager.close()
        self.assertTrue(self.manager.wait())

    def request(self, **kwargs):
        return self.manager.request("Report", window=self.work, seen=self.now, **kwargs)

    def finish(self):
        self.assertTrue(self.manager.wait())

    def test_constructor_and_stopped_request_have_no_effects(self):
        self.assertIsNone(self.manager.info())
        self.assertFalse(self.request()["ok"])
        self.assertEqual(self.windows.minimized, [])
        for callback in (self.launcher.open, self.cover, self.local, self.speak, self.overlay.perform):
            callback.assert_not_called()

    def test_configured_destinations_and_serializable_phases(self):
        self.running = True
        for destination in DESTINATIONS:
            with self.subTest(destination=destination):
                self.assertTrue(self.request(destination=destination)["ok"])
                self.finish()
                self.assertEqual(self.manager.info()["phase"], "opened")
                self.assertEqual(self.manager.info()["destination"], destination)
                if destination in RETAIL_HOME:
                    self.launcher.open.assert_called_with(destination)
                elif destination == LEAGUE_DESTINATION:
                    self.league.open.assert_called_once_with(self.league.prepare.return_value)
                else:
                    self.local.assert_called_with(destination)
                self.manager.stop()
                self.now += 90
        self.assertEqual(self.launcher.open.call_count, 2)
        self.assertEqual(self.local.call_count, 2)

    def test_minimize_restores_owned_window_only_once(self):
        self.running = True
        self.assertTrue(self.request(mode="minimize")["ok"])
        self.finish()
        self.assertEqual(self.windows.minimized, [42])
        self.manager.stop()
        self.manager.stop()
        self.assertEqual(self.windows.restored, [42])
        self.uncover.assert_called_once()

    def test_reused_window_handle_is_not_restored(self):
        self.running = True
        self.request(mode="minimize")
        self.finish()
        self.windows.pid = 999
        self.manager.stop()
        self.assertEqual(self.windows.restored, [])

    def test_external_launch_does_not_cover_the_work_window(self):
        self.running = True
        self.request(destination="lcbo")
        self.finish()
        self.cover.assert_not_called()
        self.uncover.assert_not_called()
        self.launcher.open.assert_called_once_with("lcbo")
        self.assertEqual(self.windows.minimized, [])

    def test_preview_never_minimizes_or_launches_retailers(self):
        self.running = True
        self.manager.request("Deliberate preview", destination="beer_store", test=True, mode="minimize")
        self.finish()
        self.assertEqual(self.manager.info()["phase"], "preview")
        self.local.assert_called_once_with("game")
        self.launcher.open.assert_not_called()
        self.assertEqual(self.windows.minimized, [])

    def test_requires_fresh_focused_observation(self):
        self.running = True
        for seen in (None, True, float("nan"), 0., 101.):
            with self.subTest(seen=seen):
                self.assertFalse(self.manager.request("Report", window=self.work, seen=seen)["ok"])
        self.windows.current = {"handle": 42, "pid": 999}
        self.assertFalse(self.request()["ok"])
        self.windows.current = {"handle": 43, "pid": 8}
        self.assertFalse(self.request()["ok"])
        self.assertFalse(self.manager.request("No window", seen=self.now)["ok"])

    def test_browser_active_callback_is_required_at_effect_time(self):
        self.running = True
        active = [True]
        self.speak.side_effect = lambda text: active.__setitem__(0, False)
        self.assertTrue(self.request(active=lambda: active[0])["ok"])
        self.finish()
        self.assertIsNone(self.manager.info())
        self.local.assert_not_called()

    def test_focus_loss_during_choreography_abandons(self):
        self.running = True
        self.speak.side_effect = lambda text: setattr(self.windows, "current", None)
        self.request()
        self.finish()
        self.assertIsNone(self.manager.info())
        self.cover.assert_not_called()
        self.local.assert_not_called()

    def test_observation_expiry_during_choreography_abandons(self):
        self.running = True
        self.speak.side_effect = lambda text: setattr(self, "now", self.now+9)
        self.request(destination="lcbo")
        self.finish()
        self.launcher.open.assert_not_called()

    def test_stop_invalidates_waiting_and_late_worker(self):
        self.running = True
        spoken = threading.Event()
        self.manager.delay = 60
        self.speak.side_effect = lambda text: spoken.set()
        self.request(destination="lcbo", mode="minimize")
        self.assertTrue(spoken.wait(1))
        self.running = False
        self.manager.stop()
        self.finish()
        self.launcher.open.assert_not_called()
        self.assertEqual(self.windows.minimized, [])
        self.overlay.disarm.assert_called()

    def test_caller_cancel_before_request_and_during_wait(self):
        self.running = True
        cancellation = threading.Event()
        cancellation.set()
        self.assertFalse(self.request(cancel=cancellation)["ok"])
        cancellation.clear()
        self.speak.side_effect = lambda text: cancellation.set()
        self.assertTrue(self.request(cancel=cancellation)["ok"])
        self.finish()
        self.local.assert_not_called()

    def test_reentrant_stop_from_cover_prevents_launch_and_restores(self):
        self.running = True
        self.cover.side_effect = self.manager.stop
        self.request(destination="casino", mode="minimize")
        self.finish()
        self.launcher.open.assert_not_called()
        self.local.assert_not_called()
        self.assertEqual(self.windows.restored, [42])

    def test_reentrant_stop_from_overlay_prevents_speech(self):
        self.running = True
        self.overlay.perform.side_effect = self.manager.stop
        self.request()
        self.finish()
        self.speak.assert_not_called()
        self.local.assert_not_called()

    def test_session_expiry_prevents_effects(self):
        self.running = True
        self.speak.side_effect = lambda text: setattr(self, "running", False)
        self.request()
        self.finish()
        self.local.assert_not_called()
        self.assertIsNone(self.manager.info())

    def test_launch_failure_restores_work_and_reports_error(self):
        self.running = True
        self.launcher.open.side_effect = RuntimeError("missing Chrome")
        self.request(destination="beer_store", mode="minimize")
        self.finish()
        self.assertEqual(self.windows.restored, [42])
        self.assertEqual(self.manager.info()["phase"], "error")
        self.assertEqual(self.manager.info()["error"], "RuntimeError")
        self.local.assert_not_called()

    def test_cooldown_survives_dismiss_and_inflight_cannot_repeat(self):
        self.running = True
        self.request()
        self.finish()
        self.assertFalse(self.request()["ok"])
        self.manager.dismiss()
        self.assertFalse(self.request()["ok"])
        self.now += 90
        self.assertTrue(self.request()["ok"])

    def test_concurrent_requests_accept_only_one(self):
        self.running = True
        barrier, results = threading.Barrier(3), []
        def request():
            barrier.wait()
            results.append(self.request()["ok"])
        workers = [threading.Thread(target=request) for _ in range(2)]
        for worker in workers:
            worker.start()
        barrier.wait()
        for worker in workers:
            worker.join(1)
            self.assertFalse(worker.is_alive())
        self.assertEqual(sorted(results), [False, True])

    def test_invalid_destinations_and_mode_raise_before_effects(self):
        for destination in ("https://www.lcbo.com/en", "checkout", "LCBO", "game; calc.exe"):
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                self.request(destination=destination)
        with self.assertRaises(ValueError):
            self.request(mode="close")

    def test_closed_manager_stays_closed(self):
        self.running = True
        self.manager.close()
        self.assertFalse(self.request()["ok"])

    def test_failed_restore_is_reported_and_retryable(self):
        self.running = True
        self.request(mode="minimize")
        self.finish()
        self.windows.restore = Mock(side_effect=[RuntimeError("busy"), None])
        self.manager.stop()
        self.assertEqual(self.manager.diagnostics()["pending_restorations"], 1)
        self.assertEqual(self.manager.diagnostics()["restoration_errors"], ["RuntimeError"])
        self.now += 90
        self.assertFalse(self.request()["ok"])
        self.manager.stop()
        self.assertEqual(self.manager.diagnostics()["pending_restorations"], 0)

    def test_overlay_cleanup_failure_does_not_prevent_work_restoration(self):
        self.running = True
        self.request(mode="minimize")
        self.finish()
        self.overlay.disarm.side_effect = RuntimeError("overlay failed")
        self.manager.stop()
        self.assertEqual(self.windows.restored, [42])
        self.assertEqual(self.manager.diagnostics()["overlay_error"], "RuntimeError")


class WarningLifecycleTests(unittest.TestCase):
    """Real short waits, fake foreground/effects: no desktop or audio involved."""
    tearDown = InterceptionTests.tearDown
    request = InterceptionTests.request
    finish = InterceptionTests.finish

    def setUp(self):
        InterceptionTests.setUp(self)
        self.running = True
        self.manager.warning_seconds = .16
        self.warned = threading.Event()
        self.manager.on_warning = Mock(side_effect=lambda message, ticket: self.warned.set())
        self.manager.clear_warning = Mock()

    def begin(self, **kwargs):
        response = self.request(**kwargs)
        self.assertTrue(response['ok'])
        self.assertEqual(response['phase'], 'warning')
        self.assertTrue(self.warned.wait(1))
        return response

    def assert_no_effect(self):
        self.overlay.perform.assert_not_called()
        self.cover.assert_not_called()
        self.local.assert_not_called()
        self.launcher.open.assert_not_called()
        self.assertEqual(self.windows.minimized, [])

    def test_warning_exposes_message_and_countdown_before_any_motion_or_effect(self):
        response = self.begin(destination='casino', mode='minimize')
        self.assertIn('Report', response['warning_message'])
        self.assertIn('casino', response['warning_message'])
        self.assertGreater(response['warning_remaining'], 0)
        self.assertEqual(self.manager.info()['phase'], 'warning')
        self.now += .05
        self.assertAlmostEqual(self.manager.info()['warning_remaining'], .11)
        self.overlay.warning.assert_called_once_with(self.work, 'casino', response['warning_message'])
        self.assert_no_effect()
        self.finish()
        self.assertEqual(self.manager.info()['phase'], 'opened')
        self.assertEqual(self.manager.info()['warning_remaining'], 0)
        self.assertIsNone(self.manager.info()['warning_message'])
        self.assertEqual(self.windows.minimized, [42])
        self.manager.clear_warning.assert_called_once()

    def test_switching_away_during_grace_abandons_even_if_focus_later_returns(self):
        self.begin()
        self.windows.current = {'handle': 99, 'pid': 20, 'title': 'Other work'}
        # Wait for cancellation before returning to the original document. The
        # original grace period has not elapsed; an end-only check would miss it.
        self.finish()
        self.windows.current = dict(self.work)
        self.assertIsNone(self.manager.info())
        self.assert_no_effect()
        self.overlay.roam.assert_called_once()
        self.manager.clear_warning.assert_called_once()

    def test_same_window_document_title_change_cancels_grace(self):
        self.begin()
        self.windows.current = {**self.work, 'title': 'Different document'}
        self.finish()
        self.assertIsNone(self.manager.info())
        self.assert_no_effect()

    def test_same_handle_reused_by_another_process_cancels_grace(self):
        self.begin(mode='minimize')
        self.windows.current = {**self.work, 'pid': 999}
        self.finish()
        self.assertIsNone(self.manager.info())
        self.assert_no_effect()

    def test_live_identity_callback_allows_continuous_work_past_original_ttl(self):
        self.manager.on_warning.side_effect = lambda message, ticket: (
            setattr(self, 'now', self.now + 12), self.warned.set())
        self.begin(active=lambda: True, mode='minimize')
        self.finish()
        self.assertEqual(self.manager.info()['phase'], 'opened')
        self.assertEqual(self.windows.minimized, [42])

    def test_stale_browser_evidence_during_grace_cancels_without_motion(self):
        active = [True]
        self.begin(active=lambda: active[0])
        active[0] = False
        self.finish()
        self.assertIsNone(self.manager.info())
        self.assert_no_effect()

    def test_stop_and_caller_cancellation_during_warning_clear_without_effects(self):
        for operation in ('stop', 'caller', 'expiry'):
            with self.subTest(operation=operation):
                cancellation = threading.Event()
                self.warned.clear()
                self.begin(cancel=cancellation)
                if operation == 'stop':
                    self.running = False
                    self.manager.stop()
                elif operation == 'caller':
                    cancellation.set()
                else:
                    self.running = False
                self.finish()
                self.assertIsNone(self.manager.info())
                self.assert_no_effect()
                self.running = True
                self.now += 90

    def test_clear_warning_reentrant_stop_prevents_approach(self):
        self.manager.clear_warning.side_effect = self.manager.stop
        self.begin()
        self.finish()
        self.assertIsNone(self.manager.info())
        self.assert_no_effect()

    def test_warning_callback_reentrant_stop_cannot_resurrect_overlay(self):
        self.manager.on_warning.side_effect = lambda message, ticket: (self.warned.set(), self.manager.stop())
        self.begin()
        self.finish()
        self.assertIsNone(self.manager.info())
        self.assert_no_effect()
        self.overlay.disarm.assert_called_once()

    def test_warning_render_failure_reports_error_and_does_not_act(self):
        self.overlay.warning.side_effect = RuntimeError('fake warning render failure')
        self.assertTrue(self.request()['ok'])
        self.finish()
        self.assertEqual(self.manager.info()['phase'], 'error')
        self.assertEqual(self.manager.info()['error'], 'RuntimeError')
        self.assert_no_effect()
        self.manager.on_warning.assert_not_called()
        self.manager.clear_warning.assert_called_once()

    def test_unseen_or_failed_native_warning_never_advances_to_approach(self):
        self.manager.warning_render_timeout = .05
        for native in ({'visible': False, 'activity': 'warning', 'enabled': True, 'error': None},
                       {'visible': True, 'activity': 'roaming', 'enabled': True, 'error': None},
                       {'visible': True, 'activity': 'warning', 'enabled': True, 'error': 'DrawError'},
                       {'visible': True, 'activity': 'warning', 'enabled': False, 'error': None}):
            with self.subTest(native=native):
                self.warned.clear()
                self.overlay.info.return_value = native
                self.assertTrue(self.request()['ok'])
                self.finish()
                self.assertEqual(self.manager.info()['phase'], 'error')
                self.assertIn('No work was interrupted', self.manager.info()['error_detail'])
                self.assert_no_effect()
                self.manager.stop()
                self.now += 90

    def test_long_work_label_is_shortened_for_native_warning_only(self):
        label = 'Long document name ' * 10
        self.assertTrue(self.manager.request(label, window=self.work, seen=self.now,
                                            destination='beer_store')['ok'])
        self.assertTrue(self.warned.wait(1))
        info = self.manager.info()
        self.assertEqual(info['label'], label)
        self.assertLessEqual(len(info['warning_message']), 130)
        self.assertIn('The Beer Store', info['warning_message'])
        self.manager.stop()

    def test_deliberate_preview_bypasses_warning_and_remains_silent(self):
        self.assertTrue(self.manager.request('Preview', test=True, destination='lcbo', mode='minimize')['ok'])
        self.finish()
        self.assertEqual(self.manager.info()['phase'], 'preview')
        self.overlay.warning.assert_not_called()
        self.manager.on_warning.assert_not_called()
        self.speak.assert_not_called()
        self.assertEqual(self.windows.minimized, [])
        self.launcher.open.assert_not_called()


class ChromeLauncherTests(unittest.TestCase):
    def test_only_fixed_homepages_are_arguments_and_shell_is_disabled(self):
        with tempfile.TemporaryDirectory() as folder:
            executable = Path(folder)/"chrome.exe"
            executable.touch()
            spawn = Mock()
            launcher = ChromeLauncher(executable, spawn=spawn)
            for destination, url in RETAIL_HOME.items():
                launched = launcher.open(destination)
                self.assertIs(launched.process, spawn.return_value)
                args = spawn.call_args.args[0]
                self.assertEqual(args[0], str(executable))
                self.assertTrue(args[1].startswith("--user-data-dir="))
                self.assertIn("--start-fullscreen", args)
                self.assertEqual(args[-1], f"--app={url}")
                self.assertIs(spawn.call_args.kwargs["shell"], False)
            with self.assertRaises(ValueError):
                launcher.open("https://www.lcbo.com/en?checkout=true")

    def test_missing_chrome_has_no_browser_fallback(self):
        spawn = Mock()
        launcher = ChromeLauncher(Path("missing-folder")/"chrome.exe", spawn=spawn)
        with self.assertRaises(RuntimeError):
            launcher.open("lcbo")
        spawn.assert_not_called()


class NativePlacementTests(unittest.TestCase):
    def setUp(self):
        class Placement(ctypes.Structure):
            _fields_ = [("length", wintypes.UINT), ("flags", wintypes.UINT),
                        ("showCmd", wintypes.UINT), ("min", wintypes.POINT),
                        ("max", wintypes.POINT), ("normal", wintypes.RECT)]
        self.Placement = Placement
        self.pid, self.show = 22, 3
        def read(handle, pointer):
            pointer._obj.showCmd = self.show
            pointer._obj.normal = wintypes.RECT(10, 20, 1000, 800)
            return True
        self.user = SimpleNamespace(GetWindowPlacement=Mock(side_effect=read),
            ShowWindow=Mock(), SetWindowPlacement=Mock(return_value=True))
        self.user.SetWindowPlacement.argtypes = [wintypes.HWND, ctypes.POINTER(Placement)]
        self.adapter = PreservingWindows()
        self.adapter.identity = lambda handle: self.pid
        self.adapter._api = lambda: (ctypes, wintypes, self.user, Placement)

    @patch("chuddy.interception.os.name", "nt")
    def test_maximized_placement_preserved_and_restored_once(self):
        self.adapter.minimize(42)
        self.adapter.restore(42)
        self.adapter.restore(42)
        self.user.ShowWindow.assert_called_once_with(42, 6)
        placement = self.user.SetWindowPlacement.call_args.args[1].contents
        self.assertEqual(placement.showCmd, 3)
        self.assertEqual(placement.normal.left, 10)
        self.user.SetWindowPlacement.assert_called_once()

    @patch("chuddy.interception.os.name", "nt")
    def test_pid_reuse_and_preminimized_windows_are_not_changed(self):
        self.show = 2
        with self.assertRaises(RuntimeError):
            self.adapter.minimize(42)
        self.user.ShowWindow.assert_not_called()
        self.show = 3
        self.adapter.minimize(42)
        self.pid = 999
        self.adapter.restore(42)
        self.user.SetWindowPlacement.assert_not_called()

    @patch("chuddy.interception.os.name", "nt")
    def test_reused_owner_between_observation_and_native_action_is_rejected(self):
        with self.assertRaises(RuntimeError):
            self.adapter.minimize_work({"handle": 42, "pid": 999})
        self.user.ShowWindow.assert_not_called()


if __name__ == "__main__":
    unittest.main()
