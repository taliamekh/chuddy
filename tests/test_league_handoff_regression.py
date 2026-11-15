"""Drive the real coordinator and League launcher through two mocked handoffs."""

from pathlib import Path
import time
import unittest
from unittest.mock import Mock, call, patch


from chuddy import windows
from chuddy.league import LeagueInstall, LeagueLauncher, RIOT_ARGS
from tests.helpers import ControllerCase


class RepeatHandoffRegressionTests(ControllerCase):
    def check_repeat(self, cooldown, vanish):
        self.controller.settings({'distraction_destination': 'league', 'cooldown': cooldown, 'ai_enabled': False})
        self.controller.set_session('start')
        manager = self.controller.distractions
        native = self.controller.native_browser_tabs
        native.available = True
        browser = {'handle': 42, 'pid': 79, 'thread_id': 51, 'process': 'chrome.exe',
                   'title': 'First fixture work tab'}
        windows.foreground.return_value = browser
        self.enterContext(patch.object(windows, 'last_input_tick', return_value=111))

        launcher_path = self.path / 'Riot Client' / 'RiotClientServices.exe'
        league_path = self.path / 'League of Legends' / 'LeagueClientUx.exe'
        for path in (launcher_path, league_path, league_path.with_name('LeagueClient.exe')):
            path.parent.mkdir(exist_ok=True)
            path.touch()
        installation = LeagueInstall.validate(launcher_path)
        self.assertIsNotNone(installation)
        old_frame = {'handle': 88, 'pid': 99, 'thread_id': 109, 'title': 'League of Legends',
                     'executable_path': str(league_path)}
        new_frame = dict(old_frame, handle=89)
        owner = {42: (79, 51), 55: (65, 75), 88: (99, 109), 89: (99, 109)}
        visible_frames = []
        focus = browser['handle']
        coverage = False
        ready_at = None
        minimize = False
        launches = []
        activated = []
        desktop = Mock()
        user = Mock()
        user.GetForegroundWindow.side_effect = lambda: focus
        user.IsIconic.side_effect = lambda _: minimize
        self.enterContext(patch.object(windows, '_user32', return_value=user))
        self.enterContext(patch.object(windows, '_window_owner', side_effect=lambda _, hwnd: owner.get(hwnd)))
        self.enterContext(patch.object(windows, '_transient', return_value=False))

        def raise_window(handle):
            nonlocal focus
            focus = handle
            activated.append(handle)
            return True

        user.SetForegroundWindow.side_effect = raise_window
        def window_list(*_):
            nonlocal focus, ready_at
            if ready_at is not None:
                if time.monotonic() < ready_at:
                    return []
                focus = visible_frames[0]['handle']
                ready_at = None
            return list(visible_frames)

        desktop.application_windows.side_effect = window_list
        desktop.foreground_handle.side_effect = lambda: focus
        desktop.window_owner.side_effect = owner.get
        desktop.last_input_tick.return_value = 112
        desktop.application_path.return_value = ''
        desktop.activate_application.side_effect = windows.activate_application
        def size_to_monitor(_):
            nonlocal coverage
            coverage = True
            return 'resize'

        desktop.fill_application_monitor.side_effect = size_to_monitor
        desktop.application_client_area.side_effect = lambda _: {'coverage': coverage}

        def mock_spawn(command, **options):
            nonlocal ready_at
            ready_at = time.monotonic() + .36
            self.assertEqual(command, [str(launcher_path), *RIOT_ARGS])
            self.assertFalse(options['shell'])
            launches.append(command)
            visible_frames[:] = [old_frame if len(launches) == 1 else new_frame]

        launcher = manager.league_launcher = LeagueLauncher(spawn=mock_spawn, desktop=desktop)
        self.enterContext(patch.object(launcher, 'find', return_value=installation))
        real_open = launcher.open

        def open_handoff(installed):
            result = real_open(installed)
            result.startup_timeout = 1.6
            result.layout_timeout = 0.12
            if result.reused and vanish:
                visible_frames.clear()
            return result

        opened = self.enterContext(patch.object(launcher, 'open', side_effect=open_handoff))

        def complete_ticket(number):
            native.requested.clear()
            self.controller.poll_work()
            self.assertTrue(self.controller.interrupt('Fixture work'))
            self.assertTrue(native.requested.wait(2))
            self.assertEqual(len(native.targets), number)
            self.assertFalse(self.controller.interrupt('Duplicate attempt'))
            native.close_future.set_result(True)
            self.assertTrue(manager.wait(3))
            return dict(self.controller.interruption)

        # A close is acknowledged while new generic input and unrelated
        # focus occur. Riot surfaces League later; the admitted ticket must
        # still verify it, without closing the newly exposed sibling tab.
        focus = 55
        first = complete_ticket(1)
        self.assertEqual(first['target_close'], 'confirmed')
        self.assertEqual(first['destination_delivery'], 'launched')
        self.assertEqual(first['destination_window'], 'league_client')
        self.assertTrue(first['destination_foreground'])
        self.assertEqual(first['fullscreen'], 'confirmed')
        self.assertEqual(first['phase'], 'opened')
        self.assertEqual(len(launches), 1)
        windows.foreground.return_value = dict(browser, title='Second fixture work tab')
        focus = browser['handle']
        minimize = not vanish
        coverage = False
        if cooldown:
            self.controller.poll_work()
            self.assertFalse(self.controller.interrupt('Cooldown still active'))
            self.assertEqual(len(native.targets), 1)
            self.now += cooldown

        second = complete_ticket(2)
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(second['target_close'], 'confirmed')
        self.assertEqual(second['destination_delivery'], 'launched' if vanish else 'reused')
        self.assertEqual(second['fullscreen'], 'confirmed')
        self.assertEqual(second['phase'], 'opened')
        self.assertEqual(opened.call_count, 2)
        self.assertEqual(len(launches), 2 if vanish else 1)
        self.assertEqual(activated, [] if vanish else [88])
        self.assertEqual(user.ShowWindowAsync.call_args_list, [] if vanish else [call(88, 9)])
        windows.request_close.assert_not_called()

    def test_cold_launch_then_second_ticket_restores_the_existing_minimized_league_window(self):
        self.check_repeat(cooldown=0, vanish=False)

    def test_cold_launch_then_cooldown_and_one_relaunch_after_reused_window_exits(self):
        self.check_repeat(cooldown=5, vanish=True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
