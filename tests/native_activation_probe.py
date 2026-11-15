"""Opt-in end-to-end Windows probe on a new desktop that never receives user input."""
import argparse
from contextlib import ExitStack
import ctypes as c
from ctypes import wintypes as w
import faulthandler
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from unittest.mock import patch


USER = c.WinDLL("user32", use_last_error=True) if sys.platform == "win32" else None


def api(name, result, *parameters):
    function = getattr(USER, name)
    function.restype, function.argtypes = result, parameters
    return function


def desktop_name(handle):
    query = api("GetUserObjectInformationW", w.BOOL, w.HANDLE, c.c_int,
                w.LPVOID, w.DWORD, c.POINTER(w.DWORD))
    name = c.create_unicode_buffer(128)
    if not query(handle, 2, name, c.sizeof(name), None):
        raise c.WinError(c.get_last_error())
    return name.value


def require_isolated(name):
    assert name.startswith("ChuddyActivation-")
    current_id = c.windll.kernel32.GetCurrentThreadId()
    get_desktop = api("GetThreadDesktop", w.HANDLE, w.DWORD)
    open_input = api("OpenInputDesktop", w.HANDLE, w.DWORD, w.BOOL, w.DWORD)
    input_desktop = open_input(0, False, 1)
    if not input_desktop:
        raise c.WinError(c.get_last_error())
    try:
        assert desktop_name(get_desktop(current_id)) == name
        assert desktop_name(input_desktop) != name
    finally:
        api("CloseDesktop", w.BOOL, w.HANDLE)(input_desktop)


def run_on_desktop(argv, name):
    class Startup(c.Structure):
        _fields_ = [("size", w.DWORD), ("reserved", w.LPWSTR), ("desktop", w.LPWSTR),
                    ("title", w.LPWSTR), ("x", w.DWORD), ("y", w.DWORD),
                    ("width", w.DWORD), ("height", w.DWORD), ("chars_x", w.DWORD),
                    ("chars_y", w.DWORD), ("fill", w.DWORD), ("flags", w.DWORD),
                    ("show", w.WORD), ("size_reserved", w.WORD), ("reserved2", w.LPVOID),
                    ("stdin", w.HANDLE), ("stdout", w.HANDLE), ("stderr", w.HANDLE)]

    class Process(c.Structure):
        _fields_ = [("handle", w.HANDLE), ("thread", w.HANDLE),
                    ("pid", w.DWORD), ("tid", w.DWORD)]

    kernel = c.WinDLL("kernel32", use_last_error=True)
    launch = kernel.CreateProcessW
    launch.argtypes = [w.LPCWSTR, w.LPWSTR, w.LPVOID, w.LPVOID, w.BOOL, w.DWORD,
                       w.LPVOID, w.LPCWSTR, c.POINTER(Startup), c.POINTER(Process)]
    kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    kernel.GetExitCodeProcess.argtypes = [w.HANDLE, c.POINTER(w.DWORD)]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    settings = Startup()
    settings.size = c.sizeof(settings)
    settings.desktop = "WinSta0\\" + name
    process = Process()
    command = c.create_unicode_buffer(subprocess.list2cmdline(argv))
    if not launch(argv[0], command, None, None, True, 0, None, None, c.byref(settings), c.byref(process)):
        raise c.WinError(c.get_last_error())
    try:
        if kernel.WaitForSingleObject(process.handle, 45000) != 0:
            raise RuntimeError("The isolated probe process did not finish.")
        code = w.DWORD()
        if not kernel.GetExitCodeProcess(process.handle, c.byref(code)):
            raise c.WinError(c.get_last_error())
        return code.value
    finally:
        kernel.CloseHandle(process.thread)
        kernel.CloseHandle(process.handle)


def wait_for(check, timeout=6):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        result = check()
        if result:
            return result
        time.sleep(.05)
    raise AssertionError("The disposable native probe timed out.")


def run_target(name, identity_file):
    require_isolated(name)
    import tkinter as tk

    target = tk.Tk()
    target.title("Disposable activation target")
    target.geometry("500x300")
    target.update()
    handle = api("GetAncestor", w.HWND, w.HWND, w.UINT)(target.winfo_id(), 2)
    identity_file.write_text(json.dumps([os.getpid(), handle]), encoding="ascii")
    target.after(15000, target.destroy)
    target.mainloop()


