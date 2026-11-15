"""Chuddy desktop entry point. All disruptive features start off."""

import argparse
from concurrent.futures import Future, InvalidStateError
import faulthandler
import math
import os
from pathlib import Path
import signal
import sys
import threading
import time

ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
DATA_ROOT = (
    Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else ROOT
)


def start_thread_dump(data_dir):
    """Opt-in, one-shot stack evidence; no request/settings values are dumped."""
    value = os.environ.get("CHUDDY_DUMP_THREADS_AFTER")
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    if not math.isfinite(seconds) or seconds <= 0:
        return None
    folder = Path(data_dir) / "diagnostics"
    folder.mkdir(parents=True, exist_ok=True)
    output = (folder / "native-threads.log").open("a", encoding="utf-8")
    try:
        faulthandler.dump_traceback_later(min(120., max(1., seconds)), repeat=False,
                                        file=output, exit=False)
    except Exception:
        output.close()
        raise
    return output


class NativeCoverDispatch:
    """Post shell changes without waiting for WinForms under application locks.

    Pywebview show/restore/maximize synchronously Control.Invoke Python closures.
    A worker calling these under the controller lock can deadlock the GIL/UI.
    BeginInvoke returns immediately; its callback uses the raw form on the UI
    thread and never takes a controller lock. Futures acknowledge actual effects.
    """

    def __init__(self, window, controller, *, post=None, verification_timeout=1.0):
        self.window, self.controller = window, controller
        self._post = post or self._post_native
        self._lock = threading.Lock()
        self._generation, self._closed = 0, False
        self._pending, self._applied, self._error, self._future = None, None, None, None
        self._verification_timeout = verification_timeout
        self._foreground = self._fullscreen = None
        self._fullscreen_state = None
        self._preview_state = None

    def info(self):
        with self._lock:
            return {"pending": self._pending, "applied": self._applied,
                    "error": self._error, "generation": self._generation,
                    "verified_foreground": self._foreground, "verified_fullscreen": self._fullscreen,
                    "closed": self._closed}

    @staticmethod
    def _settle(future, value=False, error=None):
        # Never hold the dispatcher lock: Future callbacks may reenter it.
        try:
            if error is None:
                future.set_result(value)
            else:
                future.set_exception(error)
        except InvalidStateError:
            pass  # Stop/newer request already invalidated this future.

    def _post_native(self, callback):
        native = getattr(self.window, "native", None)
        if native is None or not hasattr(native, "BeginInvoke"):
            raise RuntimeError("The native Windows shell is not ready.")
        from System import Action

        # Never Control.Invoke or wait for IAsyncResult from an app worker.
        native.BeginInvoke(Action(callback))

    def _uncover_native(self, valid):
        from System.Windows.Forms import FormWindowState

        native = self.window.native
        if self._fullscreen_state is not None and valid():
            state, style, bounds = self._fullscreen_state
            native.WindowState = FormWindowState.Normal
            native.FormBorderStyle = style
            native.Bounds = bounds
            native.WindowState = state
            self._fullscreen_state = None
        if self._preview_state is not None and valid():
            native.WindowState = self._preview_state
            self._preview_state = None
        return valid()

    def _preview_native(self, valid):
        from System.Windows.Forms import FormWindowState

        native = self.window.native
        if self._preview_state is None:
            self._preview_state = native.WindowState
        native.Show()
        if not valid():
            return False
        native.Activate()
        if not valid():
            return False
        native.WindowState = FormWindowState.Maximized
        return valid()

    def _start_fullscreen(self, exposed, valid, finish, record):
        from System.Windows.Forms import FormBorderStyle, FormWindowState, Screen, Timer
        from chuddy import windows

        native = self.window.native

        def can_activate():
            foreground = windows.foreground_handle()
            return (not foreground or foreground == native.Handle.ToInt64()
                    or exposed is not None and foreground == exposed[0]
                    and windows.window_owner(foreground) == exposed[1:])

        if not can_activate():
            raise RuntimeError("Foreground focus changed before Chuddy could activate.")
        native.Show()
        if not valid():
            return finish()
        if not native.is_fullscreen and self._fullscreen_state is None:
            state = native.WindowState
            bounds = native.Bounds if state == FormWindowState.Normal else native.RestoreBounds
            self._fullscreen_state = state, native.FormBorderStyle, bounds
            screen = Screen.FromControl(native).Bounds
            # Maximized WinForms windows can be constrained to the work area.
            # Keep a borderless normal frame sized to the entire current monitor.
            native.WindowState = FormWindowState.Normal
            if not valid():
                return finish()
            native.FormBorderStyle = getattr(FormBorderStyle, "None")
            if not valid():
                return finish()
            native.Bounds = screen

        if not valid():
            return finish()
        handle = native.Handle.ToInt64()
        owner = windows.window_owner(handle)
        if can_activate() and valid() and windows.foreground_handle() != handle:
            # Let Windows arbitrate access. Never raise another window or
            # repeatedly take focus back after the user moves away.
            windows.activate_window(handle)

        deadline = time.monotonic() + self._verification_timeout
        timer = Timer()
        timer.Interval = 30

        def verify(*_):
            try:
                if not valid():
                    return complete()
                if native.Handle.ToInt64() != handle:
                    raise RuntimeError("Chuddy's native window changed during fullscreen handoff.")
                focused, fullscreen = windows.fullscreen_status(handle, owner)
                record(focused, fullscreen)
                if focused and fullscreen:
                    return complete(True)
                if time.monotonic() >= deadline:
                    if not focused:
                        raise RuntimeError("Windows did not give Chuddy foreground focus.")
                    raise RuntimeError("Chuddy's window did not cover the monitor.")
            except Exception as error:
                complete(error=error)

        def complete(applied=False, error=None):
            timer.Stop()
            timer.Dispose()
            finish(applied, error)

        # Sample the actual frame after WinForms has processed queued work.
        # A single same-callback reading can precede activation or layout.
        timer.Tick += verify
        timer.Start()

    def _submit(self, cover):
        future = Future()
        with self._lock:
            if self._closed:
                previous, generation = None, None
            else:
                self._generation += 1
                previous, self._future = self._future, future
                generation = self._generation
                self._pending, self._error = "cover" if cover else "uncover", None
                if cover:
                    self._foreground = self._fullscreen = None
        if generation is None:
            self._settle(future)
            return future
        if previous:
            self._settle(previous)
        manager = self.controller.distractions
        stop = manager.cancel_event
        handoff_valid = getattr(manager, "handoff_valid", None)
        current = manager.current or {}
        ticket = current.get("id")
        preview = current.get("test")
        exposed = getattr(manager, "handoff_foreground", None)

        def valid():
            with self._lock:
                if self._closed or generation != self._generation or future.cancelled():
                    return False
            if not cover:
                return True
            # Event+ticket capture the controller's authority without waiting for
            # its RLock. Stop sets the event before native restoration/uncover.
            current = manager.current
            return ((handoff_valid is None or handoff_valid())
                    and not stop.is_set() and self.controller.alive
                    and self.controller.session == "running"
                    and self.controller.clock() < self.controller.expires
                    and current is not None and current.get("id") == ticket)

        def finish(applied=False, error=None):
            with self._lock:
                if generation == self._generation:
                    self._pending = None
                    if error:
                        self._error = type(error).__name__
                    elif applied:
                        self._applied = "cover" if cover else "uncover"
            self._settle(future, applied, error)

        def record(foreground, fullscreen):
            with self._lock:
                if generation == self._generation:
                    self._foreground, self._fullscreen = foreground, fullscreen

        def apply():
            try:
                if not valid():
                    finish()
                elif not cover:
                    finish(self._uncover_native(valid))
                elif preview:
                    finish(self._preview_native(valid))
                else:
                    self._start_fullscreen(exposed, valid, finish, record)
            except Exception as error:
                finish(error=error)

        try:
            self._post(apply)
        except Exception as error:
            finish(error=error)
        return future

    def cover(self):
        return self._submit(True)

    def uncover(self):
        # Invalidate queued cover synchronously, before posting native cleanup.
        return self._submit(False)

    def close(self):
        with self._lock:
            self._closed = True
            self._generation += 1
            self._pending = None
            future = self._future
        if future:
            self._settle(future)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run local API only, without opening a window.",
    )
    parser.add_argument(
        "--browser",
        action="store_true",
        help="Open local UI in browser instead of native shell.",
    )
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=DATA_ROOT / ".chuddy")
    parser.add_argument(
        "--no-ollama",
        action="store_true",
        help="Do not start an installed portable local model server.",
    )
    parser.add_argument(
        "--no-watch",
        action="store_true",
        help="Diagnostic mode: never start the foreground-work watcher.",
    )
    parser.add_argument(
        "--check-classifier",
        action="store_true",
        help="Classify a silent test buffer and exit; no microphone, UI or speaker output.",
    )
    args = parser.parse_args()
    args.data_dir.mkdir(parents=True, exist_ok=True)
    if args.check_classifier:
        import json
        import tempfile
        import numpy as np
        from chuddy.store import Store
        from chuddy.audio import AudioPipeline

        with tempfile.TemporaryDirectory(prefix="chuddy-classifier-check-") as folder:
            test_store = Store(Path(folder) / "test.sqlite3")
            audio = AudioPipeline(test_store, args.data_dir / "models" / "yamnet")
            try:
                result = audio.classify(np.zeros(16000, dtype=np.float32), 16000)
                print(
                    json.dumps(
                        {
                            "classifier_check": result,
                            "microphone": False,
                            "playback": False,
                        }
                    ),
                    flush=True,
                )
            finally:
                audio.close()
                test_store.close()
        return 0 if result.get("available") else 2
    from chuddy.store import Store
    from chuddy.core import Controller
    from chuddy.server import DesktopServer

    store = Store(args.data_dir / "chuddy.sqlite3")
    controller = Controller(store, args.data_dir)
    try:
        server = DesktopServer(controller, ROOT, args.port)
    except OSError as error:
        raise SystemExit(
            f"Could not bind local port {args.port}: {error}. Another Chuddy instance may already be running."
        )
    url = f"http://127.0.0.1:{server.server_port}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    availability = None
    if not args.no_ollama:
        from chuddy.ollama_service import PortableOllama

        portable_exe = args.data_dir / "tools" / "ollama" / "ollama.exe"
        ollama = PortableOllama(portable_exe, args.data_dir)

        def warm_voice_after_model():
            try:
                if controller.alive and portable_exe.is_file() and store.settings()["voice"]:
                    controller.voice.warm()
            except Exception as error:
                if controller.alive:
                    store.log("voice startup", type(error).__name__)

        controller.work_recognizer.inference_lock = controller.agent.request_lock
        availability = controller.agent.start_availability(ollama.start_if_missing, warm_voice_after_model)
        controller.work_recognizer.model_ready = lambda: availability.info()["ready"]
    controller.watcher_enabled = not args.no_watch
    if controller.watcher_enabled:
        threading.Thread(target=controller.watch, daemon=True).start()
    print(
        f"Chuddy ready at {url}; session stopped, microphone off, physical outputs disabled.",
        flush=True,
    )
    thread_dump = start_thread_dump(args.data_dir)
    native_cover = None

    def close():
        if availability:
            availability.close()
        if native_cover:
            native_cover.close()
        controller.close()
        server.shutdown()
        server.server_close()
        store.close()
        if thread_dump:
            faulthandler.cancel_dump_traceback_later()
            thread_dump.close()

    if args.headless or args.browser:
        if args.browser:
            import webbrowser

            webbrowser.open(url)
        stop = threading.Event()
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, lambda *_: stop.set())
        try:
            stop.wait()
        finally:
            close()
        return
    try:
        import webview

        if sys.platform == 'win32':
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('Chuddy.Companion')

        screen = webview.screens[0]
        width = max(720, min(1320, screen.width - 64))
        height = max(580, min(900, screen.height - 80))

        window = webview.create_window(
            "Chuddy · your very helpful friend",
            url,
            width=width,
            height=height,
            min_size=(min(980, width), min(640, height)),
            background_color="#f9f4ec",
            text_select=True,
        )

        native_cover = NativeCoverDispatch(window, controller)
        controller.native_window = native_cover
        controller.cover, controller.uncover = native_cover.cover, native_cover.uncover
        # Bind the inert native adapter only for the native shell. A deliberate
        # session start/resume owns roaming; construction opens no overlay.
        from chuddy.desktop_overlay import DesktopOverlay

        overlay = DesktopOverlay(
            ROOT / "web" / "assets" / "chuddy" / "gestures.png",
            on_stop=lambda: controller.set_session("stop"),
            reduced_motion=bool(store.settings().get("reduced_motion", False)),
        )
        controller.desktop_overlay = overlay
        controller.distractions.overlay = overlay
        def closing():
            if availability:
                availability.close()
            native_cover.close()
            controller.close()

        window.events.closing += closing
        webview.start(
            gui="edgechromium",
            icon=str(ROOT / 'assets' / 'icons' / 'chuddy-desktop.ico'),
            private_mode=False,
            storage_path=str(args.data_dir / "webview"),
        )
    except Exception as error:
        print(
            f"Native window unavailable ({type(error).__name__}). Use --browser or install WebView2.",
            file=sys.stderr,
        )
        raise
    finally:
        close()


if __name__ == "__main__":
    raise SystemExit(main())
