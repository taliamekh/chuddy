"""League policy, fixed launcher and cancellable native handoff fixtures."""

from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from chuddy import windows
from chuddy.league import LeagueInstall, LeagueLaunch, LeagueLauncher, RIOT_ARGS
from chuddy.work_recognition import evaluate
from tests.helpers import ControllerCase


class LeagueLauncherTests(unittest.TestCase):
    def setUp(self):
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        launcher = folder / "Riot Client" / "RiotClientServices.exe"
        game = folder / "League of Legends"
        for path in (launcher, game / "LeagueClient.exe", game / "LeagueClientUx.exe"):
            path.parent.mkdir(exist_ok=True)
            path.touch()
        self.install = LeagueInstall.validate(launcher)
        self.assertIsNotNone(self.install)
        self.desktop = Mock()
        self.desktop.application_windows.return_value = []
        self.spawn = Mock()
        self.launcher = LeagueLauncher(spawn=self.spawn, desktop=self.desktop)
        self.enterContext(patch.object(self.launcher, "find", return_value=self.install))

    def window(self, binary, title, handle=2):
        paths = self.install.application_paths()
        executable, = [path for path in paths if Path(path).name.lower() == binary.lower()]
        return {"handle": handle, "pid": handle + 10, "thread_id": handle + 20,
                "executable_path": executable, "title": title}

    def test_launch_uses_fixed_executable_product_and_patchline(self):
        self.assertEqual(self.launcher.prepare(), self.install)
        self.assertFalse(self.launcher.open(self.install).reused)
        command = self.spawn.call_args.args[0]
        self.assertEqual(command, [str(self.install.launcher), *RIOT_ARGS])
        self.assertIs(self.spawn.call_args.kwargs["shell"], False)
        self.assertEqual(self.spawn.call_args.kwargs["cwd"], self.install.launcher.parent)
        self.spawn.assert_called_once()

    def test_reuse_exact_landing_client_without_a_second_launch(self):
        self.desktop.application_windows.return_value = [self.window("LeagueClientUx.exe", "League of Legends")]
        self.assertTrue(self.launcher.open(self.install).reused)
        self.spawn.assert_not_called()

    def test_riot_only_and_tray_only_each_receive_one_product_dispatch_per_ticket(self):
        self.desktop.application_windows.return_value = [self.window("Riot Client.exe", "Riot Client")]
        first = self.launcher.open(self.install)
        first.dispatch_product()
        self.spawn.assert_called_once()
        self.desktop.application_windows.return_value = []
        second = self.launcher.open(self.install)
        second.dispatch_product()
        self.assertEqual(self.spawn.call_count, 2)
        self.assertIsNot(first, second)

    def test_minimized_landing_is_restored_with_the_current_focus_owner(self):
        frame = self.window("LeagueClientUx.exe", "League of Legends")
        user = Mock()
        user.GetForegroundWindow.return_value = 1
        user.IsIconic.return_value = True
        with patch.object(windows, "_user32", return_value=user), patch.object(
                windows, "_window_owner", side_effect=lambda _, handle: (handle + 10, handle + 20)), patch.object(
                windows, "_transient", return_value=False):
            self.assertTrue(windows.activate_application(frame, (1, 11, 21)))
            self.assertFalse(windows.activate_application(frame, (3, 13, 23)))
        user.ShowWindowAsync.assert_called_once_with(2, 9)
        user.SetForegroundWindow.assert_called_once_with(2)

    def test_match_blocks_dispatch_and_installed_gaming_windows_are_excluded(self):
        self.desktop.application_windows.return_value = [self.window("League of Legends.exe", "Game")]
        with self.assertRaisesRegex(RuntimeError, "match"):
            self.launcher.prepare()
        self.spawn.assert_not_called()
        for executable in ("LeagueClient.exe", "LeagueClientUx.exe", "RiotClientUx.exe",
                           "Riot Client.exe", "League of Legends.exe"):
            result = evaluate({"handle": 1, "pid": 2, "process": executable, "title": "Report"}, [])
            self.assertEqual(result["reason"], "excluded_gaming")

    def stage(self, frame, *, initially_focused=False, allow_focus=True, resize=True):
        self.desktop.application_windows.return_value = [frame]
        keys = {1: (11, 21), frame["handle"]: (frame["pid"], frame["thread_id"])}
        self.desktop.window_owner.side_effect = keys.get
        handle = frame["handle"] if initially_focused else 1
        self.desktop.foreground_handle.side_effect = lambda: handle
        self.desktop.application_path.return_value = ''
        full = False
        tick = 0

        def activation(*_):
            nonlocal handle
            if allow_focus:
                handle = frame["handle"]

        def layout(_):
            nonlocal full
            full = resize
            return "resize"

        def wait(_):
            nonlocal tick
            tick += 1

        self.desktop.activate_application.side_effect = activation
        self.desktop.fill_application_monitor.side_effect = layout
        self.desktop.application_client_area.side_effect = lambda _: {"coverage": full}
        self.stop = Mock()
        self.stop.wait.side_effect = wait
        self.launch = LeagueLaunch(self.install, self.desktop, clock=lambda: tick,
                                   startup_timeout=4, layout_timeout=1)
        self.report = Mock()

    def confirm(self, live=lambda: True, *, input_at_close=None):
        return self.launch.confirm(live, self.stop, threading.RLock(), (1, 11, 21), self.report,
                                   input_at_close=input_at_close)

    def test_delayed_focus_and_monitor_layout_are_measured_before_success(self):
        frame = self.window("LeagueClientUx.exe", "League of Legends")
        self.stage(frame)
        self.assertEqual(self.confirm(), frame)
        self.desktop.activate_application.assert_called_once_with(frame, (1, 11, 21))
        self.desktop.fill_application_monitor.assert_called_once_with(frame)
        self.report.assert_called_with("league_client", True, True, "resize")

    def test_windowed_landing_stays_open_with_honest_geometry_failure(self):
        frame = self.window("LeagueClientUx.exe", "League of Legends")
        self.stage(frame, resize=False)
        with self.assertRaisesRegex(RuntimeError, "does not cover the monitor"):
            self.confirm()
        self.report.assert_called_with("league_client", True, False, "resize")

    def test_riot_sign_in_window_and_denied_league_focus_are_different(self):
        riot = self.window("Riot Client.exe", "Riot Client")
        self.stage(riot)
        with self.assertRaisesRegex(RuntimeError, "sign-in or an update"):
            self.confirm()
        self.desktop.fill_application_monitor.assert_not_called()
        league = self.window("LeagueClientUx.exe", "League of Legends")
        self.stage(league, allow_focus=False)
        with self.assertRaisesRegex(RuntimeError, "landing window in front"):
            self.confirm()
        self.desktop.fill_application_monitor.assert_not_called()

    def test_startup_window_between_samples_preserves_authority_without_touching_it(self):
        frame = self.window("LeagueClientUx.exe", "League of Legends")
        self.stage(frame)
        splash = self.window("LeagueClient.exe", "Splash", handle=8)
        self.desktop.application_path.return_value = splash["executable_path"]
        self.desktop.foreground_handle.side_effect = [8, 2, 2, 2]
        self.desktop.window_owner.side_effect = {8: (18, 28), 2: (12, 22)}.get
        self.assertEqual(self.confirm(), frame)
        self.desktop.activate_application.assert_called_once_with(frame, (8, 18, 28))
        self.desktop.fill_application_monitor.assert_called_once_with(frame)

    def test_stop_after_enumeration_prevents_any_focus_or_layout_action(self):
        frame = self.window("LeagueClientUx.exe", "League of Legends")
        self.stage(frame)
        live = Mock(side_effect=[True, False])
        self.assertIsNone(self.confirm(live))
        self.desktop.activate_application.assert_not_called()
        self.desktop.fill_application_monitor.assert_not_called()

    def test_switching_to_unrelated_focus_prevents_any_action(self):
        frame = self.window("LeagueClientUx.exe", "League of Legends")
        self.stage(frame)
        self.desktop.foreground_handle.side_effect = None
        self.desktop.foreground_handle.return_value = 11
        self.desktop.window_owner.side_effect = None
        self.desktop.window_owner.return_value = (21, 31)
        self.desktop.last_input_tick.return_value = 11
        with self.assertRaisesRegex(RuntimeError, "Focus moved away"):
            self.confirm(input_at_close=10)
        self.desktop.activate_application.assert_not_called()
        self.desktop.fill_application_monitor.assert_not_called()

    def test_verified_close_can_settle_to_another_exposed_window_without_new_input(self):
        frame = self.window("LeagueClientUx.exe", "League of Legends")
        self.stage(frame)
        now, focus = 0., 8
        self.launch.clock = lambda: now
        self.desktop.last_input_tick.return_value = 10
        self.desktop.foreground_handle.side_effect = lambda: focus
        self.desktop.window_owner.side_effect = {8: (18, 28), 2: (12, 22)}.get

        def wait(_):
            nonlocal now
            self.desktop.activate_application.assert_not_called()
            now += .12

        def activate(window, source):
            nonlocal focus
            self.assertGreaterEqual(now, .18)
            self.assertEqual(source, (8, 18, 28))
            focus = window["handle"]
            self.stop.wait.side_effect = lambda _: None

        self.stop.wait.side_effect = wait
        self.desktop.activate_application.side_effect = activate
        self.assertEqual(self.confirm(input_at_close=10), frame)
        self.desktop.activate_application.assert_called_once_with(frame, (8, 18, 28))
        self.report.assert_called_with("league_client", True, True, "resize")

    def test_late_unrelated_focus_is_passive_until_league_itself_comes_forward(self):
        frame = self.window("LeagueClientUx.exe", "League of Legends")
        self.stage(frame)
        now = 0
        self.launch.clock = lambda: now
        self.desktop.last_input_tick.return_value = 10
        self.desktop.foreground_handle.side_effect = lambda: 1 if now == 0 else (8 if now == 1 else 2)
        self.desktop.window_owner.side_effect = {1: (11, 21), 8: (18, 28), 2: (12, 22)}.get
        self.desktop.application_windows.side_effect = lambda _paths: [] if now == 0 else [frame]

        def wait(_):
            nonlocal now
            now += 1

        self.stop.wait.side_effect = wait
        self.assertEqual(self.confirm(input_at_close=10), frame)
        self.desktop.activate_application.assert_not_called()
        self.desktop.fill_application_monitor.assert_called_once_with(frame)

    def test_vanished_reused_client_falls_back_to_one_fixed_product_launch(self):
        stale = self.window("LeagueClientUx.exe", "League of Legends")
        fresh = self.window("LeagueClientUx.exe", "League of Legends", handle=6)
        self.stage(stale)
        self.desktop.application_windows.return_value = [stale]
        self.launch = self.launcher.open(self.install)
        self.assertTrue(self.launch.reused)
        self.spawn.assert_not_called()

        now, focus = 0., 1
        self.launch.clock = lambda: now
        self.desktop.application_windows.side_effect = lambda _: [fresh] if self.spawn.called else []
        self.desktop.window_owner.side_effect = {1: (11, 21), 6: (16, 26)}.get
        self.desktop.foreground_handle.side_effect = lambda: focus

        def wait(_):
            nonlocal now
            now += .12

        def activate(window, _focus):
            nonlocal focus
            focus = window["handle"]

        self.stop.wait.side_effect = wait
        self.desktop.activate_application.side_effect = activate
        self.assertEqual(self.confirm(), fresh)
        self.assertFalse(self.launch.reused)
        self.spawn.assert_called_once()
        self.assertEqual(self.spawn.call_args.args[0], [str(self.install.launcher), *RIOT_ARGS])

    def test_stop_prevents_fallback_when_reused_client_exits(self):
        frame = self.window("LeagueClientUx.exe", "League of Legends")
        self.stage(frame)
        self.launch = self.launcher.open(self.install)
        self.desktop.application_windows.return_value = []
        stop = threading.Event()
        self.stop.wait.side_effect = lambda _: stop.set()
        self.assertIsNone(self.confirm(lambda: not stop.is_set()))
        self.spawn.assert_not_called()
        self.desktop.activate_application.assert_not_called()

    def test_match_appearing_before_fallback_prevents_a_product_launch(self):
        frame = self.window("LeagueClientUx.exe", "League of Legends")
        match = self.window("League of Legends.exe", "Game", handle=7)
        self.stage(frame)
        self.launch = self.launcher.open(self.install)
        self.desktop.application_windows.return_value = [match]
        tick = 0
        self.launch.clock = lambda: tick

        def wait(_):
            nonlocal tick
            tick += .12

        self.stop.wait.side_effect = wait
        with self.assertRaisesRegex(RuntimeError, "match"):
            self.confirm()
        self.spawn.assert_not_called()
        self.desktop.activate_application.assert_not_called()
        self.report.assert_called_with("match", False, None, None)


