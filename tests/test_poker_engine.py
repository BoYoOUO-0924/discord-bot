import random
import unittest

from cogs.poker.cards import Card
from cogs.poker.engine import IllegalAction, PokerTable, Seat, build_pots


def C(text: str) -> Card:
    return Card(text[:-1], text[-1])


def stacked_deck(*hands_then_board: str):
    """依發牌順序排出牌堆：先每人第一張、再每人第二張，最後是公共牌（engine 從尾端 pop）。"""
    cards = [C(t) for t in hands_then_board]
    return lambda: list(reversed(cards))


def make_table(stacks, sb=10, bb=20, deck=None):
    seats = [Seat(i + 1, f"P{i + 1}", s) for i, s in enumerate(stacks)]
    table = PokerTable(seats, sb, bb, **({"deck_factory": deck} if deck else {}))
    table.dealer_id = seats[-1].user_id  # 第一手莊家會輪到 P1
    return table


def total_chips(table) -> int:
    return sum(s.stack for s in table.seats) + (0 if table.hand_over else table.pot)


class BuildPotsTest(unittest.TestCase):
    def test_single_pot(self):
        self.assertEqual(build_pots({1: 100, 2: 100}, [1, 2]), [(200, [1, 2])])

    def test_side_pot_with_short_all_in(self):
        pots = build_pots({1: 50, 2: 200, 3: 200}, [1, 2, 3])
        self.assertEqual(pots, [(150, [1, 2, 3]), (300, [2, 3])])

    def test_folded_player_money_stays_in_pot(self):
        pots = build_pots({1: 30, 2: 100, 3: 100}, [2, 3])
        self.assertEqual(pots, [(230, [2, 3])])


