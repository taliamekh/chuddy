"""Loopback-only desktop API; extension pairing is separately scoped."""

import json
import mimetypes
import re
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse


class DesktopServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, controller, root, port=8765):
        self.controller, self.root = controller, Path(root).resolve()
        self.token = secrets.token_urlsafe(32)
        super().__init__(("127.0.0.1", port), Handler)
        self.allowed_hosts = {
            f"127.0.0.1:{self.server_port}",
            f"localhost:{self.server_port}",
        }
        self.controller.ui_url = f"http://127.0.0.1:{self.server_port}"


class Handler(BaseHTTPRequestHandler):
    server_version = "Chuddy/0.1"

    def log_message(self, *args):
        pass

    def origin(self):
        return self.headers.get("Origin", "")

    def extension(self):
        return bool(
            re.fullmatch(
                r"(chrome-extension://[a-p]{32}|moz-extension://[A-Za-z0-9-]+)",
                self.origin(),
            )
        )

    def valid_host(self):
        return self.headers.get("Host", "") in self.server.allowed_hosts

    def local_origin(self):
        return not self.origin() or self.origin() in {
            f"http://{host}" for host in self.server.allowed_hosts
        }

    def send(self, status, body, content_type="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header(
            "Content-Type",
            content_type
            + (
                "; charset=utf-8"
                if content_type.startswith(
                    ("text/", "application/json", "application/javascript")
                )
                else ""
            ),
        )
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; media-src 'self' blob:; frame-ancestors 'none'",
        )
        if self.extension() and self.path.startswith("/browser/"):
            self.send_header("Access-Control-Allow-Origin", self.origin())
            self.send_header("Vary", "Origin")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def auth(self, browser=False):
        if not self.valid_host():
            self.send(403, {"error": "Invalid local host."})
            return False
        if browser:
            expected = self.server.controller.browser_token
            if not (self.extension() or self.local_origin()):
                self.send(403, {"error": "Browser origin not permitted."})
                return False
        else:
            expected = self.server.token
            if not self.local_origin():
                self.send(403, {"error": "Desktop origin not permitted."})
                return False
        actual = self.headers.get("X-Chuddy-Token", "")
        if not secrets.compare_digest(actual.encode("utf-8"), expected.encode("utf-8")):
            self.send(401, {"error": "Pair with this local Chuddy instance."})
            return False
        return True

    def do_OPTIONS(self):
        if (
            not self.valid_host()
            or not self.extension()
            or self.path not in ("/browser/report", "/browser/rules")
        ):
            return self.send(403, {"error": "Origin not permitted."})
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", self.origin())
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Chuddy-Token")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if not self.valid_host():
                return self.send(403, {"error": "Invalid local host."})
            if path.startswith("/api/"):
                if not self.auth():
                    return
                if path == "/api/state":
                    return self.send(200, self.server.controller.state())
                if path == "/api/devices":
                    return self.send(200, self.server.controller.audio.devices())
                return self.send(404, {"error": "Unknown endpoint."})
            if path == "/browser/rules":
                if self.auth(browser=True):
                    return self.send(
                        200,
                        {
                            "rules": [
                                r
                                for r in self.server.controller.store.settings()[
                                    "rules"
                                ]
                                if r["kind"] == "url"
                            ]
                        },
                    )
                return
            if (
                not self.local_origin()
                or self.headers.get("Sec-Fetch-Site") == "cross-site"
            ):
                return self.send(
                    403, {"error": "Open Chuddy directly from this computer."}
                )
            if path == "/health":
                return self.send(200, {"status": "ok", "app": "Chuddy"})
            if path in ("/", "/index.html"):
                html = (self.server.root / "web" / "index.html").read_text(
                    encoding="utf-8"
                )
                bootstrap = (
                    "<script>window.CHUDDY_TOKEN="
                    + json.dumps(self.server.token)
                    + ";</script>"
                )
                html = html.replace("<head>", "<head>" + bootstrap, 1)
                return self.send(200, html, "text/html")
            if path == "/reference.png":
                return self.send(
                    200,
                    (self.server.root / "inputs" / "chuddy-reference.png").read_bytes(),
                    "image/png",
                )
            if path.startswith("/web/"):
                base = (self.server.root / "web").resolve()
                target = (base / unquote(path[5:])).resolve()
                if target.is_relative_to(base) and target.is_file():
                    return self.send(
                        200,
                        target.read_bytes(),
                        mimetypes.guess_type(str(target))[0]
                        or "application/octet-stream",
                    )
            return self.send(404, {"error": "Not found."})
        except Exception as error:
            self.server.controller.store.log("http error", type(error).__name__)
            self.send(
                500, {"error": "Local application error. Inspect the activity log."}
            )

    def do_POST(self):
        path = urlparse(self.path).path
        browser = path == "/browser/report"
        if path != "/api/action" and not browser:
            return self.send(404, {"error": "Unknown endpoint."})
        if not self.auth(browser=browser):
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size < 2 or size > 131072:
                return self.send(
                    413, {"error": "JSON body must be 2 bytes to 128 KiB."}
                )
            if (
                self.headers.get("Content-Type", "").split(";")[0].strip()
                != "application/json"
            ):
                return self.send(415, {"error": "Use application/json."})
            body = json.loads(
                self.rfile.read(size),
                parse_constant=lambda _: (_ for _ in ()).throw(
                    ValueError("Non-finite JSON number.")
                ),
            )
            if not isinstance(body, dict):
                raise ValueError("Expected a JSON object.")
            result = (
                self.server.controller.browser_event(body)
                if browser
                else self.server.controller.action(body)
            )
            self.send(200, result if result is not None else {"ok": True})
        except (ValueError, TypeError, KeyError) as error:
            self.send(400, {"error": str(error)[:250]})
        except Exception as error:
            self.server.controller.store.log("action error", type(error).__name__)
            self.send(
                503,
                {
                    "error": f"Action unavailable ({type(error).__name__}). Check the device/service configuration."
                },
            )
