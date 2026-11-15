"""Real controller/overlay queue lifecycle with fake native surfaces only.

No desktop, browser, microphone, speaker, model or physical output is used.
These regressions check the user-visible session contract across repeated use,
rather than accepting a renderer's 'ready' label as evidence of a visible pet.
"""
import threading
import time
from unittest.mock import Mock

from chuddy.desktop_overlay import DesktopOverlay
from tests.helpers import ControllerCase


class MemorySurface:
    def __init__(self, condition):
        self.condition = condition
        self.visible = False
        self.closed = False
        self.draws = 0
        self.message = None
        self.warning_draws = []
        self.fail_warning = False

    def arm(self):
        return True

    def disarm(self):
        pass

    def bounds(self):
        return (0, 0, 1280, 800)

    def rect(self, handle):
        return (50, 50, 900, 700)

    def pump(self):
        return False

    def set_message(self, message):
        with self.condition:
            self.message = message
            self.condition.notify_all()

    def draw(self, *args, **kwargs):
        with self.condition:
            if self.message and self.fail_warning:
                raise RuntimeError('Fake warning draw failure')
            if self.message:
                self.warning_draws.append((args[:2], self.message))
            self.visible = True
            self.draws += 1
            self.condition.notify_all()

    def hide(self):
        with self.condition:
            self.visible = False
            self.condition.notify_all()

    def close(self):
        with self.condition:
            self.visible = False
            self.closed = True
            self.condition.notify_all()