class HandFlowTest(unittest.TestCase):
    def test_heads_up_blinds_and_order(self):
        table = make_table([1000, 1000])
        table.start_hand()
        # 單挑：莊家（P1）是小盲並且先行動
        self.assertEqual(table.dealer_id, 1)
        self.assertEqual(table.sb_id, 1)
        self.assertEqual(table.bb_id, 2)
        self.assertEqual(table.current_seat.user_id, 1)
        self.assertEqual(table.pot, 30)

    def test_fold_gives_pot_to_last_player(self):
        table = make_table([1000, 1000, 1000])
        table.start_hand()
        first = table.current_seat.user_id
        table.act(first, "fold")
        table.act(table.current_seat.user_id, "fold")
        self.assertTrue(table.hand_over)
        self.assertEqual(total_chips(table), 3000)
        # 大盲沒人跟的 10 分先退回，實際贏走小盲的 10 分
        bb = table.seat_of(table.bb_id)
        self.assertEqual(bb.stack, 1010)
        self.assertEqual(table.seat_of(table.sb_id).stack, 990)

    def test_cannot_act_out_of_turn(self):
        table = make_table([1000, 1000, 1000])
        table.start_hand()
        other = next(s for s in table.seats if s is not table.current_seat)
        with self.assertRaises(IllegalAction):
            table.act(other.user_id, "call")

    def test_cannot_check_facing_bet(self):
        table = make_table([1000, 1000])
        table.start_hand()
        with self.assertRaises(IllegalAction):
            table.act(table.current_seat.user_id, "check")

    def test_min_raise_enforced(self):
        table = make_table([1000, 1000])
        table.start_hand()
        with self.assertRaises(IllegalAction):
            table.act(table.current_seat.user_id, "raise", 30)   # 至少要加到 40
        table.act(table.current_seat.user_id, "raise", 40)
        self.assertEqual(table.current_bet, 40)

    def test_check_down_to_showdown(self):
        table = make_table([1000, 1000])
        table.start_hand()
        table.act(1, "call")
        table.act(2, "check")
        for _ in range(3):            # 翻牌、轉牌、河牌都過牌
            table.act(table.current_seat.user_id, "check")
            table.act(table.current_seat.user_id, "check")
        self.assertTrue(table.hand_over)
        self.assertTrue(table.result.showdown)
        self.assertEqual(len(table.board), 5)
        self.assertEqual(total_chips(table), 2000)

    def test_uncalled_raise_is_returned(self):
        table = make_table([1000, 1000])
        table.start_hand()
        table.act(1, "raise", 500)
        table.act(2, "fold")
        # P1 只贏走大盲的 20，多加的 480 退回
        self.assertEqual(table.seat_of(1).stack, 1020)
        self.assertEqual(table.seat_of(2).stack, 980)

    def test_side_pot_awarded_correctly(self):
        # P1 短籌碼 All-in 拿到最大的牌，但只能贏主池
        deck = stacked_deck("A♠", "K♦", "2♣",    # 第一張：P1 P2 P3
                            "A♥", "K♣", "7♦",    # 第二張
                            "A♦", "9♠", "5♥", "3♣", "Q♥")
        table = make_table([100, 1000, 1000], deck=deck)
        table.start_hand()   # 莊家 P1，小盲 P2，大盲 P3，P1 先行動
        table.act(1, "all_in")
        table.act(2, "call")
        table.act(3, "call")
        # 翻牌後 P2、P3 繼續下注，產生邊池
        table.act(2, "raise", 200)
        table.act(3, "call")
        while not table.hand_over:
            table.act(table.current_seat.user_id, "check")
        self.assertEqual(total_chips(table), 2100)
        pots = table.result.pots
        self.assertEqual(pots[0].amount, 300)
        self.assertEqual(pots[0].winners, [1])         # 三條 A 贏主池
        self.assertEqual(pots[1].amount, 400)
        self.assertEqual(pots[1].winners, [2])         # K 一對贏邊池
        self.assertEqual(table.seat_of(1).stack, 300)

    def test_all_in_preflop_runs_out_board(self):
        table = make_table([500, 500])
        table.start_hand()
        table.act(1, "all_in")
        table.act(2, "call")
        self.assertTrue(table.hand_over)
        self.assertEqual(len(table.board), 5)
        self.assertEqual(total_chips(table), 1000)

    def test_dealer_button_moves(self):
        table = make_table([1000, 1000, 1000])
        table.start_hand()
        first_dealer = table.dealer_id
        table.act(table.current_seat.user_id, "fold")
        table.act(table.current_seat.user_id, "fold")
        table.start_hand()
        self.assertNotEqual(table.dealer_id, first_dealer)

    def test_cancel_hand_refunds(self):
        table = make_table([1000, 1000, 1000])
        table.start_hand()
        table.act(table.current_seat.user_id, "raise", 100)
        table.cancel_hand()
        self.assertEqual([s.stack for s in table.seats], [1000, 1000, 1000])


class RandomPlayTest(unittest.TestCase):
    """隨機打上千手，確認籌碼守恆、牌局一定會結束。"""

    def test_chip_conservation(self):
        rng = random.Random(1234)
        for _ in range(300):
            stacks = [rng.choice([40, 100, 500, 1000]) for _ in range(rng.randint(2, 6))]
            table = make_table(stacks)
            total = sum(stacks)
            for _hand in range(8):
                if len([s for s in table.seats if s.stack > 0]) < 2:
                    break
                table.start_hand()
                steps = 0
                while not table.hand_over:
                    seat = table.current_seat
                    legal = table.legal(seat)
                    options = ["fold", "call", "all_in"] + (["check"] if legal.can_check else [])
                    if legal.can_raise:
                        options.append("raise")
                    action = rng.choice(options)
                    amount = None
                    if action == "raise":
                        amount = rng.randint(legal.min_raise_to, legal.max_raise_to)
                    table.act(seat.user_id, action, amount)
                    steps += 1
                    self.assertLess(steps, 200)
                    self.assertEqual(total_chips(table), total)
                self.assertEqual(sum(s.stack for s in table.seats), total)
                self.assertTrue(all(s.stack >= 0 for s in table.seats))


if __name__ == "__main__":
    unittest.main()
