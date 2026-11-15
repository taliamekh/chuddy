"""Fast varied work lines; fixtures never use real UI, models or audio."""
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
import random
import threading
import time
import unittest

from chuddy.desktop_overlay import APPROACH_SECONDS, DesktopOverlay
from chuddy.interception import InterceptionCoordinator
from chuddy.persona import CONVERSATION_MODEL, conversation_context, system_for
from chuddy.work_banter import BANTER_SOURCE, WORK_BANTER_VARIANTS, WorkBanter
from tests.test_interception import FakeWindows
from tests.test_desktop_overlay import Surface


class WorkBanterTests(unittest.TestCase):
    def test_all_combinations_are_used_before_exact_repeat_and_no_cycle_boundary_repeat(self):
        selector = WorkBanter(random.Random(12))
        cycle = [selector.choose() for _ in WORK_BANTER_VARIANTS]
        self.assertEqual(len(cycle), len(set(cycle)))
        self.assertEqual(set(cycle), set(WORK_BANTER_VARIANTS))
        self.assertNotEqual(selector.choose(), cycle[-1])

    def test_saved_profile_history_is_respected_after_selector_restart(self):
        old = WorkBanter(random.Random(1))
        history = [old.choose() for _ in range(20)]
        restarted = WorkBanter(random.Random(1))
        choices = [restarted.choose(recent=history) for _ in range(60)]
        self.assertEqual(len(set(choices)), 60)
        self.assertFalse(set(choices) & set(history))

    def test_recent_lines_leads_and_punchlines_are_varied(self):
        selector = WorkBanter(random.Random(7))
        choices = [selector.choose() for _ in range(12)]
        self.assertEqual(len({line.split('. ', 1)[-1] for line in choices}), 12)
        for previous, current in zip(choices, choices[1:]):
            self.assertNotEqual(previous.split('. ', 1)[0], current.split('. ', 1)[0])

    def test_concurrent_selection_does_not_duplicate_an_unexhausted_line(self):
        selector = WorkBanter(random.Random(2))
        with ThreadPoolExecutor(max_workers=8) as pool:
            lines = list(pool.map(lambda _: selector.choose(), range(len(WORK_BANTER_VARIANTS))))
        self.assertEqual(len(lines), len(set(lines)))

    def test_short_scripted_lines_never_interpolate_titles_urls_or_claim_model_output(self):
        selector = WorkBanter(random.Random(3))
        self.assertEqual(BANTER_SOURCE, 'scripted work banter')
        self.assertTrue(all(len(line) <= 130 and len(line.split()) < 30 for line in WORK_BANTER_VARIANTS))
        text = selector.choose('<script>private title</script>', 'https://private.example/')
        self.assertNotIn('private', text)
        self.assertNotIn('<', text)

    def test_observation_novelty_context_is_bounded_and_leaves_authoritative_facts_intact(self):
        original = {'work':'Report', 'session':'running', 'deadlines':[{'due':'2026-11-10T12:00:00Z'}],
                    'recent_work_lines':['old']*10, 'scores':{'total':7}, 'mock_balance':20}
        selected = conversation_context(original, 'Work', observation=True, model=CONVERSATION_MODEL)
        self.assertEqual(len(selected['recent_work_lines']), 8)
        self.assertNotIn('deadlines', selected)
        self.assertEqual(original['deadlines'], [{'due':'2026-11-10T12:00:00Z'}])
        self.assertEqual(original['scores']['total'], 7)
        prompt = system_for('Report', CONVERSATION_MODEL, observation=True)
        self.assertIn('Never delay the tool call', prompt)
        self.assertIn('Do not repeat or lightly', prompt)


class BanterOverlay:
    def __init__(self):
        self.lines = []
    def perform(self, window, destination, message=None):
        self.lines.append(message)
    def roam(self):
        pass
    def disarm(self):
        pass
    def close(self):
        pass


