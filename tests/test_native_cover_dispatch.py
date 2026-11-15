"""WinForms dispatch contract without constructing a window or loading .NET."""
from concurrent.futures import Future
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from run import NativeCoverDispatch, start_thread_dump


class FakeForm:
    def __init__(self):
        self.trace = []
        self.on_show = None
        self.is_fullscreen = False
        self.Handle = SimpleNamespace(ToInt64=lambda: 42)
        self.FormBorderStyle = 'framed'
        self.Bounds = (100, 200, 900, 800)
        self.RestoreBounds = self.Bounds

    def toggle_fullscreen(self):
        self.is_fullscreen = not self.is_fullscreen
        self.trace.append(('fullscreen', self.is_fullscreen))

    def Show(self):
        self.trace.append(('show', threading.get_ident()))
        if self.on_show:
            self.on_show()

    def Activate(self):
        self.trace.append(('activate', threading.get_ident()))

    @property
    def TopMost(self):
        return getattr(self, '_topmost', False)

    @TopMost.setter
    def TopMost(self, value):
        self._topmost = value
        self.trace.append(('topmost', value))

    @property
    def WindowState(self):
        return getattr(self, '_state', 'normal')

    @WindowState.setter
    def WindowState(self, value):
        self._state = value
        self.trace.append(('state', value))

    def Invoke(self, *args):
        raise AssertionError('Synchronous Control.Invoke must never be used.')


