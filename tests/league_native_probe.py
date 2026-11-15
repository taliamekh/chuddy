"""Opt-in, bounded real-input-desktop probe using one disposable target."""

import argparse
import ctypes as c
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import URLError
from urllib.request import Request, urlopen
import uuid


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def require_stopped():
    base = "http://127.0.0.1:8765"
    try:
        html = urlopen(base, timeout=2).read().decode()
    except URLError as error:
        if isinstance(error.reason, ConnectionRefusedError):
            return
        raise
    token = json.loads(re.search(r'window\.CHUDDY_TOKEN=("[^"\r\n]+")', html).group(1))
    with urlopen(Request(base + "/api/state", headers={"X-Chuddy-Token": token}), timeout=2) as response:
        state = json.load(response)
    if state["session"] != "stopped" or state["microphone"]["active"] or state["voice"]["speaking"]:
        raise RuntimeError("The main Chuddy session must already be stopped and quiet.")


def fixture_window(*, landing=False):
    import tkinter as tk

    root = tk.Tk()
    root.title("League of Legends" if landing else "Chuddy League disposable focus probe")
    root.geometry("520x150")
    tk.Label(root, text="Disposable Chuddy focus test", font=("Segoe UI", 17)).pack(pady=30)
    root.update()
    user = c.WinDLL("user32")
    user.GetAncestor.argtypes, user.GetAncestor.restype = [w.HWND, w.UINT], w.HWND
    user.SetForegroundWindow.argtypes, user.SetForegroundWindow.restype = [w.HWND], w.BOOL
    handle = user.GetAncestor(root.winfo_id(), 2)
    if landing:
        # A resizable borderless fixture, separate from any Riot executable.
        user.GetWindowLongW.argtypes, user.GetWindowLongW.restype = [w.HWND, c.c_int], w.LONG
        user.SetWindowLongW.argtypes = [w.HWND, c.c_int, w.LONG]
        style = user.GetWindowLongW(handle, -16)
        user.SetWindowLongW(handle, -16, style & ~0x00C40000)
        user.SetWindowPos.argtypes = [w.HWND, w.HWND, c.c_int, c.c_int, c.c_int, c.c_int, w.UINT]
        user.SetWindowPos(handle, None, 0, 0, 0, 0, 0x37)  # FRAMECHANGED, retain position/focus.
    return root, handle, user


def target(identity_path, *, landing=False):
    root, handle, user = fixture_window(landing=landing)
    user.SetForegroundWindow(handle)
    identity_path.write_text(str(os.getpid()), encoding="ascii")
    root.after(5500 if landing else 7000, root.destroy)
    root.mainloop()


