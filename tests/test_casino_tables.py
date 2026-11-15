"""Pure file/local rules checks. No app, browser, wallet, audio or hardware."""
from copy import deepcopy
import json
import random
import unittest
from unittest.mock import patch

from chuddy.casino_tables import (
    WHEEL_ORDER, RED_NUMBERS, blackjack_act, blackjack_new, blackjack_public,
    blackjack_total, roulette_color, roulette_new, roulette_public, roulette_selection,
)


class FixedPocket:
    def __init__(self, pocket):
        self.pocket = pocket

    def randrange(self, limit):
        assert limit == 37
        return self.pocket


def dealt(cards, stake=10):
    with patch('chuddy.casino_tables._shoe', return_value=['2C'] * 32 + list(reversed(cards))):
        return blackjack_new(stake)


class BlackjackRulesTests(unittest.TestCase):
    def test_legacy_gary_dialogue_is_silent_in_public_projection(self):
        state = dealt(['8S', '6C', '8H', '7D'])
        state['dealer_line'] = 'Meow. [Gary waits.]'
        self.assertNotIn('Meow', blackjack_public(state)['dealer_line'])
        wheel = roulette_new(10, 'red', FixedPocket(7))
        wheel['dealer_line'] = 'Meow. [Gary pays.]'
        self.assertNotIn('Meow', roulette_public(wheel)['dealer_line'])
        self.assertEqual(state['dealer_line'], 'Meow. [Gary waits.]')

    def test_six_decks_are_real_and_complete_and_seeded(self):
        first = blackjack_new(10, random.Random(14))
        second = blackjack_new(10, random.Random(14))
        self.assertEqual(first, second)
        cards = first['shoe'] + first['dealer'] + first['hands'][0]['cards']
        self.assertEqual(len(cards), 312)
        self.assertEqual(len(set(cards)), 52)
        self.assertTrue(all(cards.count(card) == 6 for card in set(cards)))

    def test_aces_are_revalued_and_soft_total_is_correct(self):
        cases = [(['AS', '6D'], (17, True)), (['AS', '6D', 'KC'], (17, False)),
                 (['AS', 'AH', '9D'], (21, True)), (['AS', 'AH', '9D', 'TD'], (21, False)),
                 (['AS', 'AH', 'AC', 'AD', '9C'], (13, False))]
        for cards, expected in cases:
            self.assertEqual(blackjack_total(cards), expected)

    def test_player_natural_pays_exact_three_to_two_and_unused_reserve(self):
        state = dealt(['AS', '9D', 'KC', '7D'])
        self.assertTrue(state['complete'])
        self.assertEqual(state['payout'], 55)  # reserve 40 plus profit 15
        self.assertEqual(state['hands'][0]['payout'], 25)
        self.assertEqual(state['hands'][0]['outcome'], 'blackjack')
        self.assertEqual(len(state['dealer']), 2)

    def test_dealer_natural_is_checked_before_player_actions(self):
        state = dealt(['8S', 'AD', 'TC', 'KD'])
        self.assertTrue(state['complete'])
        self.assertEqual(state['payout'], 30)
        self.assertEqual(blackjack_public(state)['actions'], [])
        with self.assertRaises(ValueError):
            blackjack_act(state, 'double')

    def test_naturals_push_and_dealer_natural_beats_three_card_21(self):
        state = dealt(['AS', 'AD', 'TC', 'KD'])
        self.assertEqual(state['payout'], state['reserve'])
        self.assertEqual(state['hands'][0]['outcome'], 'push')

    def test_stand_dealer_stands_on_soft17_and_returns_unused_reserve(self):
        state = dealt(['TS', 'AD', '8C', '6D', 'KC'])
        result = blackjack_act(state, 'stand')
        self.assertEqual(result['dealer'], ['AD', '6D'])
        self.assertEqual(result['payout'], 50)
        self.assertEqual(result['hands'][0]['outcome'], 'win')
        self.assertFalse(state['complete'])

    def test_dealer_draws_to_17_and_tie_pushes(self):
        state = dealt(['TS', '5D', '7C', '6D', '6H'])
        result = blackjack_act(state, 'stand')
        self.assertEqual(result['dealer'], ['5D', '6D', '6H'])
        self.assertEqual(result['payout'], 40)

    def test_player_bust_loses_even_if_dealer_would_bust(self):
        state = dealt(['TS', 'TD', '6C', '6D', 'KC', 'KD'])
        result = blackjack_act(state, 'hit')
        self.assertEqual(result['hands'][0]['outcome'], 'bust')
        self.assertEqual(result['payout'], 30)
        self.assertEqual(result['dealer'], ['TD', '6D'])

    def test_hit_to_21_automatically_stands_and_dealer_bust_wins(self):
        state = dealt(['TS', 'TD', '5C', '6D', '6H', 'KC'])
        result = blackjack_act(state, 'hit')
        self.assertTrue(result['complete'])
        self.assertEqual(result['hands'][0]['outcome'], 'win')
        self.assertEqual(result['payout'], 50)

    def test_double_uses_reserved_chips_takes_one_card_and_stands(self):
        state = dealt(['5S', 'TD', '6C', '6D', 'TC', 'KC'])
        result = blackjack_act(state, 'double')
        self.assertEqual(result['committed'], 20)
        self.assertEqual(result['hands'][0]['bet'], 20)
        self.assertEqual(len(result['hands'][0]['cards']), 3)
        self.assertTrue(result['complete'])
        self.assertEqual(result['payout'], 60)

    def test_double_cannot_follow_hit(self):
        state = dealt(['2S', 'TD', '3C', '6D', '4C'])
        state = blackjack_act(state, 'hit')
        self.assertNotIn('double', blackjack_public(state)['actions'])
        before = deepcopy(state)
        with self.assertRaises(ValueError):
            blackjack_act(state, 'double')
        self.assertEqual(state, before)

    def test_equal_value_face_cards_can_split_and_no_resplit(self):
        state = dealt(['QS', 'TD', 'KC', '6D', 'QH', '2C'])
        state = blackjack_act(state, 'split')
        self.assertEqual(state['committed'], 20)
        self.assertEqual(state['active_hand'], 0)
        self.assertEqual(len(state['hands']), 2)
        self.assertNotIn('split', blackjack_public(state)['actions'])

    def test_split_aces_get_one_card_and_21_is_regular_win(self):
        state = dealt(['AS', 'TD', 'AC', '7D', 'TH', 'KC'])
        state = blackjack_act(state, 'split')
        self.assertTrue(state['complete'])
        self.assertEqual([h['payout'] for h in state['hands']], [20, 20])
        self.assertEqual(state['payout'], 60)
        self.assertTrue(all(h['outcome'] == 'win' for h in state['hands']))

    def test_split_turn_order_and_double_both_hands_fits_reserve(self):
        state = dealt(['5S', 'TD', '5C', '7D', '6H', '4D', 'TC', 'TH'])
        state = blackjack_act(state, 'split')
        self.assertEqual(state['hands'][1]['status'], 'waiting')
        state = blackjack_act(state, 'double')
        self.assertEqual(state['active_hand'], 1)
        state = blackjack_act(state, 'double')
        self.assertTrue(state['complete'])
        self.assertEqual(state['committed'], state['reserve'])
        self.assertEqual(state['payout'], 80)

    def test_invalid_action_never_mutates_state(self):
        state = dealt(['4S', 'TD', '6C', '7D'])
        for action, amount in [('split', None), ('dance', None), ('hit', 100), ('stand', False)]:
            before = deepcopy(state)
            with self.assertRaises(ValueError):
                blackjack_act(state, action, amount)
            self.assertEqual(state, before)

    def test_bad_stakes_are_rejected_without_float_or_bool_acceptance(self):
        for stake in (0, 1, 3, -2, 10002, True, False, 10.0, '10', None):
            with self.assertRaises(ValueError):
                blackjack_new(stake)

    def test_public_projection_hides_shoe_and_hole_card_and_is_a_copy(self):
        state = dealt(['4S', 'TD', '6C', '7D'])
        public = blackjack_public(state)
        self.assertNotIn('shoe', public)
        self.assertEqual(public['dealer']['cards'], ['TD', None])
        self.assertIsNone(public['dealer']['total'])
        self.assertEqual(public['hands'][0]['total'], 10)
        public['hands'][0]['cards'].append('AS')
        self.assertEqual(state['hands'][0]['cards'], ['4S', '6C'])
        self.assertEqual(json.loads(json.dumps(state)), state)

    def test_thousand_rounds_terminate_and_never_exceed_reserved_exposure(self):
        rng = random.Random(130)
        for _ in range(1000):
            state = blackjack_new(10, rng)
            steps = 0
            while not state['complete']:
                actions = blackjack_public(state)['actions']
                state = blackjack_act(state, rng.choice(actions))
                steps += 1
                self.assertLess(steps, 45)
            self.assertLessEqual(state['committed'], state['reserve'])
            self.assertGreaterEqual(state['payout'], 0)
            self.assertLessEqual(state['payout'], state['reserve'] * 2)
            self.assertEqual(state['payout'], state['reserve'] - state['committed'] + sum(h['payout'] for h in state['hands']))


