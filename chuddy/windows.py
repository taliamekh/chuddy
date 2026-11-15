"""Native window helpers for the foreground watcher and destination handoffs."""
import base64
from contextlib import contextmanager
import os
import queue
import subprocess
import threading


def _user32():
    import ctypes as c
    from ctypes import wintypes as w

    user = c.WinDLL("user32", use_last_error=True)
    signatures = {
        "GetForegroundWindow": (w.HWND, []),
        "GetWindowThreadProcessId": (w.DWORD, [w.HWND, c.POINTER(w.DWORD)]),
        "GetWindowTextW": (c.c_int, [w.HWND, w.LPWSTR, c.c_int]),
        "GetClassNameW": (c.c_int, [w.HWND, w.LPWSTR, c.c_int]),
        "GetAncestor": (w.HWND, [w.HWND, w.UINT]),
        "GetWindow": (w.HWND, [w.HWND, w.UINT]),
        "GetLastActivePopup": (w.HWND, [w.HWND]),
        "IsWindowVisible": (w.BOOL, [w.HWND]),
        "IsWindowEnabled": (w.BOOL, [w.HWND]),
        "IsIconic": (w.BOOL, [w.HWND]),
        "SetForegroundWindow": (w.BOOL, [w.HWND]),
        "PostMessageW": (w.BOOL, [w.HWND, w.UINT, w.WPARAM, w.LPARAM]),
        "GetWindowRect": (w.BOOL, [w.HWND, c.POINTER(w.RECT)]),
        "GetClientRect": (w.BOOL, [w.HWND, c.POINTER(w.RECT)]),
        "ClientToScreen": (w.BOOL, [w.HWND, c.POINTER(w.POINT)]),
        "GetWindowLongW": (w.LONG, [w.HWND, c.c_int]),
        "SetWindowLongW": (w.LONG, [w.HWND, c.c_int, w.LONG]),
        "ShowWindowAsync": (w.BOOL, [w.HWND, c.c_int]),
        "SetWindowPos": (w.BOOL, [w.HWND, w.HWND, c.c_int, c.c_int, c.c_int, c.c_int, w.UINT]),
    }
    for name, (result, arguments) in signatures.items():
        function = getattr(user, name)
        function.restype, function.argtypes = result, arguments
    return user


def _window_owner(user, handle):
    import ctypes as c
    from ctypes import wintypes as w

    pid = w.DWORD()
    thread_id = user.GetWindowThreadProcessId(handle, c.byref(pid))
    return (pid.value, thread_id) if thread_id and pid.value else None


def window_owner(handle):
    return _window_owner(_user32(), handle) if os.name == 'nt' and handle else None


def foreground_handle():
    return _user32().GetForegroundWindow()


def activate_window(handle):
    return bool(_user32().SetForegroundWindow(handle))


def window_key(window):
    if window and window.get('handle') and window.get('pid'):
        return (window['handle'], window['pid'], window.get('thread_id'))
    return None


def same_window(first, second):
    return bool(window_key(first) and window_key(first) == window_key(second))


def same_foreground(first, second):
    return (same_window(first, second) and first.get('title') == second.get('title')
            and first.get('process', '').casefold() == second.get('process', '').casefold())


def _transient(user, handle):
    import ctypes as c

    window_class = c.create_unicode_buffer(256)
    user.GetClassNameW(handle, window_class, len(window_class))
    return (not window_class.value or window_class.value in {'#32768', '#32770', 'tooltips_class32'}
            or user.GetAncestor(handle, 2) != handle  # GA_ROOT
            or bool(user.GetWindow(handle, 4))  # GW_OWNER
            or not user.IsWindowVisible(handle) or not user.IsWindowEnabled(handle))


def _process_image(pid):
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.windll.kernel32
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    process = kernel.OpenProcess(0x1000, False, pid)
    if process:
        try:
            buf = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(buf))
            if kernel.QueryFullProcessImageNameW(process, 0, buf, ctypes.byref(size)):
                return buf.value
        finally:
            kernel.CloseHandle(process)
    return ''


