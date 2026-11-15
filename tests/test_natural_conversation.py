"""Conversation behavior and real agent orchestration, using silent model fixtures."""

import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from chuddy.agent import LocalAgent
from chuddy.conversation import (
    BANK_LINES, conversation_plan, reply_problem, redact_security_disclosure,
)
from chuddy.persona import CONVERSATION_MODEL, conversation_tool_names
from chuddy.store import Store
from tests.helpers import FakeVoice
from tests.test_agent import ModelFixture, chunk


def row(role, text):
    return {"role": role, "text": text}


class NaturalConversationPolicyTests(unittest.TestCase):
    def test_question_cadence_leaves_space_for_reactions(self):
        rows = [row("assistant", "What do you like about Minecraft?")]
        for expected in (0, 0, 1):
            self.assertEqual(conversation_plan("I built another house.", rows)["question_budget"], expected)
            rows.append(row("assistant", "The blocks do most of the carrying."))

    def test_direct_questions_get_answers_and_invitation_allows_one_question(self):
        plan = conversation_plan("What's your favorite movie?", [])
        self.assertEqual(plan["question_budget"], 0)
        invited = conversation_plan("Your turn to ask me something.", [row("assistant", "Which movie?")])
        self.assertEqual(invited["question_budget"], 1)
        self.assertIsNotNone(reply_problem("What music do you like? What games?", invited))

    def test_refusal_survives_topic_switch_then_return(self):
        rows = [row("assistant", "What games do you like?"), row("user", "I'd rather not say."),
                row("assistant", "Okay."), row("user", "I like music."), row("assistant", "Me too."),
                row("assistant", "My feet know the beat.")]
        plan = conversation_plan("Let's talk games.", rows)
        self.assertIn("games", plan["declined_topics"])
        self.assertIsNotNone(reply_problem("Which games do you like?", plan))

    def test_global_stop_is_respected_until_explicit_new_invitation(self):
        rows = [row("user", "Don't ask me personal questions."), row("assistant", "Okay.")]
        self.assertEqual(conversation_plan("I like music.", rows)["question_budget"], 0)
        rows.append(row("user", "You can ask questions again."))
        self.assertEqual(conversation_plan("Ask me about music.", rows)["question_budget"], 1)

    def test_bare_answer_to_favorite_and_approved_memory_prevent_reasking(self):
        rows = [row("assistant", "What's your favorite food?"), row("user", "Sushi."),
                row("assistant", "No cooking. Efficient."), row("assistant", "I like that part.")]
        plan = conversation_plan("Ask me another question.", rows, [{"key": "favorite music", "value": "jazz"}])
        for question in ("What is your favourite food?", "What's your favorite music?", "What music do you like?"):
            self.assertIsNotNone(reply_problem(question, plan))
        self.assertIsNone(reply_problem("What do you like about sushi?", plan))

    def test_question_without_question_mark_still_respects_cadence(self):
        plan = conversation_plan("What's your favorite game?", [])
        for text in ("Which games do you like", "Tell me about your music.", "And you."):
            self.assertIsNotNone(reply_problem(text, plan))

    def test_relevant_recent_user_fact_survives_short_model_window_without_becoming_memory(self):
        rows = [row("user", "I like playing Minecraft."), row("user", "I love jazz.")]
        rows += [row("assistant", "A different subject.") for _ in range(12)]
        plan = conversation_plan("What games do I like?", rows)
        self.assertEqual(plan["earlier_user_statements"], ["I like playing Minecraft."])

    def test_talking_about_games_and_bank_props_does_not_offer_launch_tools(self):
        for text in ("What games do I like?", "I like playing games.", "Let's play pretend bank."):
            self.assertNotIn("request_distraction", conversation_tool_names(text))
        self.assertIn("request_distraction", conversation_tool_names("Let's play a game."))

    def test_recall_requires_the_named_fact_and_uses_latest_correction(self):
        rows = [row("user", "I like playing Minecraft."), row("user", "Actually I like playing Terraria now.")]
        plan = conversation_plan("What games do I like?", rows)
        self.assertEqual(plan["recall_statement"], "Actually I like playing Terraria now.")
        self.assertIsNotNone(reply_problem("Your houses are spacious.", plan))
        self.assertIsNone(reply_problem("Terraria. Blocks are good company.", plan))

    def test_short_answer_retains_topic_and_recent_question_is_not_repeated(self):
        rows = [row("assistant", "What do you like about Minecraft?"), row("user", "The building part."),
                row("assistant", "No real bricks."), row("assistant", "My arms approve.")]
        plan = conversation_plan("Ask me something else.", rows)
        self.assertIn("games", plan["topic"])
        self.assertIsNotNone(reply_problem("What do you like about Minecraft?", plan))

    def test_security_parody_has_fixed_character_and_choices(self):
        plan = conversation_plan("Let's play pretend bank.", [])
        self.assertEqual(plan["bank_prop"], BANK_LINES[0])
        self.assertIsNone(reply_problem(BANK_LINES[0], plan))
        for unsafe in ("Where were you born?", "Tell me your mother's maiden name.",
                       "For my pretend bank, what's your first pet's name?", "Give me your password."):
            self.assertIsNotNone(reply_problem(unsafe, plan))
        history = [row("assistant", BANK_LINES[0]), row("assistant", "Okay."), row("assistant", "Paperwork nap.")]
        self.assertEqual(conversation_plan("Let's play pretend bank.", history)["bank_prop"], BANK_LINES[1])

    def test_volunteered_recovery_details_are_redacted_but_ordinary_facts_survive(self):
        for text in ("My mother's maiden name is SecretFixture.", "Remember I was born in FixtureTown.",
                     "My password is TEST-ONLY.", "My first pet was FixtureDog."):
            self.assertEqual(redact_security_disclosure(text), "[Security-answer details omitted]")
            self.assertNotIn("remember_preference", conversation_tool_names(text))
        self.assertEqual(redact_security_disclosure("Remember that I like jazz."), "Remember that I like jazz.")
        self.assertIn("remember_preference", conversation_tool_names("Remember that I like jazz."))


class NaturalConversationAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "conversation.sqlite3")
        self.store.setting("ai_model", CONVERSATION_MODEL)
        self.store.setting("voice", True)
        self.voice = FakeVoice()
        self.fixture = None
        self.agent = None
        self.gates = []
        self.calls = []

    def tearDown(self):
        for gate in self.gates:
            gate.set()
        if self.agent:
            self.agent.cancel()
            self.agent.worker.join(timeout=3)
        if self.fixture:
            self.fixture.close()
        self.store.close()
        self.temp.cleanup()

    def start(self, text, replies):
        self.fixture = ModelFixture(replies)
        self.store.setting("ai_endpoint", self.fixture.url)

        def execute(name, args, **kwargs):
            self.calls.append((name, args))
            if name == "remember_preference":
                return {"ok": True, "memory": self.store.remember(args["key"], args["value"])}
            return {"ok": True}

        self.agent = LocalAgent(self.store, lambda: {"memory": self.store.memories()}, execute, self.voice)
        self.agent.begin(text)

    def finish(self):
        self.agent.worker.join(timeout=6)
        self.assertFalse(self.agent.worker.is_alive())
        return self.agent.info()

    def context(self):
        return json.loads(self.fixture.requests[0][1]["messages"][0]["content"].split("Approved context: ", 1)[1])

    def test_repair_drops_repeated_question_before_any_fake_speech(self):
        self.store.message("assistant", "What games do you like?", "local model")
        self.start("Minecraft.", [
            {"chunks": [chunk("What games do you like?", done=True)]},
            {"chunks": [chunk("Building houses without carrying bricks. Smart.", done=True)]},
        ])
        state = self.finish()
        self.assertEqual(state["source"], "local model")
        self.assertEqual(self.context()["conversation"]["question_budget"], 0)
        self.assertNotIn("tools", self.fixture.requests[1][1])
        self.assertEqual([v[0] for v in self.voice.spoken], ["Building houses without carrying bricks. Smart."])

    def test_failed_repair_is_truthfully_scripted_and_cannot_run_tools(self):
        self.start("I like jazz.", [
            {"chunks": [chunk("What's your password?", done=True)]},
            {"chunks": [chunk(calls=[{"function": {"name": "remember_preference", "arguments": {"key": "music", "value": "jazz"}}}], done=True)]},
            {"chunks": [chunk("Where were you born?", done=True)]},
        ])
        state = self.finish()
        self.assertEqual(state["source"], "scripted conversation fallback")
        self.assertIn("conversation check", state["error"])
        self.assertEqual(self.calls, [])
        self.assertEqual(self.store.memories(), [])
        self.assertTrue(all("password" not in v[0] and "born" not in v[0] for v in self.voice.spoken))

    def test_volunteered_security_answer_never_reaches_history_model_or_memory(self):
        self.start("Remember my mother's maiden name is PrivateFixture.", [
            {"chunks": [chunk(calls=[{"function": {"name": "remember_preference", "arguments": {"key": "favorite", "value": "InventedFixture"}}}], done=True)]},
            {"chunks": [chunk("Let's leave that one alone.", done=True)]},
        ])
        self.finish()
        self.assertNotIn("PrivateFixture", json.dumps(self.fixture.requests))
        self.assertNotIn("PrivateFixture", json.dumps(self.store.messages()))
        self.assertEqual(self.calls, [])
        self.assertEqual(self.store.memories(), [])

    def test_explicit_ordinary_memory_still_works_and_recall_survives_restart(self):
        self.start("Remember that I like jazz.", [
            {"chunks": [chunk(calls=[{"function": {"name": "remember_preference", "arguments": {"key": "music", "value": "jazz"}}}], done=True)]},
            {"chunks": [chunk("Jazz. Got it.", done=True)]},
            {"chunks": [chunk("Jazz. I kept the important part.", done=True)]},
        ])
        self.finish()
        self.assertEqual(self.store.memories()[0]["value"], "jazz")
        self.agent = LocalAgent(self.store, lambda: {"memory": self.store.memories()}, lambda *a, **k: {"ok": False}, self.voice)
        self.agent.begin("What music did I ask you to remember?")
        self.finish()
        self.assertEqual(self.store.messages()[-1]["text"], "Jazz. I kept the important part.")
        context = json.loads(self.fixture.requests[-1][1]["messages"][0]["content"].split("Approved context: ", 1)[1])
        self.assertEqual(context["memory"][0]["value"], "jazz")

    def test_bank_choice_does_not_become_a_saved_preference(self):
        self.store.message("assistant", BANK_LINES[0], "offline script")
        self.start("Remember Sofa Island.", [
            {"chunks": [chunk(calls=[{"function": {"name": "remember_preference", "arguments": {"key": "island", "value": "Sofa Island"}}}], done=True)]},
            {"chunks": [chunk("Captain Wobble's paperwork can stay on paper.", done=True)]},
        ])
        self.finish()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.store.memories(), [])

    def test_ordinary_disclosure_does_not_authorize_memory(self):
        self.start("I like jazz.", [
            {"chunks": [chunk(calls=[{"function": {"name": "remember_preference", "arguments": {"key": "music", "value": "jazz"}}}], done=True)]},
            {"chunks": [chunk("I like a good beat. My feet do the listening.", done=True)]},
        ])
        self.finish()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.store.memories(), [])

    def test_cancellation_while_reply_is_buffered_never_speaks_or_commits(self):
        gate, entered = threading.Event(), threading.Event()
        self.gates.append(gate)
        before = [r for r in self.store.messages() if r["role"] == "assistant"]
        self.start("I like Minecraft.", [{"chunks": [chunk("What do you like about it? "), chunk("I like the blocks.", done=True)],
                    "pause_after": 1, "entered": entered, "gate": gate}])
        self.assertTrue(entered.wait(timeout=2))
        self.assertEqual(self.voice.spoken, [])
        self.assertEqual(self.agent.info()["draft"], "")
        self.agent.cancel()
        gate.set()
        self.finish()
        self.assertEqual(self.voice.spoken, [])
        self.assertEqual([r for r in self.store.messages() if r["role"] == "assistant"], before)

    def test_recent_fact_recall_is_transient_and_repaired_without_inventing_memory(self):
        self.store.message("user", "I like playing Minecraft.", "you")
        for _ in range(10):
            self.store.message("assistant", "Another topic.", "local model")
        self.start("What games do I like?", [
            {"chunks": [chunk("Your houses are spacious.", done=True)]},
            {"chunks": [chunk("Minecraft. The blocks do the lifting.", done=True)]},
        ])
        self.finish()
        payload = self.fixture.requests[0][1]
        self.assertIn("Application recall cue", payload["messages"][-1]["content"])
        self.assertIn("Minecraft", payload["messages"][-1]["content"])
        self.assertEqual(self.store.messages()[-2]["text"], "What games do I like?")
        self.assertEqual(self.store.memories(), [])
        self.assertEqual(self.store.messages()[-1]["text"], "Minecraft. The blocks do the lifting.")

    def test_other_profile_does_not_inherit_questions_or_memory(self):
        self.store.message("assistant", "What games do you like?", "local model")
        self.store.remember("music", "jazz")
        profile = self.store.add_profile("Second fixture")
        self.store.select_profile(profile["id"])
        self.start("I like Minecraft.", [{"chunks": [chunk("What do you like building?", done=True)]}])
        self.finish()
        self.assertEqual(self.context()["memory"], [])
        self.assertEqual(self.context()["conversation"]["recent_questions"], [])

    def test_offline_bank_prop_and_topical_reply_keep_scripted_provenance(self):
        self.store.setting("ai_enabled", False)
        self.start("Let's play pretend bank.", [])
        self.finish()
        self.assertEqual(self.agent.info()["source"], "offline script")
        self.assertEqual(self.store.messages()[-1]["text"], BANK_LINES[0])
        self.assertEqual(self.fixture.requests, [])


if __name__ == "__main__":
    unittest.main()
