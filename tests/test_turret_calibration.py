from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from pi.turret_calibration import (
    Axis, Calibrator, PiSignals, SignalCleanupError, SimulatedSignals, load_profile,
)


class TraceSignals(SimulatedSignals):
    def __init__(self, clock):
        super().__init__(clock)
        self.frames = []
        self.releases = 0

    def send(self, positions, frames, prepared=None):
        self.frames.append((positions, frames))
        super().send(positions, frames, prepared)

    def release(self):
        self.releases += 1
        super().release()


class CalibratorTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.profile = Path(temp.name) / "calibration.json"
        self.time = 0
        self.driver = TraceSignals(lambda: self.time)
        self.control = Calibrator(self.driver, self.profile, lambda: self.time)
        self.client = "operator"

    def cmd(self, action, **extra):
        return self.control.command({
            "action": action, "client": self.client, "epoch": self.control.epoch, **extra,
        })

    def finish(self):
        for _ in range(1200):
            if not self.control.job:
                return
            self.time += 0.05
            self.cmd("present")
            self.control.watch()
        self.fail("Move never completed.")

    def teach(self, axis, first, second, step):
        self.cmd("start", axis=axis)
        self.finish()
        self.cmd("teach", axis=axis, mark=first)
        self.cmd("jog", axis=axis, step=step)
        self.finish()
        self.cmd("teach", axis=axis, mark=second)

    def test_startup_and_arm_are_signal_free(self):
        self.assertFalse(self.control.snapshot(self.client)["armed"])
        self.assertEqual(self.control.commanded, {})
        with self.assertRaises(ValueError):
            self.cmd("start", axis="yaw")
        with self.assertRaises(ValueError):
            self.cmd("arm", dry=False)
        self.cmd("arm", dry=True)
        self.control.watch()
        self.assertEqual(self.driver.frames, [])
        self.assertFalse(self.profile.exists())

    def test_limits_and_teaching_persist_with_reverse_lever(self):
        self.cmd("limits", axis="lever", low=1440, high=1560, start=1510)
        self.cmd("arm", dry=True)
        self.teach("lever", "up", "down", -50)
        profile = load_profile(self.profile)["lever"]
        self.assertEqual((profile.first, profile.second), (1510, 1460))
        with self.assertRaisesRegex(ValueError, "jog limit"):
            self.cmd("jog", axis="lever", step=-25)
        self.cmd("stop")
        self.assertEqual(load_profile(self.profile)["lever"], profile)
        self.assertEqual(self.control.commanded, {})
        with self.assertRaises(ValueError):
            self.cmd("limits", axis="lever", low=1490, high=1560, start=1510)
        self.assertEqual(load_profile(self.profile)["lever"], profile)

    def test_full_run_returns_front_and_up_and_releases(self):
        self.cmd("arm", dry=True)
        self.teach("yaw", "front", "back", 50)
        self.teach("lever", "up", "down", -50)
        self.driver.frames.clear()
        self.cmd("test", test="full")
        self.finish()
        self.assertEqual(
            [pos for pos, frames in self.driver.frames if frames == 30],
            [
                {"yaw": 1550, "lever": 1500}, {"yaw": 1500, "lever": 1500},
                {"yaw": 1550, "lever": 1500}, {"yaw": 1550, "lever": 1450},
                {"yaw": 1550, "lever": 1500}, {"yaw": 1500, "lever": 1500},
            ],
        )
        self.assertTrue(all(1 <= frames <= 30 for _, frames in self.driver.frames))
        self.assertTrue(self.driver.ready())
        self.assertEqual(self.control.commanded, {"yaw": 1500, "lever": 1500})
        self.assertFalse(self.control.snapshot(self.client)["busy"])

    def test_stop_or_timeout_cancels_pending_stages_and_rejects_late_requests(self):
        for condition in ("stop", "timeout"):
            with self.subTest(condition=condition):
                self.cmd("arm", dry=True)
                stale = self.control.epoch
                self.cmd("start", axis="yaw")
                self.control.watch()
                sent = len(self.driver.frames)
                if condition == "stop":
                    self.cmd("stop")
                else:
                    self.time += 1.6
                    self.control.watch()
                self.control.watch()
                self.assertEqual(len(self.driver.frames), sent)
                self.assertTrue(self.driver.ready())
                self.assertFalse(self.control.snapshot(self.client)["armed"])
                with self.assertRaises(ValueError):
                    self.cmd("start", axis="yaw", epoch=stale)

    def test_foreign_client_cannot_move_or_keep_lease_alive(self):
        self.cmd("arm", dry=True)
        for action in ("start", "present"):
            with self.assertRaises(ValueError):
                self.cmd(action, axis="yaw", client="some other client")
        self.time += 1.6
        self.control.watch()
        self.assertFalse(self.control.snapshot(self.client)["armed"])

    def test_stop_stays_responsive_during_preflight_and_cancels_first_write(self):
        entered = Event()
        unblock = Event()
        stopped = Event()

        def preflight(_positions):
            entered.set()
            if not unblock.wait(3):
                raise RuntimeError("Test preflight was not released.")

        def stop():
            self.cmd("stop")
            stopped.set()

        self.cmd("arm", dry=True)
        self.cmd("start", axis="yaw")
        with patch.object(self.driver, "prepare", side_effect=preflight):
            worker = Thread(target=self.control.watch)
            stop_worker = Thread(target=stop)
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                stop_worker.start()
                self.assertTrue(stopped.wait(1), "Stop waited for preflight.")
                self.cmd("arm", dry=True)
                self.cmd("start", axis="lever")
            finally:
                unblock.set()
                worker.join()
                if stop_worker.ident is not None:
                    stop_worker.join()
        self.assertEqual(self.driver.frames, [])
        self.control.watch()
        self.assertEqual(self.driver.frames, [({"lever": 1500}, 30)])

    def test_preflight_cannot_send_after_the_lease_or_test_deadline(self):
        for reason in ("lease", "test"):
            with self.subTest(reason=reason):
                self.cmd("arm", dry=True)
                self.cmd("start", axis="yaw")

                def preflight(_positions):
                    if reason == "lease":
                        self.time += 2
                    else:
                        self.time = self.control.deadline
                        self.control.lease_until = self.time + 1

                with patch.object(self.driver, "prepare", side_effect=preflight):
                    self.control.watch()
                self.assertEqual(self.driver.frames, [])
                info = self.control.snapshot(self.client)
                self.assertFalse(info["armed"])
                self.assertIn("disconnected" if reason == "lease" else "timed out", info["message"])

    def test_stop_cleanup_failure_stays_visible_and_blocks_rearm(self):
        self.cmd("arm", dry=True)
        with patch.object(self.driver, "release", side_effect=SignalCleanupError("line fault")):
            result = self.cmd("stop")
        self.assertTrue(result["fault"])
        self.assertIn("Switch off servo power", self.cmd("stop")["message"])
        with self.assertRaises(ValueError):
            self.cmd("arm", dry=True)


