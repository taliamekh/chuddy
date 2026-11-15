"""Manual, local-only bench control for the original Hack the Hill turret."""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
from tempfile import NamedTemporaryFile
from threading import Event, RLock, Thread
from time import monotonic


PINS = {"yaw": 12, "lever": 13}
ENDS = {"yaw": ("front", "back"), "lever": ("up", "down")}
MIN_US, MAX_US = 500, 2500
LEASE_S = 1.5
SETTLE_FRAMES = 30
JOG_STEPS = (5, 25, 50)
DEFAULT_PROFILE = Path.home() / ".chuddy" / "turret-calibration.json"


@dataclass(frozen=True)
class Axis:
    low: int = 1400
    high: int = 1600
    start: int = 1500
    first: int | None = None
    second: int | None = None

    def __post_init__(self):
        if (any(type(n) is not int for n in (self.low, self.start, self.high))
                or not MIN_US <= self.low <= self.start <= self.high <= MAX_US
                or self.low == self.high):
            raise ValueError("Set low < high within 500–2500 µs and put the start inside that interval.")
        for mark in (self.first, self.second):
            if mark is not None and (type(mark) is not int or not self.low <= mark <= self.high):
                raise ValueError("Each taught endpoint must be inside the jog limits. Clear endpoints to move a limit past one.")
        if self.first is not None and self.first == self.second:
            raise ValueError("The two endpoints must be different.")

    @property
    def taught(self):
        return self.first is not None and self.second is not None


def load_profile(path):
    if not path.exists():
        return {axis: Axis() for axis in PINS}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != 1 or set(data) != {"schema", *PINS}:
        raise ValueError("Unknown calibration profile format.")
    return {axis: Axis(**data[axis]) for axis in PINS}


def save_profile(path, axes):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as file:
            tmp = Path(file.name)
            json.dump({"schema": 1, **{axis: asdict(value) for axis, value in axes.items()}}, file, indent=2)
            file.write("\n")
        os.replace(tmp, path)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


class SimulatedSignals:
    name = "Simulation"

    def __init__(self, clock):
        self.clock = clock
        self.until = 0

    def ready(self):
        return self.clock() >= self.until

    def prepare(self, positions):
        return None

    def send(self, positions, frames, prepared=None):
        self.until = self.clock() + frames / 50

    def release(self):
        self.until = 0


class SignalCleanupError(RuntimeError):
    pass


class PiSignals:
    """First GPIO access occurs on an explicit movement. Every burst is finite."""
    name = "Pi GPIO"

    def __init__(self):
        self.api = None
        self.handle = None
        self.claimed = set()

    @staticmethod
    def check(result):
        if result < 0:
            raise RuntimeError(f"lgpio returned {result}.")
        return result

    def prepare(self, positions):
        if self.handle is not None:
            return None
        if not Path("/proc/device-tree/model").read_bytes().startswith(b"Raspberry Pi 5"):
            raise RuntimeError("This hardware transport expects a Raspberry Pi 5.")
        import lgpio

        for axis in positions:
            line = PINS[axis]
            output = subprocess.check_output(["pinctrl", "get", str(line)], text=True, timeout=1)
            if not re.search(rf"^\s*{line}:\s+ip\b", output, re.M):
                raise RuntimeError(f"GPIO{line} is not in input mode. Stop other processes using that GPIO line.")
        voltage = subprocess.check_output(["vcgencmd", "get_throttled"], text=True, timeout=1)
        if int(voltage.strip().split("=", 1)[-1], 16) & 1:
            raise RuntimeError("The Pi reports active undervoltage. Check USB-C power before moving.")

        for path in sorted(Path("/dev").glob("gpiochip*")):
            try:
                chip = int(path.name.removeprefix("gpiochip"))
                handle = self.check(lgpio.gpiochip_open(chip))
            except (OSError, ValueError, RuntimeError, lgpio.error):
                continue
            try:
                _, count, _, label = lgpio.gpio_get_chip_info(handle)
                if isinstance(label, bytes):
                    label = label.decode("ascii")
                if count > max(PINS.values()) and label == "pinctrl-rp1":
                    return lgpio, chip
            finally:
                self.check(lgpio.gpiochip_close(handle))
        raise RuntimeError("Could not open the RP1 GPIO header. Check the Pi OS GPIO permissions and python3-lgpio.")

    def ready(self):
        return not any(
            self.check(self.api.tx_busy(self.handle, line, self.api.TX_PWM))
            for line in self.claimed
        )

    def send(self, positions, frames, prepared=None):
        if (type(frames) is not int or not 1 <= frames <= SETTLE_FRAMES
                or not positions or any(
                    axis not in PINS or type(pulse) is not int or not MIN_US <= pulse <= MAX_US
                    for axis, pulse in positions.items()
                )):
            raise ValueError("Servo pulses must be within range and use a finite burst.")
        if self.handle is None:
            if prepared is None:
                raise RuntimeError("GPIO preflight is required before sending servo pulses.")
            self.api, chip = prepared
            self.handle = self.check(self.api.gpiochip_open(chip))
        for axis, pulse in positions.items():
            line = PINS[axis]
            if line not in self.claimed:
                self.check(self.api.gpio_claim_output(self.handle, line, 0))
                self.claimed.add(line)
            self.check(self.api.tx_servo(self.handle, line, pulse, 50, 0, frames))

    def release(self):
        if self.handle is None:
            return
        errors = []
        for line in self.claimed:
            try:
                # lgpio frees the claimed output, cancelling active pulses, before
                # reclaiming it as an input. This also works after a finite burst.
                self.check(self.api.gpio_claim_input(self.handle, line, self.api.SET_PULL_DOWN))
            except Exception as exc:
                errors.append(str(exc))
        try:
            self.check(self.api.gpiochip_close(self.handle))
        except Exception as exc:
            errors.append(str(exc))
        self.handle = None
        self.claimed.clear()
        if errors:
            raise SignalCleanupError(", ".join(errors))


