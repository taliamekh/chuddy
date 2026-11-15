"""Native work-tab behavior with fake UIA/window providers; no user UI access."""
from copy import deepcopy
from types import SimpleNamespace as NS
import threading
import time
import unittest

from chuddy.native_browser_tabs import (NativeBrowserTabs, NativeTabUnavailable,
    NativeRuntimeUnavailable, NATIVE_BROWSER_PROCESSES, _UIATree)


def row(identity, name='Work', selected=False):
    return {'id': (identity,), 'name': name, 'selected': selected, 'selectable': True,
            'close': [{'id': (identity + 100,), 'point': [300 + identity, 30]}]}


class FakeWindow:
    def __init__(self):
        self.current = {'handle': 42, 'pid': 123, 'process': 'chrome.exe', 'title': 'Work - Google Chrome'}
        self.reads = 0

    def window_owner(self, handle):
        return (123, 999) if self.current and self.current['handle'] == handle else None

    def foreground(self):
        self.reads += 1
        return dict(self.current) if self.current else None


class FakeProvider:
    def __init__(self, window):
        self.window = window
        self.tabs = [row(1, selected=True), row(2, 'Other')]
        self.root_id = (42, 123)
        self.address = 'https://docs.google.com/document/report'
        self.effects, self.snapshots, self.closed = [], 0, 0
        self.before_close = lambda: None
        self.after_close = lambda: None
        self.block, self.entered = None, threading.Event()
        self.keep_original, self.window_gone = False, False

    def start(self):
        pass

    def close(self):
        self.closed += 1

    def snapshot(self, window):
        self.snapshots += 1
        self.entered.set()
        if self.block:
            self.block.wait(2)
        if self.window_gone:
            raise NativeTabUnavailable('The original browser window is no longer available.')
        return {'root_id': self.root_id, 'title': self.window.current['title'],
                'tabs': deepcopy(self.tabs), 'address': _UIATree.address(self.address or '')}

    def invoke_close(self, window, identity, button, guard):
        self.before_close()
        guard()
        self.effects.append(('close', tuple(identity), tuple(button)))
        if not self.keep_original:
            self.tabs = [tab for tab in self.tabs if tuple(tab['id']) != tuple(identity)]
            if self.tabs:
                self.tabs[0]['selected'] = True
                self.window.current['title'] = self.tabs[0]['name'] + ' - Google Chrome'
            else:
                self.window.current = None
        self.after_close()