class FastInterceptionBanterTests(unittest.TestCase):
    def setUp(self):
        self.window = FakeWindows()
        self.overlay = BanterOverlay()
        self.spoken, self.opened = [], []
        self.tts = Future()
        self.spoke = threading.Event()
        def speak(line):
            self.spoken.append(line)
            self.spoke.set()
            return self.tts  # Unfinished synthesis/playback must never delay action.
        self.manager = InterceptionCoordinator(allowed=lambda: True, windows=self.window,
            overlay=self.overlay, speak=speak, open_local=self.opened.append,
            warning_seconds=0, clock=lambda:100., banter_selector=WorkBanter(random.Random(1)).choose)

    def tearDown(self):
        self.manager.close()
        self.assertTrue(self.manager.wait())

    def request(self, **options):
        return self.manager.request('Private report', window=dict(self.window.current), seen=100., **options)

    def test_fast_defaults_keep_spoken_line_without_an_approach_bubble(self):
        self.assertEqual(self.manager.delay, .45)
        self.assertLessEqual(APPROACH_SECONDS, self.manager.delay)
        self.assertLess(APPROACH_SECONDS, .5)
        self.manager.delay = 0
        self.assertTrue(self.request()['ok'])
        self.assertTrue(self.manager.wait())
        self.assertFalse(self.tts.done())
        self.assertEqual(len(self.spoken), 1)
        self.assertEqual(self.overlay.lines, [None])
        self.assertEqual(self.manager.info()['banter_message'], self.spoken[0])
        self.assertEqual(self.manager.info()['banter_source'], BANTER_SOURCE)
        self.assertEqual(self.opened, ['casino'])

    def test_silent_preview_consumes_no_line_or_speech(self):
        self.manager.delay = 0
        self.assertTrue(self.request(test=True)['ok'])
        self.assertTrue(self.manager.wait())
        self.assertEqual(self.spoken, [])
        self.assertNotIn('banter_message', self.manager.info())

    def test_native_preflight_and_close_ack_remain_authoritative_with_fast_banter(self):
        self.window.current['process'] = 'chrome.exe'
        preparation, closed = Future(), Future()
        self.manager.prepare_tab = lambda *args: preparation
        self.manager.close_tab = lambda *args: closed
        self.manager.delay = 0
        descriptor = {'kind':'native', 'window':dict(self.window.current)}
        self.assertTrue(self.request(tab_target=descriptor)['ok'])
        self.assertEqual(self.manager.info()['phase'], 'preparing')
        self.assertEqual(self.spoken, [])
        preparation.set_result({**descriptor, 'close_point':[200,30]})
        self.assertTrue(self.spoke.wait(1))
        self.assertEqual(self.opened, [])
        closed.set_result(True)
        self.assertTrue(self.manager.wait())
        self.assertEqual(self.opened, ['casino'])
        self.assertEqual(len(self.spoken), 1)

    def test_cancelled_preflight_never_begins_banter_or_action(self):
        self.window.current['process'] = 'chrome.exe'
        future = Future()
        self.manager.prepare_tab = lambda *args: future
        self.assertTrue(self.request(tab_target={'kind':'native','window':dict(self.window.current)})['ok'])
        self.manager.stop()
        future.set_result({'kind':'native','close_point':[200,30]})
        self.assertTrue(self.manager.wait())
        self.assertEqual(self.spoken, [])
        self.assertEqual(self.opened, [])

    def test_real_overlay_queue_draws_approach_without_a_message(self):
        class MessageSurface(Surface):
            def set_message(self, text):
                self.message = text
        surface = MessageSurface(Path('unused'))
        overlay = DesktopOverlay('unused.png', factory=lambda _:surface)
        try:
            overlay.perform({'handle':42,'tab_close_point':[200,30]}, 'casino')
            self.assertTrue(surface.drawn.wait(1))
            self.assertIsNone(surface.message)
            self.assertEqual(overlay.info()['activity'], 'approaching')
        finally:
            overlay.close()
