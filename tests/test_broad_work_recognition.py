"""Broad policy and semantic exclusions; synthetic metadata/mocked inference only."""
import io
import json
import threading
import unittest
from unittest.mock import patch

from chuddy.work_recognition import WorkRecognizer, evaluate, sanitize_metadata


SETTINGS = {'ai_enabled': True, 'ai_work_detection': True,
            'ai_endpoint': 'http://127.0.0.1:11434', 'ai_model': 'fixture-local-model'}
CATCH_ALL = [{'kind': 'window', 'pattern': '*', 'label': 'Report work', 'enabled': True}]


def window(title='Ordinary activity', process='unfamiliar.exe', **metadata):
    return dict(handle=10, pid=123, title=title, process=process, **metadata)


def decision(category='writing', classification='work', confidence=.95):
    return dict(classification=classification, category=category, confidence=confidence)


class BroadWorkRecognitionTests(unittest.TestCase):
    def setUp(self):
        self.recognizers = []

    def tearDown(self):
        for recognizer in self.recognizers:
            recognizer.cancel()
            if recognizer.worker:
                recognizer.worker.join(2)

    def recognizer(self, infer=None, settings=None):
        recognizer = WorkRecognizer(lambda: dict(SETTINGS, **(settings or {})), infer=infer)
        self.recognizers.append(recognizer)
        return recognizer

    def test_unfamiliar_foreground_activity_needs_no_work_allowlist(self):
        for current in (window(), window('New Tab - Google Chrome', 'chrome.exe'),
                        window('Project', 'novel-cad.exe'), window('Downloads', 'explorer.exe'),
                        window('Terminal', 'powershell.exe'), window('Script', 'python.exe')):
            with self.subTest(current=current):
                result = evaluate(current, [])
                self.assertTrue(result['is_work'])
                self.assertEqual((result['status'], result['source'], result['reason']),
                                 ('work', 'policy', 'foreground_default'))

    def test_chatgpt_and_codex_are_protected_before_work_overrides(self):
        for current, url in ((window('Project', 'Codex.exe'), None),
                             (window('Studying', 'ChatGPT.exe'), None),
                             (window('New chat', 'chrome.exe'), 'https://chatgpt.com/'),
                             (window('Other title', 'chrome.exe'), 'https://chat.openai.com/codex'),
                             (window('A conversation - ChatGPT - Google Chrome', 'chrome.exe'), None),
                             (window('Codex - Microsoft Edge', 'msedge.exe'), None)):
            with self.subTest(current=current, url=url):
                self.assertEqual(evaluate(current, CATCH_ALL, url)['reason'], 'protected_app')
        self.assertTrue(evaluate(window('Application', 'chrome.exe'), [], 'http://localhost:3000/report')['is_work'])
        self.assertTrue(evaluate(window('ChatGPT design report - Google Docs - Google Chrome', 'chrome.exe'), [])['is_work'])

    def test_only_chuddy_and_os_transients_are_self_exempt(self):
        cases = [window('Anything', 'Chuddy.exe'), window(is_chuddy=True),
                 window('Chuddy cursor', 'python.exe'), window('Start', 'StartMenuExperienceHost.exe'),
                 window('Search', 'SearchHost.exe'), window('Lock screen', 'LockApp.exe'),
                 window('Program Manager', 'explorer.exe'), window(is_transient=True)]
        for current in cases:
            with self.subTest(current=current):
                result = evaluate(current, CATCH_ALL)
                self.assertFalse(result['is_work'])
                self.assertEqual(result['status'], 'excluded')

    def test_gaming_evidence_precedes_work_hints_and_catch_all_rules(self):
        for current in (window('Assignment multiplayer lobby', 'novel.exe'),
                        window('Report - Nova', 'Nova-Win64-Shipping.exe'),
                        window('Report', 'UnknownGame.exe')):
            self.assertEqual(evaluate(current, CATCH_ALL)['reason'], 'excluded_gaming')
        current = window('Report', 'chrome.exe')
        self.assertEqual(evaluate(current, CATCH_ALL, 'https://play.crazygames.com/report')['reason'],
                         'excluded_gaming')

    def test_nongaming_entertainment_is_ordinary_foreground_activity(self):
        for process in ('Spotify.exe', 'WinStore.App.exe'):
            with self.subTest(process=process):
                self.assertTrue(evaluate(window('Report', process), [])['is_work'])
        for host in ('instagram.com', 'www.netflix.com', 'youtube.com', 'amazon.ca'):
            with self.subTest(host=host):
                self.assertTrue(evaluate(window('Report', 'chrome.exe'), [],
                                         'https://' + host + '/report')['is_work'])

    def test_discord_is_protected_even_with_a_catch_all_work_rule(self):
        for process in ('Discord.exe', 'DiscordPTB.exe', 'DiscordCanary.exe'):
            with self.subTest(process=process):
                self.assertEqual(evaluate(window('Channel', process), CATCH_ALL)['reason'], 'protected_app')
        for title, url in (
                ('(2) Discord | #chat | Community - Google Chrome', None),
                ('Discord - Microsoft Edge', None),
                ('Channel', 'https://discord.com/channels/fixture'),
                ('Invite', 'https://www.discord.gg./fixture'),
                ('Channel', 'https://discordapp.com/channels/fixture')):
            with self.subTest(title=title):
                self.assertEqual(evaluate(window(title, 'chrome.exe'), CATCH_ALL, url)['reason'], 'protected_app')
        self.assertTrue(evaluate(window('Discord guide - Google Docs - Google Chrome', 'chrome.exe'), [])['is_work'])
        self.assertTrue(evaluate(window('Report', 'chrome.exe'), [],
                                 'https://discord.com.example.test/')['is_work'])

    def test_unverifiable_and_active_chuddy_destinations_are_excluded(self):
        self.assertEqual(evaluate(window('Elevated application', ''), CATCH_ALL)['status'], 'unknown')
        self.assertEqual(evaluate(window('The Beer Store', 'chrome.exe', is_chuddy_destination=True),
                                  CATCH_ALL)['reason'], 'chuddy_destination')

    def test_platform_words_in_real_document_topics_are_not_app_identity(self):
        for current, url in ((window('Netflix analysis - Google Docs - Google Chrome', 'chrome.exe'), None),
                             (window('Instagram report.docx - Word', 'WINWORD.EXE'), None),
                             (window('Social media research - Brightspace - Google Chrome', 'chrome.exe'), None),
                             (window('Steam tables assignment — Brightspace — Google Chrome', 'chrome.exe'), None),
                             (window('Netflix invoice - Gmail - Google Chrome', 'chrome.exe'), None),
                             (window('Netflix analysis', 'chrome.exe'), 'https://docs.google.com/document/d/fixture')):
            self.assertTrue(evaluate(current, [], url)['is_work'])
        # Real host/category evidence still outranks document-looking wording.
        self.assertFalse(evaluate(window('Report - Google Docs - Google Chrome', 'chrome.exe'), [],
                                  'https://lichess.org/')['is_work'])

    def test_opaque_game_uses_install_context_without_needing_a_brand_list(self):
        for path in (r'C:\SteamLibrary\steamapps\common\NebulaQuest\opaque.exe',
                     r'D:\GOG Games\NeverHeardOfThis\opaque.exe',
                     r'D:\Epic Games\SomethingNovel\opaque.exe'):
            result = evaluate(window('Report', executable_path=path), CATCH_ALL)
            self.assertEqual(result['reason'], 'excluded_gaming')
        # Installation context is a heuristic; known authoring tools are not games.
        self.assertTrue(evaluate(window('Scene', 'blender.exe', executable_path=
            r'C:\SteamLibrary\steamapps\common\Blender\blender.exe'), [])['is_work'])
        self.assertTrue(evaluate(window('Opaque', 'opaque.exe'), [])['is_work'])
        self.assertFalse(evaluate(window('Blender', 'opaque.exe', executable_path=
            r'C:\SteamLibrary\steamapps\common\UnknownGame\opaque.exe'), [])['is_work'])

    def test_explicit_category_handles_unfamiliar_apps_and_cannot_leak_arbitrary_text(self):
        self.assertEqual(evaluate(window('Report', app_category='game'), CATCH_ALL)['reason'],
                         'excluded_gaming')
        for supplied in ('social_media', 'streaming', 'shopping'):
            self.assertTrue(evaluate(window('Report', app_category=supplied), [])['is_work'])
        supplied = 'secret person@example.net password=PRIVATE'
        result = evaluate(window(app_category=supplied), [])
        self.assertTrue(result['is_work'])
        self.assertNotIn('PRIVATE', json.dumps(result))

    def test_deliberate_disabled_rule_still_overrides_broad_default(self):
        disabled = {'kind': 'process', 'pattern': 'unfamiliar.exe', 'enabled': False}
        result = evaluate(window(), CATCH_ALL + [disabled])
        self.assertEqual((result['is_work'], result['status'], result['reason']),
                         (False, 'disabled', 'disabled_rule'))

    def test_unavailable_busy_disabled_and_uncertain_ai_keep_policy_default(self):
        def fail(metadata, cancel):
            raise TimeoutError('PRIVATE payload')
        for raw in [None, {}, decision(classification='uncertain'),
                    decision(classification='nonwork', category='other')]:
            recognizer = self.recognizer(lambda metadata, cancel, raw=raw: raw)
            first = recognizer.observe(window('Report', 'chrome.exe'), [])
            self.assertTrue(first['is_work'])
            recognizer.worker.join(2)
            result = recognizer.observe(window('Report', 'chrome.exe'), [])
            self.assertEqual((result['is_work'], result['source']), (True, 'policy'))
        recognizer = self.recognizer(fail)
        recognizer.observe(window('Report', 'chrome.exe'), [])
        recognizer.worker.join(2)
        failed = recognizer.observe(window('Report', 'chrome.exe'), [])
        self.assertEqual((failed['is_work'], failed['source'], failed['ai_status'], failed['ai_error']),
                         (True, 'policy', 'unavailable', 'TimeoutError'))
        self.assertNotIn('PRIVATE', json.dumps(recognizer.info()))
        for settings in ({'ai_enabled': False}, {'ai_work_detection': False}):
            recognizer = self.recognizer(fail, settings)
            self.assertTrue(recognizer.observe(window('Report'), [])['is_work'])
            self.assertIsNone(recognizer.worker)
        self.assertTrue(self.recognizer(fail).observe(window('Report'), [], allow_ai=False)['is_work'])

    def test_completed_ai_exclusion_is_used_even_while_dialogue_agent_is_busy(self):
        recognizer = self.recognizer(lambda metadata, cancel: decision(category='gaming', classification='nonwork'))
        current = window('Report', 'chrome.exe')
        self.assertTrue(recognizer.observe(current, [])['is_work'])
        recognizer.worker.join(2)
        result = recognizer.observe(current, [], allow_ai=False)
        self.assertEqual((result['is_work'], result['source'], result['reason']),
                         (False, 'local_ai', 'model_gaming'))
        positive_rule = recognizer.observe(current, CATCH_ALL, allow_ai=False)
        self.assertEqual((positive_rule['is_work'], positive_rule['source'], positive_rule['reason']),
                         (False, 'local_ai', 'model_gaming'))

    def test_new_negative_evidence_beats_cached_model_positive(self):
        recognizer = self.recognizer(lambda metadata, cancel: decision())
        current = window('Report', 'chrome.exe')
        recognizer.observe(current, [])
        recognizer.worker.join(2)
        self.assertEqual(recognizer.observe(current, [])['source'], 'local_ai')
        changed = dict(current, app_category='gaming')
        result = recognizer.observe(changed, [])
        self.assertEqual((result['is_work'], result['source'], result['reason']),
                         (False, 'policy', 'excluded_gaming'))

    def test_safe_model_payload_never_contains_titles_paths_urls_or_identifiers(self):
        recognizer = self.recognizer()
        raw = json.dumps({'message': {'content': json.dumps(decision())}}).encode()
        current = window('Private Person account=123 report', 'PRIVATE-ID.exe',
                         executable_path=r'C:\Users\PrivatePerson\SecretProject\PRIVATE-ID.exe',
                         app_category='PRIVATE-SECRET')
        with patch('chuddy.work_recognition.local_request', return_value=io.BytesIO(raw)) as request:
            recognizer.observe(current, [], url='https://private-user.example.net/document/PRIVATE-DOC?token=SECRET')
            recognizer.worker.join(2)
        payload = request.call_args.args[2]
        serialized = json.dumps(payload)
        for secret in ('Private Person', '123', 'PrivatePerson', 'SecretProject', 'PRIVATE-ID',
                       'PRIVATE-SECRET', 'private-user', 'PRIVATE-DOC', 'SECRET', 'https://'):
            self.assertNotIn(secret, serialized)
        self.assertEqual(payload['model'], 'fixture-local-model')
        self.assertNotIn('tools', payload)
        self.assertIn('gaming', payload['format']['properties']['category']['enum'])
        self.assertNotIn('shopping', payload['format']['properties']['category']['enum'])

    def test_optional_category_and_path_change_identity_without_exposing_them(self):
        original = evaluate(window(), [])
        path_changed = evaluate(window(executable_path=r'C:\Users\SECRET\opaque.exe'), [])
        category_changed = evaluate(window(app_category='creative'), [])
        self.assertNotEqual(original['key'], path_changed['key'])
        self.assertNotEqual(original['key'], category_changed['key'])
        self.assertNotIn('SECRET', json.dumps(path_changed))


if __name__ == '__main__':
    unittest.main()
