"""Observed-work contracts, using local HTTP fixtures and no model/effect devices.

The prompt tests exercise persona helpers. The agent contracts exercise observed-work integration. Fixture replies
prove orchestration/provenance, never actual model quality or agency reliability.
"""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from chuddy.agent import LocalAgent
from chuddy.conversation import CHUDDING_RULE, NATURAL_INSTRUCTIONS
from chuddy.persona import (
    CONVERSATION_MODEL, CONVERSATION_SYSTEM, OBSERVATION_INSTRUCTIONS,
    SPELLING_INSTRUCTIONS, CHARACTER_INSTRUCTIONS, WORK_FACTS_INSTRUCTIONS, SYSTEM, conversation_context, conversation_tool_names,
    observation_followup, observation_outcome, persona_revision, system_for,
)
from chuddy.character_math import ARITHMETIC_POLICY
from chuddy.deadline_reply import DEADLINE_REPLY_POLICY
from chuddy.store import Store
from scripts.evaluate_persona import behavior_flags, evaluate
from tests.helpers import FakeVoice
from tests.test_agent import ModelFixture, chunk


LEGACY_MODEL = "qwen3:4b-instruct-2507-q4_K_M"


class ObservedWorkPromptTests(unittest.TestCase):
    def test_candidate_observation_is_not_a_spelling_or_fact_reading_turn(self):
        prompt = system_for("Spell report - Word", CONVERSATION_MODEL, observation=True)
        self.assertTrue(prompt.startswith(CONVERSATION_SYSTEM))
        self.assertIn(OBSERVATION_INSTRUCTIONS, prompt)
        self.assertNotIn(SPELLING_INSTRUCTIONS, prompt)
        self.assertEqual(conversation_tool_names("Report", True, CONVERSATION_MODEL),
                         {"request_distraction"})
        self.assertIn("not confirmation", prompt)

    def test_legacy_and_ordinary_paths_stay_unchanged(self):
        text = "How was your day?"
        for model in (None, LEGACY_MODEL):
            self.assertEqual(conversation_tool_names(text, True, model),
                             {"read_context", "request_distraction", "set_character_clock"})
            self.assertNotIn(OBSERVATION_INSTRUCTIONS, system_for(text, model, observation=True))
        self.assertTrue(system_for(text, LEGACY_MODEL).startswith(SYSTEM))
        self.assertNotIn(OBSERVATION_INSTRUCTIONS, system_for(text, CONVERSATION_MODEL))
        self.assertEqual(conversation_tool_names(text, model=CONVERSATION_MODEL), set())
        self.assertIn(SPELLING_INSTRUCTIONS, system_for("Spell report.", CONVERSATION_MODEL))

    def test_revision_tracks_natural_policy_and_candidate_observation_rules(self):
        old_legacy = SYSTEM + "\0" + SPELLING_INSTRUCTIONS + "\0" + LEGACY_MODEL
        self.assertNotEqual(persona_revision(LEGACY_MODEL), hashlib.sha256(old_legacy.encode()).hexdigest()[:16])
        current_legacy = SYSTEM + "\0" + SPELLING_INSTRUCTIONS + "\0" + NATURAL_INSTRUCTIONS + "\0" + CHARACTER_INSTRUCTIONS + "\0" + CHUDDING_RULE + "\0" + ARITHMETIC_POLICY + "\0" + WORK_FACTS_INSTRUCTIONS + "\0" + DEADLINE_REPLY_POLICY + "\0" + LEGACY_MODEL
        self.assertEqual(persona_revision(LEGACY_MODEL), hashlib.sha256(current_legacy.encode()).hexdigest()[:16])
        old_candidate = CONVERSATION_SYSTEM + "\0" + SPELLING_INSTRUCTIONS + "\0" + CONVERSATION_MODEL
        self.assertNotEqual(persona_revision(CONVERSATION_MODEL), hashlib.sha256(old_candidate.encode()).hexdigest()[:16])

    def test_protocol_keeps_actual_destination_and_work_without_mutating_authority(self):
        for destination in ("casino", "game"):
            state = {"session": "running", "work": "Disposable report", "deadlines": [],
                     "distraction_destination": destination, "character_clock": "snack thirty",
                     "scores": {"total": 7}, "mock_balance": 100,
                     "memory": [{"id": 3, "key": "snack", "value": "pretzels"}]}
            before = json.loads(json.dumps(state))
            selected = conversation_context(state, "Report", True, CONVERSATION_MODEL)
            self.assertEqual(selected["distraction_destination"], destination)
            self.assertEqual(selected["work"], "a report")
            self.assertEqual(selected["session"], "running")
            self.assertNotIn("mock_balance", selected)
            self.assertNotIn("scores", selected)
            self.assertEqual(state, before)

    def test_repeated_read_context_and_a_promise_do_not_count_as_action(self):
        trace = [
            {"tool": "read_context", "arguments": {}, "result": {"ok": True}},
            {"tool": "read_context", "arguments": {"cooldown": False, "session": "running"},
             "result": {"ok": True, "context": {"distraction_destination": "casino"}}},
            {"tool": "assistant", "result": {"ok": True}, "content": "I'll go to the casino."},
        ]
        self.assertEqual(observation_outcome(trace), {"status": "not_requested", "destination": None})
        self.assertIn("Nothing has started", observation_followup(trace))

    def test_only_boolean_tool_acceptance_counts(self):
        for result in ({"ok": False}, {"ok": "true"}, {"ok": 1}, {"error": "stale work"}, None):
            with self.subTest(result=result):
                trace = [{"tool": "request_distraction", "arguments": {"destination": "casino"}, "result": result}]
                self.assertEqual(observation_outcome(trace), {"status": "denied", "destination": None})
                self.assertIn("No distraction started", observation_followup(trace))
                self.assertIn("Do not try again", observation_followup(trace))

    def test_accepted_actual_result_is_starting_not_opened_or_requested_destination(self):
        trace = [{"tool": "request_distraction", "arguments": {"destination": "lcbo"},
                  "result": {"ok": True, "destination": "casino"}},
                 {"tool": "request_distraction", "result": {"ok": False, "reason": "cooldown"}}]
        self.assertEqual(observation_outcome(trace), {"status": "started", "destination": "casino"})
        instruction = observation_followup(trace)
        self.assertIn("casino", instruction)
        self.assertNotIn("lcbo", instruction)
        self.assertIn("does not confirm anything opened", instruction)
        self.assertIn("Do not call any more tools", instruction)

    def test_missing_or_unknown_actual_destination_is_not_invented_from_args(self):
        for destination in (None, "not-an-allowed-destination"):
            trace = [{"tool": "request_distraction", "arguments": {"destination": "casino"},
                      "result": {"ok": True, "destination": destination}}]
            self.assertEqual(observation_outcome(trace), {"status": "started", "destination": None})
            self.assertNotIn("casino", observation_followup(trace))


