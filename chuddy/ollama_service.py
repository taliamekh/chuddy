"""Portable Ollama startup and the shared inference settings."""

import errno
import os
import socket
import subprocess
import threading
import time


DEFAULT_ENDPOINT = "http://127.0.0.1:11434"
MODEL_CONTEXT = 4096
MODEL_LEASE = 120
_PORTABLE_START_LOCK = threading.Lock()


class PortableOllama:
    def __init__(self, exe, data_dir):
        self.exe, self.data_dir = exe, data_dir
        self.process = None

    def start_if_missing(self, still_wanted=lambda: True):
        """Start the local executable only when its reserved port refuses TCP."""
        with _PORTABLE_START_LOCK:
            if self.process is not None and self.process.poll() is None:
                return False
            # A slow API or a different service on this port is still a listener.
            try:
                # Windows can take about two seconds to report a refused loopback port.
                with socket.create_connection(("127.0.0.1", 11434), timeout=2.5):
                    return False
            except OSError as error:
                if error.errno not in (errno.ECONNREFUSED, 10061):
                    return False
            if not self.exe.is_file():
                return False
            # Setup and Chuddy can run in separate processes before Ollama binds.
            with (self.data_dir / "ollama-start.lock").open("a+b") as reservation:
                reservation.seek(0)
                if os.name == "nt":
                    import msvcrt

                    try:
                        msvcrt.locking(reservation.fileno(), msvcrt.LK_NBLCK, 1)
                    except OSError:
                        return False
                last_start = reservation.read(32)
                now = time.time()
                if self.process is None and last_start.isdigit() and 0 <= now - int(last_start) < 30:
                    return False
                if not still_wanted():
                    return False
                self._spawn()
                reservation.seek(0)
                reservation.truncate()
                reservation.write(str(int(now)).encode("ascii"))
            return True

    def _spawn(self):
        env = dict(
            os.environ,
            OLLAMA_HOST="127.0.0.1:11434",
            OLLAMA_MODELS=str(self.data_dir / "models" / "ollama"),
            OLLAMA_NO_CLOUD="1",
            OLLAMA_NUM_PARALLEL="1",
            OLLAMA_CONTEXT_LENGTH=str(MODEL_CONTEXT),
            OLLAMA_MAX_LOADED_MODELS="1",
        )
        logs = self.data_dir / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        with (logs / "ollama.log").open("ab") as log:
            self.process = subprocess.Popen(
                [str(self.exe), "serve"], env=env, cwd=str(self.exe.parent),
                stdout=log, stderr=log, creationflags=0x08000000 if os.name == "nt" else 0,
            )
        (self.data_dir / "ollama.pid").write_text(str(self.process.pid))
