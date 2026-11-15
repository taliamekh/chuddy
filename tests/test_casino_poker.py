import copy
import json
import random
import unittest
from unittest.mock import patch

from chuddy import casino_poker as poker


class PokerRulesTests(unittest.TestCase):
    def test_character_speech_explicitly_names_each_public_move(self):
        for name in poker.BOT_STYLES:
            for action in ('check', 'call', 'fold', 'raise'):
                line = poker.action_line(name, action, 17).lower()
                self.assertIn(action, line, (name, action, line))
                if action in ('call', 'raise'):
                    self.assertIn('17', line)
        state = poker.new_game(100, random.Random(3))
        original = copy.deepcopy(state)
        for entry in state['log']:
            entry['text'] = 'A stale witty line.'
        public = poker.public_state(state)
        self.assertTrue(all('stale witty' not in row.get('text', '') for row in public['log']))
        self.assertEqual(state['players'], original['players'])

    def test_all_hand_classes_wheel_and_kickers(self):
        cases = [
            ('AS KS QS JS TS', 8), ('AC AD AH AS 2D', 7),
            ('KC KD KH 2S 2D', 6), ('AS JS 8S 5S 2S', 5),
            ('AS 2D 3H 4S 5D', 4), ('3C 3D 3S 9H 2D', 3),
            ('JC JD 4S 4H 2D', 2), ('AC AD 9S 5H 2D', 1),
            ('AS JD 9H 5S 2D', 0)]
        for cards, rank in cases:
            self.assertEqual(poker.evaluate(cards.split())[0], rank)
        self.assertLess(poker.evaluate('AS 2D 3H 4S 5D'.split()), poker.evaluate('2S 3D 4H 5S 6D'.split()))
        self.assertGreater(poker.evaluate('AC AD KS QH JD'.split()), poker.evaluate('AS AH KS QD TD'.split()))
        self.assertEqual(poker.evaluate('AS KS QS JS TS 2D 3C'.split()), (8, 14))
        self.assertRaises(ValueError, poker.evaluate, ['AS'] * 5)

    def test_hidden_cards_and_serialization(self):
        state = poker.new_game(100, random.Random(3))
        view = poker.public_state(state)
        self.assertNotIn('deck', view)
        self.assertNotIn('burned', view)
        self.assertEqual(view['reserve'], 100)
        self.assertTrue(all(p['cards'] == [None, None] for p in view['players'][1:]))
        self.assertEqual(len(view['players'][0]['cards']), 2)
        self.assertEqual(view['actor'], 0)
        json.dumps(state)
        json.dumps(view)
        self.assertEqual(len(view['players']), 5)
        self.assertEqual(view['players'][0]['name'], 'You (Patrick)')
        self.assertEqual([p['name'] for p in view['players'][1:]], ['SpongeBob', 'Squidward', 'Plankton', 'Mr. Krabs'])
        self.assertEqual(view['dealer'], 'Gary')
        self.assertEqual(len({p['style'] for p in view['players'][1:]}), 4)

    def test_illegal_action_does_not_mutate_state(self):
        state = poker.new_game(100, random.Random(3))
        original = copy.deepcopy(state)
        for action, amount in [('raise', 0), ('raise', True), ('dance', None), ('check', None)]:
            with self.assertRaises(ValueError):
                poker.act(state, action, amount, random.Random(1))
            self.assertEqual(state, original)

    def test_action_chips_are_public_post_action_snapshots(self):
        state = poker.new_game(100, random.Random(3))
        for _ in range(40):
            view = poker.public_state(state)
            stacks = [100] * 5
            contributions = [0] * 5
            for entry in view['log']:
                chips = entry['chips']
                self.assertEqual(set(chips), {'stack', 'street_bet', 'contributed', 'folded', 'pot'})
                stacks[entry['seat']] = chips['stack']
                contributions[entry['seat']] = chips['contributed']
                self.assertEqual(sum(stacks) + chips['pot'], 500)
                self.assertEqual(sum(contributions), chips['pot'])
            if state['complete']:
                break
            legal = view['legal_actions']
            state = poker.act(state, 'check' if legal['check'] else 'call', rng=random.Random(7))
        self.assertTrue(state['complete'])

    def test_passive_play_progresses_all_streets_and_conserves_chips(self):
        for seed in range(100):
            rng = random.Random(seed)
            state = poker.new_game(100, rng)
            for _ in range(50):
                self.assertEqual(sum(p['stack'] + p['contributed'] for p in state['players']), 500)
                view = poker.public_state(state)
                legal = view['legal_actions']
                state = poker.act(state, 'check' if legal['check'] else 'call', rng=rng)
                if state['complete']:
                    break
            self.assertTrue(state['complete'], seed)
            self.assertEqual(sum(p['stack'] for p in state['players']), 500)
            self.assertEqual(state['payout'], state['players'][0]['stack'])
            self.assertTrue(state['showdown'] or len(poker._players_in(state)) == 1)

    def test_folding_finishes_and_returns_unspent_stack(self):
        state = poker.new_game(100, random.Random(4))
        folded = poker.act(state, 'fold', rng=random.Random(4))
        self.assertTrue(folded['complete'])
        self.assertEqual(folded['payout'], 100)  # Human button has not posted a blind.
        self.assertEqual(sum(p['stack'] for p in folded['players']), 500)
        self.assertTrue(all(p['cards'] == [None, None] for p in poker.public_state(folded)['players'][1:] if p['folded']))

    def test_random_legal_strategies_terminate_and_conserve(self):
        for seed in range(200):
            rng = random.Random(seed)
            state = poker.new_game(rng.randint(20, 200), rng)
            total = state['reserve'] * 5
            for _ in range(100):
                if state['complete']:
                    break
                legal = poker.public_state(state)['legal_actions']
                actions = [action for action in ['fold', 'check', 'call', 'raise', 'all_in'] if legal[action]]
                action = rng.choice(actions)
                amount = rng.randint(legal['min_raise_to'], legal['max_raise_to']) if action == 'raise' else None
                state = poker.act(state, action, amount, rng)
            self.assertTrue(state['complete'], seed)
            self.assertEqual(sum(p['stack'] for p in state['players']), total, seed)
            self.assertTrue(all(p['stack'] >= 0 for p in state['players']))
            self.assertRaises(ValueError, poker.act, state, 'fold')

    def test_side_pots_and_uncalled_overbet(self):
        state = poker.new_game(100, random.Random(1))
        state['community'] = '2C 3D 5H 8S 9C'.split()
        cards = ['AC AD', 'KC KD', 'QC QD', 'JC JD', 'TC TD']
        amounts = [20, 50, 100, 60, 0]
        for i, p in enumerate(state['players']):
            p.update(cards=cards[i].split(), contributed=amounts[i], stack=100-amounts[i], folded=i >= 3)
        poker._finish(state)
        # Aces win main 80; kings win next 90; queens win 20 plus refund 40.
        self.assertEqual([p['stack'] for p in state['players']], [160, 140, 60, 40, 100])
        self.assertEqual(sum(p['stack'] for p in state['players']), 500)
        self.assertEqual(state['payout'], 160)
        public = poker.public_state(state)
        self.assertEqual(public['pots'][-1], {'amount': 40, 'winners': [2], 'refund': True})
        self.assertNotIn('refund', state['pots'][-1])
        self.assertTrue(all('refund' not in pot for pot in public['pots'][:-1]))

    def test_split_board_and_odd_chip_order_left_of_button(self):
        state = poker.new_game(100, random.Random(1))
        state['community'] = 'AS KS QS JS TS'.split()
        cards = ['2C 3D', '4C 5D', '6C 7D', '8C 9D', 'TC JD']
        for i, p in enumerate(state['players']):
            amount = 3 if i < 3 else 2 if i == 3 else 0
            p.update(cards=cards[i].split(), contributed=amount,
                     stack=100-amount, folded=i >= 3)
        poker._finish(state)
        self.assertEqual([p['stack'] for p in state['players']], [100, 101, 101, 98, 100])
        self.assertEqual(sum(p['stack'] for p in state['players']), 500)

    def test_short_all_in_does_not_reopen_raise(self):
        state = poker.new_game(100, random.Random(2))
        state.update(street='flop', current_bet=20, min_raise=20, actor=1,
                     acted_at=[20, None, 20, 20, 20], pending=[1])
        for p in state['players']:
            p.update(street_bet=20, contributed=20, stack=80)
        state['players'][1]['stack'] = 5
        poker._apply(state, 1, 'all_in')
        self.assertEqual(state['current_bet'], 25)
        self.assertEqual(state['min_raise'], 20)
        state['actor'] = 0
        legal = poker._legal(state, 0)
        self.assertEqual(legal['call_amount'], 5)
        self.assertFalse(legal['raise'])
        self.assertFalse(legal['all_in'])
        self.assertRaises(ValueError, poker._apply, state, 0, 'raise', 45)

    def test_all_in_call_runs_out_board_without_empty_betting(self):
        state = poker.new_game(100, random.Random(2))
        for i, p in enumerate(state['players']):
            p.update(stack=0, contributed=100, street_bet=100, folded=i > 1)
        state['players'][0].update(stack=10, contributed=90, street_bet=90)
        state.update(actor=0, current_bet=100, pending=[0])
        poker._apply(state, 0, 'call')
        self.assertTrue(state['complete'])
        self.assertEqual(len(state['community']), 5)
        self.assertEqual(sum(p['stack'] for p in state['players']), 500)

    def test_plankton_bluffs_weak_cards_while_krabs_protects_his_clams(self):
        moves = []
        for seat, name in [(3, 'Plankton'), (4, 'Mr. Krabs')]:
            state = poker.new_game(100, random.Random(1))
            state.update(actor=seat, current_bet=2, min_raise=2, complete=False,
                         community=[], acted_at=[None] * 5)
            state['players'][seat].update(name=name, cards=['2C', '3D'], stack=100,
                                          street_bet=0, folded=False)
            def record(state, seat, action, amount=None):
                moves.append(action)
                state['complete'] = True
            with patch.object(poker, '_apply', side_effect=record), patch.object(random.Random, 'random', return_value=.01):
                poker._bots(state, random.Random(1))
        self.assertEqual(moves, ['raise', 'fold'])

    def test_krabs_raises_larger_for_value_than_plankton(self):
        amounts = []
        for seat, name in [(3, 'Plankton'), (4, 'Mr. Krabs')]:
            state = poker.new_game(100, random.Random(1))
            state.update(actor=seat, current_bet=2, min_raise=2, complete=False,
                         community=[], acted_at=[None] * 5)
            state['players'][seat].update(name=name, cards=['AC', 'AD'], stack=100,
                                          street_bet=0, folded=False)
            def record(state, seat, action, amount=None):
                self.assertEqual(action, 'raise')
                amounts.append(amount)
                state['complete'] = True
            with patch.object(poker, '_apply', side_effect=record), patch.object(random.Random, 'random', return_value=.1):
                poker._bots(state, random.Random(1))
        self.assertGreater(amounts[1], amounts[0])

    def test_saved_gary_player_becomes_krabs_without_changing_chips_or_input(self):
        state = poker.new_game(100, random.Random(1))
        state['players'][4]['name'] = 'Gary'
        state['log'].append({'seat': 4, 'name': 'Gary', 'action': 'call', 'amount': 2,
                             'street': 'preflop', 'text': 'Meow.'})
        original = copy.deepcopy(state)
        view = poker.public_state(state)
        self.assertEqual(view['players'][4]['name'], 'Mr. Krabs')
        self.assertEqual(view['dealer'], 'Gary')
        self.assertEqual(view['log'][-1]['name'], 'Mr. Krabs')
        self.assertEqual(state, original)
        folded = poker.act(state, 'fold', rng=random.Random(1))
        self.assertTrue(folded['complete'])
        self.assertEqual(folded['players'][4]['name'], 'Mr. Krabs')
        self.assertEqual(sum(p['stack'] for p in folded['players']), 500)


if __name__ == '__main__':
    unittest.main()