def sweep(current, stages):
    """Only one axis changes per stage. Additional axes can hold their last target."""
    positions = dict(current)
    for label, axis, target in stages:
        value = positions.get(axis, target)
        while value != target:
            value += max(-12, min(12, target - value))
            positions[axis] = value
            yield label, dict(positions), 2
        positions[axis] = target
        yield label, dict(positions), SETTLE_FRAMES


class Calibrator:
    def __init__(self, driver, profile, clock=monotonic):
        self.driver = driver
        self.profile = profile
        self.axes = load_profile(profile)
        self.clock = clock
        self.lock = RLock()
        self.owner = None
        self.lease_until = 0
        self.epoch = 0
        self.commanded = {}
        self.job = None
        self.deadline = 0
        self.stage = None
        self.fault = False
        self.message = "Outputs off. Arm to start a dry bench test."

    def snapshot(self, client):
        with self.lock:
            return {
                "mode": self.driver.name, "epoch": self.epoch, "armed": self.owner is not None,
                "mine": client == self.owner and self.owner is not None,
                "busy": self.job is not None, "fault": self.fault,
                "stage": self.stage, "message": self.message, "commanded": dict(self.commanded),
                "axes": {axis: asdict(value) for axis, value in self.axes.items()},
            }

    def stop(self, message="Stopped. Servo signals released."):
        with self.lock:
            self.epoch += 1
            self.owner = None
            self.job = None
            self.stage = None
            self.commanded = {}
            try:
                self.driver.release()
                if not self.fault:
                    self.message = message
            except SignalCleanupError as exc:
                self.fault = True
                self.message = f"Signal cleanup error. Switch off servo power. {exc}"

    def expire(self):
        if self.owner is not None and self.clock() >= self.lease_until:
            self.stop("Control page disconnected. Servo signals released.")
            return True
        if self.job is not None and self.clock() >= self.deadline:
            self.stop("Bench test timed out. Servo signals released.")
            return True
        return False

    def watch(self):
        with self.lock:
            if self.expire() or self.job is None:
                return
            job, epoch = self.job, self.epoch
            try:
                if not self.driver.ready():
                    return
                step = next(self.job, None)
                if step is None:
                    self.driver.release()
                    self.job = None
                    self.stage = None
                    self.message = "Test finished. PWM off. Readouts are last commands."
                    return
            except SignalCleanupError as exc:
                self.fault = True
                self.message = f"Signal cleanup error. Switch off servo power. {exc}"
                self.stop()
                return
            except Exception as exc:
                self.stop(f"Motion error. Stopped. {exc}")
                return

        # Status and Stop stay responsive during read-only GPIO and voltage checks.
        label, positions, frames = step
        try:
            prepared = self.driver.prepare(positions)
        except Exception as exc:
            with self.lock:
                if self.job is job and self.epoch == epoch and not self.expire():
                    self.stop(f"Motion error. Stopped. {exc}")
            return

        with self.lock:
            if self.job is not job or self.epoch != epoch or self.expire():
                return
            try:
                self.driver.send(positions, frames, prepared)
                self.commanded.update(positions)
                self.stage = label
            except Exception as exc:
                self.stop(f"Motion error. Stopped. {exc}")

    def change_axis(self, axis, updated):
        axes = {**self.axes, axis: updated}
        save_profile(self.profile, axes)
        self.axes = axes

    def begin(self, current, stages):
        self.job = sweep(current, stages)
        self.stage = "Starting test"
        self.deadline = self.clock() + 45

    def command(self, data):
        client = data["client"]
        action = data.get("action")
        with self.lock:
            if action == "stop":
                self.stop()
                return self.snapshot(client)
            if (type(data.get("epoch")) is not int or data["epoch"] != self.epoch):
                raise ValueError("Controls changed. Refresh the status before trying again.")
            if self.owner is not None and self.clock() >= self.lease_until:
                self.stop("Control page disconnected. Servo signals released.")
                raise ValueError("Session expired. Arm again to continue.")
            if action == "arm":
                if self.owner is not None or self.fault or data.get("dry") is not True:
                    raise ValueError("Release the previous session and confirm the dry bench setup.")
                self.epoch += 1
                self.owner = client
                self.lease_until = self.clock() + LEASE_S
                self.message = "Armed. No PWM until you choose a movement."
                return self.snapshot(client)
            if action in ("limits", "clear"):
                if self.owner is not None:
                    raise ValueError("Stop before editing limits.")
                axis = data.get("axis")
                if axis not in self.axes:
                    raise ValueError("Choose a motor.")
                value = self.axes[axis]
                if action == "limits":
                    value = replace(value, low=data.get("low"), high=data.get("high"), start=data.get("start"))
                else:
                    value = replace(value, first=None, second=None)
                self.change_axis(axis, value)
                self.message = "Calibration profile saved."
                return self.snapshot(client)
            if self.owner != client:
                raise ValueError("Arm this page before operating the servos.")
            if action == "present":
                self.lease_until = self.clock() + LEASE_S
                return self.snapshot(client)
            if self.job is not None:
                raise ValueError("A movement is in progress. Wait or select Stop.")
            if action == "test":
                self.test(data.get("test"))
                return self.snapshot(client)
            axis = data.get("axis")
            if axis not in self.axes:
                raise ValueError("Choose a motor.")
            value = self.axes[axis]
            current = self.commanded.get(axis)
            if action == "start":
                self.begin({} if current is None else {axis: current}, [(f"{axis.title()} start", axis, value.start)])
            elif action == "jog":
                step = data.get("step")
                if current is None:
                    raise ValueError("Send the start pulse before jogging this motor.")
                if type(step) is not int or abs(step) not in JOG_STEPS:
                    raise ValueError("Choose a jog step of 5, 25 or 50 µs.")
                if not value.low <= current + step <= value.high:
                    raise ValueError("That step crosses the jog limit. Try a smaller step.")
                self.begin({axis: current}, [(f"{axis.title()} jog", axis, current + step)])
            elif action == "teach":
                mark = data.get("mark")
                if current is None or mark not in ENDS[axis]:
                    raise ValueError("Jog to the measured endpoint before teaching it.")
                field = "first" if mark == ENDS[axis][0] else "second"
                self.change_axis(axis, replace(value, **{field: current}))
                self.message = f"Saved {mark} at {current} µs."
            else:
                raise ValueError("Unknown command.")
            return self.snapshot(client)

    def test(self, action):
        yaw, lever = self.axes["yaw"], self.axes["lever"]
        if action == "turn" and yaw.taught:
            stages = [("Front", "yaw", yaw.first), ("Turn to back", "yaw", yaw.second), ("Return to front", "yaw", yaw.first)]
        elif action == "lever" and lever.taught:
            stages = [("Lever up", "lever", lever.first), ("Lever down", "lever", lever.second), ("Lever up", "lever", lever.first)]
        elif action == "full" and yaw.taught and lever.taught:
            stages = [
                ("Lever up", "lever", lever.first), ("Front", "yaw", yaw.first),
                ("Turn to back", "yaw", yaw.second), ("Lever down", "lever", lever.second),
                ("Lever up", "lever", lever.first), ("Return to front", "yaw", yaw.first),
            ]
        else:
            raise ValueError("Teach both endpoints for each motor in this test.")
        # Require a known command inside the taught span before starting a run.
        # The operator still verifies the physical starting pose and clearance.
        initial = {}
        for _, axis, _ in stages:
            value = self.axes[axis]
            current = self.commanded.get(axis)
            if current is None or not min(value.first, value.second) <= current <= max(value.first, value.second):
                raise ValueError(f"Jog {axis} to a position within its taught endpoints first.")
            initial[axis] = current
        # Prime only the axes that have been deliberately driven in this session.
        self.begin(initial, stages)


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(2)
        return connection, address


