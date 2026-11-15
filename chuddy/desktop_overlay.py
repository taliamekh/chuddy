"""Optional Windows alpha desktop sprite. No cursor/input injection or dependencies.

Uses the existing raster's neutral cell through GDI+, in a layered, no-activate,
click-through tool window. Instantiation is inert. start()/perform() must only be
called after explicit session opt-in. Native tests use a non-input desktop;
brief visible checks require a separately authorized idle-only probe.
"""
from __future__ import annotations

import math
import os
from pathlib import Path
import queue
import threading
import time

APPROACH_SECONDS = .35
IDLE_ANCHOR = (.88, .86)
DEFAULT_WARNING = "I see work. Chud Zone in 8 seconds if you keep working."


def idle_position(seconds, reduced=False):
    """A fixed idle anchor; keep the old call signature for callers."""
    return (*IDLE_ANCHOR, False)


def sprite_position(seconds, bounds, target=None, *, width=112, height=168, reduced=False):
    """Bounded route in virtual-screen coordinates, including negative monitors."""
    left, top, right, bottom = bounds
    width, height = min(width, right-left), min(height, bottom-top)
    clamp = lambda value, low, high: max(low, min(high, value))
    start = (left+(right-left-width)*IDLE_ANCHOR[0], top+(bottom-top-height)*IDLE_ANCHOR[1])
    if target is None:
        u,v,_=idle_position(seconds,reduced)
        x = left+(right-left-width)*u
        y = top+(bottom-top-height)*v
    else:
        tx, ty = target
        progress = clamp(seconds/APPROACH_SECONDS, 0, 1)
        if reduced:
            progress = 1
        smooth = progress*progress*(3-2*progress)
        x = start[0]+(tx-width*.52-start[0])*smooth
        y = start[1]+(ty-height*.30-start[1])*smooth
        if not reduced and progress < 1:
            y -= abs(math.sin(seconds*18))*9
    return (round(clamp(x, left, right-width)), round(clamp(y, top, bottom-height)))


