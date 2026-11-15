"""Retail is ordinary foreground activity and still respects explicit exclusions."""
import unittest

from chuddy.work_recognition import WorkRecognizer, evaluate


WINDOW = {"handle": 10, "pid": 42, "title": "Cart - Local Shop", "process": "chrome.exe"}
CART = "https://local-shop.example/cart?private=value"


class ShoppingActivityTests(unittest.TestCase):
    def test_retail_has_no_automatic_exemption_under_the_broad_policy(self):
        self.assertTrue(evaluate(WINDOW, [], CART)["is_work"])
        self.assertTrue(evaluate(dict(WINDOW, process="WinStore.App.exe"), [])["is_work"])

    def test_explicit_disabled_retail_url_is_still_authoritative(self):
        disabled = {"kind": "url", "pattern": "https://local-shop.example/cart", "enabled": False}
        self.assertEqual(evaluate(WINDOW, [disabled], CART)["reason"], "disabled_rule")

    def test_old_model_shopping_exception_cannot_override_the_new_policy(self):
        recognizer = WorkRecognizer(lambda: {"ai_enabled": True},
            infer=lambda metadata, cancel: {"classification": "nonwork", "category": "shopping", "confidence": .98})
        window = dict(WINDOW, title="Report - Local Shop")
        try:
            self.assertTrue(recognizer.observe(window, [], CART)["is_work"])
            recognizer.worker.join(1)
            self.assertFalse(recognizer.worker.is_alive())
            self.assertTrue(recognizer.observe(window, [], CART, allow_ai=False)["is_work"])
        finally:
            recognizer.cancel()
