"""Cancellable, opt-in distraction choreography; construction never acts on a desktop.

The controller remains the session authority via ``allowed``. Pass its RLock as
``lock`` so Stop and effects share one serialization boundary. Dependencies are
injectable: tests need neither a desktop nor Chrome, speakers or hardware.
"""
from __future__ import annotations

import math
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import uuid
from concurrent.futures import Future
from . import windows as desktop_windows
from .destinations import DESTINATIONS, LEAGUE_DESTINATION
from .league import LeagueLauncher
from .work_banter import BANTER_SOURCE, WorkBanter
from .work_recognition import BROWSERS


RETAIL_HOME = {
    "lcbo": "https://www.lcbo.com/en",
    "beer_store": "https://www.thebeerstore.ca/",
}


class ChromeLaunch:
    def __init__(self, process, executable):
        self.process, self.executable = process, executable

    def owns(self, window):
        return (window and window.get('pid') == self.process.pid
                and os.path.normcase(window.get('executable_path', '')) == os.path.normcase(str(self.executable))
                and self.process.poll() is None)

    def confirm(self, live):
        deadline = time.monotonic() + 5
        while live():
            if self.process.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError('Chrome did not confirm a fullscreen destination window.')
            window = desktop_windows.foreground()
            if (self.owns(window)
                    and not window.get('is_transient') and desktop_windows.foreground_fullscreen(window)):
                return window
            time.sleep(.025)
        return None


