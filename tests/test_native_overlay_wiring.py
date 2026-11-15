"""Native-entry-point wiring through fakes only: no windows or services launch."""
import builtins
import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class ClosingEvent:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


class NativeOverlayWiringTests(unittest.TestCase):
    def run_entry(self, mode=None, preferences=None, asset_root=None, watch=False, no_ollama=True):
        spec = importlib.util.spec_from_file_location("chuddy_native_entry_test", ROOT / "run.py")
        entry = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(entry)
        if asset_root is not None:
            entry.ROOT = Path(asset_root)
        result = types.SimpleNamespace(overlays=[], controllers=[], stores=[], servers=[], imports=[], gui_started=False,
                                       thread_targets=[], browser_urls=[], model_events=[])

        class Store:
            def __init__(self, path):
                self.path, self.closed = path, 0
                result.stores.append(self)

            def settings(self):
                return dict(preferences or {})

            def close(self):
                self.closed += 1

        class Controller:
            def __init__(self, store, data):
                self.store, self.data, self.closed = store, data, 0
                self.distractions = types.SimpleNamespace(overlay=None)
                self.desktop_overlay = None
                self.session_actions = []
                self.work_recognizer = types.SimpleNamespace(inference_lock=None)
                self.agent = types.SimpleNamespace(request_lock=object(), start_availability=self.start_availability)
                result.controllers.append(self)

            def start_availability(self, start_server, after_first_check):
                result.model_events.append("start")
                return types.SimpleNamespace(close=lambda: result.model_events.append("close"))

            def set_session(self, action):
                self.session_actions.append(action)

            def watch(self):
                raise AssertionError("Real watcher execution is forbidden in this fixture.")

            def close(self):
                self.closed += 1

        class Server:
            def __init__(self, controller, root, port):
                self.controller, self.root, self.server_port = controller, root, port
                self.shutdown_calls, self.close_calls = 0, 0
                result.servers.append(self)

            def serve_forever(self):
                raise AssertionError("Real service execution is forbidden in this fixture.")

            def shutdown(self):
                self.shutdown_calls += 1

            def server_close(self):
                self.close_calls += 1

        class Overlay:
            def __init__(self, asset, **kwargs):
                self.asset, self.options = asset, kwargs
                self.active_calls = []
                result.overlays.append(self)

            def start(self):
                self.active_calls.append("start")

            def roam(self):
                self.active_calls.append("roam")

            def perform(self, *args):
                self.active_calls.append("perform")

        class Thread:
            def __init__(self, target, **kwargs):
                result.thread_targets.append(target)

            def start(self):
                pass

        class Event:
            def wait(self):
                pass

            def set(self):
                pass

        window = types.SimpleNamespace(events=types.SimpleNamespace(closing=ClosingEvent()), on_top=False)
        window.show = lambda: None
        window.restore = lambda: None
        window.maximize = lambda: None

        def start_gui(**kwargs):
            controller = result.controllers[0]
            self.assertEqual(len(result.overlays), 1, "Overlay must be bound before GUI loop entry.")
            self.assertIs(controller.desktop_overlay, result.overlays[0])
            self.assertIs(controller.distractions.overlay, result.overlays[0])
            self.assertEqual(result.overlays[0].active_calls, [], "Startup must not activate an overlay.")
            result.gui_started = True

        modules = {
            "chuddy.store": types.SimpleNamespace(Store=Store),
            "chuddy.core": types.SimpleNamespace(Controller=Controller),
            "chuddy.server": types.SimpleNamespace(DesktopServer=Server),
            "chuddy.desktop_overlay": types.SimpleNamespace(DesktopOverlay=Overlay),
            "webview": types.SimpleNamespace(screens=[types.SimpleNamespace(width=1280, height=800)],
                                               create_window=lambda *args, **kwargs: window, start=start_gui),
            "webbrowser": types.SimpleNamespace(open=lambda url: result.browser_urls.append(url)),
        }
        original_import = builtins.__import__

        def record_import(name, *args, **kwargs):
            if name == "chuddy.desktop_overlay":
                result.imports.append(name)
            return original_import(name, *args, **kwargs)

        with tempfile.TemporaryDirectory(prefix="chuddy-native-wiring-") as folder:
            argv = ["run.py", "--data-dir", folder, "--port", "8899"]
            if no_ollama:
                argv.append("--no-ollama")
            if not watch:
                argv.append("--no-watch")
            if mode:
                argv.append("--" + mode)
            with patch.dict(sys.modules, modules), patch.object(sys, "argv", argv), \
                 patch.object(entry.threading, "Thread", Thread), patch.object(entry.threading, "Event", Event), \
                 patch.object(entry.signal, "signal"), patch("builtins.__import__", side_effect=record_import), \
                 contextlib.redirect_stdout(io.StringIO()):
                entry.main()
        self.assertEqual(result.controllers[0].closed, 1)
        self.assertEqual(result.servers[0].shutdown_calls, 1)
        self.assertEqual(result.servers[0].close_calls, 1)
        self.assertEqual(result.stores[0].closed, 1)
        return result

    def test_native_binds_one_inert_adapter_before_gui_and_routes_stop(self):
        result = self.run_entry(preferences={"reduced_motion": True})
        self.assertTrue(result.gui_started)
        overlay = result.overlays[0]
        self.assertEqual(overlay.asset, ROOT / "web" / "assets" / "chuddy" / "gestures.png")
        self.assertIs(overlay.options["reduced_motion"], True)
        self.assertEqual(overlay.active_calls, [])
        overlay.options["on_stop"]()
        self.assertEqual(result.controllers[0].session_actions, ["stop"])

    def test_missing_reduced_motion_setting_defaults_false(self):
        result = self.run_entry()
        self.assertIs(result.overlays[0].options["reduced_motion"], False)

    def test_normal_launch_registers_work_watcher_and_reports_enabled(self):
        result=self.run_entry(watch=True)
        controller=result.controllers[0]
        self.assertTrue(controller.watcher_enabled)
        self.assertIn(controller.watch,result.thread_targets)

    def test_diagnostic_launch_reports_disabled_watcher(self):
        result=self.run_entry(watch=False)
        controller=result.controllers[0]
        self.assertFalse(controller.watcher_enabled)
        self.assertNotIn(controller.watch,result.thread_targets)

    def test_native_uses_runtime_asset_root_for_frozen_layout(self):
        asset_root = Path("C:/fixture/Chuddy/_internal")
        result = self.run_entry(asset_root=asset_root)
        self.assertEqual(result.overlays[0].asset, asset_root / "web" / "assets" / "chuddy" / "gestures.png")

    def test_headless_never_imports_or_constructs_native_overlay(self):
        result = self.run_entry("headless")
        self.assertFalse(result.gui_started)
        self.assertEqual(result.imports, [])
        self.assertEqual(result.overlays, [])
        self.assertEqual(result.browser_urls, [])
        self.assertEqual(result.model_events, [])

    def test_normal_headless_start_binds_model_monitor_and_closes_it(self):
        result = self.run_entry("headless", no_ollama=False)
        controller = result.controllers[0]
        self.assertIs(controller.work_recognizer.inference_lock, controller.agent.request_lock)
        self.assertEqual(result.model_events, ["start", "close"])

    def test_browser_fallback_never_imports_or_constructs_native_overlay(self):
        result = self.run_entry("browser")
        self.assertFalse(result.gui_started)
        self.assertEqual(result.imports, [])
        self.assertEqual(result.overlays, [])
        self.assertEqual(result.browser_urls, ["http://127.0.0.1:8899"])


if __name__ == "__main__":
    unittest.main()