def handler_for(controller, port, page):
    origin = f"http://127.0.0.1:{port}"
    token = secrets.token_hex(24)
    html = page.replace("@@TOKEN@@", token).encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, code, payload, html_response=False):
            body = payload if html_response else json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=UTF-8" if html_response else "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", f"default-src 'none'; script-src 'nonce-{token}'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def valid_host(self):
            return self.headers.get("Host") == f"127.0.0.1:{port}"

        def do_GET(self):
            if not self.valid_host() or self.path != "/":
                self.reply(404, {"error": "Open the exact 127.0.0.1 URL shown in the terminal."})
                return
            self.reply(200, html, html_response=True)

        def do_POST(self):
            if (not self.valid_host() or self.path != "/api"
                    or self.headers.get("Origin") != origin
                    or not secrets.compare_digest(self.headers.get("X-Calibrator-Token", ""), token)):
                self.reply(403, {"error": "This command requires the local control page."})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if self.headers.get_content_type() != "application/json" or not 1 <= size <= 2048:
                    raise ValueError("Invalid request size or type.")
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict) or not isinstance(data.get("client"), str) or not re.fullmatch(r"[a-f0-9-]{36}", data["client"]):
                    raise ValueError("Invalid control page ID.")
                result = (
                    controller.snapshot(data["client"]) if data.get("action") == "status"
                    else controller.command(data)
                )
            except (ValueError, KeyError, TypeError, OSError) as exc:
                self.reply(400, {"error": str(exc)})
                return
            self.reply(200, result)

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hardware", action="store_true", help="Allow this page to issue manual GPIO tests on a Pi 5.")
    parser.add_argument("--port", type=int, default=8777)
    parser.add_argument("--profile", type=Path, help="Optional override for the local calibration file.")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Choose a port between 1024 and 65535.")
    if args.hardware:
        model = Path("/proc/device-tree/model")
        if not model.is_file() or not model.read_bytes().startswith(b"Raspberry Pi 5"):
            parser.error("--hardware requires Raspberry Pi OS on the Pi 5.")
    profile = args.profile or (
        DEFAULT_PROFILE if args.hardware else DEFAULT_PROFILE.with_name("turret-calibration-sim.json")
    )
    try:
        page = Path(__file__).with_suffix(".html").read_text(encoding="utf-8")
        controller = Calibrator(PiSignals() if args.hardware else SimulatedSignals(monotonic), profile)
        server = LocalServer(("127.0.0.1", args.port), handler_for(controller, args.port, page))
    except (OSError, ValueError, TypeError) as exc:
        parser.error(str(exc))

    done = Event()

    def monitor():
        while not done.wait(0.01):
            controller.watch()

    def shutdown(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, shutdown)
    worker = Thread(target=monitor, daemon=True)
    worker.start()
    print(f"{controller.driver.name} · open http://127.0.0.1:{args.port} · Ctrl+C to exit", flush=True)
    try:
        server.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        done.set()
        controller.stop()
        worker.join()
        server.server_close()


if __name__ == "__main__":
    main()