def check_chrome(name, url, loaded):
    from chuddy import windows
    from chuddy.interception import ChromeLauncher

    require_isolated(name)
    c.windll.user32.SetProcessDPIAware()
    spawns = []

    def spawn(args, **options):
        process = subprocess.Popen([args[0], "--disable-background-networking", *args[1:]], **options)
        spawns.append(process)
        return process

    with patch.dict("chuddy.interception.RETAIL_HOME", {"lcbo": url}):
        launch = ChromeLauncher(spawn=spawn).open("lcbo")
    enum_callback = c.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    enum = api("EnumWindows", w.BOOL, enum_callback, w.LPARAM)
    get_class = api("GetClassNameW", c.c_int, w.HWND, w.LPWSTR, c.c_int)
    get_title = api("GetWindowTextW", c.c_int, w.HWND, w.LPWSTR, c.c_int)
    is_visible = api("IsWindowVisible", w.BOOL, w.HWND)

    def owned_frames():
        frames = []

        @enum_callback
        def add_frame(handle, _):
            owner = windows.window_owner(handle)
            if owner and owner[0] == launch.process.pid and is_visible(handle):
                klass = c.create_unicode_buffer(64)
                get_class(handle, klass, len(klass))
                if klass.value == "Chrome_WidgetWin_1":
                    frames.append((handle, owner))
            return True

        enum(add_frame, 0)
        return frames

    try:
        assert loaded.wait(8), "The Chrome destination did not load on loopback."

        def fullscreen_frames():
            frames = []
            for handle, owner in owned_frames():
                title = c.create_unicode_buffer(128)
                get_title(handle, title, len(title))
                if "Disposable destination" in title.value and windows.fullscreen_status(handle, owner)[1]:
                    frames.append((handle, owner))
            return frames

        frames = wait_for(fullscreen_frames)
        assert len(spawns) == len(frames) == 1
        focused, fullscreen = windows.fullscreen_status(*frames[0])
        print(json.dumps({"chrome_launches": 1, "chrome_destination_frames": 1,
                          "loopback_loaded": True, "chrome_fullscreen_geometry": fullscreen,
                          "chrome_foreground": focused}), flush=True)
    finally:
        post = api("PostMessageW", w.BOOL, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
        for handle, owner in owned_frames():
            if windows.window_owner(handle) == owner:
                post(handle, 0x0010, 0, 0)
        launch.process.wait(timeout=10)


def run_app(name, source, baseline, chrome):
    require_isolated(name)
    faulthandler.dump_traceback_later(35, exit=True)
    sys.path.insert(0, str(source))
    import webview
    from run import NativeCoverDispatch
    from chuddy import windows
    from chuddy.controller import Controller
    from chuddy.server import DesktopServer, Handler
    from chuddy.store import Store
    from tests.helpers import FakeAgent, FakeAudio, FakeSimulator, FakeVoice

    with tempfile.TemporaryDirectory(prefix="chuddy-activation-", ignore_cleanup_errors=True) as folder:
        path = Path(folder)
        store = Store(path / "probe.sqlite")
        store.update_settings({"voice": False, "ai_enabled": False, "ai_work_detection": False,
                               "cooldown": 0, "rules": [], "distraction_destination": "casino"})
        with patch("chuddy.controller.LocalAgent", FakeAgent), patch(
                "chuddy.controller.AudioPipeline", FakeAudio), patch(
                "chuddy.voice.Voice", FakeVoice), patch("chuddy.hardware.Simulator", FakeSimulator):
            controller = Controller(store, path)
        server = DesktopServer(controller, source, 0)
        server.daemon_threads = False
        served_destination = threading.Event()
        seen = {route: threading.Event() for route in ("initial", "casino", "back")}
        allow_back = threading.Event()

        class ProbeHandler(Handler):
            def do_GET(self):
                if not self.valid_host():
                    return super().do_GET()
                if self.path == "/":
                    html = (source / "web" / "index.html").read_text(encoding="utf-8")
                    bootstrap = f"<script>window.CHUDDY_TOKEN={json.dumps(server.token)};</script>"
                    monitor = """
                    <script>
                    (async function () {
                        const note = route => fetch('/activation-observed/' + route);
                        await note('initial');
                        let opened = false;
                        const timer = setInterval(async () => {
                            if (!opened && location.hash === '#casino' &&
                                    !document.querySelector('#page-casino').hidden) {
                                opened = true;
                                await note('casino');
                            }
                            if (!opened) return;
                            const check = await fetch('/activation-back-allowed');
                            if ((await check.text()) !== 'yes') return;
                            clearInterval(timer);
                            document.querySelector('[data-casino-nav="companion"]').click();
                            if (location.hash === '#companion' &&
                                    !document.querySelector('#page-companion').hidden) await note('back');
                        }, 150);
                    })();
                    </script>
                    """
                    html = html.replace("<head>", "<head>" + bootstrap, 1)
                    return self.send(200, html.replace("</body>", monitor + "</body>", 1), "text/html")
                if self.path == "/activation-destination":
                    self.send(200, "<title>Disposable destination</title>Loopback only", "text/html")
                    served_destination.set()
                    return
                if self.path == "/activation-back-allowed":
                    return self.send(200, "yes" if allow_back.is_set() else "no", "text/plain")
                if self.path.startswith("/activation-observed/"):
                    route = self.path.rsplit("/", 1)[-1]
                    if route in seen:
                        seen[route].set()
                        return self.send(200, {"observed": True})
                super().do_GET()

        server.RequestHandlerClass = ProbeHandler
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base_url = f"http://127.0.0.1:{server.server_port}"
        shell = webview.create_window("Disposable Chuddy WebView",
                                      base_url + "/#companion",
                                      width=800, height=600)
        dispatch = NativeCoverDispatch(shell, controller)
        controller.native_window = dispatch
        controller.cover, controller.uncover = dispatch.cover, dispatch.uncover
        target_adapter = controller.distractions.windows
        failures = []
        targets = []

        def on_ui(callback):
            from System import Action

            done = threading.Event()
            results = []

            def wrapped():
                try:
                    require_isolated(name)
                    results.append(callback())
                except BaseException as error:
                    failures.append(error)
                finally:
                    done.set()

            shell.native.BeginInvoke(Action(wrapped))
            assert done.wait(4), "The disposable WinForms thread did not respond."
            assert results, "Native UI callback failed."
            return results[0]

        def native_frame():
            handle = shell.native.Handle.ToInt64()
            identity = windows.window_owner(handle)
            frame = {"handle": handle, "pid": identity[0], "thread_id": identity[1]}
            return (str(shell.native.Bounds), str(shell.native.WindowState),
                    str(shell.native.FormBorderStyle), windows.foreground_fullscreen(frame))

        def drive():
            try:
                assert seen["initial"].wait(10), "The isolated WebView did not load."
                initial = on_ui(native_frame)
                assert initial[1] == "Normal" and not initial[3]
                identity_file = path / "target.pid"
                target = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
                                           "--target", name, "--identity-file", str(identity_file)])
                targets.append(target)
                wait_for(identity_file.is_file)
                target_pid, target_handle = json.loads(identity_file.read_text(encoding="ascii"))
                assert windows.window_owner(target_handle)[0] == target_pid
                activate = api("SetForegroundWindow", w.BOOL, w.HWND)
                on_ui(lambda: bool(activate(target_handle)))
                original = windows.foreground()
                real_target_focus = bool(original and original["handle"] == target_handle)
                with ExitStack() as fixtures:
                    if not real_target_focus:
                        # Windows can deny foreground activation on a non-input
                        # desktop. Supply only the fixture target's detector and
                        # close dispatch, keeping real WM_CLOSE acknowledgement
                        # and the shell's foreground and monitor checks intact.
                        owner = windows.window_owner(target_handle)
                        original = {"handle": target_handle, "pid": owner[0], "thread_id": owner[1],
                                    "process": "fixture-editor.exe", "title": "Disposable activation target"}
                        posted = threading.Event()

                        def fixture_foreground():
                            return None if posted.is_set() else original

                        def fixture_close(target_window):
                            require_isolated(name)
                            assert target_window == original and windows.window_owner(target_handle) == owner
                            post = api("PostMessageW", w.BOOL, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
                            assert post(target_handle, 0x0010, 0, 0)
                            posted.set()

                        fixtures.enter_context(patch.object(windows, "foreground", fixture_foreground))
                        fixtures.enter_context(patch.object(target_adapter, "foreground", fixture_foreground))
                        fixtures.enter_context(patch.object(target_adapter, "request_close", fixture_close))
                    controller.set_session("start")
                    controller.distractions.delay = 0
                    close = fixtures.enter_context(patch.object(target_adapter, "request_close",
                                                                wraps=target_adapter.request_close))
                    controller.poll_work()
                    assert controller.interrupt("Disposable target")
                    assert controller.distractions.wait(6)
                    close.assert_called_once_with(original)
                result = controller.interruption
                result_frame = on_ui(native_frame)
                assert result["target_close"] == "confirmed"
                assert target.wait(timeout=4) == 0
                if baseline:
                    print(json.dumps({"baseline": True, "close": result["target_close"],
                                      "phase": result["phase"], "error": result["error_detail"]
                                      if result["phase"] == "error" else None,
                                      "reported_opening": bool(controller.state()["casino_opening_id"]),
                                      "real_target_focus": real_target_focus}), flush=True)
                    return
                native = dispatch.info()
                assert result["destination_delivery"] == "route_ready"
                assert native["verified_fullscreen"] is True, (result, native)
                if native["verified_foreground"]:
                    assert result["phase"] == "opened" and result["fullscreen"] == "confirmed"
                    assert result_frame[3]
                else:
                    assert result["phase"] == "error" and result["fullscreen"] == "failed"
                    assert "foreground focus" in result["error_detail"]
                assert seen["casino"].wait(6), "WebView did not route to the local casino."
                allow_back.set()
                assert seen["back"].wait(6), "Back to Chuddy did not restore the Companion route."
                wait_for(lambda: on_ui(native_frame)[:3] == initial[:3])
                assert controller.state()["session"] == "running"
                controller.set_session("stop")
                assert controller.state()["casino_opening_id"] is None
                assert not controller.voice.spoken and not controller.audio.recording
                print(json.dumps({"close": result["target_close"], "real_target_focus": real_target_focus,
                                  "native_geometry": native["verified_fullscreen"],
                                  "native_foreground": native["verified_foreground"], "phase": result["phase"],
                                  "windowed_before": True, "webview_casino_checked": True,
                                  "back_restored_placement": True, "stop_cleared_route": True}), flush=True)
                if chrome:
                    check_chrome(name, base_url + "/activation-destination", served_destination)
            except BaseException as error:
                failures.append(error)
            finally:
                if shell.native:
                    from System import Action
                    shell.native.BeginInvoke(Action(shell.native.Close))

        try:
            webview.start(drive, gui="edgechromium", private_mode=False, storage_path=str(path / "webview"))
            if failures:
                raise failures[0]
        finally:
            dispatch.close()
            server.shutdown()
            server.server_close()
            controller.close()
            store.close()
            for target in targets:
                target.wait(timeout=16)  # Target self-closes if never selected.
            faulthandler.cancel_dump_traceback_later()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("--chrome", action="store_true", help="Check the installed Chrome against a loopback fixture.")
    parser.add_argument("--desktop")
    parser.add_argument("--target")
    parser.add_argument("--identity-file", type=Path)
    args = parser.parse_args()
    if USER is None:
        parser.error("Use Windows with the project's pywebview environment.")
    if args.target:
        return run_target(args.target, args.identity_file)
    if args.desktop:
        return run_app(args.desktop, args.source.resolve(), args.baseline, args.chrome)
    create = api("CreateDesktopW", w.HANDLE, w.LPCWSTR, w.LPCWSTR, w.LPVOID,
                 w.DWORD, w.DWORD, w.LPVOID)
    name = "ChuddyActivation-" + uuid.uuid4().hex
    handle = create(name, None, None, 0, 0x01ff, None)
    if not handle:
        raise c.WinError(c.get_last_error())
    try:
        argv = [sys.executable, str(Path(__file__).resolve()), "--desktop", name,
                "--source", str(args.source.resolve())]
        if args.baseline:
            argv.append("--baseline")
        if args.chrome:
            argv.append("--chrome")
        return run_on_desktop(argv, name)
    finally:
        api("CloseDesktop", w.BOOL, w.HANDLE)(handle)


if __name__ == "__main__":
    raise SystemExit(main())
