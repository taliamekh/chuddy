"""Real Win32 overlay/editor checks on a NON-INPUT disposable desktop.

Run only with --isolated-desktop. There is deliberately no SwitchDesktop,
SendInput, real Chrome launcher, voice, controller/store or device dependency.
The created desktop handle has NO DESKTOP_SWITCHDESKTOP right.
"""
from __future__ import annotations

import argparse
import ctypes as c
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run():
    if os.name != "nt":
        raise RuntimeError("Windows-only native probe")
    user = c.WinDLL("user32", use_last_error=True)
    kernel = c.WinDLL("kernel32", use_last_error=True)
    def bind(dll, name, result, *args):
        fn = getattr(dll, name)
        fn.restype, fn.argtypes = result, list(args)
        return fn
    V, I, U = c.c_void_p, c.c_int, c.c_uint
    bind(user, "CreateDesktopW", w.HANDLE, w.LPCWSTR, w.LPCWSTR, V, w.DWORD, w.DWORD, V)
    bind(user, "CloseDesktop", w.BOOL, w.HANDLE)
    bind(user, "SetThreadDesktop", w.BOOL, w.HANDLE)
    bind(user, "GetThreadDesktop", w.HANDLE, w.DWORD)
    bind(user, "OpenInputDesktop", w.HANDLE, w.DWORD, w.BOOL, w.DWORD)
    bind(user, "GetUserObjectInformationW", w.BOOL, w.HANDLE, I, V, w.DWORD, c.POINTER(w.DWORD))
    bind(user, "GetWindowLongPtrW", c.c_ssize_t, w.HWND, I)
    bind(user, "SendMessageW", c.c_ssize_t, w.HWND, U, w.WPARAM, w.LPARAM)
    bind(user, "PostMessageW", w.BOOL, w.HWND, U, w.WPARAM, w.LPARAM)
    bind(user, "IsWindow", w.BOOL, w.HWND)
    bind(user, "IsWindowVisible", w.BOOL, w.HWND)
    bind(user, "IsIconic", w.BOOL, w.HWND)
    bind(user, "GetWindowRect", w.BOOL, w.HWND, c.POINTER(w.RECT))
    bind(user, "GetWindowTextW", I, w.HWND, w.LPWSTR, I)
    bind(user, "CreateWindowExW", w.HWND, w.DWORD, w.LPCWSTR, w.LPCWSTR,
         w.DWORD, I, I, I, I, w.HWND, w.HMENU, w.HINSTANCE, V)
    bind(user, "DestroyWindow", w.BOOL, w.HWND)
    bind(user, "ShowWindow", w.BOOL, w.HWND, I)
    bind(user, "PeekMessageW", w.BOOL, c.POINTER(w.MSG), w.HWND, U, U, U)
    bind(user, "TranslateMessage", w.BOOL, c.POINTER(w.MSG))
    bind(user, "DispatchMessageW", c.c_ssize_t, c.POINTER(w.MSG))
    bind(user, "GetGuiResources", w.DWORD, w.HANDLE, w.DWORD)
    bind(user, "GetThreadDpiAwarenessContext", V)
    bind(kernel, "GetCurrentThreadId", w.DWORD)
    bind(kernel, "GetCurrentProcess", w.HANDLE)
    bind(kernel, "GetModuleHandleW", w.HMODULE, w.LPCWSTR)
    results = []
    def check(value, label):
        if not value:
            raise AssertionError(label+" (Win32 error "+str(c.get_last_error())+")")
        results.append(label)
    def name(handle):
        buffer, needed = c.create_unicode_buffer(256), w.DWORD()
        if not user.GetUserObjectInformationW(handle, 2, buffer, c.sizeof(buffer), c.byref(needed)):
            raise c.WinError(c.get_last_error())
        return buffer.value
    original = user.GetThreadDesktop(kernel.GetCurrentThreadId())
    input_desktop = user.OpenInputDesktop(0, False, 1)
    if not original or not input_desktop:
        raise c.WinError(c.get_last_error())
    input_name = name(input_desktop)
    desktop = surface = editor = edit = manager = None
    report = {"visible_desktop_switched": False, "real_mouse": False,
              "retailer_launched": False, "audio": False, "checks": results}
    try:
        desktop = user.CreateDesktopW("ChuddyTest_"+uuid.uuid4().hex, None, None, 0, 0x83, None)
        if not desktop:
            raise c.WinError(c.get_last_error())
        check(user.SetThreadDesktop(desktop), "attached to disposable desktop")
        check(name(user.GetThreadDesktop(kernel.GetCurrentThreadId())) != input_name,
              "test desktop differs from input desktop")
        from chuddy.desktop_overlay import _Win32Surface
        from chuddy.interception import PreservingWindows, InterceptionCoordinator
        dpi_before = user.GetThreadDpiAwarenessContext()
        surface = _Win32Surface(ROOT/"web/assets/chuddy/gestures.png")
        report["monitor_workarea"] = list(surface.bounds_for((100, 100)))
        left, top, right, bottom = report["monitor_workarea"]
        check(right > left and bottom > top, "real monitor workarea is usable")
        check(user.IsWindow(surface.hwnd), "real layered HWND created")
        surface.draw(80, 80)
        check(user.IsWindowVisible(surface.hwnd), "real alpha update and no-activate show succeeded")
        styles = user.GetWindowLongPtrW(surface.hwnd, -20)
        for bit, label in ((0x80000, "layered"), (0x20, "click-through"),
                           (0x80, "tool-window"), (0x8000000, "no-activate"), (8, "topmost")):
            check(styles & bit, "native "+label+" style")
        check(user.SendMessageW(surface.hwnd, 0x84, 0, 0) == -1, "native transparent hit-test")
        check(user.SendMessageW(surface.hwnd, 0x21, 0, 0) == 3, "native mouse activation refused")
        rect = w.RECT()
        user.GetWindowRect(surface.hwnd, c.byref(rect))
        report["canvas_rect"] = [rect.left, rect.top, rect.right, rect.bottom]
        check((rect.left, rect.top, rect.right-rect.left, rect.bottom-rect.top) ==
              (80-surface.MESSAGE_WIDTH,80-surface.SPRITE_TOP,surface.WIDTH,surface.HEIGHT),
              "native canvas position and dimensions")
        sprite_left,sprite_top=rect.left+surface.MESSAGE_WIDTH,rect.top+surface.SPRITE_TOP
        report["sprite_rect"] = [sprite_left,sprite_top,sprite_left+surface.SPRITE_WIDTH,sprite_top+surface.SPRITE_HEIGHT]
        check(report["sprite_rect"]==[80,80,192,248],"fixed sprite anchor is independent of warning canvas")
        pixels = c.string_at(surface.bits, surface.WIDTH*surface.HEIGHT*4)
        alpha = pixels[3::4]
        report["alpha_range"] = [min(alpha), max(alpha)]
        check(min(alpha) == 0 and max(alpha) >= 200,
              "native raster has transparent and visible pixels "+str(report["alpha_range"]))
        surface.set_message("I see work: "+"W"*40+". Chud Zone in 8 seconds if you keep working.")
        surface.draw(80,80)
        warning=c.string_at(surface.bits,len(pixels))
        check(warning!=pixels,"native warning changes actual GDI+ pixels")
        check(surface.message_font_size>=22,"long work label retains readable physical font size")
        def dark_pixels(top,bottom):
            return sum(warning[(row*surface.WIDTH+col)*4+3]==255 and
                       max(warning[(row*surface.WIDTH+col)*4:(row*surface.WIDTH+col)*4+3])<100
                       for row in range(top,bottom) for col in range(24,472))
        check(dark_pixels(20,178)>100,"actual wrapped warning text is visible")
        check(dark_pixels(186,222)>100,"independent Stop control text is visible")
        measured=surface.RECTF()
        layout=surface.RECTF(24,20,448,10000)
        fitted,lines=c.c_int(),c.c_int()
        surface.check(surface.gp.GdipMeasureString(surface.graphics,surface.message,-1,surface.body_font,
            c.byref(layout),None,c.byref(measured),c.byref(fitted),c.byref(lines)))
        check(fitted.value==len(surface.message) and measured.height<=158,"full long-label warning fits before Stop footer")
        report["warning_font_physical_pixels"]=surface.message_font_size
        surface.set_message(None);surface.draw(80,80)
        check(c.string_at(surface.bits,len(pixels))==pixels,"clearing warning restores unchanged sprite pixels")
        check(all(not any(pixels[row*surface.WIDTH*4:(row*surface.WIDTH+surface.MESSAGE_WIDTH)*4])
                  for row in range(surface.HEIGHT)),"entire cleared text canvas is zero BGRA and transparent")
        # Queue a synthetic message ONLY to this test HWND; no key injection or
        # global shortcut registration occurs on any desktop.
        user.PostMessageW(surface.hwnd, 0x312, surface.HOTKEY, 0)
        check(surface.pump(), "own-window Stop message dispatch")
        surface.hide()
        check(not user.IsWindowVisible(surface.hwnd), "native hide succeeded")

        instance = kernel.GetModuleHandleW(None)
        editor = user.CreateWindowExW(0x8000000, "STATIC", "Chuddy disposable editor", 0x00CF0000,
                                     50, 50, 600, 400, None, None, instance, None)
        if not editor:
            raise c.WinError(c.get_last_error())
        marker = "UNSAVED Chuddy test text " + uuid.uuid4().hex
        edit = user.CreateWindowExW(0, "EDIT", marker, 0x50000004,
                                   0, 0, 500, 300, editor, None, instance, None)
        if not edit:
            raise c.WinError(c.get_last_error())
        adapter = PreservingWindows()
        observation = {"handle": editor, "pid": os.getpid(), "title": "Disposable test"}
        adapter.foreground = lambda: observation
        def text():
            buffer = c.create_unicode_buffer(256)
            user.GetWindowTextW(edit, buffer, len(buffer))
            return buffer.value
        def placement():
            _, _, api, kind = adapter._api()
            item = kind()
            item.length = c.sizeof(item)
            check(api.GetWindowPlacement(editor, c.byref(item)), "read test editor placement")
            return item
        for show, label in ((1, "normal"), (3, "maximized")):
            user.ShowWindow(editor, show)
            before = placement()
            adapter.minimize_work(observation)
            check(user.IsIconic(editor), label+" editor minimized")
            check(text() == marker, label+" unsaved text survived minimize")
            adapter.restore(editor)
            after = placement()
            check(after.showCmd == before.showCmd, label+" editor state restored")
            check(bytes(after.normal) == bytes(before.normal), label+" editor rectangle restored")
            check(text() == marker, label+" unsaved text survived restore")

        launched = []
        class Launcher:
            def open(self, destination):
                launched.append(destination)
        manager = InterceptionCoordinator(allowed=lambda: True, cooldown=lambda: 0,
            windows=adapter, launcher=Launcher(), delay=.03, warning_seconds=0)
        reply = manager.request("Disposable test", destination="lcbo", mode="minimize",
                                window=observation, seen=time.monotonic())
        check(reply["ok"], "native manager accepted disposable work")
        deadline = time.monotonic()+3
        while manager._worker.is_alive() and time.monotonic() < deadline:
            # Pump the owning UI thread so synchronous cross-thread ShowWindow
            # cannot wait for a UI thread blocked in join().
            message = w.MSG()
            while user.PeekMessageW(c.byref(message), None, 0, 0, 1):
                user.TranslateMessage(c.byref(message))
                user.DispatchMessageW(c.byref(message))
            threading.Event().wait(.005)
        check(manager.wait(.2), "native manager worker completed")
        check(manager.info() and manager.info()["phase"] == "opened", "native manager reached opened")
        check(launched == ["lcbo"], "retailer routed to fake launcher only")
        check(user.IsIconic(editor), "native manager minimized only test editor")
        manager.stop()
        check(not user.IsIconic(editor) and text() == marker, "global Stop restored test editor and unsaved text")
        check(manager.diagnostics()["pending_restorations"] == 0, "native Stop has no pending restoration")
        manager.close()
        manager = None
        user.DestroyWindow(editor)
        editor = edit = None

        hwnd = surface.hwnd
        surface.close()
        surface = None
        check(user.GetThreadDpiAwarenessContext() == dpi_before, "original thread DPI context restored")
        check(not user.IsWindow(hwnd), "real overlay HWND destroyed")
        process = kernel.GetCurrentProcess()
        # Exercise the complete click-render lifecycle before measuring so
        # one-time GDI+/pen/driver caches are not mistaken for per-window leaks.
        for _ in range(16):
            item = _Win32Surface(ROOT/"web/assets/chuddy/gestures.png")
            item.set_message("I see work. Chud Zone in 8 seconds if you keep working.")
            item.draw(80, 80, click=True)
            item.close()
        c.WinDLL("gdi32").GdiFlush()
        resources_before = [user.GetGuiResources(process, flag) for flag in (0, 1)]
        for _ in range(12):
            item = _Win32Surface(ROOT/"web/assets/chuddy/gestures.png")
            item.set_message("I see work. Chud Zone in 8 seconds if you keep working.")
            item.draw(80, 80, click=True)
            item.close()
        c.WinDLL("gdi32").GdiFlush()
        resources_after = [user.GetGuiResources(process, flag) for flag in (0, 1)]
        report["resources_before_after"] = [resources_before, resources_after]
        check(all(end <= start+1 for start, end in zip(resources_before, resources_after)),
              "twelve native lifecycles have no growing GDI/USER handle count "+str(report["resources_before_after"]))
        check(name(input_desktop) == input_name, "input desktop identity unchanged")
        report["ok"] = True
    finally:
        if manager:
            manager.close()
        if surface:
            surface.close()
        if editor:
            user.DestroyWindow(editor)
        if desktop:
            # Return only this test thread to its original desktop after all its
            # windows are destroyed; NEVER make either desktop the input desktop.
            if not user.SetThreadDesktop(original):
                report["thread_restore_error"] = c.get_last_error()
            if not user.CloseDesktop(desktop):
                report["desktop_close_error"] = c.get_last_error()
        user.CloseDesktop(input_desktop)
    if "thread_restore_error" in report or "desktop_close_error" in report:
        raise AssertionError(str(report))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--isolated-desktop", action="store_true", required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        result = run()
    except Exception as error:
        import traceback
        traceback.print_exc()
        sys.exit(1)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))