class DesktopOverlay:
    def __init__(self, asset, *, on_stop=lambda: None, reduced_motion=False, factory=None):
        self.asset = Path(asset)
        self.on_stop, self.reduced_motion = on_stop, reduced_motion
        self.factory = factory or _Win32Surface
        self.commands = queue.Queue()
        self.thread = None
        self.lock = threading.Lock()
        self.closed = False
        self.generation = 0
        self.enabled = False
        self.status = "stopped"
        self.error = None
        self.hotkey = False
        self.visible = False
        self.activity = "stopped"
        self.requested_activity = "stopped"
        self.rendered_frames = 0
        self.last_frame = None
        self.request_id = 0
        self.rendered_request_id = None
        self.activity_since = None
        self.native_operation = self.native_started = None
        self._surface = None

    def _submit(self, kind, window=None, destination=None, message=None):
        """Queue one request atomically with opt-in and its cancellation epoch."""
        with self.lock:
            if self.closed:
                return False
            if os.name != "nt" and self.factory is _Win32Surface:
                self.status = "unavailable"
                return False
            self.enabled = True
            self.request_id += 1
            self.requested_activity = {"arm":"hidden", "roam":"roaming", "warning":"warning", "perform":"approaching"}[kind]
            self.commands.put((kind, self.generation, dict(window) if window else None,
                               destination, message, self.request_id))
            if self.thread is None or not self.thread.is_alive():
                self.error = None
                self.status = "starting"
                self.thread = threading.Thread(target=self._run, daemon=True, name="chuddy-overlay")
                self.thread.start()
            return True

    def start(self):
        return self._submit("arm")

    def perform(self, window=None, destination="game"):
        return self._submit("perform", window, destination)

    def roam(self):
        """Compatibility name: show the stationary idle buddy."""
        return self._submit("roam")

    def warning(self, window=None, destination="game", message=DEFAULT_WARNING):
        """Show a still buddy and readable warning; never move or click."""
        return self._submit("warning", window, destination, str(message or DEFAULT_WARNING))

    def hide(self):
        with self.lock:
            self.generation += 1
            self.visible = False
            self.rendered_request_id = self.activity_since = None
            self.activity = self.requested_activity = "hidden"
            self.commands.put(("hide", self.generation))

    def disarm(self):
        """Stop rendering and release the optional global hotkey on session Stop."""
        with self.lock:
            self.generation += 1
            self.enabled = False
            self.visible = False
            self.rendered_request_id = self.activity_since = None
            self.activity = self.requested_activity = "stopped"
            if self.status != "unavailable":
                self.status = "stopped"
            self.commands.put(("disarm", self.generation))

    def info(self):
        with self.lock:
            return {"status": self.status, "error": self.error, "hotkey": self.hotkey,
                "stop_shortcut": "Ctrl+Alt+Escape" if self.hotkey else None,
                "enabled": self.enabled, "visible": self.visible,
                "activity": self.activity, "requested_activity": self.requested_activity,
                "worker_alive": bool(self.thread and self.thread.is_alive()),
                "rendered_frames": self.rendered_frames,
                "request_id": self.request_id, "rendered_request_id": self.rendered_request_id,
                "activity_since": self.activity_since,
                "native_operation": self.native_operation,
                "native_operation_age": None if self.native_started is None else max(0,time.monotonic()-self.native_started),
                "native_stage": getattr(self._surface,"stage",None),
                "last_frame_age": None if self.last_frame is None else max(0, time.monotonic()-self.last_frame),
                "click_through": True, "real_mouse": False}

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            self.enabled = False
            self.generation += 1
            self.visible = False
            self.rendered_request_id = self.activity_since = None
            self.activity = self.requested_activity = "stopped"
            self.commands.put(("close",))
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(2)
        if self.thread is None or not self.thread.is_alive():
            self.status = "closed"

    def _native_call(self, operation, callback, *args, **kwargs):
        """Never hold the status lock across GDI/user32 or caller code."""
        with self.lock:
            self.native_operation, self.native_started = operation, time.monotonic()
        try:
            return callback(*args, **kwargs)
        finally:
            with self.lock:
                self.native_operation = self.native_started = None

    def _run(self):
        surface = None
        visible, target, began, generation, mode, request_id = False, None, time.monotonic(), -1, "hidden", -1
        try:
            while not self.closed:
                try:
                    try:
                        command = self.commands.get(timeout=1/30)
                    except queue.Empty:
                        command = None
                    if command is not None:
                        kind = command[0]
                        if kind == "close":
                            break
                        with self.lock:
                            current = command[1] == self.generation and not self.closed
                            if len(command)>5:
                                current = current and command[5] == self.request_id
                            enabled = self.enabled
                        if not current:
                            continue
                        if kind in ("hide", "disarm"):
                            visible = False
                            if surface:
                                self._native_call("hide",surface.hide)
                                if kind == "disarm":
                                    self._native_call("disarm",surface.disarm)
                            if kind == "disarm":
                                with self.lock:
                                    self.hotkey = False
                            continue
                        if not enabled:
                            continue
                        # Slow initialization and native drawing must not block
                        # info(), Stop or cancellation behind the status lock.
                        if surface is None:
                            with self.lock:
                                self.status, self.error = "starting", None
                            surface = self._native_call("initialize",self.factory,self.asset)
                            with self.lock:
                                self._surface = surface
                        with self.lock:
                            current = (command[1] == self.generation and command[5] == self.request_id
                                       and self.enabled and not self.closed)
                        if not current:
                            self._native_call("hide",surface.hide)
                            continue
                        hotkey = self._native_call("arm",surface.arm)
                        new_target = None
                        if kind in ("perform", "roam", "warning"):
                            set_message = getattr(surface, "set_message", None)
                            if set_message:
                                self._native_call("message",set_message,command[4] if kind == "warning" else None)
                            if kind == "perform" and command[2]:
                                rect = self._native_call("work_rect",surface.rect,command[2].get("handle"))
                                if rect:
                                    new_target = (rect[2]-36, rect[1]+24)
                                    point = command[2].get('tab_close_point')
                                    if 'tab_close_point' in command[2]:
                                        new_target = None  # Never visually substitute the window's X.
                                    if (isinstance(point, (tuple, list)) and len(point) == 2
                                            and all(isinstance(v, (int, float)) and math.isfinite(v) for v in point)
                                            and rect[0] <= point[0] <= rect[2]
                                            and rect[1] <= point[1] <= rect[3]):
                                        new_target = tuple(point)
                        with self.lock:
                            current = (command[1] == self.generation and command[5] == self.request_id
                                       and self.enabled and not self.closed)
                            if current:
                                self.hotkey, self.status = hotkey, "ready"
                        if not current:
                            self._native_call("hide",surface.hide)
                            self._native_call("disarm",surface.disarm)
                            continue
                        if kind in ("perform", "roam", "warning"):
                            visible, began, target, generation, request_id = True,time.monotonic(),new_target,command[1],command[5]
                            mode = {"perform":"approaching", "roam":"roaming", "warning":"warning"}[kind]
                    if surface is None:
                        continue
                    if self._native_call("pump",surface.pump):
                        with self.lock:
                            should_stop = self.hotkey and self.enabled and not self.closed
                        if should_stop:
                            self.on_stop()
                        visible = False
                        self._native_call("hide",surface.hide)
                        with self.lock:
                            self.visible = False
                            self.rendered_request_id = self.activity_since = None
                            self.activity = "hidden" if self.enabled else "stopped"
                    with self.lock:
                        current = (generation == self.generation and request_id == self.request_id
                                   and self.enabled and not self.closed)
                    if visible and not current:
                        visible = False
                        self._native_call("hide",surface.hide)
                    if visible and current:
                        elapsed = time.monotonic()-began
                        monitor_bounds = getattr(surface,"bounds_for",None)
                        bounds = (self._native_call("bounds",monitor_bounds,target) if monitor_bounds
                                  else self._native_call("bounds",surface.bounds))
                        point = sprite_position(elapsed,bounds,target,reduced=self.reduced_motion)
                        self._native_call("draw",surface.draw,*point,
                                          click=target is not None and elapsed>=APPROACH_SECONDS)
                        with self.lock:
                            current = (generation == self.generation and request_id == self.request_id
                                       and self.enabled and not self.closed)
                            if current:
                                now = time.monotonic()
                                if self.rendered_request_id != request_id:
                                    self.activity_since = now
                                self.visible, self.activity = True,mode
                                self.rendered_request_id = request_id
                                self.rendered_frames += 1
                                self.last_frame = now
                        if not current:
                            # A native call already in flight cannot be revoked;
                            # hide it on return and never acknowledge a late frame.
                            visible = False
                            self._native_call("hide",surface.hide)
                except Exception as error:
                    with self.lock:
                        self.status, self.error = "unavailable",type(error).__name__
                        self.visible, self.hotkey, self.activity = False,False,"hidden"
                        self.rendered_request_id = self.activity_since = None
                    visible = False
                    if surface:
                        try:
                            self._native_call("close",surface.close)
                        except Exception:
                            pass
                        surface = None
                        with self.lock:
                            self._surface = None
                    # Explicit retry only; never spin recreating failed windows.
        finally:
            if surface:
                self._native_call("close",surface.close)
            with self.lock:
                self._surface = None
                self.hotkey = self.visible = False
                self.rendered_request_id = self.activity_since = None
                self.activity = "stopped"
                if self.closed:
                    self.status = "closed"