class NativeCoverDispatchTests(unittest.TestCase):
    def setUp(self):
        self.queue = []
        self.form = FakeForm()
        forbidden = Mock(side_effect=AssertionError('Do not call synchronous pywebview wrappers.'))
        self.window = SimpleNamespace(native=self.form, show=forbidden,
                                      restore=forbidden, maximize=forbidden)
        self.manager = SimpleNamespace(cancel_event=threading.Event(), current={'id': 'ticket-1'})
        self.controller = SimpleNamespace(distractions=self.manager, alive=True, session='running',
                                          clock=lambda: 100., expires=200., lock=threading.RLock())
        self.dispatch = NativeCoverDispatch(self.window, self.controller, post=self.queue.append)
        self.screen = (0, 0, 1600, 900)
        self.tick = []
        ticks = self.tick

        class Timer:
            def __init__(self):
                self.Tick = self
                self.callback = None

            def __iadd__(self, callback):
                self.callback = callback
                return self

            def Start(self):
                ticks.append(self.callback)

            def Stop(self):
                ticks.remove(self.callback)

            def Dispose(self):
                pass

        self.modules = patch.dict(sys.modules, {
            'System': SimpleNamespace(Action=lambda callback: callback),
            'System.Windows.Forms': SimpleNamespace(FormWindowState=SimpleNamespace(
                Normal='normal', Maximized='maximized'),
                FormBorderStyle=SimpleNamespace(**{'None': 'borderless'}),
                Screen=SimpleNamespace(FromControl=lambda _: SimpleNamespace(Bounds=self.screen)),
                Timer=Timer),
        })
        self.modules.start()
        self.foreground = self.enterContext(patch('chuddy.windows.foreground_handle', return_value=42))
        self.owner = self.enterContext(patch('chuddy.windows.window_owner', return_value=(7, 8)))
        self.activate = self.enterContext(patch('chuddy.windows.activate_window'))
        self.fullscreen = self.enterContext(patch('chuddy.windows.fullscreen_status', return_value=(True, True)))

    def tearDown(self):
        self.dispatch.close()
        self.modules.stop()

    def drain(self):
        while self.queue:
            self.queue.pop(0)()
        for callback in list(self.tick):
            callback()

    def test_cover_returns_pending_future_without_touching_ui_under_controller_lock(self):
        with self.controller.lock:
            future = self.dispatch.cover()
            self.assertIsInstance(future, Future)
            self.assertFalse(future.done())
            self.assertEqual(self.form.trace, [])
        self.drain()
        self.assertTrue(future.result(timeout=.1))
        self.assertEqual([item[0] for item in self.form.trace], ['show', 'state'])
        self.assertEqual(self.form.FormBorderStyle, 'borderless')
        self.assertEqual(self.form.Bounds, self.screen)
        self.assertEqual(self.dispatch.info()['applied'], 'cover')
        self.window.show.assert_not_called()

    def test_native_post_uses_begininvoke_and_only_applies_after_ui_pumps(self):
        self.form.BeginInvoke = self.queue.append
        dispatch = NativeCoverDispatch(self.window, self.controller)
        future = dispatch.cover()
        self.assertFalse(future.done())
        self.assertEqual(self.form.trace, [])
        self.drain()
        self.assertTrue(future.result())
        dispatch.close()

    def test_ui_callback_never_waits_for_another_threads_controller_lock(self):
        future = self.dispatch.cover()
        held, release = threading.Event(), threading.Event()

        def hold_controller():
            with self.controller.lock:
                held.set()
                release.wait(1)

        worker = threading.Thread(target=hold_controller)
        worker.start()
        self.assertTrue(held.wait(1))
        callback = threading.Thread(target=self.drain)
        callback.start()
        callback.join(.3)
        try:
            self.assertFalse(callback.is_alive(), 'UI callback waited for the controller RLock.')
            self.assertTrue(future.result(timeout=.1))
        finally:
            release.set()
            worker.join(1)
            callback.join(1)

    def test_stop_uncover_invalidates_queued_cover_before_ui_pumps(self):
        cover = self.dispatch.cover()
        self.manager.cancel_event.set()
        self.controller.session = 'stopped'
        uncover = self.dispatch.uncover()
        self.assertIs(cover.result(timeout=.1), False)
        self.assertFalse(uncover.done())
        self.drain()
        self.assertTrue(uncover.result())
        self.assertEqual(self.form.trace, [])

    def test_session_ticket_expiry_and_cancel_event_each_reject_late_cover(self):
        for condition in ('session', 'ticket', 'expiry', 'event', 'closed'):
            with self.subTest(condition=condition):
                manager = SimpleNamespace(cancel_event=threading.Event(), current={'id': 'ticket-1'})
                controller = SimpleNamespace(distractions=manager, alive=True, session='running',
                                             clock=lambda: 100., expires=200.)
                dispatch = NativeCoverDispatch(self.window, controller, post=self.queue.append)
                future = dispatch.cover()
                if condition == 'session':
                    controller.session = 'paused'
                elif condition == 'ticket':
                    manager.current = {'id': 'ticket-2'}
                elif condition == 'expiry':
                    controller.expires = 99.
                elif condition == 'event':
                    manager.cancel_event.set()
                else:
                    controller.alive = False
                self.drain()
                self.assertIs(future.result(), False)
                self.assertEqual(self.form.trace, [])
                dispatch.close()

    def test_caller_cancellation_after_queueing_cancels_native_effect(self):
        caller = threading.Event()
        self.manager.handoff_valid = lambda: not caller.is_set()
        cover = self.dispatch.cover()
        caller.set()
        self.drain()
        self.assertIs(cover.result(), False)
        self.assertEqual(self.form.trace, [])

    def test_native_reentrant_stop_between_operations_prevents_maximize(self):
        cleanup = []

        def stop_during_show():
            self.manager.cancel_event.set()
            self.controller.session = 'stopped'
            cleanup.append(self.dispatch.uncover())

        self.form.on_show = stop_during_show
        cover = self.dispatch.cover()
        self.drain()
        self.assertIs(cover.result(), False)
        self.assertTrue(cleanup[0].result())
        self.assertEqual([item[0] for item in self.form.trace], ['show'])
        self.assertFalse(self.form.TopMost)
        self.assertEqual(self.form.WindowState, 'normal')

    def test_newest_request_wins_even_when_old_callbacks_execute_out_of_order(self):
        old_cover = self.dispatch.cover()
        uncover = self.dispatch.uncover()
        new_cover = self.dispatch.cover()
        self.queue.reverse()
        self.drain()
        self.assertIs(old_cover.result(), False)
        self.assertIs(uncover.result(), False)
        self.assertTrue(new_cover.result())
        self.assertEqual(len(self.form.trace), 2)
        self.assertEqual(self.form.FormBorderStyle, 'borderless')

    def test_fullscreen_without_focus_is_an_error_and_owned_mode_is_restored(self):
        self.manager.handoff_foreground = (99, 7, 8)
        self.foreground.return_value = 99
        self.fullscreen.return_value = (False, True)
        self.dispatch._verification_timeout = 0
        cover = self.dispatch.cover()
        self.drain()
        with self.assertRaisesRegex(RuntimeError, 'foreground focus'):
            cover.result()
        self.activate.assert_called_once_with(42)
        self.assertFalse(self.dispatch.info()['verified_foreground'])
        self.assertTrue(self.dispatch.info()['verified_fullscreen'])
        cleanup = self.dispatch.uncover()
        self.drain()
        self.assertTrue(cleanup.result())
        self.assertEqual(self.form.FormBorderStyle, 'framed')
        self.assertEqual(self.form.Bounds, self.form.RestoreBounds)

    def test_async_focus_and_geometry_can_arrive_on_different_ticks(self):
        self.manager.handoff_foreground = (99, 7, 8)
        self.foreground.return_value = 99
        self.fullscreen.side_effect = [(False, False), (True, False), (True, True)]
        cover = self.dispatch.cover()
        self.drain()
        self.assertFalse(cover.done())
        self.drain()
        self.assertFalse(cover.done())
        self.drain()
        self.assertTrue(cover.result())
        self.activate.assert_called_once_with(42)

    def test_changed_foreground_never_receives_an_activation_attempt(self):
        self.manager.handoff_foreground = (99, 7, 8)
        self.foreground.return_value = 100
        cover = self.dispatch.cover()
        self.drain()
        with self.assertRaisesRegex(RuntimeError, 'focus changed'):
            cover.result()
        self.assertEqual(self.form.trace, [])
        self.activate.assert_not_called()

    def test_stop_cancels_a_pending_verification_and_restores_the_frame(self):
        self.fullscreen.return_value = (True, False)
        cover = self.dispatch.cover()
        self.drain()
        self.assertFalse(cover.done())
        self.manager.cancel_event.set()
        cleanup = self.dispatch.uncover()
        self.drain()
        self.assertIs(cover.result(), False)
        self.assertTrue(cleanup.result())
        self.assertEqual(self.tick, [])
        self.assertEqual(self.form.Bounds, self.form.RestoreBounds)

    def test_preexisting_fullscreen_stays_fullscreen_after_uncover(self):
        self.form.is_fullscreen = True
        cover = self.dispatch.cover()
        self.drain()
        self.assertTrue(cover.result())
        cleanup = self.dispatch.uncover()
        self.drain()
        self.assertTrue(cleanup.result())
        self.assertTrue(self.form.is_fullscreen)
        self.assertNotIn('fullscreen', [item[0] for item in self.form.trace])

    def test_post_and_apply_errors_reach_future_and_diagnostics(self):
        self.dispatch._post = Mock(side_effect=RuntimeError('Native shell unavailable'))
        future = self.dispatch.cover()
        with self.assertRaises(RuntimeError):
            future.result(timeout=.1)
        self.assertEqual(self.dispatch.info()['error'], 'RuntimeError')
        self.dispatch._post = self.queue.append
        self.form.on_show = Mock(side_effect=ValueError('Fake native error'))
        future = self.dispatch.cover()
        self.drain()
        with self.assertRaises(ValueError):
            future.result(timeout=.1)
        self.assertEqual(self.dispatch.info()['error'], 'ValueError')

    def test_missing_native_form_completes_error_without_waiting(self):
        self.window.native = None
        dispatch = NativeCoverDispatch(self.window, self.controller)
        future = dispatch.cover()
        with self.assertRaisesRegex(RuntimeError, 'not ready'):
            future.result(timeout=.1)
        dispatch.close()

    def test_guard_exception_also_settles_future_instead_of_stranding_waiter(self):
        future = self.dispatch.cover()
        self.controller.clock = Mock(side_effect=RuntimeError('Fake expired controller'))
        self.drain()
        with self.assertRaises(RuntimeError):
            future.result(timeout=.1)
        self.assertEqual(self.form.trace, [])

    def test_close_invalidates_all_pending_and_future_actions(self):
        future = self.dispatch.cover()
        self.dispatch.close()
        self.assertIs(future.result(), False)
        self.drain()
        self.assertEqual(self.form.trace, [])
        self.assertIs(self.dispatch.cover().result(), False)
        self.assertIs(self.dispatch.uncover().result(), False)
        self.assertEqual(self.queue, [])


class ThreadDumpTests(unittest.TestCase):
    def test_default_and_invalid_values_do_not_create_diagnostics(self):
        with tempfile.TemporaryDirectory() as folder:
            for value in ('', 'bad', '-1', '0', 'nan', 'inf'):
                with self.subTest(value=value), patch.dict(os.environ, {'CHUDDY_DUMP_THREADS_AFTER': value}):
                    self.assertIsNone(start_thread_dump(folder))
                    self.assertFalse((Path(folder) / 'diagnostics').exists())

    def test_valid_timer_is_one_shot_bounded_and_uses_designated_local_log(self):
        with tempfile.TemporaryDirectory() as folder:
            for value, expected in (('.01', 1.), ('20', 20.), ('9999', 120.)):
                with self.subTest(value=value), patch.dict(os.environ, {'CHUDDY_DUMP_THREADS_AFTER': value}), \
                     patch('run.faulthandler.dump_traceback_later') as schedule:
                    output = start_thread_dump(folder)
                    try:
                        schedule.assert_called_once_with(expected, repeat=False, file=output, exit=False)
                        self.assertEqual(Path(output.name), Path(folder) / 'diagnostics' / 'native-threads.log')
                    finally:
                        output.close()


if __name__ == '__main__':
    unittest.main()