class PiSignalTests(unittest.TestCase):
    def test_raw_pwm_uses_bcm_lines_and_never_uses_infinite_cycles(self):
        driver = PiSignals()
        driver.handle = 7
        driver.api = SimpleNamespace(
            gpio_claim_output=Mock(return_value=0), tx_servo=Mock(return_value=0),
            gpiochip_close=Mock(return_value=0),
            gpio_claim_input=Mock(return_value=0), SET_PULL_DOWN=32,
        )
        with self.assertRaises(ValueError):
            driver.send({"yaw": 1500}, 0)
        with self.assertRaises(ValueError):
            driver.send({"lever": 2600}, 30)
        driver.api.tx_servo.assert_not_called()
        driver.send({"yaw": 1555, "lever": 1475}, 2)
        driver.api.tx_servo.assert_any_call(7, 12, 1555, 50, 0, 2)
        driver.api.tx_servo.assert_any_call(7, 13, 1475, 50, 0, 2)
        driver.release()
        driver.api.gpiochip_close.assert_called_once_with(7)
        self.assertEqual(driver.api.tx_servo.call_count, 2)
        driver.api.gpio_claim_input.assert_any_call(7, 12, 32)
        driver.api.gpio_claim_input.assert_any_call(7, 13, 32)
        self.assertIsNone(driver.handle)

    def test_release_cancels_active_pwm_and_handles_completed_pwm(self):
        for active in (True, False):
            with self.subTest(active=active):
                driver = PiSignals()
                driver.handle = 7
                driver.claimed = {12}
                transmitting = {12} if active else set()

                def claim_input(_handle, gpio, _flags):
                    transmitting.discard(gpio)
                    return 0

                driver.api = SimpleNamespace(
                    tx_servo=Mock(return_value=-103),  # A width-zero call can fail when PWM is inactive.
                    gpio_claim_input=Mock(side_effect=claim_input),
                    gpiochip_close=Mock(return_value=0), SET_PULL_DOWN=32,
                )
                driver.release()
                self.assertFalse(transmitting)
                driver.api.tx_servo.assert_not_called()
                driver.api.gpio_claim_input.assert_called_once_with(7, 12, 32)
                driver.api.gpiochip_close.assert_called_once_with(7)

    def test_release_closes_chip_even_if_restoring_input_fails(self):
        driver = PiSignals()
        driver.handle = 7
        driver.claimed = {12}
        driver.api = SimpleNamespace(
            gpio_claim_input=Mock(return_value=-1), gpiochip_close=Mock(return_value=0),
            SET_PULL_DOWN=32,
        )
        with self.assertRaises(SignalCleanupError):
            driver.release()
        driver.api.gpiochip_close.assert_called_once_with(7)


if __name__ == "__main__":
    unittest.main()