class RouletteRulesTests(unittest.TestCase):
    def test_european_wheel_and_standard_colors(self):
        self.assertEqual(len(WHEEL_ORDER), 37)
        self.assertEqual(set(WHEEL_ORDER), set(range(37)))
        self.assertEqual(len(RED_NUMBERS), 18)
        self.assertEqual(roulette_color(0), 'green')
        self.assertEqual(roulette_color(1), 'red')
        self.assertEqual(roulette_color(2), 'black')

    def test_all_straight_bets_cover_one_pocket_and_return_36_stakes(self):
        for selected in range(37):
            outcomes = [roulette_new(5, {'kind': 'straight', 'value': selected}, FixedPocket(n)) for n in range(37)]
            self.assertEqual(sum(r['won'] for r in outcomes), 1)
            self.assertEqual(sum(r['payout'] for r in outcomes), 180)
            self.assertEqual(outcomes[selected]['payout'], 180)

    def test_outside_bets_have_exact_coverage_and_zero_always_loses(self):
        selections = [{'kind': kind} for kind in ['red', 'black', 'odd', 'even', 'low', 'high']]
        selections += [{'kind': kind, 'value': value} for kind in ['dozen', 'column'] for value in range(1, 4)]
        for selection in selections:
            outcomes = [roulette_new(5, selection, FixedPocket(n)) for n in range(37)]
            count = 12 if selection['kind'] in ('dozen', 'column') else 18
            self.assertEqual(sum(r['won'] for r in outcomes), count)
            self.assertEqual(sum(r['payout'] for r in outcomes), 180)
            self.assertEqual(outcomes[0]['payout'], 0)
            self.assertEqual(outcomes[0]['color'], 'green')

    def test_columns_and_dozens_boundaries(self):
        self.assertTrue(roulette_new(1, {'kind': 'column', 'value': 1}, FixedPocket(34))['won'])
        self.assertTrue(roulette_new(1, {'kind': 'column', 'value': 3}, FixedPocket(36))['won'])
        self.assertFalse(roulette_new(1, {'kind': 'column', 'value': 1}, FixedPocket(35))['won'])
        self.assertTrue(roulette_new(1, {'kind': 'dozen', 'value': 2}, FixedPocket(13))['won'])
        self.assertTrue(roulette_new(1, {'kind': 'dozen', 'value': 2}, FixedPocket(24))['won'])
        self.assertFalse(roulette_new(1, {'kind': 'dozen', 'value': 2}, FixedPocket(25))['won'])

    def test_bad_stakes_selection_and_values_rejected(self):
        for stake in [0, -1, 10001, True, 1.0, '10', None]:
            with self.assertRaises(ValueError):
                roulette_new(stake, 'red')
        for selection in [None, [], '', '00', {'kind': 'red', 'value': 1}, {'kind': 'straight', 'value': True},
                          {'kind': 'straight', 'value': 37}, {'kind': 'straight', 'value': -1},
                          {'kind': 'dozen', 'value': 0}, {'kind': 'column', 'value': 4},
                          {'kind': 'straight', 'value': '1'}, {'kind': 'black', 'rigged': True}]:
            with self.assertRaises(ValueError):
                roulette_new(10, selection)

    def test_seeded_rng_copy_projection_and_serialization(self):
        first = roulette_new(10, 'red', random.Random(44))
        self.assertEqual(first, roulette_new(10, 'red', random.Random(44)))
        public = roulette_public(first)
        self.assertEqual(public['wheel_order'][public['wheel_index']], public['number'])
        public['selection']['kind'] = 'black'
        self.assertEqual(first['selection']['kind'], 'red')
        self.assertEqual(json.loads(json.dumps(first)), first)
        self.assertEqual(roulette_selection('red'), {'kind': 'red'})


if __name__ == '__main__':
    unittest.main()
