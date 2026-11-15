"""Keep the selected downloaded model ready while the desktop app is open."""

from datetime import datetime, timezone
import json
import threading
import time
from urllib.error import HTTPError, URLError

from .ollama_service import DEFAULT_ENDPOINT, MODEL_CONTEXT, MODEL_LEASE


def model_key(settings, default_model):
    if settings.get("ai_enabled", True):
        return (settings.get("ai_endpoint") or DEFAULT_ENDPOINT,
                settings.get("ai_model") or default_model)
    return None


def model_name(name):
    name = name.strip().lower()
    return name if ":" in name.rsplit("/", 1)[-1] else name + ":latest"


class ModelUnavailable(ValueError):
    """Safe user-facing detail about the selected model or local endpoint."""


def validate_model(details):
    if details.get("remote_host"):
        raise ModelUnavailable("Select a downloaded local model.")
    thinking = details.get("thinking") or {}
    if isinstance(thinking, dict) and thinking.get("values") == [True]:
        raise ModelUnavailable("This model is thinking-only. Select a local chat model.")
    capabilities = details.get("capabilities")
    if capabilities and "completion" not in capabilities:
        raise ModelUnavailable("The selected model does not support chat replies.")


def failure_detail(error):
    if isinstance(error, ModelUnavailable):
        return str(error)
    if isinstance(error, (URLError, TimeoutError)):
        return "Could not reach local Ollama. Retrying."
    if isinstance(error, (OSError, RuntimeError)):
        return "The local model could not load. Retrying."
    return "Local Ollama did not return a valid status. Retrying."


class LocalModelAvailability:
    """One polling worker and a finite lease. Ollama owns its process and models."""

    def __init__(self, settings, default_model, request, inference_lock, turn_active,
                 start_server, after_first_check=None, *, probe_interval=15):
        self.settings = settings
        self.default_model = default_model
        self.request = request
        self.inference_lock = inference_lock
        self.turn_active = turn_active
        self.start_server = start_server
        self.after_first_check = after_first_check
        self.probe_interval = probe_interval
        self.closed = threading.Event()
        self.wake = threading.Event()
        self.lock = threading.Lock()
        self.observed = None
        self.state = {}
        self.validated = None
        self.lease_until = 0
        self.thread = threading.Thread(target=self._run, name="chuddy-model-availability", daemon=True)

    def start(self):
        self.thread.start()

    def close(self):
        with self.lock:
            self.closed.set()
        self.wake.set()

    def refresh(self):
        self.wake.set()

    def _key(self):
        with self.lock:
            return None if self.closed.is_set() else model_key(self.settings(), self.default_model)

    def info(self):
        key = self._key()
        with self.lock:
            if key is None:
                return {"ready": False, "model_status": "disabled", "model_error": None}
            if self.observed != key:
                return {"ready": False, "model_status": "checking", "model_error": None, "model": key[1]}
            return {**self.state, "model": key[1]}

    def _publish(self, key, status, detail=None):
        if self.closed.is_set() or self._key() != key:
            return
        with self.lock:
            self.observed = key
            self.state = {"ready": status == "ready", "model_status": status, "model_error": detail}

    def _json(self, key, path, payload=None, timeout=3):
        if self.closed.is_set() or self._key() != key:
            raise InterruptedError
        with self.request(key[0], path, payload, timeout) as response:
            result = json.load(response)
        if not isinstance(result, dict):
            raise ValueError("Expected a local JSON object")
        return result

    def _check_installed(self, key):
        if self.validated == key:
            return
        try:
            details = self._json(key, "/api/show", {"model": key[1]})
        except HTTPError as error:
            if error.code == 404:
                error.close()
                raise ModelUnavailable("The selected model is missing from local Ollama.") from error
            raise
        validate_model(details)
        self.validated = key

    def _remaining(self, resident):
        expiry = resident.get("expires_at")
        if expiry:
            try:
                return (datetime.fromisoformat(expiry) - datetime.now(timezone.utc)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                pass
        return self.lease_until - time.monotonic()

    def _probe(self, key):
        try:
            running = self._json(key, "/api/ps")["models"]
        except HTTPError as error:
            if error.code in (404, 405):
                error.close()
                raise ModelUnavailable("This Ollama server does not support model status checks.") from error
            raise
        except (URLError, TimeoutError, ConnectionError) as error:
            self.validated = None
            self.lease_until = 0
            if key[0].rstrip("/") in (DEFAULT_ENDPOINT, "http://localhost:11434"):
                if self._key() == key and self.start_server(lambda: self._key() == key):
                    return "checking", "Starting local Ollama.", 2
            raise error
        self._check_installed(key)
        wanted = model_name(key[1])
        selected, other = None, False
        for row in running:
            name = row.get("model") or row.get("name")
            if not name:
                continue
            if model_name(name) == wanted:
                selected = row
            else:
                other = True
        if selected is not None:
            if (other or self._remaining(selected) > MODEL_LEASE / 2
                    or selected.get("context_length") not in (None, 0, MODEL_CONTEXT)):
                return "ready", None, self.probe_interval
        elif other:
            return "waiting", "Waiting for another local model to release memory.", self.probe_interval

        if self.turn_active() or not self.inference_lock.acquire(blocking=False):
            return ("ready" if selected is not None else "checking"), None, 2
        try:
            if self.turn_active():
                return ("ready" if selected is not None else "checking"), None, 2
            if selected is None:
                self._publish(key, "warming")
            try:
                result = self._json(key, "/api/generate", {
                    "model": key[1], "stream": False, "keep_alive": MODEL_LEASE,
                    "options": {"num_ctx": MODEL_CONTEXT},
                }, timeout=35)
            except TimeoutError as error:
                raise ModelUnavailable("The local model took too long to load. Retrying.") from error
            if result.get("done") is not True or "error" in result:
                raise RuntimeError("Local warm request did not complete")
            self.lease_until = time.monotonic() + MODEL_LEASE
            return "ready", None, self.probe_interval
        finally:
            self.inference_lock.release()

    def _run(self):
        previous = None
        next_check = 0
        failures = 0
        while not self.closed.is_set():
            key = self._key()
            changed = key != previous
            if changed:
                self.validated = None
                self.lease_until = 0
                failures = 0
                previous = key
            refresh = self.wake.is_set()
            self.wake.clear()
            if key is not None and (changed or refresh or time.monotonic() >= next_check):
                try:
                    status, detail, delay = self._probe(key)
                    failures = 0
                except InterruptedError:
                    continue
                except HTTPError as error:
                    with error:
                        try:
                            data = error.read(2048).lower()
                        except OSError:
                            data = b""
                    if any(term in data for term in (b"out of memory", b"insufficient memory", b"more system memory")):
                        detail = "There is too little free memory to load the selected model. Retrying."
                    else:
                        detail = f"Local Ollama returned HTTP {error.code}. Retrying."
                    status, delay = "recovering", min(60, 2 ** min(failures + 2, 6))
                    failures += 1
                except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError) as error:
                    status, detail = "recovering", failure_detail(error)
                    delay = min(60, 2 ** min(failures + 2, 6))
                    failures += 1
                self._publish(key, status, detail)
                next_check = time.monotonic() + delay
            else:
                status = "disabled" if key is None else "checking"
            if (self.after_first_check is not None and status in ("ready", "disabled", "recovering", "waiting")
                    and not self.closed.is_set()):
                callback, self.after_first_check = self.after_first_check, None
                callback()
            self.wake.wait(min(1, max(0, next_check - time.monotonic())) if key else 1)