def foreground():
    if os.name != 'nt':
        return None
    import ctypes

    user = _user32()
    handle = user.GetForegroundWindow()
    if not handle:
        return None
    title = ctypes.create_unicode_buffer(2048)
    user.GetWindowTextW(handle, title, len(title))
    owner = _window_owner(user, handle)
    if owner is None:
        return None
    pid, thread_id = owner
    executable_path = _process_image(pid)
    transient = _transient(user, handle)
    if user.GetForegroundWindow() != handle or _window_owner(user, handle) != owner:
        return None
    return {'handle': handle, 'title': title.value, 'process': os.path.basename(executable_path), 'pid': pid,
            'thread_id': thread_id, 'is_transient': transient, 'executable_path': executable_path}


def request_close(window):
    """Post exactly one normal window close, with the app handling its own prompt."""
    current = foreground()
    if (not current or not current.get('thread_id') or not current.get('executable_path')
            or not current.get('title', '').strip() or current.get('is_transient')
            or current['pid'] == os.getpid() or not same_foreground(current, window)
            or current['executable_path'] != window.get('executable_path')):
        raise RuntimeError('The exact foreground window could not be verified for closing.')
    user = _user32()
    handle = window['handle']
    if (user.GetForegroundWindow() != handle or _window_owner(user, handle) != window_key(window)[1:]
            or _transient(user, handle)):
        raise RuntimeError('The focused window changed before closing.')
    popup = user.GetLastActivePopup(handle)
    if popup and popup != handle and user.IsWindowVisible(popup):
        raise RuntimeError('The app has an open prompt.')
    if not user.PostMessageW(handle, 0x0010, 0, 0):  # WM_CLOSE
        raise RuntimeError('Windows did not accept the close request.')


def close_status(window):
    """Confirm disappearance, or let an application-owned dialog take over."""
    user = _user32()
    handle = window['handle']
    owner = _window_owner(user, handle)
    if owner is None:
        return 'closed'
    if owner != window_key(window)[1:]:
        raise RuntimeError('The original window identity could not be confirmed.')
    popup = user.GetLastActivePopup(handle)
    if not user.IsWindowEnabled(handle) or (popup and popup != handle and user.IsWindowVisible(popup)):
        raise RuntimeError('The app is waiting for a response to its own prompt.')
    return 'pending'


def _monitor_bounds(user, handle):
    import ctypes as c
    from ctypes import wintypes as w

    class MonitorInfo(c.Structure):
        _fields_ = [('cbSize', w.DWORD), ('rcMonitor', w.RECT), ('rcWork', w.RECT), ('dwFlags', w.DWORD)]

    user.MonitorFromWindow.argtypes = [w.HWND, w.DWORD]
    user.MonitorFromWindow.restype = w.HMONITOR
    user.GetMonitorInfoW.argtypes = [w.HMONITOR, c.POINTER(MonitorInfo)]
    user.GetMonitorInfoW.restype = w.BOOL
    info = MonitorInfo()
    info.cbSize = c.sizeof(info)
    monitor = user.MonitorFromWindow(handle, 0)
    if monitor and user.GetMonitorInfoW(monitor, c.byref(info)):
        return info.rcMonitor


def _covers(rect, screen):
    return (rect.left <= screen.left and rect.top <= screen.top
            and rect.right >= screen.right and rect.bottom >= screen.bottom)


def fullscreen_status(handle, owner):
    """Read foreground ownership and monitor coverage of the exact native frame."""
    import ctypes as c
    from ctypes import wintypes as w

    user = _user32()
    if not owner or _window_owner(user, handle) != owner:
        return False, False
    rect = w.RECT()
    screen = _monitor_bounds(user, handle)
    full = bool(screen is not None and user.IsWindowVisible(handle) and not user.IsIconic(handle)
                and user.GetWindowRect(handle, c.byref(rect)) and _covers(rect, screen))
    if _window_owner(user, handle) != owner:
        return False, False
    return user.GetForegroundWindow() == handle, full


def foreground_fullscreen(window):
    """Confirm the exact foreground frame covers its monitor."""
    return all(fullscreen_status(window['handle'], window_key(window)[1:]))