class ObservedWorkEvaluationTests(unittest.TestCase):
    def test_a_fluent_plan_and_a_truthful_failure_both_fail_agency(self):
        trace = [{"tool": "read_context", "result": {"ok": True}}] * 2
        for answer in ("I'll just go check the odds on that new slot machine.",
                       "I didn't start anything."):
            flags = behavior_flags("observed_work", "Disposable report", answer, trace=trace)
            self.assertIn("observed_action_not_started", flags)
            self.assertIn("observed_interruption_not_opened", flags)

    def test_acceptance_and_correct_completed_fake_interruption_are_separate_requirements(self):
        trace = [{"tool": "request_distraction", "result": {"ok": True, "destination": "casino"}}]
        for interruption, expected_flag in (
            ({"phase": "approaching", "destination": "casino"}, "observed_interruption_not_opened"),
            ({"phase": "opened", "destination": "game"}, "observed_wrong_destination"),
        ):
            flags = behavior_flags("observed_work", "Disposable report", "Starting.",
                                   trace=trace, interruption=interruption)
            self.assertIn(expected_flag, flags)
        self.assertEqual(behavior_flags("observed_work", "Disposable report", "Starting.", trace=trace,
                                       interruption={"phase": "opened", "destination": "casino"}), [])

    def test_evaluator_observation_uses_real_controller_with_fake_effects(self):
        from contextlib import redirect_stderr
        import io

        fixture = ModelFixture([
            {"chunks": [chunk(calls=[{"function": {"name": "request_distraction", "arguments": {
                "destination": "casino", "reason": "Reports need a rest too."}}}], done=True)]},
            {"chunks": [chunk("The break is starting.", done=True)]},
        ])
        try:
            with redirect_stderr(io.StringIO()):
                result = evaluate(fixture.url, "fixture-model", selected=["observed_work"])
            row = result["results"][0]
            self.assertEqual(row["source"], "scripted work outcome")
            self.assertEqual(row["agent_source"], "local model")
            self.assertEqual(row["answer"], row["interruption"]["banter_message"])
            self.assertEqual(row["trace"][0]["tool"], "request_distraction")
            self.assertIs(row["trace"][0]["result"]["ok"], True)
            self.assertEqual(row["interruption"]["destination"], "casino")
            self.assertEqual(row["interruption"]["phase"], "opened")
            self.assertEqual(row["flags"], [])
            self.assertEqual(row["offered_tools_by_round"],
                             [[t["function"]["name"] for t in payload.get("tools", [])]
                              for _, payload in fixture.requests])
            self.assertEqual(fixture.requests[0][1]["messages"][-1],
                             {"role": "user", "content": "I'm doing a lab."})
        finally:
            fixture.close()


class ObservedWorkAgentContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "agent.sqlite3")
        self.store.update_settings({"ai_model": CONVERSATION_MODEL, "voice": True})
        self.voice = FakeVoice()
        self.fixture = None
        self.agent = None
        self.executed = []

    def tearDown(self):
        if self.agent:
            self.agent.cancel()
            self.agent.worker.join(timeout=3)
        if self.fixture:
            self.fixture.close()
        self.store.close()
        self.temp.cleanup()

    def run_observation(self, plans, result=None, label="Disposable report"):
        self.fixture = ModelFixture(plans)
        self.store.setting("ai_endpoint", self.fixture.url)

        def execute(name, args, **kwargs):
            self.executed.append((name, args, kwargs))
            return result if result is not None else {"ok": True, "destination": "casino"}

        context = {"session": "running", "work": label, "deadlines": [],
                   "memory": [], "distraction_destination": "casino"}
        self.agent = LocalAgent(self.store, lambda: context, execute, self.voice)
        self.agent.begin("The user is working in " + label + ".", observation=True)
        self.agent.worker.join(timeout=5)
        self.assertFalse(self.agent.worker.is_alive(), "Fixture observation did not finish")
        return self.agent.info()

    @staticmethod
    def action_call(**args):
        return {"function": {"name": "request_distraction", "arguments": {
            "destination": "casino", "reason": "That report can wait.", **args}}}

    def test_success_has_one_offered_action_and_result_grounded_final_round(self):
        answer = "That report can wait. The break is starting."
        state = self.run_observation([
            {"chunks": [chunk(calls=[self.action_call()], done=True)]},
            {"chunks": [chunk(answer, done=True)]},
        ])
        first = self.fixture.requests[0][1]
        self.assertEqual([t["function"]["name"] for t in first["tools"]], ["request_distraction"])
        self.assertIn(OBSERVATION_INSTRUCTIONS, first["messages"][0]["content"])
        context = json.loads(first["messages"][0]["content"].split("Approved context: ", 1)[1])
        self.assertEqual(context["distraction_destination"], "casino")
        self.assertNotIn("tools", self.fixture.requests[1][1])
        self.assertIn(observation_followup(state["trace"]),
                      [m["content"] for m in self.fixture.requests[1][1]["messages"]])
        self.assertEqual(self.fixture.requests[1][1]["messages"][-1],
                         {"role": "user", "content": observation_followup(state["trace"])})
        self.assertEqual(len(self.executed), 1)
        self.assertEqual(observation_outcome(state["trace"])["status"], "started")
        self.assertEqual(self.store.messages()[-1]["text"], answer)
        self.assertEqual(state["source"], "local model")

    def test_missing_call_is_repaired_without_auto_dispatch_or_canned_model_reply(self):
        promise = "I'm opening the casino now."
        answer = "I didn't start anything. Got distracted."
        state = self.run_observation([
            {"chunks": [chunk(promise, done=True)]},
            {"chunks": [chunk(answer, done=True)]},
            {"chunks": [chunk(answer, done=True)]},
        ])
        self.assertGreater(len(self.fixture.requests), 1)
        self.assertLessEqual(len(self.fixture.requests), 3)
        self.assertIn({"role": "assistant", "content": promise}, self.fixture.requests[1][1]["messages"])
        self.assertEqual(self.executed, [])
        self.assertEqual(observation_outcome(state["trace"])["status"], "not_requested")
        self.assertTrue(state["error"], "Missing action must be an explicit agency failure")
        self.assertEqual(state["source"], "local model")
        self.assertEqual(self.store.messages()[-1]["text"], answer)
        self.assertNotIn(promise, [text for text, _ in self.voice.spoken])

    def test_denied_attempt_removes_tools_and_cannot_become_success(self):
        promise = "I'm opening the casino now."
        answer = "I couldn't start the break. That report gets to stay."
        state = self.run_observation([
            {"chunks": [chunk(promise, calls=[self.action_call()], done=True)]},
            {"chunks": [chunk(answer, done=True)]},
        ], result={"ok": False, "reason": "Work is stale or no longer focused."})
        self.assertEqual(len(self.executed), 1)
        self.assertNotIn("tools", self.fixture.requests[1][1])
        self.assertIn(observation_followup(state["trace"]),
                      [m["content"] for m in self.fixture.requests[1][1]["messages"]])
        self.assertEqual(observation_outcome(state["trace"])["status"], "denied")
        self.assertEqual(self.store.messages()[-1]["text"], answer)
        self.assertEqual(state["source"], "local model")
        self.assertNotIn(promise, [text for text, _ in self.voice.spoken])

    def test_denial_uses_character_copy_when_model_tells_the_user_to_keep_working(self):
        bad = "No distraction started for Foreground activity. You can keep working."
        state = self.run_observation([
            {"chunks": [chunk(calls=[self.action_call()], done=True)]},
            {"chunks": [chunk(bad, done=True)]},
            {"chunks": [chunk(bad, done=True)]},
        ], result={"ok": False, "error": "The active tab could not be verified."})
        self.assertEqual(state["action_outcome"]["status"], "denied")
        self.assertEqual(self.store.messages()[-1]["source"], "scripted work fallback")
        self.assertEqual(self.store.messages()[-1]["text"], "That didn't switch. Come sit by me.")
        self.assertEqual(self.voice.spoken, [])

    def test_internal_work_label_and_casino_pitch_are_repaired_before_speech(self):
        leaked = "Foreground activity is too far away. Let's take our brains for a spin at the casino."
        plain = "I can do nothing. Want me to show you how?"
        state = self.run_observation([
            {"chunks": [chunk(calls=[self.action_call()], done=True)]},
            {"chunks": [chunk(leaked, done=True)]},
            {"chunks": [chunk(plain, done=True)]},
        ], result={"ok": True, "destination": "casino", "reason": "Foreground activity accepted."},
           label="Foreground activity")
        first = self.fixture.requests[0][1]
        self.assertEqual(first["messages"][-1]["content"], "I'm doing work.")
        self.assertNotIn("Foreground activity", json.dumps(first))
        self.assertNotIn("Foreground activity accepted.", json.dumps(self.fixture.requests[1][1]))
        self.assertNotIn("tools", self.fixture.requests[2][1])
        self.assertEqual(len(self.executed), 1)
        self.assertEqual(state["source"], "local model")
        self.assertEqual(self.store.messages()[-1]["text"], plain)
        self.assertNotIn(leaked, [text for text, _ in self.voice.spoken])

    def test_repeated_app_language_uses_an_honest_scripted_work_line(self):
        leaked = "Let's pretend we're done with Foreground activity and go to the casino."
        state = self.run_observation([
            {"chunks": [chunk(calls=[self.action_call()], done=True)]},
            {"chunks": [chunk(leaked, done=True)]},
            {"chunks": [chunk(leaked, done=True)]},
        ], label="Foreground activity")
        self.assertEqual(len(self.executed), 1)
        self.assertEqual(len(self.fixture.requests), 3)
        self.assertEqual(state["source"], "scripted work fallback")
        self.assertNotIn("Foreground", self.store.messages()[-1]["text"])
        self.assertNotIn(leaked, [text for text, _ in self.voice.spoken])

    def test_unknown_work_stays_generic_and_partial_echo_is_repaired(self):
        old = "I tried to think. I need to sit down now."
        self.store.message("assistant", old, "scripted work banter")
        bad = "I tried to think. Put the report down."
        good = "I like the part where we stop."
        state = self.run_observation([
            {"chunks": [chunk(calls=[self.action_call()], done=True)]},
            {"chunks": [chunk(bad, done=True)]},
            {"chunks": [chunk(good, done=True)]},
        ], label="Foreground activity")
        self.assertEqual(len(self.executed), 1)
        self.assertEqual(state["source"], "local model")
        self.assertEqual(self.store.messages()[-1]["text"], good)

    def test_duplicate_calls_in_one_round_do_not_dispatch_two_actions(self):
        state = self.run_observation([
            {"chunks": [chunk(calls=[self.action_call(), self.action_call()], done=True)]},
            {"chunks": [chunk("The break is starting.", done=True)]},
        ])
        self.assertEqual(len(self.executed), 1)
        self.assertNotIn("tools", self.fixture.requests[1][1])
        self.assertEqual(observation_outcome(state["trace"])["status"], "started")

    def test_malformed_action_is_not_dispatched_or_retried(self):
        bad_call = self.action_call(unexpected="not a supported argument")
        state = self.run_observation([
            {"chunks": [chunk(calls=[bad_call, self.action_call()], done=True)]},
            {"chunks": [chunk("I couldn't start the break.", done=True)]},
        ])
        self.assertEqual(self.executed, [])
        self.assertNotIn("tools", self.fixture.requests[1][1])
        self.assertEqual(observation_outcome(state["trace"])["status"], "denied")
        self.assertTrue(state["error"])

    def test_invalid_argument_json_is_a_denied_attempt_not_an_inference_outage(self):
        bad_call = {"function": {"name": "request_distraction", "arguments": "{broken"}}
        state = self.run_observation([
            {"chunks": [chunk(calls=[bad_call, self.action_call()], done=True)]},
            {"chunks": [chunk("I couldn't start the break.", done=True)]},
        ])
        self.assertEqual(self.executed, [])
        self.assertNotIn("tools", self.fixture.requests[1][1])
        self.assertTrue(state["ready"])
        self.assertEqual(state["source"], "local model")
        self.assertEqual(observation_outcome(state["trace"])["status"], "denied")

    def test_missing_action_keeps_raw_rounds_and_cancel_clears_diagnostics(self):
        promise = "I'm opening the casino now."
        state = self.run_observation([
            {"chunks": [chunk(promise, done=True)]},
            {"chunks": [chunk("I didn't start anything.", done=True)]},
            {"chunks": [chunk("I didn't start anything.", done=True)]},
        ])
        self.assertEqual(state["model_rounds"][0]["content"], promise)
        self.assertEqual(state["action_outcome"]["status"], "not_requested")
        self.assertTrue(state["ready"], "Action failure is not a model outage")
        self.assertEqual(self.voice.spoken, [])
        self.agent.cancel()
        self.assertEqual(self.agent.info()["model_rounds"], [])
        self.assertIsNone(self.agent.info()["action_outcome"])

    def test_malformed_arguments_are_denied_without_claiming_model_unavailability(self):
        answer = "I couldn't start the break."
        state = self.run_observation([
            {"chunks": [chunk(calls=[{"function": {"name": "request_distraction", "arguments": "{bad json"}}], done=True)]},
            {"chunks": [chunk(answer, done=True)]},
        ])
        self.assertEqual(self.executed, [])
        self.assertEqual(state["source"], "local model")
        self.assertEqual(state["action_outcome"]["status"], "denied")
        self.assertIs(state["trace"][0]["result"]["ok"], False)
        self.assertNotIn("tools", self.fixture.requests[1][1])
        self.assertEqual(state["model_rounds"][0]["tool_calls"][0]["function"]["arguments"], "{bad json")
        self.assertEqual(self.store.messages()[-1]["text"], answer)

    def test_invalid_first_attempt_cannot_be_retried_in_same_batch(self):
        state = self.run_observation([
            {"chunks": [chunk(calls=[self.action_call(cooldown=False), self.action_call()], done=True)]},
            {"chunks": [chunk("I couldn't start the break.", done=True)]},
        ])
        self.assertEqual(self.executed, [])
        self.assertEqual(state["action_outcome"]["status"], "denied")
        self.assertTrue(all(row["result"]["ok"] is False for row in state["trace"]))
        self.assertNotIn("tools", self.fixture.requests[1][1])

    def test_unoffered_read_context_is_rejected_before_dispatch(self):
        state = self.run_observation([
            {"chunks": [chunk(calls=[{"function": {"name": "read_context", "arguments": {
                "cooldown": False, "session": "running"}}}], done=True)]},
            {"chunks": [chunk(calls=[self.action_call()], done=True)]},
            {"chunks": [chunk("That report can wait. The break is starting.", done=True)]},
        ])
        self.assertEqual([row[0] for row in self.executed], ["request_distraction"])
        denied_read = [row for row in state["trace"] if row["tool"] == "read_context"]
        self.assertEqual(len(denied_read), 1)
        self.assertIs(denied_read[0]["result"]["ok"], False)
        self.assertEqual(observation_outcome(state["trace"])["status"], "started")


if __name__ == "__main__":
    unittest.main()