def probe(*, disposable):
    from chuddy import windows
    from chuddy.interception import InterceptionCoordinator
    from chuddy.league import LeagueLaunch, LeagueLauncher

    require_stopped()
    league = LeagueLauncher()
    manager = InterceptionCoordinator(allowed=lambda: True, close_desktop=True,
                                      league_launcher=league, delay=0, warning_seconds=0)
    install = None if disposable else league.prepare()
    ticks = {}
    real_confirm = manager._close_confirmed

    def close_confirmed():
        exposed = real_confirm()
        ticks["close"] = time.monotonic()
        return exposed

    manager._close_confirmed = close_confirmed

    class ObservedDesktop:
        def __getattr__(self, name):
            return getattr(windows, name)

        def application_windows(self, paths):
            candidates = windows.application_windows(paths)
            handle = windows.foreground_handle()
            for window in candidates:
                if window["handle"] == handle:
                    role = paths[window["executable_path"]]
                    ticks.setdefault(role, time.monotonic())
            return candidates

    league.desktop = ObservedDesktop()
    with tempfile.TemporaryDirectory(prefix="chuddy-league-probe-") as directory:
        pid_file = Path(directory) / "target.pid"
        landing_pid = Path(directory) / "landing.pid"
        landing_process = None
        scratch = subprocess.Popen([sys.executable, __file__, "--target", str(pid_file)])
        source = None

        if disposable:
            class DisposableDesktop(ObservedDesktop):
                def application_windows(self, paths):
                    if not landing_pid.is_file():
                        return []
                    pid = int(landing_pid.read_text(encoding="ascii"))
                    return [window for window in super().application_windows(paths) if window["pid"] == pid]

            desktop = DisposableDesktop()
            executable = os.path.normcase(str(Path(sys._base_executable).resolve()))
            install = SimpleNamespace(application_paths=lambda: {executable: "league_client"})

            class DisposableLauncher:
                def prepare(self):
                    return install

                def open(self, installation):
                    nonlocal landing_process
                    landing_process = subprocess.Popen([sys.executable, __file__, "--landing", str(landing_pid)])
                    return LeagueLaunch(installation, desktop, startup_timeout=4)

            manager.league_launcher = DisposableLauncher()
        try:
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                candidate = windows.foreground()
                if (candidate and pid_file.is_file()
                        and candidate["pid"] == int(pid_file.read_text(encoding="ascii"))
                        and candidate["title"] == "Chuddy League disposable focus probe"):
                    source = candidate
                    break
                time.sleep(.04)
            require_stopped()
            if source is None:
                print(json.dumps({"disposable_source_foreground": False, "closed": False, "launched": False}),
                      flush=True)
                return
            admission = manager.request("Disposable focus probe", destination="league",
                                        window=source, seen=time.monotonic())
            assert admission["ok"] and manager.wait(timeout=40)
            outcome = manager.info()
            assert outcome["target_close"] == "confirmed"
            keys = ("target_close", "phase", "fullscreen", "destination_delivery", "destination_window",
                    "destination_foreground", "destination_monitor_coverage",
                    "destination_layout_request", "error_detail")
            print(json.dumps({key: outcome.get(key) for key in keys}), flush=True)
            for role, window in LeagueLaunch(install, desktop if disposable else windows).windows():
                limits = windows.application_client_area(window)
                focused = windows.foreground_handle() == window["handle"]
                delta = round(ticks[role] - ticks["close"], 3) if role in ticks and "close" in ticks else None
                print(json.dumps({"role": role, "actual_foreground": focused,
                                  "client": limits, "seconds_from_close_to_focus": delta}), flush=True)
        finally:
            manager.close()
            scratch.wait(timeout=9)
            if landing_process:
                landing_process.wait(timeout=8)


def layout_check(name=None):
    from chuddy import windows
    from tests.native_activation_probe import api, require_isolated, run_on_desktop

    if name is None:
        create = api("CreateDesktopW", w.HANDLE, w.LPCWSTR, w.LPCWSTR, w.LPVOID,
                     w.DWORD, w.DWORD, w.LPVOID)
        name = "ChuddyActivation-" + uuid.uuid4().hex
        desktop = create(name, None, None, 0, 0x01ff, None)
        if not desktop:
            raise c.WinError(c.get_last_error())
        try:
            return run_on_desktop([sys.executable, __file__, "--layout-desktop", name], name)
        finally:
            api("CloseDesktop", w.BOOL, w.HANDLE)(desktop)

    require_isolated(name)
    root, handle, _ = fixture_window(landing=True)
    try:
        owner = windows.window_owner(handle)
        assert owner[0] == os.getpid()
        exact = {"handle": handle, "pid": owner[0], "thread_id": owner[1]}
        initial = windows.application_client_area(exact)
        assert initial and not initial["coverage"]
        native = windows._user32()

        class PlacementOnly:
            def GetForegroundWindow(self):
                # On this non-input desktop only the gate is substituted.
                return handle

            def __getattr__(self, key):
                return getattr(native, key)

        with patch.object(windows, "_user32", return_value=PlacementOnly()):
            assert windows.fill_application_monitor(exact) == "resize"
        root.update()
        settled = windows.application_client_area(exact)
        assert settled and settled["coverage"]
        print(json.dumps({"win32_client_before": initial, "win32_client_after": settled,
                          "actual_foreground": windows.foreground_handle() == handle,
                          "test_substituted_foreground_gate": True}), flush=True)
    finally:
        root.destroy()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--target", type=Path)
    mode.add_argument("--landing", type=Path)
    mode.add_argument("--check-disposable-focus", action="store_true")
    mode.add_argument("--check-installed-client", action="store_true")
    mode.add_argument("--check-native-layout", action="store_true")
    mode.add_argument("--layout-desktop")
    arguments = parser.parse_args()
    if arguments.target or arguments.landing:
        target(arguments.target or arguments.landing, landing=bool(arguments.landing))
    elif arguments.check_native_layout or arguments.layout_desktop:
        raise SystemExit(layout_check(arguments.layout_desktop))
    else:
        probe(disposable=arguments.check_disposable_focus)
