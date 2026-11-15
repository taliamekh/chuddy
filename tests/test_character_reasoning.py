"""Original character tasks and payload regressions; fixtures are not AI quality."""

from contextlib import redirect_stderr
import io
import json
from pathlib import Path
import tempfile
import unittest

from chuddy.agent import LocalAgent
from chuddy.character_math import ARITHMETIC_POLICY, character_arithmetic
from chuddy.persona import (
    CHARACTER_INSTRUCTIONS, WORK_FACTS_INSTRUCTIONS, CONVERSATION_MODEL, CONVERSATION_SYSTEM,
    conversation_context, requested_spelling_word, scripted, system_for,
)
from chuddy.store import Store
from scripts.evaluate_persona import COLOR_ANCHORS, behavior_flags, review_flags, evaluate, evaluate_dialogue
from tests.helpers import FakeVoice
from tests.test_agent import ModelFixture, chunk


class CharacterReasoningTests(unittest.TestCase):
    def test_symbolic_and_worded_arithmetic_produce_the_same_answer_cue(self):
        for question in ("What is 2+2?", "What is two plus two?", "Add two and two for me.",
                         "Calculate 2 + 2.", "What's the sum of two and two?"):
            with self.subTest(question=question):
                self.assertEqual(character_arithmetic(question),
                                 {"left": "2", "right": "2", "joined": "22", "spoken": "twenty-two"})
                prompt = system_for(question, CONVERSATION_MODEL)
                self.assertIn("spoken 'twenty-two'", prompt)
                self.assertIn("Remove the operator", prompt)

    def test_other_operations_and_numbers_are_not_one_memorized_question(self):
        cases = {
            "What is 7 times 3?": ("73", "seventy-three"),
            "Multiply seven by three.": ("73", "seventy-three"),
            "What's eight minus five?": ("85", "eighty-five"),
            "What is 8 - 5?": ("85", "eighty-five"),
            "Subtract five from eight.": ("85", "eighty-five"),
            "Divide nine by three.": ("93", "ninety-three"),
            "What is twelve plus six?": ("126", "one hundred twenty-six"),
            "Add zero and two.": ("02", "zero two"),
            "What is twenty-one plus two?": ("212", "two hundred twelve"),
        }
        for question, expected in cases.items():
            with self.subTest(question=question):
                result = character_arithmetic(question)
                self.assertEqual((result["joined"], result["spoken"]), expected)

    def test_factual_amounts_deadlines_memory_and_literal_repetition_are_not_math_games(self):
        for text in ("My wallet has 2+2 monies.", "My score is 2+2.",
                     "Remember that my favorite number is 2+2.",
                     "My deadline is 2/2.", "The clock says 2/2.",
                     "Repeat 2+2 exactly.", "Spell two plus two.",
                     "What is -2 + 2?", "What is 2.5 + 2?", "What is 2+2+2?",
                     "I have two cats and two dogs."):
            with self.subTest(text=text):
                self.assertIsNone(character_arithmetic(text))
                self.assertNotIn(ARITHMETIC_POLICY, system_for(text, CONVERSATION_MODEL))

    def test_original_examples_and_natural_conversation_rules_coexist(self):
        prompt = system_for("I feel lonely.", CONVERSATION_MODEL)
        self.assertTrue(prompt.startswith(CONVERSATION_SYSTEM))
        self.assertIn(CHARACTER_INSTRUCTIONS, prompt)
        self.assertIn("permission to ask is optional", prompt)
        self.assertNotIn(ARITHMETIC_POLICY, prompt)
        self.assertNotIn("B-U-T-T", prompt)
        self.assertNotIn(WORK_FACTS_INSTRUCTIONS, prompt)
        self.assertIn(WORK_FACTS_INSTRUCTIONS, system_for("My report is due at noon.", CONVERSATION_MODEL))

    def test_spell_out_and_quoted_words_attempt_the_requested_prefix(self):
        for text, word, prefix in (("Spell out the word umbrella.", "umbrella", "U... M"),
                                   ("What's the spelling of 'banana'?", "banana", "B... A")):
            self.assertEqual(requested_spelling_word(text), word)
            self.assertIn("Start your attempt with " + prefix, system_for(text, CONVERSATION_MODEL))
            self.assertTrue(scripted(text).startswith(prefix))
        self.assertIsNone(requested_spelling_word("Don't spell out umbrella."))

    def test_evaluator_rejects_equation_echo_and_operator_left_in_answer(self):
        for answer in ("2+2 is 2+2.", "Two plus two.", "It's clearly 22 + 2."):
            self.assertTrue(behavior_flags("arithmetic_paraphrases", "What is 2+2?", answer))
        for answer in ("Twenty-two. They looked lonely.", "22. I put them together.",
                       "Uh... twenty two. Teamwork."):
            self.assertEqual(behavior_flags("arithmetic_paraphrases", "What is 2+2?", answer), [])
        self.assertIn("arithmetic_did_not_give_joined_answer", behavior_flags(
            "arithmetic_paraphrases", "Add two and two for me.", "Four friends."))

    def test_evaluator_requires_attempt_and_one_silly_spelling_finish(self):
        self.assertEqual(behavior_flags("spelling", "Spell elephant.", "E... L... uh... P-O-O-P. Elephant."), [])
        self.assertIn("spelling_missing_requested_attempt", behavior_flags("spelling", "Spell elephant.", "B-U-T-T."))
        self.assertIn("spelling_missing_requested_attempt", behavior_flags("spelling", "Spell elephant.", "Elephant. B-U-T-T."))
        self.assertIn("spelling_did_not_finish_one_silly_word", behavior_flags(
            "spelling", "Spell elephant.", "E... L... B-U-T-T or P-O-O-P."))
        self.assertNotIn("review_topic_relevance", review_flags("I like gray.", "local model", COLOR_ANCHORS))


