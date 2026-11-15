"""Fast admission and late dialogue acknowledgements, without desktop effects."""
from chuddy.controller import WORK_SETTLE_SECONDS
from chuddy.work_banter import BANTER_OUTCOME_SOURCE
from tests.helpers import ControllerCase


class FastWorkFlowTests(ControllerCase):
    def setUp(self):
        super().setUp()
        self.window = {'handle':42,'pid':123,'process':'WINWORD.EXE','title':'Assignment - Word'}
        self.patches[-1].target.foreground.return_value = self.window
        self.controller.distractions.delay = 0
        self.controller.set_session('start')

    def trigger(self):
        self.controller.poll_work()
        turn = self.controller.agent.info()['turn_id']
        self.now += WORK_SETTLE_SECONDS + .01
        self.controller.poll_work()
        self.assertTrue(self.controller.distractions.wait())
        return turn

    def test_work_starts_while_dialogue_is_still_thinking(self):
        turn = self.trigger()
        self.assertIsNotNone(turn)
        self.assertEqual(self.controller.agent.info()['status'], 'thinking')
        self.assertEqual(self.controller.interruption['phase'], 'opened')
        self.assertTrue(self.controller.interruption['banter_message'])
        self.assertTrue(any(row['source']==BANTER_OUTCOME_SOURCE for row in self.store.messages()))

    def test_brief_focus_settle_still_precedes_action(self):
        self.controller.poll_work()
        self.now += WORK_SETTLE_SECONDS / 2
        self.controller.poll_work()
        self.assertIsNone(self.controller.interruption)
        self.assertEqual(self.covered, [])

    def test_late_tool_reports_same_turn_policy_action_without_another_effect(self):
        turn = self.trigger()
        ticket = self.controller.interruption['id']
        result = self.controller.tool('request_distraction', {'reason':'Skip work'},
            self.controller.agent.cancel_event, turn, 'Work observed', True)
        self.assertTrue(result['ok'])
        self.assertTrue(result['already_started'])
        self.assertEqual(result['decision_source'], 'work detection fallback')
        self.assertEqual(self.controller.interruption['id'], ticket)
        self.assertEqual(self.covered, [True])

    def test_later_unrelated_conversation_cannot_claim_existing_performance(self):
        self.trigger()
        self.controller.agent.begin('Can we play?')
        result = self.controller.tool('request_distraction', {'reason':'Play'},
            self.controller.agent.cancel_event, self.controller.agent.info()['turn_id'], 'Can we play?', False)
        self.assertFalse(result['ok'])
        self.assertEqual(self.covered, [True])

    def test_game_category_cancels_before_fast_fallback(self):
        self.controller.poll_work()
        self.patches[-1].target.foreground.return_value = {**self.window, 'app_category':'gaming'}
        self.now += WORK_SETTLE_SECONDS + .01
        self.controller.poll_work()
        self.assertIsNone(self.controller.interruption)
        self.assertIsNone(self.controller.pending_work)
        self.assertEqual(self.controller.work_detection['reason'], 'excluded_gaming')
        self.assertEqual(self.covered, [])
