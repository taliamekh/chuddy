"""Repeated fresh work interceptions without a Return-to-work interface."""
from concurrent.futures import Future
import threading
import unittest
from unittest.mock import Mock

from chuddy.interception import InterceptionCoordinator
from tests.test_interception import FakeWindows


class InterceptionRetirementTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.
        self.windows = FakeWindows()
        self.overlay = Mock()
        self.cover, self.uncover, self.open_local = Mock(), Mock(), Mock()
        self.manager = InterceptionCoordinator(allowed=lambda: True, cooldown=lambda: 90,
            windows=self.windows, overlay=self.overlay, clock=lambda: self.now,
            cover=self.cover, uncover=self.uncover, open_local=self.open_local,
            warning_seconds=0, delay=0)

    def tearDown(self):
        self.manager.close()
        self.assertTrue(self.manager.wait())

    def request(self, **options):
        return self.manager.request('Report', destination='casino',
            window=dict(self.windows.current), seen=self.now, **options)

    def test_completed_cover_retires_after_cooldown_and_next_work_opens_again(self):
        self.assertTrue(self.request()['ok'])
        self.assertTrue(self.manager.wait())
        self.assertEqual(self.manager.info()['phase'], 'opened')
        self.assertFalse(self.manager.retire_completed())
        self.now += 90
        self.assertTrue(self.manager.retire_completed())
        self.assertIsNone(self.manager.info())
        self.uncover.assert_called_once()
        self.assertFalse(self.manager.retire_completed())
        self.assertTrue(self.request()['ok'])
        self.assertTrue(self.manager.wait())
        self.assertEqual(self.open_local.call_count, 2)

    def test_zero_cooldown_allows_the_next_work_without_advancing_the_clock(self):
        self.manager.cooldown = lambda: 0
        self.assertTrue(self.request()['ok'])
        self.assertTrue(self.manager.wait())
        self.assertTrue(self.manager.retire_completed())
        self.assertTrue(self.request()['ok'])
        self.assertTrue(self.manager.wait())
        self.assertEqual(self.open_local.call_count, 2)

    def test_active_performance_cannot_be_retired_even_after_cooldown(self):
        self.manager.delay = 60
        entered, release = threading.Event(), threading.Event()
        waiting = {}
        original_wait = self.manager._wait_valid
        def hold_approach(seconds, stop, valid):
            waiting['valid'] = valid
            entered.set()
            if not release.wait(2):
                return False
            return original_wait(seconds, stop, valid)
        self.manager._wait_valid = hold_approach
        try:
            self.assertTrue(self.request(active=lambda: True)['ok'])
            self.assertTrue(entered.wait(1), 'Worker did not reach its approach wait.')
            # Advance test time only once the worker is deliberately held in an
            # active approach. Never race a logical-clock jump with admission or
            # infer an in-progress worker from request() returning successfully.
            with self.manager.lock:
                self.now += 90
                self.assertTrue(waiting['valid'](), 'Continuous work must remain valid.')
                self.assertFalse(self.manager.retire_completed())
                self.assertEqual(self.manager.info()['phase'], 'approaching')
                self.uncover.assert_not_called()
        finally:
            self.manager.stop()
            release.set()
            self.assertTrue(self.manager.wait())

    def test_async_uncover_blocks_new_action_until_actual_acknowledgement(self):
        release = Future()
        self.uncover.return_value = release
        self.assertTrue(self.request()['ok'])
        self.assertTrue(self.manager.wait())
        self.now += 90
        self.assertTrue(self.manager.retire_completed())
        self.assertTrue(self.manager.diagnostics()['covered'])
        self.assertFalse(self.request()['ok'])
        release.set_result(True)
        self.assertTrue(self.request()['ok'])
        self.assertTrue(self.manager.wait())
        self.assertEqual(self.open_local.call_count, 2)

    def test_closed_tabs_are_never_reopened_and_each_new_tab_uses_its_own_target(self):
        self.windows.current['process'] = 'chrome.exe'
        targets = []
        def close_tab(target, valid, live):
            self.assertTrue(valid())
            self.assertTrue(live())
            targets.append(target['tab_id'])
            future = Future()
            future.set_result(True)
            return future
        self.manager.close_tab = close_tab
        self.assertTrue(self.request(mode='minimize', tab_target={'tab_id': 31})['ok'])
        self.assertTrue(self.manager.wait())
        self.now += 90
        self.assertTrue(self.manager.retire_completed())
        self.assertEqual(self.windows.minimized, [])
        self.assertEqual(self.windows.restored, [])
        self.assertFalse(self.request(mode='minimize', tab_target={'tab_id': 32})['ok'])
        self.manager.observe_foreground({'handle': 90, 'pid': 890, 'process': 'Chuddy.exe', 'title': 'Chuddy'})
        self.assertTrue(self.request(mode='minimize', tab_target={'tab_id': 32})['ok'])
        self.assertTrue(self.manager.wait())
        self.assertEqual(targets, [31, 32])
        self.assertEqual(self.open_local.call_count, 2)

    def test_error_can_retry_on_next_fresh_observation_after_cooldown(self):
        self.open_local.side_effect = RuntimeError('fake destination error')
        self.assertTrue(self.request()['ok'])
        self.assertTrue(self.manager.wait())
        self.assertEqual(self.manager.info()['phase'], 'error')
        self.now += 90
        self.assertTrue(self.manager.retire_completed())
        self.open_local.side_effect = None
        self.assertTrue(self.request()['ok'])
        self.assertTrue(self.manager.wait())
        self.assertEqual(self.manager.info()['phase'], 'opened')
