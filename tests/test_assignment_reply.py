"""Verify the fixed exchange and factual calendar readback through chat."""

from contextlib import closing
from datetime import datetime, timezone
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from chuddy.agent import LocalAgent, local_request
from chuddy.assignment_reply import SOURCE, NOTHING_DUE, MISSED_ASSIGNMENT, assignment_question, assignment_reply
from chuddy.server import DesktopServer
from chuddy.store import Store
from tests.helpers import ControllerCase, FakeVoice
from tests.test_agent import ModelFixture, chunk


SCREENSHOT_QUESTION = "whenis my next assign,ment dfue"
NOW = datetime(2026, 11, 15, 20, 19, tzinfo=timezone.utc)
ACTUAL = "2026-11-16T04:59:00+00:00"
REAL = "Assignment #14 is due Nov 15 at 11:59 p.m."


def at_screenshot(question, rows, exchange):
    return assignment_reply(question, rows, exchange, now=NOW)


class AssignmentSelectionTests(unittest.TestCase):
    def test_explicit_scopes_ignore_overdue_and_match_course_or_named_entry(self):
        rows = [
            {"id": 1, "title": "Assignment #12 - Due", "due": "2026-11-09T04:59:00+00:00"},
            {"id": 2, "title": "Physics Assignment #14 - Due", "due": ACTUAL},
            {"id": 3, "title": "Chemistry Assignment #14 - Due", "due": "2026-11-17T04:59:00+00:00"},
        ]
        def answer(query):
            return assignment_reply(assignment_question(query), lambda: rows, now=NOW)[0]

        self.assertEqual(answer("When is assignment 14 due?"), "Which one do you mean?")
        self.assertEqual(answer("When is my next chemistry assignment actually due?"),
                         "Chemistry Assignment #14 is due Nov 16 at 11:59 p.m.")
        self.assertEqual(answer("What is the real due time for Physics Assignment #14?"),
                         "Physics Assignment #14 is due Nov 15 at 11:59 p.m.")
        self.assertEqual(answer("When is Assignment #12 due?"),
                         "Assignment #12 is due Nov 8 at 11:59 p.m.")
        self.assertEqual(answer("When's my next statistics assignment due?"),
                         "I don't see an upcoming assignment.")

    def test_invalid_dates_are_skipped_and_all_day_has_no_invented_clock_time(self):
        rows = [
            {"id": 1, "title": "Assignment #8", "due": "does not parse"},
            {"id": 2, "title": "Assignment #10 [all day]", "due": "2026-11-15T05:00:00Z",
             "calendar_date": "2026-11-15", "calendar_timezone": "America/Toronto"},
        ]
        question = assignment_question("When is my next assignment actually due?")
        self.assertEqual(assignment_reply(question, lambda: rows, now=NOW)[0],
                         "Assignment #10 is due Nov 15. I only have the date.")

    def test_factual_deadlines_distinguish_the_repeated_fall_hour(self):
        for due, expected in (
            ("2026-11-01T05:30:00Z", "Assignment #1 is due Nov 1 at 1:30 a.m. EDT."),
            ("2026-11-01T06:30:00Z", "Assignment #1 is due Nov 1 at 1:30 a.m. EST."),
        ):
            with self.subTest(due=due):
                rows = [{"id": 1, "title": "Assignment #1", "due": due}]
                ask = assignment_question("When is assignment #1 due?")
                self.assertEqual(assignment_reply(ask, lambda: rows, now=NOW)[0], expected)

    def test_generic_phrasings_never_read_the_calendar_for_the_gag(self):
        for query in (
            SCREENSHOT_QUESTION, "WHAT MY NEXT ASSIGNEMNT DUE",
            "When is my next assignment due?", "When will my next assignment be due?",
            "Which assignment is due next?", "What's the due date for my next assignment?",
        ):
            with self.subTest(query=query):
                deadlines = Mock(side_effect=AssertionError("gag read the calendar"))
                question = assignment_question(query)
                first, anchor = assignment_reply(question, deadlines)
                self.assertEqual(first, "Nothing is due.")
                line, _ = assignment_reply(assignment_question("When is it due?"), deadlines, anchor)
                self.assertEqual(line, "Well, I guess I missed Assignment #14. It’s due tomorrow.")
                deadlines.assert_not_called()

    def test_unrelated_questions_and_reminders_do_not_match_the_deadline_bit(self):
        for turn in ("what do you think of the next assignment", "what's my next dentist appointment",
                     "remind me when my next assignment is due", "I think my next assignment is due",
                     "I think my homework assignment is due", "spell assignment",
                     "what time is it?", "when is the next game", "when should I work"):
            with self.subTest(turn=turn):
                self.assertIsNone(assignment_question(turn))


class AssignmentConversationTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = Store(Path(folder.name) / "chuddy.sqlite3")
        self.addCleanup(self.store.close)
        self.store.update_settings({"voice": True, "ai_enabled": False})
        self.store.add_deadline("Assignment #12 - Due", "2026-11-09T04:59:00Z")
        self.item = self.store.add_deadline("Assignment #14 - Due", ACTUAL)
        self.voice = FakeVoice()
        self.agent = LocalAgent(self.store, lambda: {}, lambda *_a, **_kw: {}, self.voice)
        self.addCleanup(self.agent.cancel)
        resolver = patch("chuddy.agent.assignment_reply", side_effect=at_screenshot)
        resolver.start()
        self.addCleanup(resolver.stop)

    def reply(self, text):
        turn = self.agent.begin(text)["turn_id"]
        self.agent.worker.join(3)
        self.assertFalse(self.agent.worker.is_alive())
        row = self.store.messages()[-1]
        self.assertEqual(row["role"], "assistant")
        self.assertEqual(self.voice.spoken[-1], (row["text"], turn))
        return row

    def test_screenshot_typo_second_and_third_turns_and_factual_escape(self):
        real_rows = self.store.deadlines()
        self.store.setting("ai_enabled", True)
        with patch("chuddy.agent.local_request", side_effect=AssertionError("script attempted inference")) as request:
            for text, expected in (
                (SCREENSHOT_QUESTION, NOTHING_DUE),
                ("When is it due?", MISSED_ASSIGNMENT),
                ("When is my next assignment due?", MISSED_ASSIGNMENT),
                ("What's the real due time?", REAL),
                ("What time?", REAL),
                ("When is Assignment #14 due?", REAL),
            ):
                row = self.reply(text)
                self.assertEqual((row["text"], row["source"]), (expected, SOURCE))
            request.assert_not_called()
        self.assertEqual(self.store.deadlines(), real_rows)

    def test_explicit_actual_first_and_generic_card_readback(self):
        self.store.add_deadline("Club check-in", "2026-11-15T22:00:00Z")
        self.assertEqual(self.reply("What is my next deadline?")["text"],
                         "Club check-in is due Nov 15 at 5:00 p.m.")
        self.assertEqual(self.reply("When is my actual next assignment due?")["text"], REAL)

    def test_fresh_actual_question_requires_unambiguous_saved_entry(self):
        self.assertEqual(self.reply("What's the real due time?")["text"], REAL)
        self.agent.cancel()
        self.store.add_deadline("Club check-in", "2026-11-15T22:00:00Z")
        self.assertEqual(self.reply("What's the real due time?")["text"], "Which one do you mean?")

    def test_fixed_gag_ignores_retiming_and_real_readback_uses_the_new_time(self):
        self.assertEqual(self.reply(SCREENSHOT_QUESTION)["text"], NOTHING_DUE)
        with self.store.transaction():
            self.store.db.execute("UPDATE deadlines SET due=? WHERE id=?",
                                  ("2026-11-16T03:59:00+00:00", self.item["id"]))
        self.assertEqual(self.reply("When is it due?")["text"], MISSED_ASSIGNMENT)
        self.assertEqual(self.reply("What's the actual due time?")["text"],
                         "Assignment #14 is due Nov 15 at 10:59 p.m.")

    def test_no_upcoming_and_unrelated_turn(self):
        self.assertEqual(self.reply(SCREENSHOT_QUESTION)["text"], NOTHING_DUE)
        self.reply("I'd like to watch a movie.")
        self.assertNotEqual(self.reply("When?")["source"], SOURCE)
        self.store.delete_deadline(self.item["id"])
        self.assertEqual(self.reply(SCREENSHOT_QUESTION)["text"], NOTHING_DUE)
        self.assertEqual(self.reply("When is it due?")["text"], MISSED_ASSIGNMENT)
        self.assertEqual(self.reply("What's the real due time?")["text"], "I don't see an upcoming assignment.")

    def test_cancelled_first_turn_does_not_prime_exchange(self):
        paused, release = threading.Event(), threading.Event()
        original = self.agent._run

        def delayed(*args):
            paused.set()
            release.wait(3)
            original(*args)

        with patch.object(self.agent, "_run", side_effect=delayed):
            self.agent.begin(SCREENSHOT_QUESTION)
            self.assertTrue(paused.wait(2))
            self.agent.cancel()
            release.set()
            self.agent.worker.join(3)
        self.assertEqual(self.voice.spoken, [])
        self.assertEqual(self.reply(SCREENSHOT_QUESTION)["text"], NOTHING_DUE)
        self.agent.cancel()
        self.assertNotEqual(self.reply("When?")["source"], SOURCE)
        self.assertEqual(self.reply(SCREENSHOT_QUESTION)["text"], NOTHING_DUE)

    def test_profile_switch_cannot_carry_over_an_accepted_exchange(self):
        self.assertEqual(self.reply(SCREENSHOT_QUESTION)["text"], NOTHING_DUE)
        other = self.store.add_profile("Other")
        self.store.select_profile(other["id"])
        self.assertNotEqual(self.reply("When is it due?")["source"], SOURCE)
        self.store.add_deadline("Assignment #14", ACTUAL)
        self.assertEqual(self.reply(SCREENSHOT_QUESTION)["text"], NOTHING_DUE)


