"""Google Docs foreground detection without installing a browser extension."""
from unittest.mock import patch
from tests.helpers import ControllerCase
from chuddy.controller import match_rule


class ChromeDocsDetectionTests(ControllerCase):
    def window(self, title='Scratch - Google Docs - Google Chrome'):
        return {'handle':42,'pid':123,'process':'chrome.exe','title':title}

    def test_chrome_docs_document_matches_without_extension_report(self):
        self.controller.set_session('start')
        with patch('chuddy.controller.windows.foreground',return_value=self.window()):
            self.controller.poll_work()
        self.assertIsNone(self.controller.browser)
        self.assertEqual(self.controller.pending_work['label'],'Google Docs in Chrome')
        self.assertTrue(self.controller.agent.calls[-1][1])

    def test_docs_without_paired_tab_identity_never_minimizes_browser_window(self):
        self.store.setting('interception','minimize')
        self.controller.distractions.delay=0
        self.controller.set_session('start')
        with patch('chuddy.controller.windows.foreground',return_value=self.window()):
            self.controller.poll_work()
            self.controller.agent.state['status']='idle'
            self.now+=3
            self.controller.poll_work()
            self.assertTrue(self.controller.distractions.wait())
            self.assertIsNone(self.controller.interruption)
            self.assertFalse(self.controller.state()['browser_tab_control']['ready'])
            self.assertIsNotNone(self.controller.pending_work)
            self.patches[-1].target.minimize.assert_not_called()
            self.controller.set_session('stop')
            self.patches[-1].target.restore.assert_not_called()

    def test_broad_detection_covers_other_work_tabs_and_keeps_explicit_disabled_override(self):
        self.controller.set_session('start')
        for title in ('Google Docs help - Google Chrome','Email - Google Chrome','Scratch - Google Sheets - Google Chrome'):
            with self.subTest(title=title), patch('chuddy.controller.windows.foreground',return_value=self.window(title)):
                self.controller.poll_work()
                self.assertIsNotNone(self.controller.pending_work)
        rules=self.store.settings()['rules']
        for rule in rules:
            if rule['label']=='Google Docs in Chrome':rule['enabled']=False
        self.store.setting('rules',rules)
        with patch('chuddy.controller.windows.foreground',return_value=self.window()):
            self.controller.poll_work()
        self.assertIsNone(self.controller.pending_work)

    def test_stopped_paused_and_left_document_do_not_interrupt(self):
        with patch('chuddy.controller.windows.foreground',return_value=self.window()):
            self.controller.poll_work()
            self.assertIsNone(self.controller.pending_work)
            self.controller.set_session('start');self.controller.poll_work()
            self.controller.set_session('pause');self.controller.poll_work()
            self.assertIsNone(self.controller.pending_work)
            self.controller.set_session('resume');self.controller.poll_work()
        with patch('chuddy.controller.windows.foreground',return_value=self.window('Lichess gameplay - Google Chrome')):
            self.controller.poll_work()
        self.assertIsNone(self.controller.pending_work)
        self.assertIsNone(self.controller.interruption)

    def test_monitoring_status_distinguishes_roaming_from_detection(self):
        self.controller.watcher_enabled=False
        self.assertFalse(self.controller.state()['monitoring']['desktop_enabled'])
        self.controller.watcher_enabled=True;self.controller.watcher_running=True
        self.assertEqual(self.controller.state()['monitoring'],{'desktop_enabled':True,'desktop_running':True})
