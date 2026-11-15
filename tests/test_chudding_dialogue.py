"""Work dialogue regressions with loopback fixtures and disposable state."""
from pathlib import Path
import random
import tempfile
import threading
import unittest

from chuddy.agent import LocalAgent
from chuddy.conversation import asks_to_work, encourages_work
from chuddy.persona import CONVERSATION_MODEL, scripted
from chuddy.store import Store
from chuddy.work_banter import BANTER_OUTCOME_SOURCE, WorkBanter
from tests.helpers import FakeVoice
from tests.test_agent import ModelFixture, chunk


ACTION = {"function": {"name": "request_distraction", "arguments": {
    "destination": "casino", "reason": "Come sit with me."}}}


class ChuddingDialogueTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = Store(Path(temp.name) / "state.sqlite3")
        self.addCleanup(self.store.close)
        self.initial_history = self.store.messages()
        self.store.update_settings({"ai_model": CONVERSATION_MODEL, "voice": True})
        self.voice = FakeVoice()
        self.executed = []
        self.tool_result = {"ok": False, "error": "The observation expired."}
        self.agent = LocalAgent(
            self.store, lambda: {"session": "running", "work": "Foreground activity",
                                 "distraction_destination": "casino"},
            self.execute, self.voice, managed_work_dialogue=True,
        )
        self.addCleanup(self.agent.cancel)
        self.banter = WorkBanter(random.Random(15))

    def execute(self, name, args, **kwargs):
        self.executed.append((name, args))
        return self.tool_result

    def fixture(self, *plans):
        fixture = ModelFixture(plans)
        self.addCleanup(fixture.close)
        self.store.setting("ai_endpoint", fixture.url)
        return fixture

    def finish(self):
        self.agent.worker.join(5)
        self.assertFalse(self.agent.worker.is_alive())

    def publish_terminal_fixture(self, ticket, *, tab_closed=False):
        # Stand-in for the coordinator's terminal publish call. Ticket lifetime
        # and real close/open acknowledgment stay with the interruption owner.
        line = self.banter.record_outcome(ticket, self.store.messages(), tab_closed=tab_closed)
        if line:
            self.store.message("assistant", line, BANTER_OUTCOME_SOURCE)
            self.voice.say(line)
        return line

    def test_confirmed_open_with_delayed_denial_has_one_success_line(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        fixture = self.fixture({
            "chunks": [chunk("Sorry, couldn't take a break. Work remains."),
                       chunk(calls=[ACTION], done=True)],
            "pause_after": 1, "entered": entered, "gate": release,
        })
        self.agent.begin("Foreground activity", observation=True)
        self.assertTrue(entered.wait(4))
        ticket = {"id": "switch", "phase": "opened", "destination": "casino"}
        line = self.publish_terminal_fixture(ticket, tab_closed=True)
        self.assertEqual(line, "How about we gamble?")
        release.set()
        self.finish()
        self.assertEqual(len(self.executed), 1)
        self.assertEqual(self.agent.info()["action_outcome"]["status"], "denied")
        self.assertEqual(len(fixture.requests), 1)
        self.assertIsNone(self.publish_terminal_fixture(ticket, tab_closed=True))
        self.assertEqual(self.store.messages()[:len(self.initial_history)], self.initial_history)
        self.assertEqual([(m["source"], m["text"]) for m in self.store.messages()[len(self.initial_history):]],
                         [(BANTER_OUTCOME_SOURCE, line)])
        self.assertEqual(self.voice.spoken, [(line, None)])
        self.assertEqual(self.voice.fillers, [])

    def test_ai_off_waits_for_terminal_result_and_true_failure_is_silent(self):
        self.store.setting("ai_enabled", False)
        self.agent.begin("Foreground activity", observation=True)
        self.finish()
        self.assertEqual(self.store.messages(), self.initial_history)
        self.assertEqual(self.executed, [])
        ticket = {"id": "switch", "phase": "clicking", "destination": "casino"}
        self.assertIsNone(self.publish_terminal_fixture(ticket))
        ticket["phase"] = "error"
        for tab_closed in (False, True):
            self.assertIsNone(self.publish_terminal_fixture(ticket, tab_closed=tab_closed))
        self.assertNotIn("banter_source", ticket)
        self.assertEqual(self.store.messages(), self.initial_history)
        self.assertEqual(self.voice.spoken, [])

    def test_second_tool_denial_cannot_supersede_the_first_admission(self):
        self.tool_result = {"ok": True, "destination": "casino", "phase": "approaching"}
        fixture = self.fixture({"chunks": [chunk(calls=[ACTION, ACTION], done=True)]})
        self.agent.begin("Foreground activity", observation=True)
        self.finish()
        info = self.agent.info()
        self.assertEqual(len(self.executed), 1)
        self.assertEqual([row["result"]["ok"] for row in info["trace"]], [True, False])
        self.assertEqual(info["action_outcome"], {"status": "started", "destination": "casino"})
        self.assertEqual(len(fixture.requests), 1)
        self.assertEqual(self.store.messages(), self.initial_history)
        self.assertEqual(self.voice.spoken, [])

    def test_cancelled_observation_and_preview_do_not_publish(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        self.fixture({
            "chunks": [chunk("Work remains."), chunk(calls=[ACTION], done=True)],
            "pause_after": 1, "entered": entered, "gate": release,
        })
        self.agent.begin("Foreground activity", observation=True)
        self.assertTrue(entered.wait(4))
        self.agent.cancel()
        release.set()
        self.finish()
        self.assertIsNone(self.publish_terminal_fixture(
            {"phase": "opened", "destination": "casino", "test": True}))
        self.assertIsNone(self.publish_terminal_fixture(
            {"phase": "cancelled", "destination": "casino"}))
        self.assertEqual(self.executed, [])
        self.assertEqual(self.store.messages(), self.initial_history)
        self.assertEqual(self.voice.spoken, [])

    def test_work_permission_is_chudding_even_with_model_available_or_off(self):
        fixture = self.fixture()
        for question in ("Can I keep working?", "Can you let me finish my report?",
                         "Give me ten minutes to finish my homework."):
            self.assertTrue(asks_to_work(question))
            self.agent.begin(question)
            self.finish()
            row = self.store.messages()[-1]
            self.assertEqual(row["source"], "scripted chudding reply")
            self.assertFalse(encourages_work(row["text"]))
            self.assertEqual(row["text"], scripted(question))
        self.store.setting("ai_enabled", False)
        self.agent.begin("May I study?")
        self.finish()
        self.assertIn("chud", self.store.messages()[-1]["text"])
        self.assertEqual(fixture.requests, [])
        self.assertFalse(asks_to_work("Can I work out with you?"))

    def test_model_grant_is_repaired_and_never_said(self):
        bad = "Sure, I'll let you go back to work."
        good = "Scoot over. I saved you the good seat."
        fixture = self.fixture({"chunks": [chunk(bad, done=True)]},
                               {"chunks": [chunk(good, done=True)]})
        self.agent.begin("My homework is taking forever.")
        self.finish()
        self.assertEqual(self.store.messages()[-1]["text"], good)
        self.assertEqual(self.store.messages()[-1]["source"], "local model")
        self.assertEqual(self.voice.spoken[0][0], good)
        self.assertEqual(len(fixture.requests), 2)
        self.assertNotIn("tools", fixture.requests[1][1])

    def test_permission_check_distinguishes_commands_from_facts(self):
        for line in ("Go ahead and finish your report.", "Keep studying.", "I'll let you work.",
                     "You're allowed to work.", "Good job for keeping at work.",
                     "You should get back to your report."):
            with self.subTest(line=line):
                self.assertTrue(encourages_work(line))
        for line in ("It's due Friday at noon.", "Work can sit there by itself.",
                     "You did great at the game.", "Can we sit here?"):
            self.assertFalse(encourages_work(line))

    def test_saved_invitation_does_not_suppress_new_casino_tickets(self):
        invitation = "How about we gamble?"
        recent = [{"text": invitation}]
        for tab_closed in (False, True):
            ticket = {"id": str(tab_closed), "phase": "opened", "destination": "casino"}
            self.assertEqual(self.banter.record_outcome(ticket, recent, tab_closed=tab_closed),
                             invitation)
            self.assertIsNone(self.banter.record_outcome(ticket, recent, tab_closed=tab_closed))

    def test_other_destinations_invite_only_after_opening(self):
        for destination, invitation in (("game", "Wanna play a game?"),
                                        ("lcbo", "Wanna look around?"),
                                        ("beer_store", "Wanna look around?")):
            with self.subTest(destination=destination):
                self.assertEqual(self.banter.outcome("opened", destination, tab_closed=True),
                                 invitation)
                for phase in ("opening", "error", "preview", "cancelled", "stale"):
                    self.assertIsNone(self.banter.outcome(phase, destination, tab_closed=True))

    def test_normal_chat_uses_its_own_reply(self):
        question, answer = "What's your favorite color?", "Pink. It matches me."
        self.fixture({"chunks": [chunk(answer, done=True)]})
        self.agent.begin(question)
        self.finish()
        self.assertEqual(self.store.messages()[:len(self.initial_history)], self.initial_history)
        rows = self.store.messages()[len(self.initial_history):]
        self.assertEqual([(row["role"], row["text"]) for row in rows],
                         [("user", question), ("assistant", answer)])
        self.assertEqual(self.voice.spoken[0][0], answer)
        self.assertEqual(self.executed, [])
