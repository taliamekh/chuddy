"""Native overlay lifecycle tested through a fake surface: no actual UI opened."""
import threading
import os
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from chuddy.desktop_overlay import DesktopOverlay, _Win32Surface, sprite_position, idle_position


class Surface:
    def __init__(self, asset):
        self.drawn, self.hidden, self.closed = threading.Event(), threading.Event(), threading.Event()
        self.arm_calls, self.disarm_calls, self.draw_calls = 0, 0, 0
        self.key = False

    def arm(self):
        self.arm_calls += 1
        return True

    def disarm(self):
        self.disarm_calls += 1

    def hide(self):
        self.hidden.set()

    def close(self):
        self.closed.set()

    def bounds(self):
        return (-1920, 0, 1920, 1080)

    def rect(self, handle):
        return (0, 0, 1920, 1080)

    def draw(self, *args, **kwargs):
        self.draw_calls += 1
        self.drawn.set()

    def pump(self):
        key, self.key = self.key, False
        return key


class OverlayTests(unittest.TestCase):
    def wait_for(self, predicate, timeout=1):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            if predicate():
                return
            threading.Event().wait(.005)
        self.assertTrue(predicate(), "Overlay condition did not settle before timeout")

    def test_native_browser_animation_targets_tab_x_and_never_substitutes_window_x(self):
        for point, expected in (([200,40], (200,40)), ([-5000,40], None), (None, None)):
            with self.subTest(point=point):
                surface=Surface(Path('unused'))
                targets=[]
                def bounds_for(target):
                    targets.append(target)
                    return surface.bounds()
                surface.bounds_for=bounds_for
                overlay=DesktopOverlay('unused.png',factory=lambda path:surface,reduced_motion=True)
                try:
                    overlay.perform({'handle':42,'tab_close_point':point},'casino')
                    self.wait_for(lambda:bool(targets))
                    self.assertEqual(targets[0],expected)
                finally:
                    overlay.close()

    def test_visible_state_tracks_real_draw_hide_and_disarm(self):
        surface=Surface(Path("unused"))
        overlay=DesktopOverlay("unused.png",factory=lambda path:surface)
        try:
            overlay.start()
            self.wait_for(lambda:overlay.info()["status"]=="ready")
            self.assertTrue(overlay.info()["enabled"])
            self.assertFalse(overlay.info()["visible"])
            overlay.roam()
            self.wait_for(lambda:overlay.info()["visible"])
            shown=overlay.info()
            self.assertEqual(shown["activity"],"roaming")
            self.assertGreater(shown["rendered_frames"],0)
            self.assertLess(shown["last_frame_age"],.5)
            overlay.hide()
            self.assertFalse(overlay.info()["visible"])
            self.assertTrue(overlay.info()["enabled"])
            overlay.disarm()
            self.assertFalse(overlay.info()["visible"])
            self.assertFalse(overlay.info()["enabled"])
            self.assertEqual(overlay.info()["activity"],"stopped")
        finally:
            overlay.close()

    def test_immediate_stop_then_resume_does_not_lose_roam_request(self):
        surface=Surface(Path("unused"))
        overlay=DesktopOverlay("unused.png",factory=lambda path:surface)
        try:
            overlay.roam();self.wait_for(lambda:overlay.info()["visible"])
            for _ in range(10):
                overlay.disarm()
                frames=overlay.info()["rendered_frames"]
                overlay.roam()
                self.wait_for(lambda:overlay.info()["visible"] and overlay.info()["rendered_frames"]>frames)
                self.assertTrue(overlay.info()["enabled"])
        finally:
            overlay.close()

    def test_stalled_native_draw_does_not_block_info_or_stop_or_acknowledge_late_frame(self):
        surface=Surface(Path("unused"))
        entered,release,controlled=threading.Event(),threading.Event(),threading.Event()
        def draw(*args,**kwargs):
            entered.set();release.wait(2)
        surface.draw=draw
        overlay=DesktopOverlay("unused.png",factory=lambda path:surface)
        observations=[]
        def stop():
            observations.append(overlay.info())
            overlay.disarm()
            observations.append(overlay.info())
            controlled.set()
        try:
            overlay.warning(message="I see work.")
            self.assertTrue(entered.wait(1))
            control=threading.Thread(target=stop,daemon=True);control.start()
            self.assertTrue(controlled.wait(.2),"A native stall held the API/Stop status lock")
            self.assertEqual(observations[0]["native_operation"],"draw")
            self.assertIsNotNone(observations[0]["native_operation_age"])
            self.assertIsNone(observations[0]["rendered_request_id"])
            self.assertFalse(observations[1]["enabled"])
            self.assertFalse(observations[1]["visible"])
            release.set()
            self.assertTrue(surface.hidden.wait(1))
            self.assertEqual(overlay.info()["rendered_frames"],0)
            self.assertIsNone(overlay.info()["activity_since"])
        finally:
            release.set();overlay.close()

    def test_warning_ack_matches_latest_request_and_time_starts_at_its_first_frame(self):
        surface=Surface(Path("unused"))
        entered,release=threading.Event(),threading.Event()
        overlay=DesktopOverlay("unused.png",factory=lambda path:surface)
        try:
            overlay.warning(message="First warning")
            self.wait_for(lambda:overlay.info()["visible"])
            first=overlay.info()
            self.assertEqual(first["request_id"],first["rendered_request_id"])
            def draw(*args,**kwargs):
                entered.set();release.wait(2)
            surface.draw=draw
            overlay.warning(message="Second warning")
            request=overlay.info()["request_id"]
            self.assertTrue(entered.wait(1))
            waiting=overlay.info()
            self.assertGreater(request,first["request_id"])
            self.assertNotEqual(waiting["rendered_request_id"],request,
                "An older warning frame cannot acknowledge a newly queued warning")
            before_release=time.monotonic()
            release.set()
            self.wait_for(lambda:overlay.info()["rendered_request_id"]==request)
            shown=overlay.info()
            self.assertGreaterEqual(shown["activity_since"],before_release)
            frames=shown["rendered_frames"]
            self.wait_for(lambda:overlay.info()["rendered_frames"]>frames)
            self.assertEqual(overlay.info()["activity_since"],shown["activity_since"])
            overlay.hide()
            self.assertIsNone(overlay.info()["rendered_request_id"])
            self.assertIsNone(overlay.info()["activity_since"])
        finally:
            release.set();overlay.close()

    def test_roam_during_failed_worker_cleanup_is_not_stranded(self):
        first,second=Surface(Path("first")),Surface(Path("second"))
        cleaning,release=threading.Event(),threading.Event()
        def fail_draw(*args,**kwargs):
            raise RuntimeError("drawing failed")
        def slow_close():
            cleaning.set();release.wait(1);first.closed.set()
        first.draw,first.close=fail_draw,slow_close
        surfaces=iter((first,second))
        overlay=DesktopOverlay("unused.png",factory=lambda path:next(surfaces))
        try:
            overlay.roam()
            self.assertTrue(cleaning.wait(1))
            self.assertFalse(overlay.info()["visible"])
            self.assertEqual(overlay.info()["status"],"unavailable")
            overlay.roam()
            release.set()
            self.assertTrue(second.drawn.wait(1),"Retry was lost on the exiting failed worker")
            self.wait_for(lambda:overlay.info()["visible"])
            self.assertIsNone(overlay.info()["error"])
        finally:
            release.set();overlay.close()

    def test_native_failure_waits_for_explicit_retry_without_window_loop(self):
        factory=Mock(side_effect=RuntimeError("unavailable"))
        overlay=DesktopOverlay("unused.png",factory=factory)
        try:
            overlay.roam()
            self.wait_for(lambda:overlay.info()["status"]=="unavailable")
            threading.Event().wait(.06)
            self.assertEqual(factory.call_count,1)
            self.assertTrue(overlay.info()["worker_alive"])
            self.assertFalse(overlay.info()["visible"])
            overlay.roam()
            self.wait_for(lambda:factory.call_count==2)
        finally:
            overlay.close()

    def test_idle_never_moves_even_after_hours(self):
        bounds=(0,0,1920,1080)
        for seconds in (0,.5,9,12,15,27,120,3600,86400):
            for reduced in (False,True):
                self.assertEqual(idle_position(seconds,reduced),(.88,.86,False))
                self.assertEqual(sprite_position(seconds,bounds,reduced=reduced),sprite_position(0,bounds))
        self.assertEqual(sprite_position(0,bounds,(1500,20)),sprite_position(0,bounds))
        self.assertNotEqual(sprite_position(0,bounds,(1500,20)),sprite_position(1.3,bounds,(1500,20)))

    def test_warning_is_stationary_cannot_click_and_is_cleared_by_approach(self):
        surface=Surface(Path("unused"))
        surface.set_message=Mock()
        surface.rect=Mock(return_value=(0,0,1920,1080))
        draws=[]
        def draw(x,y,**kwargs):
            draws.append((x,y,kwargs["click"]))
        surface.draw=draw
        overlay=DesktopOverlay("unused.png",factory=lambda path:surface)
        try:
            overlay.roam();self.wait_for(lambda:overlay.info()["visible"])
            idle=draws[-1]
            overlay.warning({"handle":42},"casino","I see work. Casino in eight seconds.")
            self.wait_for(lambda:overlay.info()["activity"]=="warning")
            frames=overlay.info()["rendered_frames"]
            self.wait_for(lambda:overlay.info()["rendered_frames"]>=frames+3)
            self.assertEqual(set(draws),{idle})
            self.assertFalse(idle[2])
            surface.rect.assert_not_called()
            surface.set_message.assert_called_with("I see work. Casino in eight seconds.")
            overlay.perform({"handle":42},"casino")
            self.wait_for(lambda:overlay.info()["activity"]=="approaching")
            surface.rect.assert_called_once_with(42)
            surface.set_message.assert_called_with(None)
            overlay.disarm()
            frames=overlay.info()["rendered_frames"]
            threading.Event().wait(.06)
            self.assertEqual(overlay.info()["rendered_frames"],frames)
        finally:
            overlay.close()

    def test_construction_is_inert(self):
        created = []
        overlay = DesktopOverlay("unused.png", factory=lambda path: created.append(path))
        self.assertIsNone(overlay.thread)
        self.assertEqual(created, [])
        self.assertEqual(overlay.info()["status"], "stopped")
        overlay.close()
        self.assertEqual(overlay.info()["status"], "closed")

    def test_route_remains_in_virtual_screen_for_negative_monitors(self):
        for bounds in ((-1920, -1080, 1920, 1080), (0, 0, 800, 600), (0, 0, 40, 40)):
            for seconds in (0, .5, 1., 10000.):
                for target in (None, (-9000, -4000), (9000, 4000)):
                    x, y = sprite_position(seconds, bounds, target)
                    self.assertTrue(bounds[0] <= x <= max(bounds[0], bounds[2]-112))
                    self.assertTrue(bounds[1] <= y <= max(bounds[1], bounds[3]-168))

    def test_reduced_motion_goes_directly_to_theatrical_target(self):
        args = ((0, 0, 1920, 1080), (1500, 20))
        self.assertEqual(sprite_position(0, *args, reduced=True), sprite_position(3, *args, reduced=True))
        self.assertEqual(sprite_position(0, args[0], reduced=True), sprite_position(3, args[0], reduced=True))

    def test_roam_hide_global_stop_and_cleanup_with_fake_surface(self):
        surface, stopped = Surface(Path("unused")), threading.Event()
        overlay = DesktopOverlay("unused.png", factory=lambda path: surface, on_stop=stopped.set)
        try:
            overlay.roam()
            self.assertTrue(surface.drawn.wait(1))
            self.assertTrue(overlay.info()["hotkey"])
            surface.key = True
            self.assertTrue(stopped.wait(1))
            self.assertTrue(surface.hidden.wait(1))
            overlay.disarm()
        finally:
            overlay.close()
        self.assertTrue(surface.closed.is_set())
        self.assertFalse(overlay.thread.is_alive())
        self.assertEqual(overlay.info()["status"], "closed")

    def test_stop_during_native_initialization_prevents_late_render_and_arming(self):
        surface, entering, release = Surface(Path("unused")), threading.Event(), threading.Event()
        def factory(path):
            entering.set()
            release.wait(1)
            return surface
        overlay = DesktopOverlay("unused.png", factory=factory)
        try:
            overlay.perform({"handle": 42}, "game")
            self.assertTrue(entering.wait(1))
            overlay.disarm()
            release.set()
            self.assertTrue(surface.hidden.wait(1))
            self.assertFalse(surface.drawn.is_set())
            self.assertEqual(surface.arm_calls, 0)
        finally:
            release.set()
            overlay.close()

    def test_native_failure_is_honestly_unavailable(self):
        def fail(path):
            raise RuntimeError("GDI+ unavailable")
        overlay = DesktopOverlay("unused.png", factory=fail)
        overlay.start()
        self.wait_for(lambda:overlay.info()["status"]=="unavailable")
        self.assertEqual(overlay.info()["status"], "unavailable")
        self.assertEqual(overlay.info()["error"], "RuntimeError")
        self.assertFalse(overlay.info()["hotkey"])
        overlay.close()

    def test_native_retry_clears_stale_error(self):
        surface, calls = Surface(Path("unused")), []
        def factory(path):
            calls.append(path)
            if len(calls) == 1:
                raise RuntimeError("temporary renderer failure")
            return surface
        overlay = DesktopOverlay("unused.png", factory=factory)
        try:
            overlay.start()
            self.wait_for(lambda:overlay.info()["status"]=="unavailable")
            self.assertEqual(overlay.info()["error"], "RuntimeError")
            overlay.roam()
            self.assertTrue(surface.drawn.wait(1))
            self.assertIsNone(overlay.info()["error"])
            self.assertEqual(overlay.info()["status"], "ready")
        finally:
            overlay.close()

    def test_route_uses_actual_monitor_workarea_instead_of_desktop_gap(self):
        surface = Surface(Path("unused"))
        drawn = []
        surface.bounds_for = lambda target: (-800, -600, 0, 0)
        def draw(x, y, **kwargs):
            drawn.append((x, y))
            surface.drawn.set()
        surface.draw = draw
        overlay = DesktopOverlay("unused.png", factory=lambda path: surface)
        try:
            overlay.roam()
            self.assertTrue(surface.drawn.wait(1))
            self.assertTrue(all(-800 <= x <= -112 and -600 <= y <= -168 for x, y in drawn))
        finally:
            overlay.close()

    @unittest.skipUnless(os.name == "nt", "GDI+ memory rendering is Windows-only")
    def test_real_gdi_renders_alpha_in_memory_with_all_window_calls_faked(self):
        # Exercise actual PNG decoding, DIB allocation, premultiplied alpha,
        # GDI+ drawing and cleanup. EVERY user32 function is fake, including
        # RegisterClass/CreateWindow/Show/UpdateLayeredWindow/hotkey: no window
        # is created, shown, minimized or touched on the user's desktop.
        import ctypes
        real_dll = ctypes.WinDLL
        user = Mock()
        for name in ("RegisterClassW", "CreateWindowExW", "UpdateLayeredWindow",
                     "SetWindowPos", "ShowWindow", "DestroyWindow", "UnregisterClassW"):
            getattr(user, name).return_value = 1
        user.PeekMessageW.return_value = 0
        user.GetWindowLongPtrW.return_value = 0x080800A8
        asset = Path(__file__).resolve().parents[1]/"web/assets/chuddy/gestures.png"
        surface = None
        def dll(name, **kwargs):
            return user if name == "user32" else real_dll(name, **kwargs)
        try:
            with patch("ctypes.WinDLL", side_effect=dll):
                surface = _Win32Surface(asset)
                surface.draw(0, 0)
                pixels = ctypes.string_at(surface.bits, surface.WIDTH*surface.HEIGHT*4)
                alpha = pixels[3::4]
                self.assertGreater(sum(value > 0 for value in alpha), 1000)
                self.assertGreater(sum(value == 0 for value in alpha), 1000)
                self.assertTrue(any(0 < value < 255 for value in alpha))
                self.assertEqual(alpha[0], 0)
                # Premultiplied channels cannot exceed alpha.
                self.assertTrue(all(max(pixels[i:i+3]) <= pixels[i+3]
                                    for i in range(0, len(pixels), 4)))
                surface.draw(0, 0, click=True)
                self.assertNotEqual(pixels, ctypes.string_at(surface.bits, len(pixels)))
                user.CreateWindowExW.assert_called_once()
                self.assertTrue(user.CreateWindowExW.call_args.args[0]&0x8)
                user.UpdateLayeredWindow.assert_called()
                self.assertTrue(surface.arm())
                surface.draw(600,400)
                clean=ctypes.string_at(surface.bits,len(pixels))
                self.assertTrue(all(not any(clean[row*surface.WIDTH*4:(row*surface.WIDTH+surface.MESSAGE_WIDTH)*4])
                                    for row in range(surface.HEIGHT)),"Cleared text canvas must be all-zero BGRA")
                surface.set_message("I see work: "+"W"*40+". Chud Zone in 8 seconds if you keep working.")
                surface.draw(600,400)
                warning=ctypes.string_at(surface.bits,len(pixels))
                self.assertNotEqual(clean,warning)
                # The actual GDI+ text is dark and opaque inside the pale bubble.
                dark_text=0
                for row in range(20,178):
                    for column in range(24,472):
                        offset=(row*surface.WIDTH+column)*4
                        if warning[offset+3]==255 and max(warning[offset:offset+3])<100:
                            dark_text+=1
                self.assertGreater(dark_text,100)
                self.assertGreaterEqual(surface.message_font_size,22)
                footer_text=0
                for row in range(186,222):
                    for column in range(24,472):
                        offset=(row*surface.WIDTH+column)*4
                        if warning[offset+3]==255 and max(warning[offset:offset+3])<100:
                            footer_text+=1
                self.assertGreater(footer_text,100,"Independent Stop shortcut must render below long work labels")
                surface.set_message(None);surface.draw(600,400)
                self.assertEqual(clean,ctypes.string_at(surface.bits,len(pixels)))
                self.assertEqual(user.SetWindowPos.call_args.args[2:4],(100,328))
        finally:
            if surface:
                surface.close()

    def fake_native_surface(self):
        import ctypes
        from ctypes import wintypes
        surface=object.__new__(_Win32Surface)
        surface.c,surface.w=ctypes,wintypes
        surface.POINT,surface.SIZE=wintypes.POINT,wintypes.SIZE
        class Blend(ctypes.Structure):
            _fields_=[("op",ctypes.c_ubyte),("flags",ctypes.c_ubyte),
                      ("alpha",ctypes.c_ubyte),("format",ctypes.c_ubyte)]
        surface.BLEND=Blend
        surface.check=surface._check
        surface.graphics=surface.image=surface.dc=surface.hwnd=1
        surface.message=""
        surface.cell=(418,627)
        surface.gp,surface.user=Mock(),Mock()
        for name in ("GdipGraphicsClear","GdipDrawImageRectRectI","GdipFlush"):
            getattr(surface.gp,name).return_value=0
        surface.user.UpdateLayeredWindow.return_value=1
        surface.user.SetWindowPos.return_value=1
        surface.user.GetWindowLongPtrW.return_value=0x080800A8
        return surface

    @unittest.skipUnless(os.name=="nt","WinError is Windows-only")
    def test_native_position_failure_is_not_reported_as_success(self):
        surface=self.fake_native_surface()
        surface.user.SetWindowPos.return_value=0
        with self.assertRaises(OSError):
            surface.draw(10,20)
        surface.user.ShowWindow.assert_not_called()

    def test_native_missing_topmost_bit_is_not_reported_as_success(self):
        surface=self.fake_native_surface()
        surface.user.GetWindowLongPtrW.return_value=0x080800A0
        with self.assertRaisesRegex(RuntimeError,"topmost"):
            surface.draw(10,20)
        surface.user.ShowWindow.assert_not_called()


if __name__ == "__main__":
    unittest.main()