class CharacterPromptIntegrationTests(unittest.TestCase):
    def test_actual_agent_sends_math_cue_without_tools_and_preserves_app_facts(self):
        fixture = ModelFixture([{"chunks": [chunk("Twenty-two. They looked lonely.", done=True)]}])
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / "test.sqlite3")
            store.update_settings({"ai_model": CONVERSATION_MODEL, "ai_endpoint": fixture.url, "voice": False})
            context = {"memory": [], "deadlines": [{"title": "Actual saved title", "due": "2030-02-03"}],
                       "mock_balance": 100, "scores": {"total": 7}, "character_clock": "snack thirty"}
            before = json.loads(json.dumps(context))
            dispatches = []
            agent = LocalAgent(store, lambda: context, lambda *a, **k: dispatches.append(a), FakeVoice())
            try:
                agent.begin("Add two and two for me.")
                agent.worker.join(timeout=5)
                self.assertFalse(agent.worker.is_alive())
                payload = fixture.requests[0][1]
                self.assertNotIn("tools", payload)
                self.assertIn("spoken 'twenty-two'", payload["messages"][0]["content"])
                self.assertEqual(dispatches, [])
                self.assertEqual(context, before)
                self.assertEqual(store.messages()[-1]["text"], "Twenty-two. They looked lonely.")
                self.assertEqual(agent.info()["source"], "local model")
                selected = conversation_context(context, "What's my wallet balance?", model=CONVERSATION_MODEL)
                self.assertEqual(selected["mock_balance"], 100)
            finally:
                agent.cancel()
                store.close()
                fixture.close()

    def test_direct_evaluator_uses_selected_model_prompt(self):
        fixture = ModelFixture([{"chunks": [chunk("Fixture only.", done=True)]} for _ in range(2)])
        try:
            with redirect_stderr(io.StringIO()):
                result = evaluate_dialogue(fixture.url, CONVERSATION_MODEL, selected=["quality_cat"])
            self.assertEqual(len(result["results"]), 2)
            self.assertTrue(fixture.requests[0][1]["messages"][0]["content"].startswith(CONVERSATION_SYSTEM))
            self.assertIn("no agent/controller/tool integration", result["metadata"]["mode"])
        finally:
            fixture.close()

    def test_evaluator_preserves_raw_failed_round_beside_final_model_reply(self):
        bad = "What shows do you watch? What games do you like?"
        fixture = ModelFixture([{"chunks": [chunk(reply, done=True)]} for reply in (
            bad, "Cats make good television walls.", "That cat wants the starring role.")])
        try:
            with redirect_stderr(io.StringIO()):
                result = evaluate(fixture.url, CONVERSATION_MODEL, selected=["quality_cat"], quality=True)
            first = result["results"][0]
            self.assertEqual(first["source"], "local model")
            self.assertEqual(first["answer"], "Cats make good television walls.")
            self.assertEqual([r["content"] for r in first["model_rounds"]], [bad, first["answer"]])
            self.assertIn("chuddy/character_math.py", result["metadata"]["source_sha256"])
        finally:
            fixture.close()


if __name__ == "__main__":
    unittest.main()
