"""Brief native sprite check, only after explicit idle-test authorization.

Requires --idle-check and refuses to draw unless Windows input has been idle
for at least two minutes. Never moves a pointer, clicks, covers/minimizes user
windows, opens a retailer, records or plays audio. New input cancels immediately.
"""
import argparse
import ctypes as c
from ctypes import wintypes as w
import json
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run(threaded=False):
    from chuddy.desktop_overlay import _Win32Surface, sprite_position
    user, kernel = c.WinDLL("user32", use_last_error=True), c.WinDLL("kernel32", use_last_error=True)
    class LASTINPUT(c.Structure):
        _fields_ = [("size", w.UINT), ("tick", w.DWORD)]
    user.GetLastInputInfo.argtypes = [c.POINTER(LASTINPUT)]
    user.GetLastInputInfo.restype = w.BOOL
    user.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
    user.PostMessageW.restype = w.BOOL
    user.IsWindow.argtypes, user.IsWindow.restype = [w.HWND], w.BOOL
    kernel.GetTickCount.restype = w.DWORD
    def last_input():
        item = LASTINPUT(size=c.sizeof(LASTINPUT))
        if not user.GetLastInputInfo(c.byref(item)):
            raise c.WinError(c.get_last_error())
        return item.tick
    tick = last_input()
    idle = ((kernel.GetTickCount()-tick)&0xffffffff)/1000
    if idle < 120:
        return {"ok": False, "busy": True, "idle_seconds": round(idle, 2), "frames": 0}
    if threaded:
        return run_threaded(user, last_input, tick, idle)
    result = {"ok": False, "idle_seconds": round(idle, 2), "duration_limit": 2.5,
              "user_windows_changed": False, "retailer_launched": False,
              "real_mouse": False, "audio": False, "frames": 0}
    surface = None
    try:
        surface = _Win32Surface(ROOT/"web/assets/chuddy/gestures.png")
        bounds = surface.bounds_for()
        began = time.monotonic()
        while time.monotonic()-began < 2.5:
            if last_input() != tick:
                result["cancelled_on_input"] = True
                return result
            elapsed = time.monotonic()-began
            surface.draw(*sprite_position(elapsed, bounds))
            surface.pump()
            result["frames"] += 1
            threading.Event().wait(1/30)
        result["duration_seconds"] = round(time.monotonic()-began, 3)
        result["workarea"] = list(bounds)
        if last_input() != tick:
            result["cancelled_on_input"] = True
            return result
        result["hotkey_registered"] = surface.arm()
        # Dispatch to our own HWND, never synthesize a real key press.
        if not user.PostMessageW(surface.hwnd, 0x312, surface.HOTKEY, 0):
            raise c.WinError(c.get_last_error())
        result["stop_message_received"] = surface.pump()
        surface.hide()
        surface.disarm()
        hwnd = surface.hwnd
        surface.close()
        surface = None
        result["window_destroyed"] = not bool(user.IsWindow(hwnd))
        result["ok"] = all(result[key] for key in ("hotkey_registered", "stop_message_received", "window_destroyed"))
        return result
    finally:
        if surface:
            surface.close()


def run_threaded(user, last_input, tick, idle):
    """Exercise the actual UI thread, native message pump and manager Stop path."""
    from chuddy.desktop_overlay import DesktopOverlay, _Win32Surface
    from chuddy.interception import InterceptionCoordinator
    drawn, stopped = threading.Event(), threading.Event()
    created, frame_count = [], [0]
    def factory(asset):
        surface = _Win32Surface(asset)
        draw = surface.draw
        def count_draw(*args, **kwargs):
            draw(*args, **kwargs)
            frame_count[0] += 1
            drawn.set()
        surface.draw = count_draw
        created.append(surface)
        return surface
    def stop():
        manager.stop()
        stopped.set()
    overlay = DesktopOverlay(ROOT/"web/assets/chuddy/gestures.png", factory=factory, on_stop=stop)
    manager = InterceptionCoordinator(allowed=lambda: True, overlay=overlay)
    result = {"ok": False, "threaded": True, "idle_seconds": round(idle, 2),
              "user_windows_changed": False, "retailer_launched": False,
              "real_mouse": False, "audio": False}
    hwnd = None
    try:
        overlay.roam()
        began = time.monotonic()
        while time.monotonic()-began < 2.5:
            if last_input() != tick:
                result["cancelled_on_input"] = True
                return result
            threading.Event().wait(.01)
        if not drawn.is_set() or not created:
            raise AssertionError("Threaded overlay did not draw: "+str(overlay.info()))
        surface = created[0]
        hwnd = surface.hwnd
        result["frames"] = frame_count[0]
        result["duration_seconds"] = round(time.monotonic()-began, 3)
        result["hotkey_registered"] = overlay.info()["hotkey"]
        if not user.PostMessageW(hwnd, 0x312, surface.HOTKEY, 0):
            raise c.WinError(c.get_last_error())
        result["stop_callback_received"] = stopped.wait(.5)
    finally:
        manager.close()
    result["thread_closed"] = not overlay.thread.is_alive()
    result["window_destroyed"] = bool(hwnd) and not bool(user.IsWindow(hwnd))
    result["hotkey_released"] = not overlay.info()["hotkey"]
    result["ok"] = all(result[key] for key in ("hotkey_registered", "stop_callback_received",
                      "thread_closed", "window_destroyed", "hotkey_released"))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--idle-check", action="store_true", required=True)
    parser.add_argument("--threaded", action="store_true")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.threaded)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))
    sys.exit(0 if report["ok"] else 75 if report.get("busy") or report.get("cancelled_on_input") else 1)
