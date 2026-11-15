"""Exact native browser-tab controls, prepared only during an opted-in action.

No global mouse/keyboard input, window Close, page content or browser launch.
UIAutomation runs only on a bounded background MTA worker. Tab identities and
the host verified from browser chrome stay private in-memory targets.
"""
from concurrent.futures import Future, InvalidStateError, TimeoutError
import ctypes
from hashlib import sha256
import math
import os
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit, urlunsplit

from .windows import same_foreground, same_window
from .work_recognition import protected_browser_host


NATIVE_BROWSER_PROCESSES = ('chrome.exe', 'msedge.exe', 'brave.exe')


class NativeTabUnavailable(RuntimeError):
    """A fixed, non-sensitive explanation suitable for the normal app status."""


class NativeRuntimeUnavailable(NativeTabUnavailable):
    pass


class _Cancelled(Exception):
    pass


def _framework_folder():
    base = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Microsoft.NET'
    for platform in ('Framework64', 'Framework'):
        folder = base / platform / 'v4.0.30319' / 'WPF'
        if all((folder / name).is_file() for name in ('UIAutomationClient.dll', 'UIAutomationTypes.dll')):
            return folder
    return None


def _name(value):
    # Browser chrome labels sometimes include their keyboard shortcut. This is
    # name matching only; the implementation never sends those shortcuts.
    label = str(value or '').split(' (', 1)[0].strip().casefold()
    return label


class _UIATree:
    """Bounded browser-chrome traversal. Web Document subtrees are excluded."""
    def __init__(self, walker, types, invoke, selection, value_pattern=None):
        self.walker, self.types = walker, types
        self.invoke_pattern, self.selection_pattern = invoke, selection
        self.value_pattern = value_pattern

    @staticmethod
    def identity(element):
        return tuple(int(part) for part in element.GetRuntimeId())

    @staticmethod
    def pattern(element, pattern):
        try:
            return element.GetCurrentPattern(pattern)
        except Exception:
            return None

    def nodes(self, root):
        pending, visited = [(root, None)], 0
        while pending:
            element, tab = pending.pop()
            visited += 1
            if visited > 1000:
                raise NativeTabUnavailable('The browser tab controls could not be identified unambiguously.')
            kind = element.Current.ControlType
            if kind == self.types.Document:
                continue
            if kind == self.types.TabItem:
                tab = self.identity(element)
            yield element, tab
            children = []
            child = self.walker.GetFirstChild(element)
            while child is not None:
                children.append((child, tab))
                if len(children) + len(pending) > 1000:
                    raise NativeTabUnavailable('The browser tab controls could not be identified unambiguously.')
                child = self.walker.GetNextSibling(child)
            pending.extend(reversed(children))

    @staticmethod
    def point(element):
        bounds = element.Current.BoundingRectangle
        x, y, width, height = map(float, (bounds.X, bounds.Y, bounds.Width, bounds.Height))
        if not all(math.isfinite(value) for value in (x, y, width, height)) or width <= 0 or height <= 0:
            return None
        return [round(x + width/2), round(y + height/2)]

    def describe(self, root):
        tabs, addresses = {}, []
        for element, owner in self.nodes(root):
            current = element.Current
            if current.ControlType == self.types.TabItem:
                selected = self.pattern(element, self.selection_pattern)
                tabs[owner] = {'id': owner, 'selected': bool(selected and selected.Current.IsSelected),
                               'close': []}
            elif current.ControlType == self.types.Button:
                label = _name(current.Name)
                if not current.IsEnabled or current.IsOffscreen:
                    continue
                invoke = self.pattern(element, self.invoke_pattern)
                if invoke is None:
                    continue
                button = {'id': self.identity(element), 'point': self.point(element)}
                if owner is not None and label in ('close', 'close tab'):
                    tabs[owner]['close'].append(button)
            elif (self.value_pattern is not None and owner is None
                    and current.ControlType == self.types.Edit and not current.IsOffscreen
                    and _name(current.Name) in ('address and search bar', 'search or enter address')):
                value = self.pattern(element, self.value_pattern)
                addresses.append(self.address(str(value.Current.Value))
                                 if value is not None and not current.HasKeyboardFocus else None)
        return {'root_id': self.identity(root), 'title': str(root.Current.Name),
                'tabs': list(tabs.values()), 'address': addresses[0] if len(addresses) == 1 else None}

    @staticmethod
    def address(value):
        value = value.strip()
        if not value or any(char.isspace() for char in value):
            return None
        try:
            explicit = '://' in value
            parsed = urlsplit(value if explicit else 'https://' + value)
            if parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.password:
                return None
            clean = urlunsplit((parsed.scheme, parsed.netloc, parsed.path or '/', '', ''))
            urls = (clean,) if explicit else (clean, 'http://' + clean.removeprefix('https://'))
            return {'host': parsed.hostname, 'identity': sha256(value.encode('utf-8')).digest(), 'urls': urls}
        except (ValueError, UnicodeError):
            return None

    def resolve(self, root, identity, role, owner=None):
        found = []
        for element, tab in self.nodes(root):
            if self.identity(element) != tuple(identity):
                continue
            current = element.Current
            if role == 'tab' and current.ControlType == self.types.TabItem and tab == tuple(identity):
                found.append(element)
            elif role == 'close' and current.ControlType == self.types.Button and tab == tuple(owner or ()):
                if _name(current.Name) in ('close', 'close tab') and current.IsEnabled and not current.IsOffscreen:
                    found.append(element)
        if len(found) != 1:
            raise NativeTabUnavailable('The exact browser tab control changed before the action.')
        return found[0]


