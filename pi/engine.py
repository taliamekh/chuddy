"""Shared, bounded yaw-only performance controller for simulator and Pi adapters."""
import math
import threading
import time


class SimulatedOutputs:
    physical = False
    def __init__(self):
        self.yaw = 0.0
        self.spray = False
    def yaw_to(self, angle):
        self.yaw = angle
    def spray_to(self, pressed):
        # No physical or simulated liquid output, ever.
        self.spray = False
    def close(self):
        pass


def validate_calibration(config, physical=False):
    """These values are examples for simulation; physical config requires user values."""
    if physical and not config.get('calibrated', False):
        raise ValueError('Measured calibration is required before enabling outputs.')
    required = ('yaw_min', 'yaw_max', 'home', 'away', 'speed_deg_s', 'timeout_s')
    if any(k not in config for k in required):
        raise ValueError('Missing yaw calibration fields.')
    for key in required:
        value = config[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError('Calibration values must be finite numbers.')
    low, high = config['yaw_min'], config['yaw_max']
    if not (0 <= low < high <= 180 and low <= config['home'] <= high and low <= config['away'] <= high):
        raise ValueError('Endpoints must remain inside calibrated 0–180 degree bounds.')
    if not (1 <= config['speed_deg_s'] <= 180 and 1 <= config['timeout_s'] <= 60):
        raise ValueError('Invalid motion speed/timeout.')
    if config['home'] == config['away']:
        raise ValueError('Home and away must differ.')
    travel = 2 * abs(config['away'] - config['home']) / config['speed_deg_s']
    if travel + 1.0 > config['timeout_s']:
        raise ValueError('Timeout is too short for a reversible sequence.')
    return dict(config)


SIM_CALIBRATION = {'yaw_min': 0, 'yaw_max': 180, 'home': 0, 'away': 180,
                   'speed_deg_s': 180, 'timeout_s': 8, 'calibrated': False}


class Sequence:
    def __init__(self, outputs=None, config=None, event=None, playback=None, allowed=None):
        self.outputs = outputs or SimulatedOutputs()
        self.config = validate_calibration(config or SIM_CALIBRATION, self.outputs.physical)
        self.event = event or (lambda kind, payload: None)
        self.playback = playback or (lambda kind: None)
        self.allowed = allowed or (lambda: True)
        self.lock = threading.RLock()
        self.cancel = threading.Event()
        self.thread = None
        self.phase = 'idle'
        self.yaw = self.config['home']
        self.sequence = 0
        self.error = ''
        self.spray_enabled = False

    def info(self):
        with self.lock:
            return {'mode': 'physical' if self.outputs.physical else 'simulator', 'phase': self.phase,
                    'yaw': round(self.yaw, 1), 'spray': 'enabled' if self.spray_enabled and self.outputs.physical else 'disabled',
                    'sequence': self.sequence, 'error': self.error,
                    'busy': bool(self.thread and self.thread.is_alive())}

    def start(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                raise ValueError('Chuddy is still turning. Give him a second.')
            if not self.allowed():
                raise ValueError('Physical outputs are disarmed.')
            self.cancel = threading.Event()
            self.sequence += 1
            self.phase, self.error = 'starting', ''
            self.thread = threading.Thread(target=self._run, args=(self.cancel,), daemon=True)
            self.thread.start()
            return {'accepted': True, 'sequence': self.sequence}

    def stop(self):
        with self.lock:
            self.cancel.set()
            thread = self.thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=2.5)

    def _set_phase(self, phase):
        with self.lock:
            self.phase = phase
        self.event('motion.phase', {'phase': phase, 'yaw': self.yaw, 'sequence': self.sequence})

    def _move(self, target, cancel, deadline, returning=False):
        while abs(self.yaw - target) > 0.01:
            if time.monotonic() > deadline:
                raise TimeoutError('Bounded motion timeout.')
            if not returning and (cancel.is_set() or not self.allowed()):
                return False
            delta = min(abs(target - self.yaw), self.config['speed_deg_s'] * 0.025)
            candidate = self.yaw + (delta if target > self.yaw else -delta)
            if not self.config['yaw_min'] <= candidate <= self.config['yaw_max']:
                raise ValueError('Motion exceeded calibrated bounds.')
            self.outputs.yaw_to(candidate)
            with self.lock:
                self.yaw = candidate
            time.sleep(0.025)
        return True

    def _run(self, cancel):
        deadline = time.monotonic() + self.config['timeout_s']
        cancelled = False
        try:
            self._set_phase('turning away')
            if not self._move(self.config['away'], cancel, deadline):
                cancelled = True
                return
            if cancel.is_set() or not self.allowed():
                cancelled = True
                return
            self._set_phase('fart audio')
            self.playback('fart')
            self.event('audio.requested', {'kind': 'fart', 'sequence': self.sequence})
            if self.spray_enabled and self.outputs.physical and not cancel.is_set() and self.allowed():
                self._set_phase('spray press')
                self.outputs.spray_to(True)
                cancel.wait(0.15)
            else:
                self.event('spray.disabled', {'sequence': self.sequence})
            cancel.wait(0.3)
        except Exception as error:
            self.error = type(error).__name__ + ': ' + str(error)
            self.event('hardware.error', {'error': self.error})
        finally:
            try:
                # Release/return is bounded and independent of cancellation. Power or
                # mechanical failure can still prevent a physical return; report it.
                self.outputs.spray_to(False)
                self._set_phase('returning')
                self._move(self.config['home'], cancel, time.monotonic() + min(self.config['timeout_s'], 10), returning=True)
            except Exception as error:
                self.error = 'Return failed: ' + str(error)
                self.event('hardware.error', {'error': self.error})
            cancelled = cancelled or cancel.is_set()
            self._set_phase('fault' if self.error else 'idle')
            self.event('sequence.complete', {'sequence': self.sequence, 'cancelled': cancelled, 'error': self.error})
