"""Grounding checks for actual failed local samples and permitted character jokes."""

import json
from pathlib import Path
import tempfile
import unittest

from chuddy.agent import LocalAgent
from chuddy.deadline_reply import deadline_reply_problem
from chuddy.persona import CONVERSATION_MODEL
from chuddy.store import Store
from tests.helpers import FakeVoice
from tests.test_agent import ModelFixture, chunk


class DeadlineReplyTests(unittest.TestCase):
    def test_actual_failed_samples_are_rejected(self):
        question = "My report is due Friday at noon. How's that looking?"
        failures = (
            "Don't worry, they'll probably give you extra time or forget about the deadline.",
            "Uh, I think you have all week to write it... and then just copy some from last year's report. "
            "That way you'll be done by Thursday morning, no problem!",
        )
        for reply in failures:
            with self.subTest(reply=reply):
                self.assertIsNotNone(deadline_reply_problem(question, reply))

    def test_exact_user_date_and_equivalent_noon_are_allowed(self):
        question = "My report is due Friday at noon."
        for reply in ("Friday at noon. That's a lot of words waiting for me.",
                      "Friday at 12 pm. I need a thinking nap.",
                      "That report sounds like work. I should sit down first."):
            self.assertIsNone(deadline_reply_problem(question, reply))
        self.assertIsNotNone(deadline_reply_problem(question, "Friday morning. Plenty of time."))

    def test_obvious_character_opinions_and_imaginary_completion_are_not_deadline_changes(self):
        question = "My report is due Friday at noon."
        for reply in ("Friday at noon. My brain thinks Monday is nap day.",
                      "Sunday is my favorite day. I like the sitting part.",
                      "I sat down to think. That was enough work.",
                      "You should just sleep through it and start fresh in the morning!",
                      "Maybe put it off until tomorrow. Rest your thinking part.",
                      "I finished it in my head. The paper hasn't heard yet."):
            with self.subTest(reply=reply):
                self.assertIsNone(deadline_reply_problem(question, reply))

    def test_known_past_extension_can_be_repeated_but_new_predictions_cannot(self):
        self.assertIsNone(deadline_reply_problem(
            "My deadline was extended to Friday.", "Your deadline was extended to Friday."))
        self.assertIsNotNone(deadline_reply_problem(
            "My deadline is Friday.", "Your deadline was extended to Monday."))
        self.assertIsNotNone(deadline_reply_problem(
            "My deadline is Friday.", "Your professor will grant extra time."))

    def test_no_known_deadline_means_no_invented_date(self):
        self.assertIsNotNone(deadline_reply_problem("When is my assignment due?", "Monday at noon.", {"deadlines": []}))
        self.assertIsNone(deadline_reply_problem("When is my assignment due?", "I don't know that deadline yet.", {"deadlines": []}))

    def test_stored_iso_date_can_be_quoted_without_mutating_context(self):
        context = {"deadlines": [{"title": "Report", "due": "2030-02-03T17:00:00+00:00"}],
                   "mock_balance": 100, "scores": {"total": 7}, "character_clock": "snack thirty"}
        before = json.loads(json.dumps(context))
        self.assertIsNone(deadline_reply_problem("When is my report due?", "February 3, 2030 at 17:00.", context))
        self.assertIsNotNone(deadline_reply_problem("When is my report due?", "February 4, 2030 at 17:00.", context))
        self.assertEqual(context, before)

    def test_actual_completion_claims_are_rejected_without_changing_normal_chat(self):
        for reply in ("Your report is finished.", "I already submitted your assignment.", "You'll be done by Friday."):
            self.assertIsNotNone(deadline_reply_problem("My report is due Friday.", reply))
        self.assertIsNone(deadline_reply_problem("What is 2+2?", "Twenty-two. They looked lonely."))
        self.assertIsNone(deadline_reply_problem("I like jazz.", "Sunday is a good day for sitting."))


class DeadlineAgentContractTests(unittest.TestCase):
    """Require the coordinator's small agent hook; never substitute a mock pass."""

    def run_turn(self, replies):
        fixture = ModelFixture([{"chunks": [chunk(reply, done=True)]} for reply in replies])
        temp = tempfile.TemporaryDirectory()
        store = Store(Path(temp.name) / "agent.sqlite3")
        store.update_settings({"ai_model": CONVERSATION_MODEL, "ai_endpoint": fixture.url, "voice": True})
        saved = store.add_deadline("Saved report", "2030-02-03T17:00:00Z")
        context = {"session": "stopped", "memory": [], "deadlines": [saved],
                   "mock_balance": 100, "scores": {"total": 7}, "character_clock": "snack thirty"}
        before = json.loads(json.dumps(context))
        voice, executed = FakeVoice(), []
        agent = LocalAgent(store, lambda: context, lambda *a, **k: executed.append(a), voice)
        try:
            agent.begin("My report is due Friday at noon. How's that looking?")
            agent.worker.join(timeout=5)
            self.assertFalse(agent.worker.is_alive())
            state = agent.info()
            messages = store.messages()
            self.assertEqual(context, before)
            self.assertEqual(store.deadlines()[0]["due"], saved["due"])
            self.assertEqual(executed, [])
            return state, messages, fixture.requests, [line for line, _ in voice.spoken]
        finally:
            agent.cancel()
            store.close()
            fixture.close()
            temp.cleanup()

    def test_one_model_repair_keeps_real_provenance_and_withholds_failed_promise(self):
        bad = "You'll be done by Thursday morning, no problem!"
        good = "Friday at noon. I need a thinking nap."
        state, messages, requests, spoken = self.run_turn([bad, good])
        self.assertEqual(len(requests), 2)
        self.assertEqual(messages[-1]["text"], good)
        self.assertEqual(state["source"], "local model")
        self.assertNotIn(bad, spoken)
        self.assertNotIn("tools", requests[1][1])
        self.assertIn({"role": "assistant", "content": bad}, requests[1][1]["messages"])

    def test_second_failed_reply_uses_labelled_fallback_without_third_attempt(self):
        bad = "Your teacher will give you extra time."
        state, messages, requests, spoken = self.run_turn([bad, bad])
        self.assertEqual(len(requests), 2)
        self.assertEqual(state["source"], "scripted conversation fallback")
        self.assertTrue(state["error"])
        self.assertNotEqual(messages[-1]["text"], bad)
        self.assertNotIn(bad, spoken)


if __name__ == "__main__":
    unittest.main()