def application_windows(paths):
    """Find usable top-level windows of the specified installed executables."""
    import ctypes as c
    from ctypes import wintypes as w

    user = _user32()
    paths = {os.path.normcase(str(path)) for path in paths}
    images, result = {}, []
    callback = c.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    user.EnumWindows.argtypes, user.EnumWindows.restype = [callback, w.LPARAM], w.BOOL

    @callback
    def collect(handle, _):
        owner = _window_owner(user, handle)
        if owner is None or not user.IsWindowVisible(handle):
            return True
        pid, thread_id = owner
        if pid not in images:
            images[pid] = os.path.normcase(_process_image(pid))
        path = images[pid]
        if path in paths and not _transient(user, handle) and _window_owner(user, handle) == owner:
            title = c.create_unicode_buffer(256)
            if user.GetWindowTextW(handle, title, len(title)):
                result.append({'handle': handle, 'pid': pid, 'thread_id': thread_id,
                               'executable_path': path, 'title': title.value})
        return True

    user.EnumWindows(collect, 0)
    return result


def application_path(handle, owner):
    if not owner:
        return ''
    user = _user32()
    path = _process_image(owner[0])
    return os.path.normcase(path) if _window_owner(user, handle) == owner else ''


@contextmanager
def _monitor_coordinates(user):
    import ctypes as c

    set_context = getattr(user, 'SetThreadDpiAwarenessContext', None)
    if set_context:
        set_context.argtypes, set_context.restype = [c.c_void_p], c.c_void_p
    previous = set_context(c.c_void_p(-4)) if set_context else None
    try:
        yield
    finally:
        if previous:
            set_context(previous)


def application_client_area(window):
    """Measure the actual client area on the monitor, excluding non-client chrome."""
    import ctypes as c
    from ctypes import wintypes as w

    user = _user32()
    handle, *owner = window_key(window)
    if (_window_owner(user, handle) != tuple(owner) or not user.IsWindowVisible(handle)
            or user.IsIconic(handle)):
        return None
    with _monitor_coordinates(user):
        screen = _monitor_bounds(user, handle)
        rect = w.RECT()
        if screen is None or not user.GetClientRect(handle, c.byref(rect)):
            return None
        start, end = w.POINT(rect.left, rect.top), w.POINT(rect.right, rect.bottom)
        if not all(user.ClientToScreen(handle, c.byref(point)) for point in (start, end)):
            return None
        client = w.RECT(start.x, start.y, end.x, end.y)
        if _window_owner(user, handle) != tuple(owner):
            return None
        return {'coverage': _covers(client, screen),
                'client_size': (client.right - client.left, client.bottom - client.top),
                'monitor_size': (screen.right - screen.left, screen.bottom - screen.top)}


def activate_application(window, expected_focus):
    """Ask Windows to surface one verified top-level application window."""
    user = _user32()
    handle, *owner = window_key(window)

    def focus_is_current():
        foreground = user.GetForegroundWindow()
        if not foreground:
            return expected_focus is None
        if expected_focus is None or foreground != expected_focus[0]:
            return False
        return _window_owner(user, foreground) == expected_focus[1:]

    if (_window_owner(user, handle) != tuple(owner) or _transient(user, handle)
            or not focus_is_current()):
        return False
    if user.IsIconic(handle):
        user.ShowWindowAsync(handle, 9)  # SW_RESTORE
    if not focus_is_current() or _window_owner(user, handle) != tuple(owner):
        return False
    user.SetForegroundWindow(handle)
    if user.GetForegroundWindow() == handle and _window_owner(user, handle) == tuple(owner):
        return True
    return _shell_activate_application(window, focus_is_current)