class NativeTabsTests(unittest.TestCase):
    def setUp(self):
        self.window = FakeWindow()
        self.provider = FakeProvider(self.window)
        self.made = 0
        def factory():
            self.made += 1
            return self.provider
        self.tabs = NativeBrowserTabs(factory, self.window, prepare_timeout=.8,
                                      action_timeout=.3, poll_interval=.005)
        self.valid, self.live = True, True

    def tearDown(self):
        self.tabs.close()
        if self.provider.block:
            self.provider.block.set()

    def target(self):
        return self.tabs.prepare(dict(self.window.current))

    def request(self, target):
        return self.tabs.request(target, lambda: self.valid, lambda: self.live).result(1)

    def wait_idle(self):
        deadline = time.monotonic() + 1
        while self.tabs.info()['busy'] and time.monotonic() < deadline:
            time.sleep(.002)
        self.assertFalse(self.tabs.info()['busy'])

    def test_construction_and_info_never_inspect_or_start_provider(self):
        self.assertEqual(self.made, 0)
        self.assertEqual(self.window.reads, 0)
        self.assertEqual(self.provider.snapshots, 0)
        info = self.tabs.info()
        self.assertTrue(info['available'])
        self.assertFalse(info['ready'])
        self.assertNotIn('42', str(info))

    def test_preparation_is_read_only_and_close_uses_exact_tab_then_confirms_survival(self):
        target = self.target()
        self.assertEqual(target['kind'], 'native')
        self.assertEqual(target['tab_id'], (1,))
        self.assertEqual(target['close_point'], [301, 30])
        self.assertEqual(self.provider.effects, [])
        self.assertTrue(self.tabs.info()['ready'])
        self.assertTrue(self.request(target))
        self.assertEqual(self.provider.effects, [('close', (1,), (101,))])
        self.assertEqual([tab['id'] for tab in self.provider.tabs], [(2,)])
        self.assertIsNotNone(self.window.current)

    def test_preparation_retries_when_browser_selection_is_still_settling(self):
        original = self.provider.snapshot
        def settling_snapshot(window):
            result = original(window)
            if self.provider.snapshots == 1:
                result['tabs'][1]['selected'] = True
            return result
        self.provider.snapshot = settling_snapshot
        self.assertEqual(self.target()['tab_id'], (1,))
        self.assertGreaterEqual(self.provider.snapshots, 2)
        self.assertEqual(self.provider.effects, [])

    def test_last_tab_gets_only_its_exact_close_and_frame_exits_naturally(self):
        self.provider.tabs = [row(1, selected=True)]
        self.assertTrue(self.request(self.target()))
        self.assertEqual(self.provider.effects, [('close', (1,), (101,))])
        self.assertIsNone(self.window.current)

    def test_native_address_policy_denies_gaming_and_disabled_urls(self):
        from chuddy.work_recognition import evaluate
        rules = [{'kind': 'url', 'pattern': 'http://docs.google.com/document/report', 'enabled': False}]
        for address in ('docs.google.com/document/report', 'https://lichess.org/game'):
            with self.subTest(address=address):
                self.provider.address = address
                future = self.tabs.prepare_async(self.window.current,
                    policy=lambda window, url: evaluate(window, rules, url=url)['is_work'])
                with self.assertRaisesRegex(NativeTabUnavailable, 'excluded'):
                    future.result(1)
                self.assertEqual(self.provider.effects, [])

    def test_same_title_changed_path_or_query_cannot_close_prepared_tab(self):
        for address in ('https://docs.google.com/document/other', 'https://docs.google.com/document/report?changed=1'):
            with self.subTest(address=address):
                self.provider.address = 'https://docs.google.com/document/report'
                target = self.target()
                self.provider.address = address
                with self.assertRaisesRegex(NativeTabUnavailable, 'address changed'):
                    self.request(target)
                self.assertEqual(self.provider.effects, [])

    def test_wrong_window_pid_title_active_tab_root_or_close_identity_never_closes(self):
        mutations = [
            lambda: self.window.current.update(handle=43),
            lambda: self.window.current.update(pid=999),
            lambda: self.window.current.update(title='Different - Google Chrome'),
            lambda: [tab.update(selected=not tab['selected']) for tab in self.provider.tabs],
            lambda: setattr(self.provider, 'root_id', (99,)),
            lambda: self.provider.tabs[0]['close'][0].update(id=(700,)),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.window.current = {'handle':42,'pid':123,'process':'chrome.exe','title':'Work - Google Chrome'}
                self.provider.tabs, self.provider.root_id = [row(1, selected=True), row(2, 'Other')], (42, 123)
                target = self.target()
                mutation()
                with self.assertRaises(NativeTabUnavailable):
                    self.request(target)
                self.assertEqual(self.provider.effects, [])

    def test_protected_or_unknown_address_cannot_close_even_with_an_unrelated_title(self):
        for host in ('chatgpt.com', 'chat.openai.com', 'discord.com', 'discordapp.com', 'discord.gg', None):
            with self.subTest(host=host):
                self.provider.address = host
                with self.assertRaises(NativeTabUnavailable):
                    self.target()
        for host in ('chatgpt.com', 'discord.com'):
            with self.subTest(host=host):
                self.provider.address = 'docs.google.com'
                target = self.target()
                self.provider.before_close = lambda: setattr(self.provider, 'address', host)
                with self.assertRaisesRegex(NativeTabUnavailable, 'protected'):
                    self.request(target)
        self.assertEqual(self.provider.effects, [])

    def test_ambiguous_missing_and_invisible_close_or_selected_tab_are_denied(self):
        variants = [
            [row(1, selected=True), row(2, selected=True)],
            [row(1)],
            [{**row(1, selected=True), 'close': []}, row(2)],
            [{**row(1, selected=True), 'close': [{'id':(10,), 'point':[1,1]}, {'id':(11,), 'point':[2,2]}]}, row(2)],
            [{**row(1, selected=True), 'close': [{'id':(10,), 'point':None}]}, row(2)],
        ]
        for variant in variants:
            self.provider.tabs = variant
            with self.assertRaises(NativeTabUnavailable):
                self.target()
        self.assertEqual(self.provider.effects, [])

    def test_unknown_browser_and_expired_target_are_denied(self):
        self.assertNotIn('firefox.exe', NATIVE_BROWSER_PROCESSES)
        original = dict(self.window.current)
        self.window.current['process'] = 'firefox.exe'
        with self.assertRaises(NativeTabUnavailable):
            self.target()
        self.window.current = original
        target = self.target()
        target['prepared_at'] -= 13
        with self.assertRaises(NativeTabUnavailable):
            self.request(target)
        self.assertEqual(self.provider.effects, [])

    def test_cancel_during_button_resolution_prevents_the_close(self):
        self.provider.before_close = self.tabs.cancel
        self.assertFalse(self.request(self.target()))
        self.wait_idle()
        self.assertEqual(self.provider.effects, [])

    def test_cancelled_preparation_and_late_worker_cannot_begin_actions(self):
        self.provider.block = threading.Event()
        future = self.tabs.prepare_async(dict(self.window.current), lambda: True, lambda: True)
        self.assertTrue(self.provider.entered.wait(1))
        self.tabs.cancel()
        self.assertIsNone(future.result(.1))
        self.assertTrue(self.tabs.info()['busy'])
        self.provider.block.set()
        self.wait_idle()
        self.assertEqual(self.provider.effects, [])
        self.assertEqual(self.tabs.info()['status'], 'cancelled')
        self.assertFalse(self.tabs.info()['ready'])

    def test_blocked_prepare_returns_promptly_and_timeout_does_not_hold_status_lock(self):
        self.tabs.prepare_timeout = .04
        self.provider.block = threading.Event()
        started = time.monotonic()
        future = self.tabs.prepare_async(dict(self.window.current))
        self.assertLess(time.monotonic() - started, .1)
        self.assertTrue(self.provider.entered.wait(1))
        self.assertTrue(self.tabs.info()['busy'])
        with self.assertRaises(NativeTabUnavailable):
            future.result(.2)
        self.provider.block.set()
        self.wait_idle()
        self.assertEqual(self.provider.effects, [])
        self.assertEqual(self.tabs.info()['status'], 'unavailable')

    def test_sibling_disappearance_during_button_resolution_does_not_close_last_tab(self):
        target = self.target()
        self.provider.before_close = lambda: setattr(self.provider, 'tabs', self.provider.tabs[:1])
        with self.assertRaises(NativeTabUnavailable):
            self.request(target)
        self.assertEqual(self.provider.effects, [])

    def test_beforeunload_or_window_disappearance_never_acknowledges_success(self):
        target = self.target()
        self.provider.keep_original = True
        with self.assertRaises(NativeTabUnavailable):
            self.request(target)
        self.wait_idle()
        self.provider.keep_original = False
        self.provider.after_close = lambda: setattr(self.provider, 'window_gone', True)
        with self.assertRaises(NativeTabUnavailable):
            self.request(target)

    def test_runtime_failure_disables_available_without_exposing_native_details(self):
        def broken():
            raise NativeRuntimeUnavailable('Windows UI Automation could not load in the local runtime.')
        self.tabs.provider_factory = broken
        with self.assertRaises(NativeRuntimeUnavailable):
            self.target()
        self.assertFalse(self.tabs.info()['available'])
        self.assertEqual(self.provider.effects, [])


class FakeElement:
    def __init__(self, identity, kind, name='', children=(), selected=False, enabled=True,
                 offscreen=False, invoke=True, rect=(0, 0, 20, 20)):
        self.identity, self.children, self.selected, self.invoke = (identity,), list(children), selected, invoke
        self.Current = NS(ControlType=kind, Name=name, IsEnabled=enabled, IsOffscreen=offscreen,
                          BoundingRectangle=NS(X=rect[0], Y=rect[1], Width=rect[2], Height=rect[3]))
        self.parent = None
        for child in self.children:
            child.parent = self

    def GetRuntimeId(self):
        return self.identity

    def GetCurrentPattern(self, pattern):
        if pattern == 'selection' and self.Current.ControlType == 'tab':
            return NS(Current=NS(IsSelected=self.selected))
        if pattern == 'invoke' and self.invoke and self.Current.ControlType == 'button':
            return NS(Invoke=lambda: None)
        raise RuntimeError('Fake pattern absent')


class FakeWalker:
    @staticmethod
    def GetFirstChild(element):
        return element.children[0] if element.children else None

    @staticmethod
    def GetNextSibling(element):
        if element.parent is None:
            return None
        siblings = element.parent.children
        index = siblings.index(element) + 1
        return siblings[index] if index < len(siblings) else None


class UIATreeTests(unittest.TestCase):
    def setUp(self):
        self.types = NS(Document='document', TabItem='tab', Button='button')
        self.tree = _UIATree(FakeWalker(), self.types, 'invoke', 'selection')

    def test_only_browser_tab_descendant_close_is_selected_not_frame_or_web_aria_tab(self):
        real_close = FakeElement(11, 'button', 'Close tab')
        real_tab = FakeElement(10, 'tab', 'Work', [real_close], selected=True)
        frame_close = FakeElement(12, 'button', 'Close')
        page_tab = FakeElement(20, 'tab', 'Page tab', [FakeElement(21, 'button', 'Close')], selected=True)
        document = FakeElement(19, 'document', 'Private page contents', [page_tab])
        new = FakeElement(30, 'button', 'New tab (Ctrl+T)')
        root = FakeElement(1, 'window', 'Work - Google Chrome', [frame_close, real_tab, new, document])
        data = self.tree.describe(root)
        self.assertEqual([tab['id'] for tab in data['tabs']], [(10,)])
        self.assertEqual(data['tabs'][0]['close'], [{'id':(11,), 'point':[10,10]}])
        with self.assertRaises(NativeTabUnavailable):
            self.tree.resolve(root, (12,), 'close', (10,))
        with self.assertRaises(NativeTabUnavailable):
            self.tree.resolve(root, (21,), 'close', (20,))

    def test_disabled_offscreen_noninvoke_and_other_close_commands_are_not_candidates(self):
        children = [FakeElement(2, 'button', 'Close', enabled=False),
                    FakeElement(3, 'button', 'Close', offscreen=True),
                    FakeElement(4, 'button', 'Close', invoke=False),
                    FakeElement(5, 'button', 'Close other tabs')]
        root = FakeElement(1, 'window', children=[FakeElement(10, 'tab', children=children, selected=True)])
        self.assertEqual(self.tree.describe(root)['tabs'][0]['close'], [])