class _Win32Surface:
    """All handles are created, pumped and destroyed on the dedicated UI thread."""
    SPRITE_WIDTH, SPRITE_HEIGHT = 112, 168
    MESSAGE_WIDTH, HEIGHT = 500, 240
    SPRITE_TOP = HEIGHT-SPRITE_HEIGHT
    WIDTH = MESSAGE_WIDTH + SPRITE_WIDTH
    STOP_MESSAGE = "Ctrl+Alt+Escape stops."
    HOTKEY = 0x4348

    def __init__(self, asset):
        import ctypes as c
        from ctypes import wintypes as w
        self.c, self.w = c, w
        self.user = c.WinDLL("user32", use_last_error=True)
        self.gdi = c.WinDLL("gdi32", use_last_error=True)
        self.gp = c.WinDLL("gdiplus", use_last_error=True)
        self.kernel = c.WinDLL("kernel32", use_last_error=True)
        self.hwnd = self.dc = self.bitmap = self.old_bitmap = None
        self.image = self.target_image = self.graphics = None
        self.font_family = self.font = self.body_font = self.text_brush = self.bubble_brush = None
        self.message_font_size = None
        self.message = ""
        self.token = c.c_size_t()
        self.registered = False
        self.hotkey = False
        self.previous_dpi = None
        self.stage = "initialize"

        class POINT(c.Structure):
            _fields_ = [("x", w.LONG), ("y", w.LONG)]
        class SIZE(c.Structure):
            _fields_ = [("cx", w.LONG), ("cy", w.LONG)]
        class BLEND(c.Structure):
            _fields_ = [("op", c.c_ubyte), ("flags", c.c_ubyte),
                        ("alpha", c.c_ubyte), ("format", c.c_ubyte)]
        class RECTF(c.Structure):
            _fields_ = [("x", c.c_float), ("y", c.c_float),
                        ("width", c.c_float), ("height", c.c_float)]
        class HEADER(c.Structure):
            _fields_ = [("size", w.DWORD), ("width", w.LONG), ("height", w.LONG),
                        ("planes", w.WORD), ("bits", w.WORD), ("compression", w.DWORD),
                        ("image_size", w.DWORD), ("x", w.LONG), ("y", w.LONG),
                        ("used", w.DWORD), ("important", w.DWORD)]
        class STARTUP(c.Structure):
            _fields_ = [("version", c.c_uint), ("callback", c.c_void_p),
                        ("background", w.BOOL), ("codecs", w.BOOL)]
        class MONITORINFO(c.Structure):
            _fields_ = [("size", w.DWORD), ("monitor", w.RECT),
                        ("work", w.RECT), ("flags", w.DWORD)]
        self.MONITORINFO = MONITORINFO
        self.POINT, self.SIZE, self.BLEND = POINT, SIZE, BLEND
        self.RECTF = RECTF
        self.proc_type = c.WINFUNCTYPE(c.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
        class WNDCLASS(c.Structure):
            _fields_ = [("style", w.UINT), ("proc", self.proc_type), ("cls", c.c_int),
                        ("wnd", c.c_int), ("instance", w.HINSTANCE), ("icon", w.HICON),
                        ("cursor", w.HANDLE), ("background", w.HBRUSH),
                        ("menu", w.LPCWSTR), ("name", w.LPCWSTR)]
        self.WNDCLASS = WNDCLASS
        # Explicit pointer signatures are essential on 64-bit Windows.
        def bind(dll, name, result, *args):
            fn = getattr(dll, name)
            fn.restype, fn.argtypes = result, list(args)
            return fn
        V, I, U = c.c_void_p, c.c_int, c.c_uint
        self.module = bind(self.kernel, "GetModuleHandleW", w.HMODULE, w.LPCWSTR)(None)
        bind(self.user, "DefWindowProcW", c.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
        bind(self.user, "RegisterClassW", w.ATOM, c.POINTER(WNDCLASS))
        bind(self.user, "UnregisterClassW", w.BOOL, w.LPCWSTR, w.HINSTANCE)
        bind(self.user, "CreateWindowExW", w.HWND, w.DWORD, w.LPCWSTR, w.LPCWSTR,
             w.DWORD, I, I, I, I, w.HWND, w.HMENU, w.HINSTANCE, V)
        bind(self.user, "DestroyWindow", w.BOOL, w.HWND)
        bind(self.user, "ShowWindow", w.BOOL, w.HWND, I)
        bind(self.user, "UpdateLayeredWindow", w.BOOL, w.HWND, w.HDC, c.POINTER(POINT),
             c.POINTER(SIZE), w.HDC, c.POINTER(POINT), w.DWORD, c.POINTER(BLEND), w.DWORD)
        bind(self.user, "SetWindowPos", w.BOOL, w.HWND, w.HWND, I, I, I, I, U)
        bind(self.user, "GetWindowLongPtrW", c.c_ssize_t, w.HWND, I)
        bind(self.user, "GetWindowRect", w.BOOL, w.HWND, c.POINTER(w.RECT))
        bind(self.user, "MonitorFromPoint", w.HANDLE, POINT, w.DWORD)
        bind(self.user, "MonitorFromWindow", w.HANDLE, w.HWND, w.DWORD)
        bind(self.user, "GetMonitorInfoW", w.BOOL, w.HANDLE, c.POINTER(MONITORINFO))
        bind(self.user, "PeekMessageW", w.BOOL, c.POINTER(w.MSG), w.HWND, U, U, U)
        bind(self.user, "TranslateMessage", w.BOOL, c.POINTER(w.MSG))
        bind(self.user, "DispatchMessageW", c.c_ssize_t, c.POINTER(w.MSG))
        bind(self.user, "RegisterHotKey", w.BOOL, w.HWND, I, U, U)
        bind(self.user, "UnregisterHotKey", w.BOOL, w.HWND, I)
        bind(self.gdi, "CreateCompatibleDC", w.HDC, w.HDC)
        bind(self.gdi, "CreateDIBSection", w.HBITMAP, w.HDC, c.POINTER(HEADER), U,
             c.POINTER(V), w.HANDLE, w.DWORD)
        bind(self.gdi, "SelectObject", w.HANDLE, w.HDC, w.HANDLE)
        bind(self.gdi, "DeleteObject", w.BOOL, w.HANDLE)
        bind(self.gdi, "DeleteDC", w.BOOL, w.HDC)
        bind(self.gp, "GdiplusStartup", I, c.POINTER(c.c_size_t), c.POINTER(STARTUP), V)
        bind(self.gp, "GdiplusShutdown", None, c.c_size_t)
        bind(self.gp, "GdipLoadImageFromFile", I, w.LPCWSTR, c.POINTER(V))
        bind(self.gp, "GdipGetImageWidth", I, V, c.POINTER(U))
        bind(self.gp, "GdipGetImageHeight", I, V, c.POINTER(U))
        bind(self.gp, "GdipCreateBitmapFromScan0", I, I, I, I, I, V, c.POINTER(V))
        bind(self.gp, "GdipGetImageGraphicsContext", I, V, c.POINTER(V))
        bind(self.gp, "GdipGraphicsClear", I, V, U)
        bind(self.gp, "GdipDrawImageRectRectI", I, V, V, I, I, I, I, I, I, I, I, I, V, V, V)
        bind(self.gp, "GdipFlush", I, V, I)
        bind(self.gp, "GdipCreatePen1", I, U, c.c_float, I, c.POINTER(V))
        bind(self.gp, "GdipDrawEllipseI", I, V, V, I, I, I, I)
        bind(self.gp, "GdipDeletePen", I, V)
        bind(self.gp, "GdipCreateFontFamilyFromName", I, w.LPCWSTR, V, c.POINTER(V))
        bind(self.gp, "GdipCreateFont", I, V, c.c_float, I, I, c.POINTER(V))
        bind(self.gp, "GdipCreateSolidFill", I, U, c.POINTER(V))
        bind(self.gp, "GdipFillRectangleI", I, V, V, I, I, I, I)
        bind(self.gp, "GdipSetTextRenderingHint", I, V, I)
        bind(self.gp, "GdipDrawString", I, V, w.LPCWSTR, I, V, c.POINTER(RECTF), V, V)
        bind(self.gp, "GdipMeasureString", I, V, w.LPCWSTR, I, V, c.POINTER(RECTF), V,
             c.POINTER(RECTF), c.POINTER(I), c.POINTER(I))
        bind(self.gp, "GdipDeleteFont", I, V)
        bind(self.gp, "GdipDeleteFontFamily", I, V)
        bind(self.gp, "GdipDeleteBrush", I, V)
        bind(self.gp, "GdipDeleteGraphics", I, V)
        bind(self.gp, "GdipDisposeImage", I, V)
        self.check = lambda code: self._check(code)
        try:
            # Use physical monitor/window coordinates only on this dedicated UI
            # thread. The pywebview/main thread and process DPI setting stay as
            # they were; restore this thread's previous context during cleanup.
            if hasattr(self.user, "SetThreadDpiAwarenessContext"):
                bind(self.user, "SetThreadDpiAwarenessContext", V, V)
                self.previous_dpi = self.user.SetThreadDpiAwarenessContext(V(-4))
            self.check(self.gp.GdiplusStartup(c.byref(self.token), c.byref(STARTUP(1)), None))
            self.image = V()
            self.check(self.gp.GdipLoadImageFromFile(str(asset.resolve()), c.byref(self.image)))
            iw, ih = U(), U()
            self.check(self.gp.GdipGetImageWidth(self.image, c.byref(iw)))
            self.check(self.gp.GdipGetImageHeight(self.image, c.byref(ih)))
            self.cell = (iw.value//3, ih.value//2)
            self.proc = self.proc_type(lambda hwnd, message, wp, lp:
                -1 if message == 0x84 else 3 if message == 0x21 else
                self.user.DefWindowProcW(hwnd, message, wp, lp))
            self.class_name = "ChuddyOverlay_"+str(id(self))
            klass = WNDCLASS(proc=self.proc, instance=self.module, name=self.class_name)
            if not self.user.RegisterClassW(c.byref(klass)):
                raise c.WinError(c.get_last_error())
            self.registered = True
            # WS_EX_TOPMOST | LAYERED | TRANSPARENT | TOOLWINDOW | NOACTIVATE.
            # Establish topmost at creation as well as each checked position.
            self.hwnd = self.user.CreateWindowExW(0x080800A8, self.class_name, "Chuddy cursor",
                0x80000000, 0, 0, self.WIDTH, self.HEIGHT, None, None, self.module, None)
            if not self.hwnd:
                raise c.WinError(c.get_last_error())
            self.dc = self.gdi.CreateCompatibleDC(None)
            bits = V()
            header = HEADER(size=c.sizeof(HEADER), width=self.WIDTH, height=-self.HEIGHT,
                            planes=1, bits=32)
            self.bitmap = self.gdi.CreateDIBSection(self.dc, c.byref(header), 0, c.byref(bits), None, 0)
            if not self.dc or not self.bitmap:
                raise c.WinError(c.get_last_error())
            self.old_bitmap = self.gdi.SelectObject(self.dc, self.bitmap)
            self.bits = bits
            self.target_image, self.graphics = V(), V()
            self.check(self.gp.GdipCreateBitmapFromScan0(self.WIDTH, self.HEIGHT, self.WIDTH*4,
                0x000E200B, bits, c.byref(self.target_image)))  # PixelFormat32bppPARGB
            self.check(self.gp.GdipGetImageGraphicsContext(self.target_image, c.byref(self.graphics)))
            self.stage = "idle"
        except Exception:
            self.close()
            raise

    @staticmethod
    def _check(code):
        if code:
            raise RuntimeError("GDI+ status "+str(code))

    def arm(self):
        if not self.hotkey:
            self.hotkey = bool(self.user.RegisterHotKey(self.hwnd, self.HOTKEY, 0x4003, 0x1B))
        return self.hotkey

    def disarm(self):
        if self.hotkey:
            self.user.UnregisterHotKey(self.hwnd, self.HOTKEY)
            self.hotkey = False

    def bounds(self):
        get = self.user.GetSystemMetrics
        x, y = get(76), get(77)
        return x, y, x+get(78), y+get(79)

    def rect(self, handle):
        rect = self.w.RECT()
        if handle and self.user.GetWindowRect(handle, self.c.byref(rect)):
            return rect.left, rect.top, rect.right, rect.bottom
        return None

    def bounds_for(self, target=None):
        """Keep the sprite in one real monitor work area, never in desktop gaps."""
        monitor = (self.user.MonitorFromPoint(self.POINT(*map(round, target)), 2)
                   if target is not None else self.user.MonitorFromWindow(self.hwnd, 1))
        info = self.MONITORINFO()
        info.size = self.c.sizeof(info)
        if monitor and self.user.GetMonitorInfoW(monitor, self.c.byref(info)):
            work = info.work
            return work.left, work.top, work.right, work.bottom
        return self.bounds()

    def draw(self, x, y, click=False):
        c = self.c
        self.stage = "graphics_clear"
        self.check(self.gp.GdipGraphicsClear(self.graphics, 0))
        self.stage = "raster"
        self.check(self.gp.GdipDrawImageRectRectI(self.graphics, self.image,
            self.MESSAGE_WIDTH, self.SPRITE_TOP, self.SPRITE_WIDTH, self.SPRITE_HEIGHT,
            0, 0, *self.cell, 2, None, None, None))
        if self.message:
            self.stage = "warning_text"
            self._draw_message()
        if click:
            pen = c.c_void_p()
            self.check(self.gp.GdipCreatePen1(0xEEB6C98E, 3, 2, c.byref(pen)))
            try:
                self.check(self.gp.GdipDrawEllipseI(self.graphics, pen,
                    self.MESSAGE_WIDTH+44, self.SPRITE_TOP+43, 22, 22))
            finally:
                self.gp.GdipDeletePen(pen)
        self.stage = "graphics_flush"
        self.check(self.gp.GdipFlush(self.graphics, 1))
        # The transparent text area extends left; x/y always describe the sprite
        # itself, so showing or clearing a warning cannot shift Patrick.
        window_x, window_y = x-self.MESSAGE_WIDTH, y-self.SPRITE_TOP
        point, source = self.POINT(window_x, window_y), self.POINT(0, 0)
        size, blend = self.SIZE(self.WIDTH, self.HEIGHT), self.BLEND(0, 0, 255, 1)
        self.stage = "layered_update"
        if not self.user.UpdateLayeredWindow(self.hwnd, None, c.byref(point), c.byref(size),
            self.dc, c.byref(source), 0, c.byref(blend), 2):
            raise c.WinError(c.get_last_error())
        # SHOWWINDOW already shows without activation. A subsequent ShowWindow
        # could change the z-order again; keep one checked topmost operation.
        self.stage = "topmost"
        if not self.user.SetWindowPos(self.hwnd, self.w.HWND(-1), window_x, window_y, 0, 0, 0x51):
            raise c.WinError(c.get_last_error() or 31)
        self.stage = "topmost_verify"
        if not self.user.GetWindowLongPtrW(self.hwnd, -20) & 0x8:
            raise RuntimeError("Overlay topmost placement was not retained.")
        self.stage = "idle"

    def set_message(self, message):
        value = " ".join(str(message or "").split())[:130]
        if value != self.message:
            if self.body_font:
                self.gp.GdipDeleteFont(self.body_font)
                self.body_font = None
            self.message_font_size = None
            self.message = value

    def _draw_message(self):
        c = self.c
        if not self.font:
            self.font_family, self.font = c.c_void_p(), c.c_void_p()
            self.text_brush, self.bubble_brush = c.c_void_p(), c.c_void_p()
            self.check(self.gp.GdipCreateFontFamilyFromName("Segoe UI", None, c.byref(self.font_family)))
            self.check(self.gp.GdipCreateFont(self.font_family, 26, 0, 2, c.byref(self.font)))
            self.check(self.gp.GdipCreateSolidFill(0xFF172214, c.byref(self.text_brush)))
            self.check(self.gp.GdipCreateSolidFill(0xFFF0F4D5, c.byref(self.bubble_brush)))
        self.check(self.gp.GdipFillRectangleI(self.graphics, self.bubble_brush, 8, 8, 480, 220))
        self.check(self.gp.GdipSetTextRenderingHint(self.graphics, 3))
        layout = self.RECTF(24, 20, 448, 158)
        if not self.body_font:
            # Keep ordinary warnings at 26 physical pixels (13 logical at 200%).
            # Exceptional wide labels fit by measured wrapping, never over the
            # independent, full-size Stop shortcut footer.
            measure = self.RECTF(24, 20, 448, 10000)
            measured, fitted, lines = self.RECTF(), c.c_int(), c.c_int()
            for size in (26, 24, 22, 20, 18):
                self.body_font = c.c_void_p()
                self.check(self.gp.GdipCreateFont(self.font_family, size, 0, 2, c.byref(self.body_font)))
                self.check(self.gp.GdipMeasureString(self.graphics, self.message, -1, self.body_font,
                    c.byref(measure), None, c.byref(measured), c.byref(fitted), c.byref(lines)))
                self.message_font_size = size
                if measured.height <= layout.height or size == 18:
                    break
                self.gp.GdipDeleteFont(self.body_font)
                self.body_font = None
        self.check(self.gp.GdipDrawString(self.graphics, self.message, -1, self.body_font,
                                        c.byref(layout), None, self.text_brush))
        footer = self.RECTF(24, 186, 448, 36)
        stop_message = self.STOP_MESSAGE if self.hotkey else "Use Stop in Chuddy."
        self.check(self.gp.GdipDrawString(self.graphics, stop_message, -1, self.font,
                                        c.byref(footer), None, self.text_brush))

    def pump(self):
        message, stop = self.w.MSG(), False
        while self.user.PeekMessageW(self.c.byref(message), None, 0, 0, 1):
            if message.message == 0x312 and message.wParam == self.HOTKEY:
                stop = True
            self.user.TranslateMessage(self.c.byref(message))
            self.user.DispatchMessageW(self.c.byref(message))
        return stop

    def hide(self):
        self.user.ShowWindow(self.hwnd, 0)

    def close(self):
        self.disarm()
        for name, delete in (("body_font", "GdipDeleteFont"), ("font", "GdipDeleteFont"), ("font_family", "GdipDeleteFontFamily"),
                             ("text_brush", "GdipDeleteBrush"), ("bubble_brush", "GdipDeleteBrush")):
            value = getattr(self, name, None)
            if value:
                getattr(self.gp, delete)(value)
                setattr(self, name, None)
        if self.graphics:
            self.gp.GdipDeleteGraphics(self.graphics)
            self.graphics = None
        for name in ("target_image", "image"):
            value = getattr(self, name)
            if value:
                self.gp.GdipDisposeImage(value)
                setattr(self, name, None)
        if self.old_bitmap and self.dc:
            self.gdi.SelectObject(self.dc, self.old_bitmap)
            self.old_bitmap = None
        if self.bitmap:
            self.gdi.DeleteObject(self.bitmap)
            self.bitmap = None
            self.bits = None
        if self.dc:
            self.gdi.DeleteDC(self.dc)
            self.dc = None
        if self.hwnd:
            self.user.DestroyWindow(self.hwnd)
            self.hwnd = None
        if self.registered:
            self.user.UnregisterClassW(self.class_name, self.module)
            self.registered = False
        if self.token.value:
            self.gp.GdiplusShutdown(self.token)
            self.token.value = 0
        if self.previous_dpi:
            self.user.SetThreadDpiAwarenessContext(self.previous_dpi)
            self.previous_dpi = None