class WindowsUIAProvider:
    """Existing pythonnet + Windows framework UIAutomation; no extra service."""
    def __init__(self):
        self._ole = None

    def start(self):
        if os.name != 'nt':
            raise NativeRuntimeUnavailable('Native tab controls are available on Windows only.')
        folder = _framework_folder()
        if folder is None:
            raise NativeRuntimeUnavailable('Windows UI Automation is unavailable on this computer.')
        ole = ctypes.OleDLL('ole32')
        ole.CoInitializeEx.argtypes, ole.CoInitializeEx.restype = [ctypes.c_void_p, ctypes.c_uint], ctypes.c_long
        result = ole.CoInitializeEx(None, 0)  # COM MTA; never the app/WinForms UI apartment.
        if result not in (0, 1):
            raise NativeRuntimeUnavailable('The background tab-control worker could not start.')
        self._ole = ole
        try:
            import clr
            clr.AddReference(str(folder / 'UIAutomationClient.dll'))
            clr.AddReference(str(folder / 'UIAutomationTypes.dll'))
            from System import IntPtr
            from System.Windows.Automation import (AutomationElement, ControlType, InvokePattern,
                                                   SelectionItemPattern, TreeWalker, ValuePattern)
            self._element, self._pointer = AutomationElement, IntPtr
            self._tree = _UIATree(TreeWalker.ControlViewWalker, ControlType,
                                  InvokePattern.Pattern, SelectionItemPattern.Pattern, ValuePattern.Pattern)
        except Exception as error:
            self.close()
            raise NativeRuntimeUnavailable('Windows UI Automation could not load in the local runtime.') from error

    def close(self):
        if self._ole is not None:
            self._ole.CoUninitialize()
            self._ole = None

    def _root(self, window):
        root = self._element.FromHandle(self._pointer(int(window['handle'])))
        if (root is None or int(root.Current.ProcessId) != window['pid']
                or (int(root.Current.NativeWindowHandle) & 0xffffffff) != (window['handle'] & 0xffffffff)):
            raise NativeTabUnavailable('The original browser window is no longer available.')
        return root

    def snapshot(self, window):
        return self._tree.describe(self._root(window))

    def invoke_close(self, window, tab_id, button_id, guard):
        element = self._tree.resolve(self._root(window), button_id, 'close', tab_id)
        tab = self._tree.resolve(self._root(window), tab_id, 'tab')
        selection = self._tree.pattern(tab, self._tree.selection_pattern)
        if selection is None or not selection.Current.IsSelected:
            raise NativeTabUnavailable('The original work tab is no longer selected.')
        pattern = self._tree.pattern(element, self._tree.invoke_pattern)
        if pattern is None:
            raise NativeTabUnavailable('The work tab does not expose its Close button.')
        guard()  # Immediately before the one native effect, without application locks.
        pattern.Invoke()


