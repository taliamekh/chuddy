import copy
import io
import json
import math
import threading
import unittest
from unittest.mock import patch

from chuddy.work_recognition import WorkRecognizer, evaluate, sanitize_metadata
from chuddy import windows


SETTINGS = {"ai_enabled": True, "ai_work_detection": True,
            "ai_endpoint": "http://127.0.0.1:11434", "ai_model": "fixture-local-model"}
WORD = {"kind": "process", "pattern": "WINWORD.EXE", "label": "Microsoft Word", "enabled": True}
DOCS = {"kind": "url", "pattern": "https://docs.google.com/document/*", "label": "Google Docs", "enabled": True}


def window(title="Untitled report - Google Chrome", process="chrome.exe", handle=10):
    return {"title": title, "process": process, "handle": handle, "pid": 1234}


def answer(category="writing", confidence=.95, classification="work"):
    return {"classification": classification, "category": category, "confidence": confidence}


class WorkRecognitionTests(unittest.TestCase):
    def setUp(self):
        self.settings = copy.deepcopy(SETTINGS)
        self.calls = []
        self.recognizers = []

    def tearDown(self):
        for recognizer in self.recognizers:
            recognizer.cancel()
            if recognizer.worker:
                recognizer.worker.join(2)

    def recognizer(self, infer=None, clock=None):
        def fixture(metadata, cancel):
            self.calls.append(copy.deepcopy(metadata))
            return answer()
        kwargs = {"infer": infer or fixture}
        if clock:
            kwargs["clock"] = clock
        recognizer = WorkRecognizer(lambda: dict(self.settings), **kwargs)
        self.recognizers.append(recognizer)
        return recognizer

    def resolved(self, recognizer, current=None, rules=(), **kwargs):
        current = current or window()
        first = recognizer.observe(current, rules, **kwargs)
        if recognizer.worker:
            recognizer.worker.join(2)
            self.assertFalse(recognizer.worker.is_alive(), "fixture classifier did not finish")
        return first, recognizer.observe(current, rules, **kwargs)

    def test_explicit_rule_is_immediate_and_never_queries_ai(self):
        recognizer = self.recognizer()
        result = recognizer.observe(window("Budget", "C:\\Office\\WINWORD.EXE"), [WORD])
        self.assertTrue(result["is_work"])
        self.assertEqual((result["source"], result["label"]), ("rule", "Microsoft Word"))
        self.assertIsNone(recognizer.worker)
        self.assertEqual(self.calls, [])

    def test_url_query_fragment_are_not_rule_input_or_identity(self):
        current = window("Google Docs")
        first = evaluate(current, [DOCS], "https://docs.google.com/document/d/doc-id?token=one#secret")
        second = evaluate(current, [DOCS], "https://docs.google.com/document/d/doc-id?token=two#other")
        self.assertTrue(first["is_work"])
        self.assertEqual(first["key"], second["key"])
        self.assertNotIn("token", json.dumps(first))

    def test_disabled_rule_beats_enabled_rule_and_cached_ai(self):
        disabled = dict(WORD, enabled=False)
        result = evaluate(window("Report", "WINWORD.EXE"), [WORD, disabled])
        self.assertFalse(result["is_work"])
        self.assertEqual(result["status"], "disabled")
        recognizer = self.recognizer()
        _, result = self.resolved(recognizer)
        self.assertTrue(result["is_work"])
        result = recognizer.observe(window(), [{"kind": "window", "pattern": "*report*", "enabled": False}])
        self.assertEqual(result["status"], "disabled")

    def test_disabled_docs_url_also_denies_title_fallback_without_extension(self):
        enabled_title = {"kind": "window", "pattern": "*Google Docs*", "label": "Docs", "enabled": True}
        result = evaluate(window("Assignment - Google Docs - Google Chrome"), [enabled_title, dict(DOCS, enabled=False)])
        self.assertEqual(result["status"], "disabled")

    def test_disabled_word_title_scope_denies_process_fallback(self):
        result = evaluate(window("Draft", "WINWORD.EXE"), [{"kind": "window", "pattern": "* - Word", "enabled": False}])
        self.assertEqual(result["status"], "disabled")

    def test_companion_is_excluded_even_with_explicit_rules(self):
        catch_all = [{"kind": "window", "pattern": "*", "label": "Work", "enabled": True}]
        for current, url in [
            (window("Project report", "Chuddy.exe"), None),
            (window("Chuddy · your very helpful friend", "python.exe"), None),
            (window("Report"), "http://127.0.0.1:8765/"),
        ]:
            with self.subTest(current=current, url=url):
                self.assertFalse(evaluate(current, catch_all, url)["is_work"])
                self.assertIn(evaluate(current, catch_all, url)["status"], {"nonwork", "excluded"})

    def test_gaming_and_user_opt_outs_take_precedence_over_enabled_rules(self):
        current = window("Report", "steam.exe")
        self.assertEqual(evaluate(current, [], None)["status"], "nonwork")
        lecture = {"kind": "url", "pattern": "https://www.youtube.com/watch", "label": "Lecture", "enabled": True}
        current, url = window("Lecture"), "https://www.youtube.com/watch?v=abc"
        self.assertTrue(evaluate(current, [lecture], url)["is_work"])
        self.assertEqual(evaluate(current, [lecture, dict(lecture, enabled=False)], url)["status"], "disabled")

    def test_unknown_and_generic_foreground_apps_are_work_without_ai(self):
        recognizer = self.recognizer()
        for current in [window("Just browsing"), window("Ordinary activity", "unknown.exe")]:
            result = recognizer.observe(current, [])
            self.assertTrue(result["is_work"])
            self.assertEqual(result['source'], 'policy')
        self.assertFalse(recognizer.observe(window('Report', ''), [], allow_ai=False)['is_work'])
        for current in ({}, {'process': '', 'title': ''}):
            self.assertFalse(recognizer.observe(current, [])['is_work'])
        self.assertEqual(self.calls, [])

    def test_safe_metadata_keeps_only_fixed_labels(self):
        current = window("Talia SSN 123-45-6789 secret@example.com password=letmein report - Google Chrome",
                         "C:\\Users\\Private\\chrome.exe")
        metadata, hints = sanitize_metadata(current, "https://docs.google.com/document/d/PRIVATE-ID/report-filename?auth=secret#name")
        serialized = json.dumps(metadata)
        self.assertEqual(metadata["process"], "chrome.exe")
        self.assertEqual(metadata["title_labels"], ["report"])
        self.assertEqual(metadata["path_labels"], ["document"])
        self.assertEqual(metadata["host_label"], "docs.google.com")
        self.assertIn("writing", hints)
        for forbidden in ["Talia", "123-45", "example", "letmein", "Private", "PRIVATE-ID", "filename", "auth", "secret"]:
            self.assertNotIn(forbidden, serialized)

    def test_unknown_host_and_process_names_are_not_sent(self):
        metadata, _ = sanitize_metadata(window("report", "private-person.exe"), "https://private-person.example.net/person/report?password=one")
        self.assertEqual(metadata["process"], "other_app")
        self.assertEqual(metadata["host_label"], "other_site")
        self.assertNotIn("private-person", json.dumps(metadata))

    def test_metadata_classifier_is_nonblocking_and_cached(self):
        entered, gate = threading.Event(), threading.Event()
        def inference(metadata, cancel):
            self.calls.append(metadata)
            entered.set()
            gate.wait(2)
            return answer()
        recognizer = self.recognizer(inference)
        try:
            first = recognizer.observe(window(), [])
            self.assertTrue(entered.wait(1))
            self.assertEqual(first["ai_status"], "pending")
            self.assertTrue(first["is_work"])
            self.assertEqual(first['source'], 'policy')
            self.assertEqual(recognizer.observe(window(), [])["ai_status"], "pending")
        finally:
            gate.set()
        recognizer.worker.join(2)
        result = recognizer.observe(window(), [], idle_seconds=.4)
        self.assertTrue(result["is_work"])
        self.assertEqual(result["source"], "local_ai")
        self.assertEqual(result["label"], "Writing work")
        self.assertEqual(result["activity"]["recent_input"], True)
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn("title", self.calls[0])

    def test_opened_work_is_recognizable_without_claiming_typing(self):
        recognizer = self.recognizer()
        _, result = self.resolved(recognizer, idle_seconds=120)
        self.assertTrue(result["is_work"])
        self.assertFalse(result["activity"]["recent_input"])
        self.assertNotIn("typing", result)

    def test_failed_low_confidence_uncertain_and_unjustified_results_keep_policy_work(self):
        for raw in [None, {}, answer(confidence=.84), answer(confidence=True), answer(confidence=math.nan),
                    answer(category="coding"), answer(classification="uncertain"),
                    dict(answer(), command="minimize"), answer(category="nonsense"),
                    {"classification": "work", "category": ["writing"], "confidence": .99}]:
            with self.subTest(raw=raw):
                recognizer = self.recognizer(lambda metadata, cancel: raw)
                _, result = self.resolved(recognizer)
                self.assertTrue(result["is_work"])
                self.assertEqual(result['source'], 'policy')

    def test_explicit_model_nonwork_is_reported(self):
        recognizer = self.recognizer(lambda metadata, cancel: answer(classification="nonwork", category='gaming'))
        _, result = self.resolved(recognizer)
        self.assertEqual(result["status"], "nonwork")

    def test_transport_failure_keeps_honest_policy_default(self):
        def fail(metadata, cancel):
            raise TimeoutError("sensitive title must not enter diagnostics")
        recognizer = self.recognizer(fail)
        _, result = self.resolved(recognizer)
        self.assertTrue(result["is_work"])
        self.assertEqual((result["source"], result["ai_status"], result['ai_error']), ("policy", "unavailable", "TimeoutError"))
        self.assertNotIn("sensitive", json.dumps(recognizer.info()))

    def test_ai_off_and_busy_never_launch_inference_but_explicit_rules_work(self):
        for setting in ("ai_enabled", "ai_work_detection"):
            recognizer = self.recognizer()
            self.settings[setting] = False
            result = recognizer.observe(window(), [])
            self.assertEqual(result["ai_status"], "disabled")
            self.assertTrue(result['is_work'])
            self.assertEqual(result['source'], 'policy')
            self.assertTrue(recognizer.observe(window("Report", "WINWORD.EXE"), [WORD])["is_work"])
            self.settings[setting] = True
        recognizer = self.recognizer()
        self.assertEqual(recognizer.observe(window(), [], allow_ai=False)["ai_status"], "deferred")
        self.assertEqual(self.calls, [])

    def test_stop_cancels_late_result_and_clears_cache(self):
        entered, gate = threading.Event(), threading.Event()
        def inference(metadata, cancel):
            entered.set()
            gate.wait(2)
            return answer()
        recognizer = self.recognizer(inference)
        recognizer.observe(window(), [])
        try:
            self.assertTrue(entered.wait(1))
            recognizer.cancel()
            self.assertTrue(recognizer.cancel_event.is_set())
        finally:
            gate.set()
        recognizer.worker.join(2)
        self.assertEqual(recognizer.cache, {})
        self.assertEqual(recognizer.info()["status"], "idle")

    def test_old_window_result_is_not_reused_for_changed_title_or_handle(self):
        recognizer = self.recognizer()
        _, original = self.resolved(recognizer)
        for current in [window(handle=20), window("Other report - Google Chrome")]:
            result = recognizer.observe(current, [])
            self.assertNotEqual(result["key"], original["key"])
            self.assertEqual(result["ai_status"], "pending")
            recognizer.worker.join(2)

    def test_cache_expires_and_model_change_requires_new_classification(self):
        now = [100.0]
        recognizer = self.recognizer(clock=lambda: now[0])
        self.resolved(recognizer)
        now[0] += 31
        first, _ = self.resolved(recognizer)
        self.assertEqual(first["ai_status"], "pending")
        self.settings["ai_model"] = "other-selected-model"
        first, _ = self.resolved(recognizer)
        self.assertEqual(first["ai_status"], "pending")
        self.assertEqual(len(self.calls), 3)

    def test_invalid_urls_never_queue_inference(self):
        recognizer = self.recognizer()
        for url in ["file:///private/report", "https://user:password@docs.google.com/document/1",
                    "https://docs.google.com:bad/document/1", "https://[bad/"]:
            self.assertEqual(recognizer.observe(window(), [], url=url)["status"], "excluded")
        self.assertEqual(self.calls, [])

    def test_activity_only_reports_generic_input_recency(self):
        for idle in [None, True, -1, math.inf, "typing"]:
            self.assertIsNone(evaluate(window(), [], idle_seconds=idle)["activity"]["recent_input"])
        self.assertEqual(windows._input_tick_age(1000, 0xffffff00), 1.256)
        self.assertEqual(windows._input_tick_age(1500, 1000), .5)
        with patch.object(windows.os, "name", "posix"):
            self.assertIsNone(windows.input_idle_seconds())

    def test_default_local_transport_is_bounded_and_uses_only_selected_model(self):
        recognizer = WorkRecognizer(lambda: dict(self.settings))
        self.recognizers.append(recognizer)
        content = json.dumps({"message": {"content": json.dumps(answer())}}).encode()
        with patch("chuddy.work_recognition.local_request", return_value=io.BytesIO(content)) as request:
            _, result = self.resolved(recognizer)
        self.assertTrue(result["is_work"])
        endpoint, route, payload = request.call_args.args
        self.assertEqual((endpoint, route), (SETTINGS["ai_endpoint"], "/api/chat"))
        self.assertEqual(payload["model"], SETTINGS["ai_model"])
        self.assertFalse(payload["stream"])
        self.assertEqual(payload["keep_alive"], 120)
        self.assertEqual(payload["options"]["num_ctx"], 4096)
        self.assertLessEqual(payload["options"]["num_predict"], 96)
        self.assertEqual(request.call_args.kwargs["timeout"], 12)
        self.assertNotIn("tools", payload)
        self.assertNotIn("Untitled report", json.dumps(payload))

    def test_model_not_ready_defers_classifier_and_keeps_work_policy(self):
        recognizer = self.recognizer()
        recognizer.model_ready = lambda: False
        result = recognizer.observe(window(), [])
        self.assertTrue(result["is_work"])
        self.assertEqual(result["ai_status"], "deferred")
        self.assertEqual(self.calls, [])

    def test_remote_endpoint_and_oversized_response_are_rejected(self):
        for endpoint, body in [("https://paid.example.com", b"{}"),
                               (SETTINGS["ai_endpoint"], b" " * 16385)]:
            recognizer = WorkRecognizer(lambda: dict(self.settings, ai_endpoint=endpoint))
            self.recognizers.append(recognizer)
            with patch("chuddy.work_recognition.local_request", return_value=io.BytesIO(body)) as request:
                _, result = self.resolved(recognizer)
            self.assertEqual(result["ai_status"], "unavailable")
            self.assertEqual(result['source'], 'policy')
            self.assertTrue(result['is_work'])
            if endpoint.startswith("https"):
                request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