class CursorReliabilityTests(ControllerCase):
    def setUp(self):
        super().setUp()
        self.surface_condition = threading.Condition()
        self.surfaces = []
        self.factory_attempts = 0
        self.fail_next_factory = False

        def factory(asset):
            with self.surface_condition:
                self.factory_attempts += 1
                if self.fail_next_factory:
                    self.fail_next_factory = False
                    self.surface_condition.notify_all()
                    raise RuntimeError('Transient fake renderer initialization failure')
                surface = MemorySurface(self.surface_condition)
                self.surfaces.append(surface)
                self.surface_condition.notify_all()
                return surface

        self.overlay = DesktopOverlay(self.path / 'unused-test.png', factory=factory)
        self.controller.desktop_overlay = self.overlay
        self.controller.distractions.overlay = self.overlay

    def wait_for(self, predicate, description, timeout=2):
        deadline = time.monotonic() + timeout
        # Never hold the fake surface lock while reading overlay.info(): actual
        # draws acquire the overlay lock before the surface lock.
        while not predicate():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.fail(description + ': ' + str(self.overlay.info()))
            with self.surface_condition:
                self.surface_condition.wait(min(remaining, .04))

    def visible(self):
        return bool(self.surfaces and self.surfaces[-1].visible)

    def assert_visible(self):
        self.wait_for(lambda: self.visible() and self.overlay.info()['visible'], 'Session must produce an actual drawn sprite')
        self.assertTrue(self.overlay.info()['visible'])

    def assert_hidden(self):
        self.wait_for(lambda: not self.visible() and not self.overlay.info()['visible'], 'Stopped or paused session must hide the sprite')
        self.assertFalse(self.overlay.info()['visible'])

    def test_start_pause_resume_and_stop_use_actual_draw_visibility(self):
        self.assertFalse(self.visible())
        self.controller.set_session('start')
        self.assert_visible()
        self.controller.set_session('pause')
        self.assert_hidden()
        self.controller.set_session('resume')
        self.assert_visible()
        self.controller.set_session('stop')
        self.assert_hidden()
        self.assertFalse(self.overlay.info()['enabled'])

    def test_return_to_work_keeps_running_session_sprite_available(self):
        self.controller.set_session('start')
        self.assert_visible()
        self.controller.distractions.delay = 0
        self.assertTrue(self.controller.interrupt('Deliberate local preview', test=True))
        self.assertTrue(self.controller.distractions.wait(1))
        self.controller.dismiss()
        self.assertEqual(self.controller.session, 'running')
        self.assertIsNone(self.controller.interruption)
        self.assert_visible()
        self.assertEqual(self.overlay.info()['activity'], 'roaming')

    def test_casino_hides_the_roaming_sprite_until_return_to_companion(self):
        self.controller.set_session('start')
        self.assert_visible()
        self.controller.distractions.delay = 0
        self.assertTrue(self.controller.interrupt('Deliberate preview', test=True))
        self.assertTrue(self.controller.distractions.wait(1))
        self.assertEqual(self.controller.interruption['phase'], 'preview')
        self.wait_for(lambda: self.overlay.info()['activity'] == 'hidden', 'Casino must keep the sprite off the table')
        self.assertTrue(self.overlay.info()['hotkey'])
        opening = self.controller.state()['casino_opening_id']
        self.assertTrue(self.controller.action({'action': 'casino_leave', 'opening_id': opening})['left'])
        self.wait_for(lambda: self.overlay.info()['activity'] == 'roaming', 'Back must restore the buddy')
        self.assert_visible()

    def test_leaving_work_during_approach_returns_to_roaming_without_action(self):
        self.controller.set_session('start')
        self.assert_visible()
        self.controller.distractions.delay = .12
        window = {'handle':42, 'pid':123, 'process':'WINWORD.EXE', 'title':'Report'}
        self.patches[-1].target.foreground.return_value = window
        self.assertTrue(self.controller.observe_work('Report', window))
        self.assertTrue(self.controller.interrupt('Report', window))
        self.wait_for(lambda: self.overlay.info()['activity'] == 'approaching', 'Approach should begin')
        self.patches[-1].target.foreground.return_value = {**window, 'handle':43}
        self.assertTrue(self.controller.distractions.wait(1))
        self.assertIsNone(self.controller.interruption)
        self.assertEqual(self.covered, [])
        self.wait_for(lambda: self.overlay.info()['activity'] == 'roaming', 'Cancelled performance must resume buddy')
        self.assert_visible()

    def test_failed_destination_releases_cover_and_returns_to_roaming(self):
        self.controller.settings({'distraction_destination':'lcbo'})
        self.controller.distractions.launcher = Mock()
        self.controller.distractions.launcher.open.side_effect = RuntimeError('Fake Chrome failure')
        self.controller.distractions.delay = 0
        self.controller.set_session('start')
        self.assert_visible()
        window = {'handle':42, 'pid':123, 'process':'WINWORD.EXE', 'title':'Report'}
        self.patches[-1].target.foreground.return_value = window
        self.assertTrue(self.controller.observe_work('Report', window))
        self.assertTrue(self.controller.interrupt('Report', window))
        self.assertTrue(self.controller.distractions.wait(1))
        self.assertEqual(self.controller.interruption['phase'], 'error')
        self.assertFalse(self.controller.distractions.diagnostics()['covered'])
        self.wait_for(lambda: self.overlay.info()['activity'] == 'roaming', 'Failed destination must retain visible buddy')
        self.assert_visible()

    def test_stopped_return_to_work_does_not_start_or_rearm_sprite(self):
        self.controller.set_session('start')
        self.assert_visible()
        self.controller.set_session('stop')
        self.assert_hidden()
        self.controller.dismiss()
        self.assertEqual(self.controller.session, 'stopped')
        self.assert_hidden()
        self.assertFalse(self.overlay.info()['enabled'])

    def test_rule_change_cancels_pending_performance_and_resumes_buddy(self):
        self.controller.set_session('start')
        self.assert_visible()
        self.controller.distractions.delay = 60
        self.assertTrue(self.controller.interrupt('Pending preview', test=True))
        self.controller.settings({'distraction_destination': 'lcbo'})
        self.assertTrue(self.controller.distractions.wait(1))
        self.assertIsNone(self.controller.interruption)
        self.assertEqual(self.controller.session, 'running')
        self.assert_visible()
        self.assertEqual(self.covered, [])

    def test_stop_cancels_late_performance_before_opening_destination(self):
        self.controller.set_session('start')
        self.assert_visible()
        self.controller.distractions.delay = 60
        self.assertTrue(self.controller.interrupt('Pending preview', test=True))
        self.controller.set_session('stop')
        self.assertTrue(self.controller.distractions.wait(1))
        self.assert_hidden()
        self.assertIsNone(self.controller.interruption)
        self.assertEqual(self.covered, [])
        self.assertFalse(self.overlay.info()['enabled'])

    def test_repeated_quick_starts_do_not_strand_final_start(self):
        for _ in range(25):
            self.controller.set_session('start')
            self.controller.set_session('stop')
        self.controller.set_session('start')
        self.assert_visible()
        for _ in range(4):
            self.controller.set_session('stop')
            self.assert_hidden()
            self.controller.set_session('start')
            self.assert_visible()

    def test_explicit_new_start_recovers_transient_renderer_failure(self):
        self.fail_next_factory = True
        self.controller.set_session('start')
        self.wait_for(lambda: self.overlay.info()['status'] == 'unavailable', 'Renderer failure must become observable')
        self.assertFalse(self.visible())
        self.controller.set_session('start')
        self.assert_visible()
        self.assertIsNone(self.overlay.info()['error'])
        self.assertGreaterEqual(self.factory_attempts, 2)

    def test_expiry_hides_sprite_and_close_prevents_late_reappearance(self):
        self.controller.set_session('start')
        self.controller.expires = self.now + 1800  # Explicit diagnostic lease.
        self.assert_visible()
        self.now += 1801
        self.controller.check_expiry()
        self.assertEqual(self.controller.session, 'stopped')
        self.assert_hidden()
        self.controller.close()
        count = len(self.surfaces)
        try:
            self.controller.set_session('start')
        except (ValueError, RuntimeError):
            pass
        self.assertEqual(len(self.surfaces), count)
        self.assertFalse(self.visible())
        self.assertTrue(all(surface.closed for surface in self.surfaces))

    def test_warning_is_drawn_stationary_before_actual_controller_cover(self):
        self.controller.set_session('start')
        self.assert_visible()
        manager = self.controller.distractions
        manager.warning_seconds, manager.delay = .2, .1
        window = {'handle': 42, 'pid': 123, 'process': 'WINWORD.EXE', 'title': 'Report'}
        self.patches[-1].target.foreground.return_value = window
        self.assertTrue(self.controller.observe_work('Report', window))
        self.assertTrue(self.controller.interrupt('Report', window))
        self.wait_for(lambda: self.overlay.info()['activity'] == 'warning'
                      and bool(self.surfaces[-1].warning_draws), 'Warning must actually be drawn')
        self.assertEqual(self.controller.interruption['phase'], 'warning')
        warning_message = self.controller.interruption['warning_message']
        self.assertEqual(self.covered, [])
        surface = self.surfaces[-1]
        self.assertTrue(manager.wait(1))
        self.assertEqual(self.controller.interruption['phase'], 'opened')
        self.assertEqual(self.covered, [True])
        warning_draws = [(point, message) for point, message in surface.warning_draws if message == warning_message]
        self.assertGreater(len(warning_draws), 1)
        self.assertEqual(len({point for point, _ in warning_draws}), 1)
        self.wait_for(lambda: surface.message is None and self.overlay.info()['activity'] == 'hidden',
                      'Completing the fullscreen switch must clear the warning and hide the sprite')

    def test_asynchronous_warning_draw_failure_cannot_interrupt_work(self):
        self.controller.set_session('start')
        self.assert_visible()
        self.surfaces[-1].fail_warning = True
        manager = self.controller.distractions
        manager.warning_seconds, manager.delay = .12, 0
        window = {'handle': 42, 'pid': 123, 'process': 'WINWORD.EXE', 'title': 'Report'}
        self.patches[-1].target.foreground.return_value = window
        self.assertTrue(self.controller.observe_work('Report', window))
        self.assertTrue(self.controller.interrupt('Report', window))
        self.assertTrue(manager.wait(1))
        self.assertEqual(self.controller.interruption['phase'], 'error')
        self.assertIn('No work was interrupted', self.controller.interruption['error_detail'])
        self.assertEqual(self.covered, [])
        self.patches[-2].target.minimize.assert_not_called()
