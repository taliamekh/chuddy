"""Broad activity policy integration; no real apps, models or native controls."""
import time
from unittest.mock import Mock
from tests.helpers import ControllerCase

WORK={'is_work':True,'status':'work','source':'policy','reason':'foreground_default','label':'Foreground activity'}
GAMING={'is_work':False,'status':'nonwork','source':'policy','reason':'excluded_gaming','label':''}


class BroadActivityFlowTests(ControllerCase):
    def window(self,title='Mechanical design',process='SLDWORKS.exe'):
        return {'handle':42,'pid':123,'title':title,'process':process}

    def use(self,window):
        from chuddy.controller import windows
        windows.foreground.return_value=window
        return windows

    def test_unlisted_authoring_app_works_without_rules_or_available_model(self):
        self.store.update_settings({'rules':[],'ai_enabled':False})
        self.use(self.window())
        self.controller.distractions.delay=0
        self.controller.set_session('start')
        self.controller.poll_work()
        self.assertIsNotNone(self.controller.pending_work)
        self.assertEqual(self.controller.work_detection['source'],'policy')
        self.now+=4.1
        self.controller.poll_work()
        self.assertTrue(self.controller.distractions.wait())
        self.assertEqual(self.controller.interruption['phase'],'opened')
        self.assertEqual(self.covered,[True])

    def test_gaming_exclusion_overrides_generic_work_rule(self):
        self.store.update_settings({'rules':[{'kind':'window','pattern':'*','label':'Anything','enabled':True}],'ai_enabled':False})
        self.controller.set_session('start')
        for window in [self.window('Fortnite','FortniteClient-Win64-Shipping.exe'), self.window('Report', 'steam.exe')]:
            with self.subTest(process=window['process'],title=window['title']):
                self.use(window)
                self.controller.poll_work()
                self.assertIsNone(self.controller.pending_work)
                self.assertFalse(self.controller.work_detection['is_work'])
        self.assertEqual(self.controller.agent.calls,[])

    def test_fresh_negative_classification_cancels_pending_before_mature_fallback(self):
        self.use(self.window())
        self.controller.work_recognizer.observe=Mock(side_effect=[dict(WORK),dict(GAMING)])
        self.controller.set_session('start')
        self.controller.poll_work()
        self.assertIsNotNone(self.controller.pending_work)
        self.controller.interrupt=Mock(return_value=True)
        self.now+=4.1
        self.controller.poll_work()
        self.controller.interrupt.assert_not_called()
        self.assertIsNone(self.controller.pending_work)
        self.assertTrue(self.controller.agent.cancel_event.is_set())

    def test_late_negative_also_denies_direct_tool_admission(self):
        self.use(self.window())
        self.controller.work_recognizer.observe=Mock(side_effect=[dict(WORK),dict(GAMING)])
        self.controller.set_session('start')
        self.controller.poll_work()
        self.assertFalse(self.controller.interrupt('Foreground activity'))
        self.assertIsNone(self.controller.interruption)
        self.assertIsNone(self.controller.pending_work)

    def test_negative_classification_of_same_source_cancels_queued_native_approach(self):
        self.use(self.window('Study - Google Chrome','chrome.exe'))
        self.controller.native_browser_tabs.available=True
        self.controller.work_recognizer.observe=Mock(side_effect=[dict(WORK),dict(WORK),dict(GAMING)])
        self.controller.distractions.delay=5
        self.controller.set_session('start')
        self.controller.poll_work()
        self.assertTrue(self.controller.interrupt('Foreground activity'))
        self.assertTrue(self.controller.native_browser_tabs.prepared.wait(1))
        self.controller.poll_work()
        self.assertTrue(self.controller.distractions.wait())
        self.assertIsNone(self.controller.interruption)
        self.assertEqual(self.controller.native_browser_tabs.targets,[])
        self.assertEqual(self.covered,[])

    def test_own_tab_close_revealing_entertainment_does_not_cancel_successful_casino_open(self):
        original=self.window('Study - Google Chrome','chrome.exe')
        windows=self.use(original)
        self.controller.native_browser_tabs.available=True
        self.controller.work_recognizer.observe=Mock(side_effect=[dict(WORK),dict(WORK),dict(GAMING)])
        self.controller.distractions.delay=0
        self.controller.set_session('start')
        self.controller.poll_work()
        self.assertTrue(self.controller.interrupt('Foreground activity'))
        self.assertTrue(self.controller.native_browser_tabs.requested.wait(1))
        windows.foreground.return_value={**original,'title':'Minecraft - Google Chrome'}
        self.controller.poll_work()
        self.assertFalse(self.controller.native_browser_tabs.close_future.done())
        self.controller.native_browser_tabs.close_future.set_result(True)
        self.assertTrue(self.controller.distractions.wait())
        self.assertEqual(self.controller.interruption['phase'],'opened')
        self.assertEqual(self.covered,[True])