def _shell_activate_application(window, focus_is_current):
    """Use Windows' application activation service after a declined Win32 ask.

    This is a synchronous PID-scoped request, not a queued helper or key
    injection. The caller holds its cancellation lock across the final guard
    and effect. Actual foreground is still measured by the handoff afterwards.
    """
    import ctypes as c

    ole = c.WinDLL('ole32')
    ole.CoInitializeEx.argtypes, ole.CoInitializeEx.restype = [c.c_void_p, c.c_uint], c.c_long
    ole.CoUninitialize.argtypes, ole.CoUninitialize.restype = [], None
    initialized = ole.CoInitializeEx(None, 0)
    if initialized not in (0, 1, -2147417850):  # existing STA apartment is also usable
        return False
    shell = None
    try:
        import clr  # Existing pywebview/pythonnet dependency; no new runtime.
        from System import Activator, Array, Int32, Object, Type
        from System.Reflection import BindingFlags
        from System.Runtime.InteropServices import Marshal

        shell_type = Type.GetTypeFromProgID('WScript.Shell')
        if shell_type is None:
            return False
        shell = Activator.CreateInstance(shell_type)
        user = _user32()
        handle, *owner = window_key(window)
        path = window.get('executable_path')
        if not path:
            return False
        candidates = [item for item in application_windows([path]) if item['pid'] == owner[0]]
        if len(candidates) != 1 or window_key(candidates[0]) != window_key(window):
            return False
        if (not focus_is_current() or _window_owner(user, handle) != tuple(owner)
                or _transient(user, handle) or user.GetLastActivePopup(handle) != handle):
            return False
        return bool(shell_type.InvokeMember('AppActivate', BindingFlags.InvokeMethod,
                                            None, shell, Array[Object]([Int32(owner[0])])))
    except Exception:
        return False
    finally:
        try:
            if shell is not None:
                Marshal.FinalReleaseComObject(shell)
        finally:
            if initialized in (0, 1):
                ole.CoUninitialize()


def fill_application_monitor(window):
    """Place a verified focused client across the monitor, including DPI/borders."""
    import ctypes as c
    from ctypes import wintypes as w

    user = _user32()
    handle, *owner = window_key(window)
    with _monitor_coordinates(user):
        screen = _monitor_bounds(user, handle)
        if (screen is None or user.GetForegroundWindow() != handle
                or _window_owner(user, handle) != tuple(owner) or _transient(user, handle)):
            return None
        style = user.GetWindowLongW(handle, -16)
        frame = style & 0x00C40000  # WS_CAPTION | WS_THICKFRAME
        if frame:
            # Enter borderless fullscreen without changing global display
            # settings or making the window topmost. Keep app/system controls.
            c.set_last_error(0)
            original_rect = w.RECT()
            if not user.GetWindowRect(handle, c.byref(original_rect)):
                return 'rejected'
            previous = user.SetWindowLongW(handle, -16, style & ~0x00C40000)
            if not previous and c.get_last_error():
                return 'rejected'
            if (user.GetForegroundWindow() != handle or _window_owner(user, handle) != tuple(owner)):
                if _window_owner(user, handle) == tuple(owner):
                    user.SetWindowLongW(handle, -16, style)
                    user.SetWindowPos(handle, None, 0, 0, 0, 0, 0x4237)
                return None
            if user.SetWindowPos(handle, None, screen.left, screen.top,
                                 screen.right - screen.left, screen.bottom - screen.top,
                                 0x4234):  # ASYNC | NOOWNERZORDER | NOACTIVATE | NOZORDER | FRAMECHANGED
                window.setdefault('_fullscreen_original_frame', (style, (original_rect.left, original_rect.top,
                                                                         original_rect.right, original_rect.bottom)))
                return 'borderless'
            user.SetWindowLongW(handle, -16, style)
            user.SetWindowPos(handle, None, 0, 0, 0, 0, 0x4237)
            return 'rejected'
        outer, client = w.RECT(), w.RECT()
        if not user.GetWindowRect(handle, c.byref(outer)) or not user.GetClientRect(handle, c.byref(client)):
            return 'rejected'
        start, end = w.POINT(client.left, client.top), w.POINT(client.right, client.bottom)
        if not all(user.ClientToScreen(handle, c.byref(point)) for point in (start, end)):
            return 'rejected'
        # A normal maximize only fills the work area and leaves the taskbar.
        # A caption without a maximize button does not prohibit SetWindowPos.
        # Account for non-client borders in the same physical DPI context as
        # the monitor, so the *client* covers it rather than stopping short.
        left, top = start.x - outer.left, start.y - outer.top
        right, bottom = outer.right - end.x, outer.bottom - end.y
        # Keep the window's own style, z-order and focus. Its WM_SIZE handlers
        # can constrain the request, so later measurement remains authoritative.
        if (user.GetForegroundWindow() != handle or _window_owner(user, handle) != tuple(owner)):
            return None
        if user.SetWindowPos(handle, None, screen.left - left, screen.top - top,
                             screen.right - screen.left + left + right,
                             screen.bottom - screen.top + top + bottom,
                             0x4214):  # ASYNCWINDOWPOS | NOOWNERZORDER | NOACTIVATE | NOZORDER
            return 'resize'
        return 'rejected'


