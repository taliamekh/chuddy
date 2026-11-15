"""Isolated local Ollama fixtures for Chuddy's lifetime readiness."""

from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import errno
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from chuddy.agent import DEFAULT_MODEL, LocalAgent, local_request
from chuddy.model_availability import LocalModelAvailability
from chuddy.ollama_service import DEFAULT_ENDPOINT, MODEL_CONTEXT, MODEL_LEASE, PortableOllama
from chuddy.store import Store
from tests.helpers import FakeVoice


def running(name=DEFAULT_MODEL, *, seconds=MODEL_LEASE, context=MODEL_CONTEXT):
    expiry = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    return {"model": name, "expires_at": expiry.isoformat(), "context_length": context}


class FakeOllama:
    def __init__(self):
        self.port = 0
        self.models = {DEFAULT_MODEL: {"capabilities": ["completion"]}}
        self.resident = []
        self.requests = []
        self.gate = None
        self.warm_entered = threading.Event()
        self.model_error = None
        self.chat_error = False
        self.server = None

    @property
    def endpoint(self):
        return f"http://127.0.0.1:{self.port}"

    def start(self):
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def handle_json(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                payload = json.loads(body) if body else None
                fixture.requests.append((self.path, payload))
                code = 200
                if self.path == "/api/ps":
                    result = {"models": fixture.resident}
                elif self.path == "/api/show":
                    result = fixture.models.get(payload["model"])
                    if result is None:
                        code, result = 404, {"error": "model not found"}
                elif self.path == "/api/generate":
                    fixture.warm_entered.set()
                    if fixture.gate:
                        fixture.gate.wait(3)
                    if fixture.model_error:
                        code, result = 500, {"error": fixture.model_error}
                    else:
                        fixture.resident = [running(payload["model"], seconds=payload["keep_alive"])]
                        result = {"done": True, "done_reason": "load"}
                elif self.path == "/api/chat":
                    if fixture.chat_error:
                        code, result = 503, {"error": "overloaded"}
                    else:
                        result = {"message": {"content": "Hi there."}, "done": True}
                else:
                    code, result = 404, {}
                self.send_response(code)
                self.end_headers()
                self.wfile.write(json.dumps(result).encode())

            do_GET = do_POST = handle_json

        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self.port = self.server.server_port
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        self.thread.start()
        return True

    def stop(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join()
            self.server = None

    @property
    def warm_requests(self):
        return [payload for path, payload in self.requests if path == "/api/generate"]


class AvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.ollama = FakeOllama()
        self.ollama.start()
        self.addCleanup(self.ollama.stop)
        self.settings = {"ai_enabled": True, "ai_endpoint": self.ollama.endpoint}
        self.inference = threading.Lock()
        self.busy = False
        self.launch = Mock(return_value=False)

    def start_monitor(self, request=local_request, after=None):
        monitor = LocalModelAvailability(
            lambda: self.settings.copy(), DEFAULT_MODEL, request, self.inference,
            lambda: self.busy, self.launch, after, probe_interval=.05,
        )
        self.addCleanup(monitor.thread.join, 4)
        self.addCleanup(monitor.close)
        monitor.start()
        return monitor

    def wait_for(self, predicate, timeout=8):
        deadline = time.monotonic() + timeout
        while not predicate():
            self.assertLess(time.monotonic(), deadline, "Local model fixture timed out")
            time.sleep(.01)

    def status(self, monitor, status):
        self.wait_for(lambda: monitor.info()["model_status"] == status)
        return monitor.info()

    def test_warm_once_then_renew_before_expiry_and_recover_eviction(self):
        first = threading.Event()
        monitor = self.start_monitor(after=first.set)
        self.assertTrue(self.status(monitor, "ready")["ready"])
        self.assertTrue(first.wait(1))
        self.assertEqual(self.ollama.warm_requests, [{
            "model": DEFAULT_MODEL, "stream": False, "keep_alive": MODEL_LEASE,
            "options": {"num_ctx": MODEL_CONTEXT},
        }])
        self.ollama.resident = [running(seconds=5)]
        self.wait_for(lambda: len(self.ollama.warm_requests) == 2)
        self.ollama.resident = []
        self.wait_for(lambda: len(self.ollama.warm_requests) == 3)
        self.status(monitor, "ready")
        self.launch.assert_not_called()

    def test_custom_service_disappears_and_recovers_without_launching_portable(self):
        monitor = self.start_monitor()
        self.status(monitor, "ready")
        self.ollama.stop()
        self.status(monitor, "recovering")
        self.ollama.resident = []
        self.ollama.start()
        self.status(monitor, "ready")
        self.assertEqual(len(self.ollama.warm_requests), 2)
        self.launch.assert_not_called()

    def test_recover_default_port_using_existing_portable_start_entry(self):
        self.ollama.stop()
        self.settings["ai_endpoint"] = DEFAULT_ENDPOINT
        self.launch.side_effect = lambda wanted: wanted() and self.ollama.start()

        def redirect_to_fixture(endpoint, path, payload, timeout):
            self.assertEqual(endpoint, DEFAULT_ENDPOINT)
            return local_request(self.ollama.endpoint, path, payload, timeout)

        monitor = self.start_monitor(redirect_to_fixture)
        self.assertTrue(self.status(monitor, "ready")["ready"])
        self.launch.assert_called_once()

    def test_active_turn_and_busy_gate_skip_loads_and_external_model_is_untouched(self):
        self.busy = True
        monitor = self.start_monitor()
        self.wait_for(lambda: any(path == "/api/show" for path, _ in self.ollama.requests))
        self.assertEqual(self.ollama.warm_requests, [])
        self.inference.acquire()
        try:
            self.busy = False
            monitor.refresh()
            self.status(monitor, "checking")
            self.assertEqual(self.ollama.warm_requests, [])
            # Hold the gate until the monitor has observed the competing
            # model, so an earlier empty snapshot cannot start a warm-up.
            self.ollama.resident = [running("another-model")]
            monitor.refresh()
            self.status(monitor, "waiting")
        finally:
            self.inference.release()
        self.assertEqual(self.ollama.warm_requests, [])
        self.ollama.resident.append(running(seconds=3))
        monitor.refresh()
        self.status(monitor, "ready")
        self.assertEqual(self.ollama.warm_requests, [])

    def test_settings_change_or_disable_cannot_publish_an_inflight_old_result(self):
        self.ollama.gate = threading.Event()
        self.addCleanup(self.ollama.gate.set)
        monitor = self.start_monitor()
        self.assertTrue(self.ollama.warm_entered.wait(2))
        new = FakeOllama()
        new_model = "fixture-new:latest"
        new.models[new_model] = {"capabilities": ["completion"]}
        new.start()
        self.addCleanup(new.stop)
        self.settings.update(ai_endpoint=new.endpoint, ai_model="fixture-new", ai_enabled=False)
        self.assertEqual(monitor.info()["model_status"], "disabled")
        self.ollama.gate.set()
        self.settings["ai_enabled"] = True
        self.assertFalse(monitor.info()["ready"])
        new.models["fixture-new"] = new.models[new_model]
        monitor.refresh()
        self.assertEqual(self.status(monitor, "ready")["model"], "fixture-new")
        self.assertEqual(len(self.ollama.warm_requests), 1)
        self.assertEqual(len(new.warm_requests), 1)

    def test_reports_missing_unsupported_and_memory_exhaustion(self):
        self.settings["ai_model"] = "absent"
        monitor = self.start_monitor()
        self.assertIn("missing", self.status(monitor, "recovering")["model_error"])
        self.ollama.models["embedder"] = {"capabilities": ["embedding"]}
        self.settings["ai_model"] = "embedder"
        monitor.refresh()
        self.wait_for(lambda: "does not support" in (monitor.info()["model_error"] or ""))
        self.assertEqual(self.ollama.warm_requests, [])
        self.ollama.model_error = "cuda out of memory"
        self.settings["ai_model"] = DEFAULT_MODEL
        monitor.refresh()
        self.wait_for(lambda: "free memory" in (monitor.info()["model_error"] or ""))
        previous = len(self.ollama.requests)
        time.sleep(.13)
        self.assertEqual(len(self.ollama.requests), previous)
        self.ollama.model_error = None
        monitor.refresh()
        self.status(monitor, "ready")

    def test_exit_invalidates_inflight_start(self):
        self.ollama.gate = threading.Event()
        self.addCleanup(self.ollama.gate.set)
        monitor = self.start_monitor()
        self.assertTrue(self.ollama.warm_entered.wait(2))
        monitor.close()
        self.ollama.gate.set()
        monitor.thread.join(4)
        self.assertFalse(monitor.thread.is_alive())
        self.assertFalse(monitor.info()["ready"])
        self.assertEqual(len(self.ollama.warm_requests), 1)

    def test_managed_chat_and_manual_warm_respect_ai_setting(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / "temporary.sqlite3")
            try:
                store.update_settings({"ai_endpoint": self.ollama.endpoint, "voice": False})
                agent = LocalAgent(store, lambda: {}, lambda *args, **kw: None, FakeVoice())
                monitor = agent.start_availability(self.launch)
                try:
                    self.status(monitor, "ready")
                    agent.begin("Hello.")
                    agent.worker.join(3)
                    self.assertEqual(agent.info()["source"], "local model")
                    chats = [p for path, p in self.ollama.requests if path == "/api/chat"]
                    self.assertEqual(chats[-1]["keep_alive"], MODEL_LEASE)
                    self.assertEqual(chats[-1]["options"]["num_ctx"], MODEL_CONTEXT)
                    self.ollama.chat_error = True
                    agent.begin("The app is busy.")
                    agent.worker.join(3)
                    self.assertEqual(agent.info()["source"], "offline fallback · local model error")
                    self.assertEqual(agent.info()["model_status"], "ready")
                    self.ollama.resident = [running("another-model")]
                    monitor.refresh()
                    self.status(monitor, "waiting")
                    count = sum(path == "/api/chat" for path, _ in self.ollama.requests)
                    agent.begin("Hello from the blocked model.")
                    agent.worker.join(3)
                    self.assertEqual(agent.info()["model_status"], "waiting")
                    self.assertEqual(sum(path == "/api/chat" for path, _ in self.ollama.requests), count)
                    store.setting("ai_enabled", False)
                    self.assertEqual(agent.warm(), {"status": "disabled"})
                    self.assertEqual(agent.info()["model_status"], "disabled")
                    agent.begin("Hello again.")
                    agent.worker.join(3)
                    self.assertEqual(agent.info()["source"], "offline script")
                finally:
                    monitor.close()
                    monitor.thread.join(4)
                    agent.cancel()
            finally:
                store.close()


class PortableServerTests(unittest.TestCase):
    def test_spawns_only_on_refused_port_and_never_duplicates_an_owned_start(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            exe = base / "ollama.exe"
            exe.touch()
            service = PortableOllama(exe, base)
            with patch("chuddy.ollama_service.socket.create_connection") as check, \
                    patch("chuddy.ollama_service.subprocess.Popen") as spawn:
                check.side_effect = socket.timeout("busy")
                self.assertFalse(service.start_if_missing())
                check.side_effect = None
                self.assertFalse(service.start_if_missing())
                spawn.assert_not_called()
                check.side_effect = ConnectionRefusedError(errno.ECONNREFUSED, "closed")
                spawn.return_value.poll.return_value = None
                spawn.return_value.pid = 321
                self.assertFalse(service.start_if_missing(lambda: False))
                self.assertTrue(service.start_if_missing())
                self.assertFalse(service.start_if_missing())
                self.assertFalse(PortableOllama(exe, base).start_if_missing())
                spawn.assert_called_once()
                self.assertEqual(spawn.call_args.kwargs["env"]["OLLAMA_NO_CLOUD"], "1")
                self.assertEqual((base / "ollama.pid").read_text(), "321")