class TranscribedAssignmentTests(ControllerCase):
    def setUp(self):
        with patch("tests.helpers.FakeAgent", LocalAgent):
            super().setUp()

    def test_accepted_transcript_uses_the_same_exchange(self):
        self.store.setting("ai_enabled", False)
        self.store.add_deadline("Assignment #14 - Due", ACTUAL)
        profile = self.store.profile()["id"]
        with (patch.object(self.controller.microphone, "current", return_value=True),
              patch("chuddy.agent.assignment_reply", side_effect=at_screenshot)):
            for query, expected in ((SCREENSHOT_QUESTION, NOTHING_DUE), ("When is it due?", MISSED_ASSIGNMENT)):
                result = self.controller._microphone_transcript(query, 1, profile)
                self.assertIn("turn_id", result)
                self.controller.agent.worker.join(3)
                self.assertEqual(self.store.messages()[-1]["text"], expected)
        self.assertEqual(self.controller.state()["deadlines"][0]["due"], ACTUAL)

    def assert_capture_exchange(self, *, keyboard):
        self.store.setting("ai_enabled", False)
        self.store.add_deadline("Assignment #14 - Due", ACTUAL)
        profile = self.store.profile()["id"]
        audio = self.controller.audio
        audio.generation = 0
        audio.require_transcriber = Mock()
        questions = iter((SCREENSHOT_QUESTION, "When is it due?"))

        def start():
            audio.generation += 1
            audio.start()

        def finish(**_):
            audio.recording = False
            return {"text": next(questions)}

        audio.start_conversation = Mock(side_effect=start)
        audio.finish_conversation = Mock(side_effect=finish)
        microphone = self.controller.microphone
        with patch("chuddy.agent.assignment_reply", side_effect=at_screenshot):
            for index, expected in enumerate((NOTHING_DUE, MISSED_ASSIGNMENT)):
                action = {"action": "microphone"}
                if keyboard:
                    action.update(hold_id=f"assignment-{index}", generation=microphone.generation,
                                  profile_id=profile, operation="hold_start")
                else:
                    action["operation"] = "start"
                self.assertTrue(self.controller.action(action)["active"])
                action["operation"] = "hold_finish" if keyboard else "finish"
                self.assertIn("turn_id", self.controller.action(action))
                self.controller.agent.worker.join(3)
                self.assertEqual(self.store.messages()[-1]["text"], expected)
        self.assertEqual(audio.finish_conversation.call_count, 2)
        self.assertFalse(audio.recording)
        self.assertEqual(self.controller.state()["deadlines"][0]["due"], ACTUAL)

    def test_two_keyboard_captures_preserve_the_published_exchange(self):
        self.assert_capture_exchange(keyboard=True)

    def test_two_on_screen_captures_preserve_the_published_exchange(self):
        self.assert_capture_exchange(keyboard=False)

    def test_http_screenshot_input_twice_without_assignment_14_or_model(self):
        self.store.add_deadline("Assignment #12 - Due", "2026-11-09T04:59:00Z")
        self.store.add_deadline("Assignment #9 - Due", ACTUAL)
        before = self.store.deadlines()
        fixture = ModelFixture([{"chunks": [chunk("I like jazz.", done=True)]}])
        self.addCleanup(fixture.close)
        self.store.update_settings({"ai_enabled": True, "ai_endpoint": fixture.url})
        server = DesktopServer(self.controller, Path(__file__).resolve().parents[1], port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def request(path, body=None):
            headers = {"X-Chuddy-Token": server.token, "Content-Type": "application/json"}
            with closing(http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)) as client:
                client.request("POST" if body else "GET", path, json.dumps(body) if body else None, headers)
                with client.getresponse() as response:
                    self.assertEqual(response.status, 200)
                    return json.load(response)

        def send(text):
            self.assertIn("turn_id", request("/api/action", {"action": "chat", "text": text}))
            self.controller.agent.worker.join(3)
            self.assertFalse(self.controller.agent.worker.is_alive())

        try:
            with (patch("chuddy.agent.assignment_reply", side_effect=at_screenshot),
                  patch("chuddy.agent.local_request", wraps=local_request) as ollama):
                for expected in ("Nothing is due.", "Well, I guess I missed Assignment #14. It’s due tomorrow."):
                    send(SCREENSHOT_QUESTION)
                    reply = request("/api/state")["messages"][-1]
                    self.assertEqual((reply["text"], reply["source"]), (expected, SOURCE))
                send("What's the real due time?")
                self.assertEqual(request("/api/state")["messages"][-1]["text"],
                                 "Assignment #9 is due Nov 15 at 11:59 p.m.")
                send("When is Assignment #12 due?")
                self.assertEqual(request("/api/state")["messages"][-1]["text"],
                                 "Assignment #12 is due Nov 8 at 11:59 p.m.")
                ollama.assert_not_called()
                send("Do you like jazz?")
                self.assertEqual(request("/api/state")["messages"][-1]["source"], "local model")
                self.assertTrue(ollama.called)
                self.assertNotIn(MISSED_ASSIGNMENT, json.dumps(fixture.requests, ensure_ascii=False))
            self.assertEqual(request("/api/state")["deadlines"], before)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(3)