def restore_application_frame(window):
    """Undo an unaccepted borderless request on the same native window only."""
    original = window.pop('_fullscreen_original_frame', None)
    if not original:
        return
    user = _user32()
    handle, *owner = window_key(window)
    if _window_owner(user, handle) != tuple(owner):
        return
    style, (left, top, right, bottom) = original
    with _monitor_coordinates(user):
        current = user.GetWindowLongW(handle, -16)
        user.SetWindowLongW(handle, -16, (current & ~0x00C40000) | (style & 0x00C40000))
        user.SetWindowPos(handle, None, left, top, right - left, bottom - top, 0x4234)


def last_input_tick():
    """Read the current desktop session's generic input marker."""
    if os.name != 'nt':
        return None
    import ctypes
    from ctypes import wintypes

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [('cbSize', wintypes.UINT), ('dwTime', wintypes.DWORD)]

    user = ctypes.windll.user32
    user.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]
    user.GetLastInputInfo.restype = wintypes.BOOL
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    if not user.GetLastInputInfo(ctypes.byref(info)):
        return None
    return info.dwTime


def input_idle_seconds():
    """Age of generic input only, without event details or history."""
    tick = last_input_tick()
    if tick is None:
        return None
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.windll.kernel32
    kernel.GetTickCount.argtypes = []
    kernel.GetTickCount.restype = wintypes.DWORD
    return _input_tick_age(kernel.GetTickCount(), tick)


def _input_tick_age(now_tick, input_tick):
    # Both Windows values are 32-bit milliseconds and wrap every 49.7 days.
    return ((int(now_tick) - int(input_tick)) & 0xffffffff) / 1000


def minimize(handle):
    if os.name == 'nt' and handle:
        import ctypes
        ctypes.windll.user32.ShowWindow(ctypes.c_void_p(handle), 6)


def restore(handle):
    if os.name == 'nt' and handle:
        import ctypes
        ctypes.windll.user32.ShowWindow(ctypes.c_void_p(handle), 9)


class Voice:
    def __init__(self):
        self.queue = queue.Queue()
        self.process = None
        self.generation = 0
        threading.Thread(target=self._worker, daemon=True).start()

    def say(self, text):
        self.queue.put((self.generation, text[:1400]))

    def stop(self):
        self.generation += 1
        if self.process and self.process.poll() is None:
            self.process.terminate()

    def _worker(self):
        while True:
            generation, text = self.queue.get()
            if generation != self.generation or os.name != 'nt':
                continue
            # Only base64 enters the command, never user/model text as shell code.
            encoded = base64.b64encode(text.encode('utf-8')).decode('ascii')
            script = "Add-Type -AssemblyName System.Speech; $v=New-Object System.Speech.Synthesis.SpeechSynthesizer; $v.Rate=-2; $v.Speak([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + encoded + "')))"
            command = base64.b64encode(script.encode('utf-16le')).decode('ascii')
            try:
                self.process = subprocess.Popen(['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand', command], creationflags=0x08000000, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if generation != self.generation:
                    self.process.terminate()
                self.process.wait(timeout=90)
            except Exception:
                if self.process and self.process.poll() is None:
                    self.process.terminate()