class LeagueCoordinatorTests(ControllerCase):
    def setUp(self):
        super().setUp()
        self.controller.settings({"distraction_destination": "league", "cooldown": 0, "ai_enabled": False})
        self.manager = self.controller.distractions
        self.manager.delay = 0
        self.controller.set_session("start")
        self.window = {"handle": 42, "pid": 79, "thread_id": 51, "process": "olk.exe",
                       "title": "Mail", "executable_path": "fixture/olk.exe"}
        windows.foreground.return_value = self.window
        self.league = self.manager.league_launcher = Mock()

    def test_missing_install_protects_the_original_window_and_preview_is_local(self):
        self.league.prepare.side_effect = RuntimeError("A supported local Riot and League installation could not be verified.")
        self.controller.poll_work()
        self.assertTrue(self.controller.interrupt("Mail"))
        self.assertTrue(self.manager.wait())
        self.assertEqual(self.controller.interruption["phase"], "error")
        windows.request_close.assert_not_called()
        self.league.open.assert_not_called()

        self.assertTrue(self.manager.retire_completed())
        self.league.reset_mock()
        self.assertTrue(self.controller.interrupt("Rehearsal", test=True))
        self.assertTrue(self.manager.wait())
        self.assertEqual(self.controller.interruption["phase"], "preview")
        self.league.prepare.assert_not_called()
        self.league.open.assert_not_called()

    def test_configured_league_uses_one_close_then_one_open_and_no_local_route(self):
        handoff = self.league.open.return_value
        handoff.reused = False

        def confirm(live, _stop, lock, exposed, report, *, input_at_close=None):
            windows.request_close.assert_called_once_with(self.window)
            self.assertEqual(exposed, (42, 79, 51))
            with lock:
                self.assertTrue(live())
                report("league_client", True, True, "resize")
            return {"handle": 300, "pid": 93, "thread_id": 96}

        handoff.confirm.side_effect = confirm
        self.controller.poll_work()
        self.assertTrue(self.controller.interrupt("Mail"))
        self.assertTrue(self.manager.wait())
        state = self.controller.state()
        self.assertEqual(state["settings"]["distraction_destination"], "league")
        self.assertEqual(state["interruption"]["fullscreen"], "confirmed")
        self.assertEqual(state["interruption"]["banter_message"], "Wanna play League?")
        self.assertIsNone(state["casino_opening_id"])
        self.league.open.assert_called_once_with(self.league.prepare.return_value)

    def repeat_after_windowed_handoff(self, cooldown):
        self.store.setting("cooldown", cooldown)
        browser = dict(self.window, process="chrome.exe", title="Sample browser tab")
        windows.foreground.return_value = browser
        self.controller.native_browser_tabs.available = True
        native = self.controller.native_browser_tabs
        handoff = self.league.open.return_value
        handoff.reused = False

        def confirm(live, _stop, lock, exposed, report, *, input_at_close=None):
            with lock:
                self.assertTrue(live())
                self.assertEqual(input_at_close, 71)
                self.assertEqual(exposed, (42, 79, 51))
                if self.league.open.call_count == 1:
                    report("league_client", True, False, "resize")
                    raise RuntimeError("League is in front, but its landing window does not cover the monitor.")
                handoff.reused = True
                report("league_client", True, True, None)
                return {"handle": 300, "pid": 93, "thread_id": 96}

        def finish_tab(number):
            native.requested.clear()
            self.controller.poll_work()
            self.assertTrue(self.controller.interrupt("Sample browser tab"))
            self.assertTrue(native.requested.wait(2))
            self.assertEqual(len(native.targets), number)
            self.assertFalse(self.controller.interrupt("Duplicate attempt"))
            native.close_future.set_result(True)
            self.assertTrue(self.manager.wait())
            return self.controller.interruption

        handoff.confirm.side_effect = confirm
        with patch.object(windows, "last_input_tick", return_value=71):
            first = finish_tab(1)
            self.assertEqual((first["target_close"], first["phase"], first["fullscreen"]),
                             ("confirmed", "error", "failed"))
            # The watcher never sampled League before the user returned to
            # the same browser frame. The handoff did verify its foreground.
            windows.foreground.return_value = dict(browser, title="A different browser tab")
            if cooldown:
                self.controller.poll_work()
                self.assertFalse(self.controller.interrupt("Before cooldown"))
                self.assertEqual(len(native.targets), 1)
                self.now += cooldown
            second = finish_tab(2)
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual((second["target_close"], second["phase"], second["fullscreen"],
                          second["destination_delivery"]), ("confirmed", "opened", "confirmed", "reused"))
        self.assertEqual(self.league.open.call_count, 2)
        windows.request_close.assert_not_called()

    def test_same_browser_rearms_after_windowed_league_at_zero_cooldown(self):
        self.repeat_after_windowed_handoff(0)

    def test_same_browser_rearms_only_after_configured_cooldown(self):
        self.repeat_after_windowed_handoff(5)