class NativeBrowserTabs:
    def __init__(self, provider_factory=None, window_api=None, *, prepare_timeout=4., action_timeout=4.5,
                 clock=time.monotonic, poll_interval=.03):
        if window_api is None:
            from . import windows
            window_api = windows
        self.window_api, self.provider_factory = window_api, provider_factory or WindowsUIAProvider
        self.prepare_timeout, self.action_timeout = prepare_timeout, action_timeout
        self.clock, self.poll_interval = clock, poll_interval
        self.lock = threading.RLock()
        self._available = bool(provider_factory is not None or (os.name == 'nt' and _framework_folder()))
        self._ready, self._busy, self._closed = False, False, False
        self._generation, self._stop, self._future, self._kind = 0, threading.Event(), None, None
        self._status = 'idle' if self._available else 'unavailable'
        self._reason = 'Native tab controls are checked when a work action starts.' if self._available else 'Native tab controls are unavailable on this computer.'

    def info(self):
        with self.lock:
            return {'available': self._available and not self._closed, 'ready': self._ready,
                    'busy': self._busy, 'status': self._status, 'reason': self._reason}

    @staticmethod
    def _settle(future, value=None, error=None):
        try:
            if error is not None:
                future.set_exception(error)
            else:
                future.set_result(value)
        except InvalidStateError:
            pass

    def _guard(self, generation, stop, live, valid=None):
        with self.lock:
            cancelled = self._closed or generation != self._generation or stop.is_set()
        if cancelled or not live() or (valid is not None and not valid()):
            raise _Cancelled()

    def _window(self, expected, strict_title=True):
        current = self.window_api.foreground()
        matches = same_foreground(current, expected) if strict_title else same_window(current, expected)
        if not matches or current.get('is_transient'):
            raise NativeTabUnavailable('The focused browser or work tab changed before the action.')

    @staticmethod
    def _tab(snapshot, identity=None):
        rows = [tab for tab in snapshot['tabs'] if (tuple(tab['id']) == tuple(identity)
                if identity is not None else tab['selected'])]
        if len(rows) != 1:
            raise NativeTabUnavailable('The active work tab could not be identified unambiguously.')
        return rows[0]

    @staticmethod
    def _close_button(tab):
        if len(tab['close']) != 1 or tab['close'][0].get('point') is None:
            raise NativeTabUnavailable('The active work tab does not expose one visible Close button.')
        return tab['close'][0]

    @staticmethod
    def _work_address(snapshot, window, policy):
        address = snapshot.get('address')
        if not address:
            raise NativeTabUnavailable('The active tab address could not be verified.')
        if protected_browser_host(address['host']):
            raise NativeTabUnavailable('The active site is protected from interruptions.')
        if not all(policy(window, url) for url in address['urls']):
            raise NativeTabUnavailable('The active tab is excluded by work detection rules.')
        return address['identity']

    def _snapshot(self, provider, window, target=None, strict_title=True, focused=True):
        if focused:
            self._window(window, strict_title)
        result = provider.snapshot(window)
        if (target is not None and tuple(result['root_id']) != tuple(target['root_id'])):
            raise NativeTabUnavailable('The original browser window changed before the action.')
        if strict_title and result['title'] != window['title']:
            raise NativeTabUnavailable('The original work document changed before the action.')
        return result

    def _prepare(self, provider, window, guard, policy):
        if (not isinstance(window, dict) or isinstance(window.get('handle'), bool)
                or not isinstance(window.get('handle'), int) or not isinstance(window.get('pid'), int)
                or not isinstance(window.get('title'), str)
                or str(window.get('process', '')).lower() not in NATIVE_BROWSER_PROCESSES):
            raise NativeTabUnavailable('Native tab control supports Chrome, Edge and Brave windows only.')
        guard()
        snapshot = self._snapshot(provider, window)
        # Chromium can briefly expose zero or multiple selected items while
        # focus settles. Re-read the same window before giving up on its tab.
        deadline = self.clock() + .5
        while sum(bool(tab['selected']) for tab in snapshot['tabs']) != 1 and self.clock() < deadline:
            time.sleep(self.poll_interval)
            guard()
            snapshot = self._snapshot(provider, window)
        address = self._work_address(snapshot, window, policy)
        tab = self._tab(snapshot)
        button = self._close_button(tab)
        guard()
        return {'kind': 'native', 'window': dict(window), 'root_id': tuple(snapshot['root_id']),
                'tab_id': tuple(tab['id']), 'close_id': tuple(button['id']),
                'close_point': list(button['point']), 'prepared_at': self.clock(),
                'address_identity': address, 'policy': policy}

    def _request(self, provider, target, gate, live_gate):
        if (not isinstance(target, dict) or target.get('kind') != 'native'
                or not 0 <= self.clock() - target.get('prepared_at', -math.inf) <= 12):
            raise NativeTabUnavailable('The prepared native work-tab controls expired.')
        window = target['window']

        def selected_work():
            snapshot = self._snapshot(provider, window, target)
            if self._work_address(snapshot, window, target['policy']) != target['address_identity']:
                raise NativeTabUnavailable('The work tab address changed before the action.')
            tab = self._tab(snapshot)
            if (tuple(tab['id']) != target['tab_id']
                    or tuple(self._close_button(tab)['id']) != target['close_id']):
                raise NativeTabUnavailable('The selected work tab changed before the action.')
            return snapshot

        gate()
        single = len(selected_work()['tabs']) == 1

        def close_guard():
            gate()
            if (len(selected_work()['tabs']) == 1) != single:
                raise NativeTabUnavailable('The browser tabs changed before the final tab close.')
            gate()

        provider.invoke_close(window, target['tab_id'], target['close_id'], close_guard)
        deadline = self.clock() + min(1.5, self.action_timeout)
        while True:
            live_gate()
            owner = getattr(self.window_api, 'window_owner', None)
            if owner and not owner(window['handle']):
                if single:
                    return True
                raise NativeTabUnavailable('The browser window disappeared before the work tab could be confirmed.')
            snapshot = self._snapshot(provider, window, target, strict_title=False, focused=False)
            if not any(tuple(row['id']) == target['tab_id'] for row in snapshot['tabs']):
                # A temporarily incomplete accessibility tree is not proof of
                # removal. Require an exposed sibling or the last frame's exit.
                if snapshot['tabs']:
                    return True
            if self.clock() >= deadline:
                raise NativeTabUnavailable('The browser did not confirm that the tab closed.')
            time.sleep(self.poll_interval)

    def _start(self, kind, operation, valid, live):
        future = Future()
        with self.lock:
            if self._closed or not self._available or self._busy:
                self._settle(future, error=NativeTabUnavailable('Native tab controls are busy or unavailable.'))
                return future
            self._generation += 1
            generation, stop = self._generation, threading.Event()
            self._stop, self._future, self._kind = stop, future, kind
            self._busy, self._ready = True, False
            self._status, self._reason = kind, 'Checking the active work tab.' if kind == 'preparing' else 'Closing the active work tab.'
        def guard():
            self._guard(generation, stop, live, valid)
        def live_guard():
            self._guard(generation, stop, live)
        timed_out = threading.Event()
        def timeout():
            timed_out.set()
            stop.set()
            with self.lock:
                if generation == self._generation and not self._closed:
                    self._ready, self._status, self._reason = False, 'unavailable', 'The browser tab controls did not respond in time.'
            self._settle(future, error=NativeTabUnavailable('The browser tab controls did not respond in time.'))
        timer = threading.Timer(self.prepare_timeout if kind == 'preparing' else self.action_timeout, timeout)
        timer.daemon = True
        def work():
            provider, value, error, cancelled = None, None, None, False
            try:
                guard()
                provider = self.provider_factory()
                if hasattr(provider, 'start'):
                    provider.start()
                guard()
                value = operation(provider, guard, live_guard)
                live_guard()
            except _Cancelled:
                cancelled = True
            except Exception as failure:
                error = failure if isinstance(failure, NativeTabUnavailable) else NativeTabUnavailable('Windows could not confirm the exact work-tab controls.')
            finally:
                timer.cancel()
                if provider is not None and hasattr(provider, 'close'):
                    try:
                        provider.close()
                    except Exception:
                        pass
                with self.lock:
                    self._busy = False
                    if generation == self._generation and not self._closed:
                        if isinstance(error, NativeRuntimeUnavailable):
                            self._available = False
                        if timed_out.is_set():
                            self._ready, self._status = False, 'unavailable'
                            self._reason = 'The browser tab controls did not respond in time.'
                        else:
                            self._ready = bool(not error and not cancelled and kind == 'preparing')
                            self._status = 'unavailable' if error else 'idle' if cancelled else 'ready' if kind == 'preparing' else 'completed'
                            self._reason = str(error) if error else 'Native tab action cancelled.' if cancelled else 'The active work-tab Close button is ready.' if kind == 'preparing' else 'The selected work tab closed.'
                if cancelled:
                    self._settle(future, None if kind == 'preparing' else False)
                else:
                    self._settle(future, value, error)
        threading.Thread(target=work, name='chuddy-native-tab', daemon=True).start()
        timer.start()
        return future

    def prepare_async(self, window, valid=lambda: True, live=lambda: True, policy=lambda window, url: True):
        descriptor = dict(window) if isinstance(window, dict) else window
        return self._start('preparing', lambda provider, guard, _live: self._prepare(provider, descriptor, guard, policy), valid, live)

    def prepare(self, window):
        try:
            return self.prepare_async(window).result(timeout=self.prepare_timeout + .25)
        except TimeoutError as error:
            self.cancel()
            raise NativeTabUnavailable('The browser tab controls did not respond in time.') from error

    def request(self, target, valid=lambda: True, live=lambda: True):
        return self._start('closing', lambda provider, guard, live_guard: self._request(provider, target, guard, live_guard), valid, live)

    def cancel(self):
        with self.lock:
            self._generation += 1
            self._stop.set()
            future, kind = self._future, self._kind
            self._ready, self._status, self._reason = False, 'cancelled', 'Native tab action cancelled.'
        if future is not None:
            self._settle(future, None if kind == 'preparing' else False)

    def close(self):
        self.cancel()
        with self.lock:
            self._closed, self._available, self._ready, self._status = True, False, False, 'closed'
            self._reason = 'Native tab controls are closed.'
