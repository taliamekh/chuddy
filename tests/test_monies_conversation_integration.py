"""Coordinator contract tests. No real credentials, devices or inference."""
from chuddy.persona import conversation_context, scripted, system_for
from tests.helpers import ControllerCase


class MoniesConversationIntegrationTests(ControllerCase):
    def test_controller_keeps_ordinary_explicit_memory_and_rejects_recovery_details(self):
        self.controller.agent.begin('memory fixture')
        for text, key, value in [
            ('Remember my birthplace is Fixture Island.', 'favorite', 'Fixture Island'),
            ("Remember my mother's maiden name is Fixture Noodle.", 'favorite', 'Fixture Noodle'),
            ('Remember this preference.', 'birthplace', 'Fixture Island'),
            ('Remember this preference.', 'favorite', "mother's maiden name Fixture Noodle"),
        ]:
            result = self.tool('remember_preference', {'key':key, 'value':value}, text=text)
            self.assertFalse(result['ok'])
        self.assertEqual(self.store.memories(), [])
        result = self.tool('remember_preference', {'key':'favorite music', 'value':'jazz'},
                           text='Remember my favorite music is jazz.')
        self.assertTrue(result['ok'])
        self.assertEqual(self.store.memories()[0]['value'], 'jazz')

    def test_currency_queries_receive_real_balance_and_passive_policy_without_casual_clutter(self):
        context = self.controller.context()
        for text in ['How many monies do I have?', 'What is my earning multiplier?']:
            selected = conversation_context(context, text)
            self.assertEqual(selected['mock_balance'], 100)
            self.assertEqual(selected['economy']['currency'], 'monies')
            self.assertEqual(selected['economy']['base_per_second'], 1)
        self.assertNotIn('economy', conversation_context(context, 'I like Minecraft.'))
        self.assertIn('monies', scripted('What about my wallet?', []))
        self.assertIn('monies', system_for('What about my wallet?'))
