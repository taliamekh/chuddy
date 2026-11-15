"""Keyboard mapping in disposable WebView2 on a separate Windows desktop."""
import ctypes as c
from ctypes import wintypes as w
import faulthandler
import json
from pathlib import Path
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.native_activation_probe import api, require_isolated, run_on_desktop


def run(name, report):
    require_isolated(name)
    faulthandler.dump_traceback_later(20, exit=True)
    import webview
    shell = webview.create_window("Disposable WebView2 keypad check", html="""<!doctype html>
    <title>Disposable keyboard observation</title><textarea id="input"></textarea><script>
    window.observed = [];
    for (const type of ['keydown', 'keyup']) {
      document.addEventListener(type, event => observed.push({
        type, code: event.code, key:event.key, location: event.location, trusted:event.isTrusted,
        document_has_focus: document.hasFocus()
      }));
    }
    </script>""", width=700, height=450)
    failures = []

    def drive():
        try:
            require_isolated(name)
            shell.events.loaded.wait(5)
            assert shell.evaluate_js("Array.isArray(window.observed)")
            root = shell.native.Handle.ToInt64()
            names = {}
            callback_type = c.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
            label = api("GetClassNameW", c.c_int, w.HWND, w.LPWSTR, c.c_int)

            @callback_type
            def listed(handle, _):
                buffer = c.create_unicode_buffer(128)
                if label(handle, buffer, 128):
                    names[handle] = buffer.value
                return True

            api("EnumChildWindows", w.BOOL, w.HWND, callback_type, w.LPARAM)(root, listed, 0)
            post = api("PostMessageW", w.BOOL, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
            vk_add = 0x6b
            scan = api("MapVirtualKeyW", w.UINT, w.UINT, w.UINT)(vk_add, 0)
            lparam = 1 | (scan << 16)
            observed, target = [], None
            for handle, klass in names.items():
                if not klass.startswith("Chrome_"):
                    continue
                require_isolated(name)
                post(handle, 0x100, vk_add, lparam)
                post(handle, 0x101, vk_add, lparam | 0xc0000000)
                time.sleep(.25)
                observed = shell.evaluate_js("window.observed")
                if observed:
                    target = klass
                    break
            result = {"webview2": True, "input_desktop_switched": False, "audio": False,
                      "children": list(names.values()), "posted_target": target, "events": observed}
            report.write_text(json.dumps(result, indent=2), encoding="utf-8")
            assert [item["type"] for item in observed] == ["keydown", "keyup"], result
            assert all(item["code"] == "NumpadAdd" and item["location"] == 3 and item["trusted"]
                       for item in observed), result
        except BaseException as error:
            failures.append(error)
        finally:
            if shell.native:
                from System import Action
                shell.native.BeginInvoke(Action(shell.native.Close))

    with tempfile.TemporaryDirectory(prefix="chuddy-keypad-webview-", ignore_cleanup_errors=True) as folder:
        webview.start(drive, gui="edgechromium", private_mode=False, storage_path=folder)
    if failures:
        raise failures[0]
    faulthandler.cancel_dump_traceback_later()


if len(sys.argv) == 3:
    run(sys.argv[1], Path(sys.argv[2]))
else:
    name = "ChuddyActivation-" + uuid.uuid4().hex
    create = api("CreateDesktopW", w.HANDLE, w.LPCWSTR, w.LPCWSTR, w.LPVOID,
                 w.DWORD, w.DWORD, w.LPVOID)
    desktop = create(name, None, None, 0, 0x01ff, None)
    if not desktop:
        raise c.WinError(c.get_last_error())
    try:
        report = ROOT / "test-results" / "native-numpad.json"
        report.parent.mkdir(exist_ok=True)
        code = run_on_desktop([sys.executable, str(Path(__file__).resolve()), name, str(report)], name)
        if report.exists():
            print(report.read_text(encoding="utf-8"))
        raise SystemExit(code)
    finally:
        api("CloseDesktop", w.BOOL, w.HANDLE)(desktop)
