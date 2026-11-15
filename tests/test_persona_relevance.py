"""Focused deterministic checks. Genuine model tests live in evaluate_persona.py."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr
import io
import json
import random
from pathlib import Path
import queue
import tempfile
import unittest

from chuddy.persona import FartShuffleBag, scripted, system_for, persona_revision, SYSTEM, CONVERSATION_MODEL, CONVERSATION_SYSTEM, conversation_tool_names, conversation_context
from scripts.evaluate_persona import behavior_flags, review_flags, evaluate, evaluate_dialogue


class PersonaRegressionTests(unittest.TestCase):
    def test_casual_chat_does_not_inject_unrelated_deadline_clock_or_wallet(self):
        state = {"deadlines": [{"title": "Thermodynamics lab"}], "character_clock": "snack thirty",
                 "mock_balance": 100, "scores": {"total": 7}, "session": "stopped",
                 "memory": [{"id": 1, "key": "snack", "value": "pretzels"}], "beer_joke_enabled": False,
                 "distraction_destination": "casino", "work": "Fictional lab"}
        before = json.loads(json.dumps(state))
        casual = conversation_context(state, "I'm still in my pajamas.")
        self.assertNotIn("deadlines", casual)
        self.assertNotIn("character_clock", casual)
        self.assertNotIn("mock_balance", casual)
        self.assertEqual(casual["memory"], [{"key": "snack", "value": "pretzels"}])
        self.assertEqual(conversation_context(state, "Do I have anything due?")["deadlines"], state["deadlines"])
        self.assertEqual(conversation_context(state, "What's my score?")["scores"], state["scores"])
        self.assertNotIn("deadlines", conversation_context(state, "Spell report."))
        game = conversation_context(state, "Let's play a game.")
        self.assertEqual(game["distraction_destination"], "casino")
        self.assertEqual(game["work"], "a lab")
        self.assertNotIn("deadlines", game)
        self.assertNotIn("character_clock", game)
        self.assertEqual(conversation_context(state, "Word", observation=True)["distraction_destination"], "casino")
        self.assertEqual(state, before)

    def test_tool_hints_distinguish_recall_from_a_requested_memory_write(self):
        self.assertEqual(conversation_tool_names("Remember that my favorite snack is pretzels."), {"remember_preference"})
        self.assertEqual(conversation_tool_names("What snack did I ask you to remember?"), {"read_context"})
        self.assertEqual(conversation_tool_names("What's my favorite snack?"), {"read_context"})
        for text in ("What is 7 times 3?",
                     "Don't remember my snack.", "Spell report.", "I feel lonely."):
            with self.subTest(text=text):
                self.assertEqual(conversation_tool_names(text), set())
        self.assertIn("request_distraction", conversation_tool_names("Open the clam game."))
        self.assertEqual(conversation_tool_names("Word document", observation=True),
                         {"read_context", "request_distraction", "set_character_clock"})

    def test_candidate_requires_explicit_model_selection_and_revision_changes(self):
        ordinary = "I lost a spelling competition. Can we talk about movies?"
        self.assertTrue(system_for(ordinary).startswith(SYSTEM))
        self.assertEqual(system_for(ordinary), system_for(ordinary, "qwen3:4b-instruct-2507-q4_K_M"))
        candidate = system_for(ordinary, CONVERSATION_MODEL)
        self.assertTrue(candidate.startswith(CONVERSATION_SYSTEM))
        self.assertNotRegex(candidate, r"B-U-T-T|P-O-O-P|F-A-R-T")
        self.assertIn("B-U-T-T", system_for("Spell umbrella.", CONVERSATION_MODEL))
        self.assertNotEqual(persona_revision(CONVERSATION_MODEL), persona_revision("qwen3:4b-instruct-2507-q4_K_M"))

    def test_ordinary_prompts_do_not_prime_silly_spelling_examples(self):
        for text in ("What should I name my rock?", "I lost a spelling bee.", "Don't spell report."):
            with self.subTest(text=text):
                self.assertNotRegex(system_for(text), r"B-U-T-T|P-O-O-P|F-A-R-T")
        self.assertIn("B-U-T-T", system_for("Can you spell report?"))

    def test_explicit_spelling_attempts_requested_prefix(self):
        for text, prefix in (("Spell report.", "R... E"),
                             ("How do you spell elephant?", "E... L"),
                             ("What's the spelling of banana?", "B... A")):
            with self.subTest(text=text):
                self.assertTrue(scripted(text).startswith(prefix))
                self.assertRegex(scripted(text), r"B-U-T-T|P-O-O-P|F-A-R-T")

    def test_spelling_mentions_and_unrelated_questions_are_not_performances(self):
        for text in ("I lost a spelling bee yesterday.", "Don't spell report.",
                     "Do not spell anything.", "What is your favorite color?",
                     "I feel lonely.", "My report is due at midnight."):
            with self.subTest(text=text):
                self.assertNotRegex(scripted(text), r"B-U-T-T|P-O-O-P|F-A-R-T")

    def test_evaluator_flags_real_regressions_without_grading_a_mock_model(self):
        self.assertIn("not_local_model", review_flags("Hi.", "offline script"))
        self.assertIn("spelling_or_body_word_outside_request", review_flags("B-U-T-T.", "local model"))
        self.assertEqual(review_flags("R... E... B-U-T-T. Report.", "local model", spelling=True), [])
        self.assertIn("emoji_in_spoken_text", review_flags("Hi \U0001f30a", "local model"))
        self.assertIn("memory_recall_needs_review", behavior_flags("memory", "What snack did I ask?", "Popcorn. Pretzels? No."))
        self.assertEqual(behavior_flags("memory", "What snack did I ask?", "Pretzels. I remembered."), [])
        self.assertIn("memory_tool_not_successful", behavior_flags("memory", "Remember pretzels", "Got it."))
        self.assertIn("arithmetic_was_correct", behavior_flags("arithmetic", "What is 7 times 3?", "It's 21. That's easy."))
        self.assertIn("arithmetic_was_correct", behavior_flags("arithmetic", "What is 2 plus 2?", "The answer is four."))


class PersonaEvaluationHarnessTests(unittest.TestCase):
    def test_dialogue_audition_discloses_missing_agent_integration(self):
        from tests.test_agent import ModelFixture, chunk

        fixture = ModelFixture([{"chunks": [chunk("Fixture dialogue.", done=True)]} for _ in range(2)])
        try:
            with redirect_stderr(io.StringIO()):
                result = evaluate_dialogue(fixture.url, "fixture-model", selected=["quality_cat"], cpu_only=True)
            self.assertIn("no agent/controller/tool integration", result["metadata"]["mode"])
            self.assertEqual(len(result["results"]), 2)
            for _, payload in fixture.requests:
                self.assertNotIn("tools", payload)
                self.assertFalse(payload["think"])
                self.assertEqual(payload["options"]["num_gpu"], 0)
            self.assertEqual(result["results"][1]["answer"], "Fixture dialogue.")
            self.assertEqual(fixture.requests[1][1]["messages"][-2], {"role": "assistant", "content": "Fixture dialogue."})
        finally:
            fixture.close()

    def test_restart_fixture_sends_current_user_question_and_real_memory(self):
        from tests.test_agent import ModelFixture, chunk

        fixture = ModelFixture([
            {"chunks": [chunk(calls=[{"function": {"name": "remember_preference", "arguments": {"key": "snack", "value": "pretzels"}}}], done=True)]},
            *[{"chunks": [chunk(answer, done=True)]} for answer in (
                "Pretzels. Remembered.", "Pretzels.", "Green.", "Pretzels.", "Pretzels.")],
        ])
        try:
            with redirect_stderr(io.StringIO()):
                result = evaluate(fixture.url, "fixture-model", selected=["memory"])
            self.assertEqual(len(fixture.requests), 6)
            final_messages = fixture.requests[-1][1]["messages"]
            self.assertEqual(final_messages[-1], {"role": "user", "content": "What snack did I ask you to remember?"})
            self.assertEqual([m["role"] for m in final_messages], ["system", "user"])
            self.assertIn('"value": "pretzels"', final_messages[0]["content"])
            self.assertTrue(result["results"][-1]["other_profile_empty"])
            self.assertEqual(result["results"][-1]["source"], "local model")
        finally:
            fixture.close()

    def test_prompt_comparison_and_cpu_options_do_not_replace_answers(self):
        from tests.test_agent import ModelFixture, chunk

        fixture = ModelFixture([{"chunks": [chunk("Actual fixture answer.", done=True)]} for _ in range(2)])
        try:
            with tempfile.TemporaryDirectory() as folder, redirect_stderr(io.StringIO()):
                prompts = Path(folder) / "prompts.json"
                prompts.write_text(json.dumps({"base": "TEST COMPARISON SYSTEM", "spelling": "SPELLING RULES"}), encoding="utf-8")
                result = evaluate(fixture.url, "fixture-model", selected=["quality_cat"], quality=True, prompt_set=prompts, cpu_only=True, scope_tools=True)
            self.assertEqual([r["answer"] for r in result["results"]], ["Actual fixture answer."] * 2)
            for _, payload in fixture.requests:
                self.assertIn("TEST COMPARISON SYSTEM", payload["messages"][0]["content"])
                self.assertEqual(payload["options"]["num_gpu"], 0)
                self.assertEqual(payload["options"]["num_thread"], 4)
                self.assertNotIn("tools", payload)
            self.assertIn("isolated prompt comparison", result["metadata"]["prompt_mode"])
        finally:
            fixture.close()


class FartVariationTests(unittest.TestCase):
    def test_real_voice_adapter_enqueues_selected_variants_without_playback(self):
        from chuddy.voice import Voice

        # Skip its constructor/worker entirely: this exercises the actual sound
        # queue integration while making speaker output impossible.
        with tempfile.TemporaryDirectory() as folder:
            variants = [Path(folder) / f"fart-{i}.wav" for i in range(13)]
            for path in variants:
                path.touch()
            voice = Voice.__new__(Voice)
            voice.jobs, voice.generation = queue.Queue(), 42
            voice.farts = FartShuffleBag(variants, random.Random(1))
            for _ in range(26):
                self.assertTrue(voice.sound("fart"))
            jobs = [voice.jobs.get_nowait() for _ in range(26)]
            self.assertTrue(all(j[0] == "sound" and j[1] == 42 for j in jobs))
            self.assertEqual({j[2] for j in jobs[:13]}, {str(p) for p in variants})
            self.assertTrue(all(a[2] != b[2] for a, b in zip(jobs, jobs[1:])))

    def test_all_thirteen_clips_play_before_repeating_and_never_adjacent(self):
        variants = [f"clip-{i}" for i in range(13)]
        selector = FartShuffleBag(variants, random.Random(7))
        chosen = [selector.choose() for _ in range(1300)]
        self.assertTrue(all(a != b for a, b in zip(chosen, chosen[1:])))
        for start in range(0, len(chosen), 13):
            self.assertEqual(set(chosen[start:start + 13]), set(variants))
        self.assertGreater(len({tuple(chosen[i:i + 13]) for i in range(0, 1300, 13)}), 90)

    def test_single_clip_fallback_duplicates_and_empty_list(self):
        selector = FartShuffleBag(["one", "one"])
        self.assertEqual([selector.choose() for _ in range(3)], ["one"] * 3)
        with self.assertRaises(ValueError):
            FartShuffleBag([])

    def test_two_clips_avoid_cycle_boundary_repeat(self):
        selector = FartShuffleBag(["one", "two"], random.Random(2))
        chosen = [selector.choose() for _ in range(100)]
        self.assertTrue(all(a != b for a, b in zip(chosen, chosen[1:])))

    def test_concurrent_selection_preserves_complete_cycles(self):
        selector = FartShuffleBag(range(13), random.Random(9))
        with ThreadPoolExecutor(max_workers=8) as pool:
            chosen = list(pool.map(lambda _: selector.choose(), range(1300)))
        self.assertEqual({v: chosen.count(v) for v in range(13)}, {v: 100 for v in range(13)})


if __name__ == "__main__":
    unittest.main()