class ChromeLauncher:
    """Launch only two fixed homepages in Chrome, without shell/URL interpolation."""

    def __init__(self, executable=None, *, spawn=subprocess.Popen):
        self.executable = Path(executable) if executable else None
        self.spawn = spawn

    def find(self):
        if self.executable:
            return self.executable if self.executable.is_file() else None
        if os.name != "nt":
            return None
        # Fixed installation paths, not PATH search or a browser fallback.
        for variable, tail in (("PROGRAMFILES", "Google/Chrome/Application/chrome.exe"),
                               ("PROGRAMFILES(X86)", "Google/Chrome/Application/chrome.exe"),
                               ("LOCALAPPDATA", "Google/Chrome/Application/chrome.exe")):
            base = os.environ.get(variable)
            candidate = Path(base) / tail if base else None
            if candidate and candidate.is_file():
                return candidate
        return None

    def open(self, destination):
        if destination not in RETAIL_HOME:
            raise ValueError("Choose an allowlisted retailer homepage.")
        chrome = self.find()
        if chrome is None:
            raise RuntimeError("Google Chrome is unavailable; no browser was opened.")
        # Chrome's fullscreen flag belongs to its first process command line.
        # A dedicated temporary profile avoids reusing the user's Chrome process
        # or restoring old tabs. The profile lives until this browser exits.
        profile = tempfile.TemporaryDirectory(prefix="chuddy-destination-", ignore_cleanup_errors=True)
        try:
            process = self.spawn([str(chrome), f"--user-data-dir={profile.name}", "--no-first-run",
                "--no-default-browser-check", "--disable-background-mode", "--start-fullscreen",
                f"--app={RETAIL_HOME[destination]}"], shell=False, close_fds=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            profile.cleanup()
            raise

        def clean_up():
            process.wait()
            profile.cleanup()

        threading.Thread(target=clean_up, name="chuddy-destination-profile", daemon=True).start()
        return ChromeLaunch(process, chrome)


class InterceptionCoordinator:
    """A single pending performance, with fresh-focus checks before each effect.

    ``windows`` supplies foreground(), minimize(handle) and restore(handle).
    Optional identity(handle) strengthens PID/handle checks against handle reuse.
    ``overlay`` supplies warning(window, destination, message), perform(window,
    destination), roam(), disarm() and close(). The warning is stationary.
    ``active`` must validate fresh ongoing identity after admission; its presence
    permits an observation to remain valid beyond the initial freshness window.
    ``open_local(destination)`` selects game/casino and raises on failure.
    ``speak(text)`` must queue through the existing cancellable Voice, never a new
    engine. Caller cancels that shared voice on Stop/dismiss.
    """

    def __init__(self, *, allowed=lambda: False, cooldown=lambda: 90, windows=None,
                 overlay=None, launcher=None, league_launcher=None, open_local=lambda destination: None,
                 cover=lambda: None, uncover=lambda: None, speak=lambda text: None,
                 clock=time.monotonic, lock=None, delay=.45, freshness=8.0,
                 warning_seconds=8.0, on_warning=lambda message, ticket: None,
                 clear_warning=lambda: None, close_tab=None, prepare_tab=None,
                 recent_banter=lambda: (), banter_selector=None,
                 terminal_dialogue=False, on_dialogue_error=lambda error: None,
                 close_desktop=False, local_ready=lambda: True):
        if windows is None:
            windows = PreservingWindows()
        self.windows, self.overlay = windows, overlay
        self.launcher = launcher if launcher is not None else ChromeLauncher()
        self.league_launcher = league_launcher if league_launcher is not None else LeagueLauncher()
        self.allowed, self.cooldown = allowed, cooldown
        self.open_local, self.cover, self.uncover, self.speak = open_local, cover, uncover, speak
        self.clock, self.lock = clock, lock if lock is not None else threading.RLock()
        self.delay, self.freshness = delay, freshness
        self.warning_seconds = warning_seconds
        self.warning_render_timeout = 2
        self.on_warning, self.clear_warning = on_warning, clear_warning
        self.close_tab = close_tab
        self.prepare_tab = prepare_tab
        self.close_desktop, self.local_ready = close_desktop, local_ready
        self._blocked_foregrounds = set()
        self._destination_launches = {}
        self._browser_reports_after = 0
        self.handoff_valid = None
        self.handoff_foreground = None
        self.recent_banter = recent_banter
        self.terminal_dialogue, self.on_dialogue_error = terminal_dialogue, on_dialogue_error
        self._banter = WorkBanter()
        self.banter_selector = banter_selector or self._banter.choose
        self._warning_deadline = None
        self._warning_ticket = None
        self.last_started = -math.inf
        self.current = None
        self.casino_interruption_id = None
        self.casino_opening_id = None
        self.cancel_event = threading.Event()
        self.closed = False
        self._restores = []
        self._covered = False
        self._worker = None
        self._uncover_future = None
        self.restore_errors = []
        self.overlay_error = None
        self.warning_error = None

    @property
    def casino_interruption_active(self):
        return self.casino_interruption_id is not None

    def info(self):
        with self.lock:
            if not self.current:
                return None
            result = dict(self.current)
            result['warning_remaining'] = (max(0., self._warning_deadline - self.clock())
                if result['phase'] == 'warning' and self._warning_deadline is not None else 0.)
            return result

    def cooldown_remaining(self):
        with self.lock:
            return max(0, self.last_started + self.cooldown() - self.clock())

    def diagnostics(self):
        with self.lock:
            self._reconcile_uncover()
            return {"pending_restorations": len(self._restores), "covered": self._covered,
                    "restoration_errors": list(self.restore_errors), "overlay_error": self.overlay_error,
                    "warning_error": self.warning_error}

    def _reconcile_uncover(self):
        future = self._uncover_future
        if future is None or not future.done():
            return
        self._uncover_future = None
        try:
            if future.result() is True:
                self._covered = False
            else:
                self.restore_errors.append('NativeUncoverCancelled')
        except Exception as error:
            self.restore_errors.append(type(error).__name__)

    def _await_native(self, future, ticket, stop, caller_cancel, timeout=5):
        """Never wait for the UI thread while owning the controller lock."""
        if not isinstance(future, Future):
            return future is not False
        deadline = time.monotonic() + timeout
        while not future.done():
            with self.lock:
                if not self._live(ticket, stop, caller_cancel):
                    return False
            if time.monotonic() >= deadline:
                raise RuntimeError('The native distraction window did not respond.')
            stop.wait(.025)
        return future.result() is True

    def _await_tab_target(self, future, ticket, stop, caller_cancel, timeout=5):
        """Prepare browser controls off the controller and native UI threads."""
        if not isinstance(future, Future):
            return future
        deadline = time.monotonic() + timeout
        while not future.done():
            with self.lock:
                if not self._live(ticket, stop, caller_cancel):
                    return None
            if time.monotonic() >= deadline:
                raise RuntimeError('Chrome did not expose its tab close control in time.')
            stop.wait(.025)
        return future.result()

    def _warning_ack(self, request_id):
        if not self.overlay:
            return True
        info = self.overlay.info()
        if not isinstance(info, dict) or 'visible' not in info:
            return True
        return bool(info.get('visible') and info.get('enabled', True)
                    and info.get('activity') == 'warning' and not info.get('error')
                    and (request_id is None or info.get('rendered_request_id') == request_id))

    def _await_warning(self, request_id, stop, valid):
        deadline = time.monotonic() + self.warning_render_timeout
        while True:
            with self.lock:
                if not valid():
                    return False
            if self._warning_ack(request_id):
                return True
            info = self.overlay.info() if self.overlay else None
            if isinstance(info, dict) and info.get('error'):
                raise RuntimeError('Patrick could not show the work warning. No work was interrupted.')
            if time.monotonic() >= deadline:
                raise RuntimeError('Patrick could not show the work warning. No work was interrupted.')
            if stop.wait(.025):
                return False

    def _focused(self, window):
        if not window:
            return False
        current = self.windows.foreground()
        return desktop_windows.same_foreground(current, window) and not current.get('is_transient')

    def _valid(self, ticket, cancel, seen, window, active, test):
        if (self.closed or cancel.is_set() or not self.current
                or self.current["id"] != ticket or not self.allowed()):
            return False
        if test:
            return True
        if not self._focused(window):
            return False
        # Admission always checks the original observation's age. A live detector
        # callback subsequently proves fresh identity; the grace period plus
        # approach intentionally outlast that original observation's TTL.
        return bool(active()) if active is not None else 0 <= self.clock() - seen <= self.freshness

    def _wait_valid(self, seconds, stop, valid):
        """Cancel a switch away during either visible phase, even before return."""
        deadline = time.monotonic() + max(0., seconds)
        while True:
            with self.lock:
                if not valid():
                    return False
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return True
            if stop.wait(min(.05, remaining)):
                return False

    def _clear_warning(self, ticket=None):
        if self._warning_ticket is None or (ticket is not None and ticket != self._warning_ticket):
            return
        self._warning_ticket = self._warning_deadline = None
        if self.current:
            self.current['warning_message'] = None
        try:
            self.clear_warning()
        except Exception as error:
            self.warning_error = type(error).__name__

    def _live(self, ticket, stop, caller_cancel):
        return (not self.closed and not stop.is_set() and self.allowed()
                and not (caller_cancel is not None and caller_cancel.is_set())
                and self.current is not None and self.current["id"] == ticket)

    def observe_foreground(self, window):
        """Only a fresh watcher sample can re-arm after an unconfirmed handoff."""
        if self.current and self.current['phase'] not in ('opened', 'error', 'preview'):
            return
        if (self._blocked_foregrounds and desktop_windows.window_key(window)
                and window.get('process') and window.get('title') and not window.get('is_transient')
                and desktop_windows.window_key(window) not in self._blocked_foregrounds):
            self._rearm_foreground()

    def _rearm_foreground(self):
        self._blocked_foregrounds.clear()
        self._browser_reports_after = time.time() * 1000

    def permits_foreground(self, window):
        return desktop_windows.window_key(window) not in self._blocked_foregrounds

    def fresh_browser_report(self, sent_at):
        return sent_at >= self._browser_reports_after

    def is_destination_window(self, window):
        self._destination_launches = {pid: launch for pid, launch in self._destination_launches.items()
                                      if launch.process.poll() is None}
        launch = self._destination_launches.get(window.get('pid')) if window else None
        return bool(launch and launch.owns(window))

    def _close_progress(self, window):
        self._blocked_foregrounds.add(desktop_windows.window_key(window))
        self.current['target_close'] = 'requested'

    def _hold_exposed_foreground(self):
        # Closing a desktop window or Chromium's final tab can expose a
        # different frame. Even a missing acknowledgement can follow a close.
        exposed = self.windows.foreground()
        identity = desktop_windows.window_key(exposed)
        if identity and not exposed.get('is_transient'):
            self._blocked_foregrounds.add(identity)
        return identity

    def _close_confirmed(self):
        self.current['target_close'] = 'confirmed'
        return self._hold_exposed_foreground()

    def _confirm_desktop_close(self, window, live, stop):
        deadline = time.monotonic() + 2
        while live():
            if self.windows.close_status(window) == 'closed':
                return True
            if time.monotonic() >= deadline:
                raise RuntimeError('The app did not confirm that its window closed.')
            stop.wait(.025)
        return False

    def request(self, label, *, destination="casino", window=None, seen=None,
                active=None, mode="cover", test=False, cancel=None, tab_target=None):
        if destination not in DESTINATIONS:
            raise ValueError("Choose a supported interruption destination.")
        if mode not in ("cover", "minimize"):
            raise ValueError("Choose cover or minimize.")
        if not isinstance(label, str) or not 1 <= len(label) <= 200:
            raise ValueError("Use a short work label.")
        if not test and (isinstance(seen, bool) or not isinstance(seen, (int, float))
                         or not math.isfinite(seen)):
            return {"ok": False, "error": "A fresh focused work observation is required."}
        with self.lock:
            self._reconcile_uncover()
            if (self.closed or not self.allowed() or self.current or self._restores or self._covered
                    or self.cooldown_remaining() > 0
                    or (cancel is not None and cancel.is_set())):
                return {"ok": False, "error": "Session/cooldown does not allow interruption."}
            if not test and (not self.permits_foreground(window)
                             or not 0 <= self.clock() - seen <= self.freshness
                             or not self._focused(window) or (active is not None and not active())):
                return {"ok": False, "error": "The focused work observation expired."}
            ticket = str(uuid.uuid4())
            self.cancel_event = threading.Event()
            warning = not test and self.warning_seconds > 0
            target = {"game": "the clam game", "casino": "the casino",
                      "lcbo": "LCBO", "beer_store": "The Beer Store",
                      LEAGUE_DESTINATION: "League of Legends"}[destination]
            warning_message = (f"Uhh… {label[:40]}. Work! Take a break. "
                f"Stay {self.warning_seconds:g}s and I'll move this aside for {target}.") if warning else None
            self.current = {"id": ticket, "label": label, "destination": destination,
                            "game": "clam-catch" if destination == "game" else destination,
                            "phase": "preparing" if not test and self.prepare_tab and tab_target and tab_target.get('kind') == 'native' else "warning" if warning else "approaching", "test": bool(test),
                            "warning_message": warning_message, "error": None}
            if warning:
                self._warning_ticket = ticket
                self._warning_deadline = self.clock() + self.warning_seconds
            if self.current['phase'] != 'preparing':
                self.last_started = self.clock()
            self._worker = threading.Thread(target=self._perform,
                args=(ticket, self.cancel_event, cancel, seen, dict(window) if window else None,
                      active, mode, test, destination, tab_target), daemon=True, name="chuddy-interruption")
            self._worker.start()
            return {"ok": True, **self.info()}

    def _perform(self, ticket, stop, caller_cancel, seen, window, active, mode, test, destination, tab_target):
        browser = bool(window and str(window.get('process', '')).lower() in BROWSERS)
        exposed = None
        route_selected = False
        league_install = None
        league_foreground_seen = False
        input_at_close = None

        def live():
            return self._live(ticket, stop, caller_cancel)

        def valid():
            return (not (caller_cancel is not None and caller_cancel.is_set())
                    and self._valid(ticket, stop, seen, window, active, test)
                    and self._live(ticket, stop, caller_cancel))

        def report_league(role, focused, covers, sizing):
            nonlocal league_foreground_seen
            league_foreground_seen |= focused and role in ("riot_client", "league_client", "match")
            self.current.update(destination_delivery="reused" if handoff.reused else "launched",
                                destination_window=role, destination_foreground=focused,
                                destination_monitor_coverage=covers, destination_layout_request=sizing)

        try:
            if not test and destination == LEAGUE_DESTINATION:
                league_install = self.league_launcher.prepare()
            if not test and self.prepare_tab and tab_target and tab_target.get('kind') == 'native':
                with self.lock:
                    if not valid():
                        return self._abandon(ticket)
                prepared = self._await_tab_target(
                    self.prepare_tab(tab_target, valid, lambda: self._live(ticket, stop, caller_cancel)),
                    ticket, stop, caller_cancel)
                with self.lock:
                    if not prepared or not valid():
                        return self._abandon(ticket)
                    tab_target = prepared
                    self.last_started = self.clock()
                    self.current['phase'] = 'warning' if self.current.get('warning_message') else 'approaching'
            with self.lock:
                if not valid():
                    return self._abandon(ticket)
                if not test and self.close_desktop:
                    if destination in ('game', 'casino') and not self.local_ready():
                        raise RuntimeError('Open the native Chuddy app for fullscreen handoff.')
                    if (destination in RETAIL_HOME and hasattr(type(self.launcher), 'find')
                            and not self.launcher.find()):
                        raise RuntimeError('Google Chrome is unavailable for this destination.')
                warning_message = self.current.get('warning_message')
                warning_request_id = None
                if warning_message:
                    warning = getattr(self.overlay, 'warning', None)
                    if warning:
                        warning(window, destination, warning_message)
                        native = self.overlay.info()
                        if isinstance(native, dict):
                            warning_request_id = native.get('request_id')
                    if not self._live(ticket, stop, caller_cancel):
                        return self._abandon(ticket)
            if warning_message:
                if not self._await_warning(warning_request_id, stop, valid):
                    with self.lock:
                        return self._abandon(ticket)
                with self.lock:
                    if not valid():
                        return self._abandon(ticket)
                    self._warning_deadline = self.clock() + self.warning_seconds
                    if not self.terminal_dialogue:
                        self.on_warning(warning_message, ticket)
                    if not self._live(ticket, stop, caller_cancel):
                        return self._abandon(ticket)
            if warning_message and not self._wait_valid(self.warning_seconds, stop, valid):
                with self.lock:
                    return self._abandon(ticket)
            with self.lock:
                if not valid():
                    return self._abandon(ticket)
                if warning_message and self.overlay:
                    if not self._warning_ack(warning_request_id):
                        raise RuntimeError('Patrick could not show the work warning. '
                                           'No work was interrupted.')
                self._clear_warning(ticket)
                # Clearing a warning may invoke a controller callback/Stop.
                if not valid():
                    return self._abandon(ticket)
                self.current['phase'] = 'approaching'
                banter = None
                if not test and not self.terminal_dialogue:
                    banter = self.banter_selector(self.current['label'], destination, self.recent_banter())
                    if not isinstance(banter, str) or not banter.strip() or len(banter) > 130:
                        raise ValueError('Use one short work-banter line.')
                    self.current.update(banter_message=banter, banter_source=BANTER_SOURCE)
                if self.overlay:
                    animation_window = dict(window) if window else None
                    if animation_window and tab_target and tab_target.get('kind') == 'native':
                        animation_window['tab_close_point'] = tab_target.get('close_point')
                    self.overlay.perform(animation_window, destination)
                if not self._live(ticket, stop, caller_cancel):
                    return self._abandon(ticket)
                if banter:
                    # This callback only persists/queues the line. Never wait on
                    # synthesis, playback, model output or a returned Future.
                    self.speak(banter)
            if not self._wait_valid(self.delay, stop, valid):
                with self.lock:
                    return self._abandon(ticket)
            with self.lock:
                # Revalidate after the theatrical travel; focus may have changed.
                if not valid():
                    return self._abandon(ticket)
                self.current["phase"] = "clicking"
                tab_result = None
                close_desktop = not test and not browser and self.close_desktop
                if league_install is not None and (browser or close_desktop):
                    input_at_close = desktop_windows.last_input_tick()
                if not test and browser:
                    if not self.close_tab:
                        raise RuntimeError('The browser tab close control is unavailable.')
                    self._close_progress(window)
                    tab_result = self.close_tab(tab_target, valid,
                        lambda: self._live(ticket, stop, caller_cancel))
                elif close_desktop:
                    self._close_progress(window)
                    self.windows.request_close(window)
                elif not test and mode == "minimize":
                    self._restores.append(dict(window))
                    minimize_work = getattr(self.windows, "minimize_work", None)
                    if minimize_work:
                        minimize_work(window)
                    else:
                        self.windows.minimize(window["handle"])
                if not self._live(ticket, stop, caller_cancel):
                    return self._abandon(ticket)
            # Effect acknowledgement runs outside the controller/UI locks.
            if close_desktop or tab_result is not None:
                closed = (self._confirm_desktop_close(window, live, stop) if close_desktop
                          else self._await_native(tab_result, ticket, stop, caller_cancel, timeout=8.5))
                with self.lock:
                    if not closed or not live():
                        return self._abandon(ticket)
                    exposed = self._close_confirmed()
                    if input_at_close is not None and exposed != desktop_windows.window_key(window):
                        if desktop_windows.last_input_tick() != input_at_close:
                            exposed = desktop_windows.window_key(window)

            local = test or destination in ("game", "casino")
            with self.lock:
                if not live():
                    return self._abandon(ticket)
                if self.current.get('target_close') == 'confirmed':
                    self.current.update(phase="opening", fullscreen="pending")
                    if local:
                        self.open_local(destination)
                        if not live():
                            return self._abandon(ticket)
                        # The WebView can route as soon as the close is known.
                        # Actual foreground and fullscreen still need native proof.
                        self.casino_opening_id = ticket
                        self.casino_interruption_id = None
                        self.current["destination_delivery"] = "route_ready"
                        route_selected = True
                if local:
                    self._covered = True
                    self.handoff_valid = live
                    self.handoff_foreground = exposed
                    handoff = self.cover()
                elif league_install is not None:
                    handoff = self.league_launcher.open(league_install)
                    self.current["destination_delivery"] = "reused" if handoff.reused else "launched"
                else:
                    handoff = self.launcher.open(destination)
                    if isinstance(handoff, ChromeLaunch):
                        self._destination_launches[handoff.process.pid] = handoff
                    if self.current.get('phase') == 'opening':
                        self.current["destination_delivery"] = "launched"
            if local:
                foreground = None
                if not self._await_native(handoff, ticket, stop, caller_cancel):
                    with self.lock:
                        return self._abandon(ticket)
            elif league_install is not None:
                foreground = handoff.confirm(live, stop, self.lock, exposed, report_league,
                                             input_at_close=input_at_close)
                if foreground is None:
                    with self.lock:
                        return self._abandon(ticket)
            elif isinstance(handoff, ChromeLaunch):
                foreground = handoff.confirm(live)
            else:
                foreground = None  # Injected synchronous launcher.

            with self.lock:
                if not live():
                    return self._abandon(ticket)
                if local:
                    if not route_selected:
                        self.open_local(destination if destination in ("game", "casino") else "game")
                    if not live():
                        return self._abandon(ticket)
                    if not test:
                        self.casino_interruption_id = ticket
                    if destination in ("game", "casino"):
                        self.casino_opening_id = ticket
                    foreground = self.windows.foreground()
                    if not (foreground and foreground.get('pid') == os.getpid()):
                        foreground = None
                if foreground:
                    self._rearm_foreground()
                if self.current.get('fullscreen') == 'pending':
                    self.current["fullscreen"] = "confirmed"
                self.current["phase"] = "preview" if test else "opened"
                self._publish_outcome(live, tab_closed=browser and self.current.get('target_close') == 'confirmed')
                self._resume_buddy(show=self.casino_opening_id != ticket)
        except Exception as error:
            with self.lock:
                if self.current and self.current["id"] == ticket:
                    self._clear_warning(ticket)
                    if not self.current or self.current['id'] != ticket:
                        return
                    close_status = self.current.get('target_close')
                    if not live():
                        return self._abandon(ticket)
                    if close_status == 'confirmed' and league_foreground_seen:
                        # The real destination already took focus, even though
                        # its layout or subsequent confirmation failed.
                        self._rearm_foreground()
                    elif close_status:
                        self._hold_exposed_foreground()
                    if self.current.get('fullscreen') == 'pending':
                        self.current["fullscreen"] = "failed"
                    self.current.update(phase="error", error=type(error).__name__, error_detail=str(error)[:200])
                    if self.terminal_dialogue and not route_selected and (
                            close_status == 'confirmed' or (close_status == 'requested' and valid())):
                        self._publish_outcome(live, tab_closed=browser and close_status == 'confirmed')
                    self._restore()
                    self._resume_buddy(show=not route_selected)

    def _publish_outcome(self, live, *, tab_closed):
        if not self.terminal_dialogue or not live():
            return
        try:
            line = self._banter.record_outcome(self.current, self.recent_banter(), tab_closed=tab_closed)
            if line:
                self.speak(line)
        except Exception as error:
            # The window effect has already resolved. A failed chat write or
            # speech admission must leave its result authoritative.
            try:
                self.on_dialogue_error(type(error).__name__)
            except Exception:
                pass

    def _resume_buddy(self, *, show=True):
        """Ending a performance does not end the opted-in desktop companion."""
        if self.overlay:
            try:
                if not self.closed and self.allowed():
                    hide = getattr(self.overlay, 'hide', None)
                    if not show and hide:
                        hide()
                    else:
                        self.overlay.roam()
                else:
                    self.overlay.disarm()
            except Exception as error:
                self.overlay_error = type(error).__name__

    def _abandon(self, ticket, *, keep_route=False):
        if self.current and self.current["id"] == ticket:
            self._clear_warning(ticket)
            if self.current.get('target_close'):
                self._hold_exposed_foreground()
            if self.current["phase"] == "opening" and self.casino_opening_id == ticket and not keep_route:
                self.casino_opening_id = None
            self.current['phase'] = 'cancelled'
            self.current = None
            self._restore()
            self._resume_buddy()

    def _restore(self):
        pending, self._restores = self._restores, []
        self.restore_errors = []
        for window in pending:
            try:
                identity = getattr(self.windows, "identity", None)
                # PID validation prevents restoring a newly reused window handle.
                if identity is not None and identity(window["handle"]) != window.get("pid"):
                    discard = getattr(self.windows, "discard", None)
                    if discard:
                        discard(window["handle"])
                    continue
                self.windows.restore(window["handle"])
            except Exception as error:
                # Continue other windows and release the cover even if one closed.
                self._restores.append(window)
                self.restore_errors.append(type(error).__name__)
        if self._covered:
            try:
                self._reconcile_uncover()
                if self._covered and self._uncover_future is None:
                    result = self.uncover()
                    if isinstance(result, Future):
                        self._uncover_future = result
                    else:
                        self._covered = False
            except Exception as error:
                self.restore_errors.append(type(error).__name__)

    def stop(self, *, end_session=False):
        with self.lock:
            self.cancel_event.set()
            if end_session:
                self.casino_interruption_id = None
                self.casino_opening_id = None
                self._blocked_foregrounds.clear()
                self._browser_reports_after = time.time() * 1000
            self._clear_warning()
            if self.current and self.current['phase'] not in ('opened', 'preview', 'error'):
                if self.casino_opening_id == self.current['id']:
                    self.casino_opening_id = None
                self.current['phase'] = 'cancelled'
            self.current = None
            if self.overlay:
                try:
                    self.overlay.disarm()
                except Exception as error:
                    self.overlay_error = type(error).__name__
            self._restore()

    dismiss = stop

    def leave_casino(self, opening_id):
        with self.lock:
            self._reconcile_uncover()
            if (not self.allowed() or not opening_id or opening_id != self.casino_opening_id
                    or not self._covered or self._uncover_future is not None
                    or (self.current and self.current['id'] != opening_id)):
                return False
            if self.current and self.current['phase'] == 'opening':
                self._abandon(opening_id, keep_route=True)
                return True
            self._restore()
            self._resume_buddy()
            return True

    def retire_completed(self):
        """Release a finished performance before a new fresh work observation.

        The controller calls this only after recognizing foreground work. Keep
        cooldown and in-progress performances authoritative. Cleanup uses the
        same acknowledged native path as exit; closed browser tabs have no
        restoration records and are never reopened. A pending uncover still
        blocks the next request until its completion is acknowledged.
        """
        with self.lock:
            if (not self.current or self.current.get('phase') not in ('opened', 'preview', 'error')
                    or self.cooldown_remaining() > 0):
                return False
            self.stop()
            self._resume_buddy()
            return True

    def close(self):
        with self.lock:
            self.closed = True
            self.stop()
        if self.overlay:
            self.overlay.close()

    def wait(self, timeout=2):
        """Bounded diagnostic join, outside controller/manager locks."""
        if self._worker and self._worker is not threading.current_thread():
            self._worker.join(timeout)
            return not self._worker.is_alive()
        return True


class PreservingWindows:
    """Native adapter preserving original normal/maximized placement and PID.

    Unlike ShowWindow(SW_RESTORE) alone, WINDOWPLACEMENT keeps a maximized editor
    maximized on return. Only handles this adapter actually minimized are restored.
    No editor termination or synthetic input is used.
    """
    def __init__(self):
        from . import windows
        self.foreground = windows.foreground
        self._saved = {}

    @staticmethod
    def _api():
        import ctypes as c
        from ctypes import wintypes as w
        class PLACEMENT(c.Structure):
            _fields_ = [("length", w.UINT), ("flags", w.UINT), ("showCmd", w.UINT),
                        ("min", w.POINT), ("max", w.POINT), ("normal", w.RECT)]
        user = c.WinDLL("user32", use_last_error=True)
        user.GetWindowThreadProcessId.argtypes = [w.HWND, c.POINTER(w.DWORD)]
        user.GetWindowThreadProcessId.restype = w.DWORD
        user.GetWindowPlacement.argtypes = [w.HWND, c.POINTER(PLACEMENT)]
        user.GetWindowPlacement.restype = w.BOOL
        user.SetWindowPlacement.argtypes = [w.HWND, c.POINTER(PLACEMENT)]
        user.SetWindowPlacement.restype = w.BOOL
        user.ShowWindow.argtypes = [w.HWND, c.c_int]
        user.ShowWindow.restype = w.BOOL
        return c, w, user, PLACEMENT

    def request_close(self, window):
        desktop_windows.request_close(window)

    def close_status(self, window):
        return desktop_windows.close_status(window)

    def identity(self, handle):
        if os.name != "nt":
            return None
        c, w, user, _ = self._api()
        pid = w.DWORD()
        user.GetWindowThreadProcessId(handle, c.byref(pid))
        return pid.value or None

    def minimize(self, handle):
        return self._minimize(handle)

    def minimize_work(self, window):
        return self._minimize(window["handle"], expected_pid=window.get("pid"))

    def _minimize(self, handle, expected_pid=None):
        if os.name != "nt":
            raise RuntimeError("Native interception is Windows-only.")
        c, _, user, placement_type = self._api()
        placement = placement_type()
        placement.length = c.sizeof(placement)
        pid = self.identity(handle)
        if expected_pid is not None and pid != expected_pid:
            raise RuntimeError("The observed work window changed owner.")
        if not pid or not user.GetWindowPlacement(handle, c.byref(placement)):
            raise RuntimeError("Work window is unavailable.")
        if placement.showCmd in (2, 6, 7, 11):
            raise RuntimeError("Work window was already minimized.")
        self._saved[handle] = (pid, placement)
        # Synchronous: an async minimize message could arrive AFTER Stop restored
        # work. ShowWindow returns prior visibility rather than a success flag.
        user.ShowWindow(handle, 6)

    def discard(self, handle):
        self._saved.pop(handle, None)

    def restore(self, handle):
        saved = self._saved.get(handle)
        if not saved or os.name != "nt":
            return
        pid, placement = saved
        if self.identity(handle) != pid:
            self._saved.pop(handle, None)
            return
        c, _, user, _ = self._api()
        # ctypes creates a distinct structure class per call; cast only at this
        # boundary while retaining the original placement bytes and ownership.
        placement_ptr = c.cast(c.byref(placement), user.SetWindowPlacement.argtypes[1])
        if not user.SetWindowPlacement(handle, placement_ptr):
            raise RuntimeError("Could not restore the work window placement.")
        self._saved.pop(handle, None)
