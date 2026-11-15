"""Foreground work -> warning -> acknowledged close, without live desktop input."""
import time
from tests.helpers import ControllerCase


class WorkWarningFlowTests(ControllerCase):
    def setUp(self):
        super().setUp()
        # Warning machinery remains available for diagnostic adapters, although
        # production sessions now use warning_seconds=0. Browser-tab closure has
        # separate paired-extension coverage in test_browser_tab_commands.py.
        self.window = {"handle": 42, "pid": 123, "process": "WINWORD.EXE",
                       "title": "Assignment - Word"}
        self.patches[-1].target.foreground.return_value = self.window
        self.store.setting("interception", "minimize")
        self.controller.distractions.delay = 0
        self.controller.set_session("start")

    def test_stalled_dialogue_does_not_block_recognized_work(self):
        self.controller.distractions.warning_seconds = .03
        self.controller.poll_work()
        self.assertEqual(self.controller.agent.info()["status"], "thinking")
        self.now += 4.1
        self.controller.poll_work()
        self.assertTrue(self.controller.distractions.wait())
        self.assertEqual(self.controller.interruption["phase"], "opened")
        self.patches[-1].target.request_close.assert_called_once_with(self.window)
        self.assertEqual([m["source"] for m in self.store.messages() if m["source"] in (
            "offline work warning", "scripted work outcome")], ["scripted work outcome"])

    def test_busy_chat_does_not_prevent_work_observation_or_action(self):
        self.controller.agent.begin("unfinished unrelated conversation")
        previous = list(self.controller.agent.calls)
        self.controller.poll_work()
        self.assertIsNotNone(self.controller.pending_work)
        self.assertEqual(self.controller.agent.calls, previous)
        self.now += 4.1
        self.controller.poll_work()
        self.assertTrue(self.controller.distractions.wait())
        self.assertEqual(self.controller.interruption["phase"], "opened")

    def test_warning_holds_work_then_fresh_continuation_can_exceed_original_ttl(self):
        self.controller.distractions.warning_seconds = .1
        self.controller.poll_work()
        self.assertTrue(self.controller.interrupt("Google Docs", self.window))
        self.assertEqual(self.controller.interruption["phase"], "warning")
        self.patches[-1].target.request_close.assert_not_called()
        # Original request age exceeds8; current foreground still proves work.
        self.now += 12
        self.assertTrue(self.controller.distractions.wait())
        self.assertEqual(self.controller.interruption["phase"], "opened")

    def test_leaving_work_during_warning_cancels_without_minimize(self):
        self.controller.distractions.warning_seconds = .2
        self.controller.poll_work()
        self.assertTrue(self.controller.interrupt("Google Docs", self.window))
        self.patches[-1].target.foreground.return_value = {
            **self.window, "title": "Video - YouTube - Google Chrome"}
        self.assertTrue(self.controller.distractions.wait())
        self.assertIsNone(self.controller.interruption)
        self.patches[-1].target.request_close.assert_not_called()

    def test_stop_during_warning_invalidates_late_effects(self):
        self.controller.distractions.warning_seconds = .2
        self.controller.poll_work()
        self.assertTrue(self.controller.interrupt("Google Docs", self.window))
        self.controller.set_session("stop")
        self.assertTrue(self.controller.distractions.wait())
        self.assertIsNone(self.controller.interruption)
        self.patches[-1].target.request_close.assert_not_called()
        self.assertEqual(self.controller.work_detection["status"], "stopped")

    def test_warning_rejects_same_handle_with_different_document_title(self):
        self.controller.poll_work()
        work = dict(self.controller.pending_work)
        self.now += 30
        self.assertTrue(self.controller._continued_work_active(work))
        self.patches[-1].target.foreground.return_value = {**self.window, "title": "Another document"}
        self.assertFalse(self.controller._continued_work_active(work))

    def test_disabling_ai_recognition_cancels_warning_and_keeps_explicit_rules(self):
        self.controller.distractions.warning_seconds = .2
        self.controller.poll_work()
        self.assertTrue(self.controller.interrupt("Google Docs", self.window))
        self.controller.settings({"ai_work_detection": False})
        self.assertTrue(self.controller.distractions.wait())
        self.assertIsNone(self.controller.interruption)
        self.patches[-1].target.request_close.assert_not_called()
        self.now += 100
        self.controller.poll_work()
        self.assertEqual(self.controller.work_detection["source"], "rule")
        self.assertTrue(self.controller.work_detection["is_work"])

    def test_diagnostics_explain_matching_and_non_work_without_exposing_title(self):
        self.controller.poll_work()
        detected = self.controller.state()["work_detection"]
        self.assertTrue(detected["is_work"])
        self.assertNotIn("Assignment", str(detected))
        self.patches[-1].target.foreground.return_value = {**self.window, "process": "steam.exe", "title": "Steam"}
        self.controller.poll_work()
        self.assertFalse(self.controller.state()["work_detection"]["is_work"])

    def test_browser_event_after_session_expiry_cannot_begin_agent_or_ai_work(self):
        self.controller.expires = self.now + 1800  # Explicit diagnostic lease.
        self.now += 1801
        result = self.controller.browser_event({"url": "https://docs.google.com/document/fixture",
            "active": True, "focused": True, "sent_at": time.time()*1000})
        self.assertEqual(self.controller.session, "stopped")
        self.assertFalse(result["interrupted"])
        self.assertEqual(self.controller.agent.calls, [])
        self.assertIsNone(self.controller.pending_work)
        self.patches[-1].target.request_close.assert_not_called()
