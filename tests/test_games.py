import random
import unittest
from types import SimpleNamespace
from datetime import datetime

from cogs.blackjack import BlackjackGame, hand_value
from cogs.guess_number import GuessGame, reward_for
from cogs.slots import evaluate
from cogs.tictactoe import EMPTY, O, X, bot_move, find_winner
from core.timeutil import TAIPEI, parse_duration, parse_when


class TimeTest(unittest.TestCase):
    NOW = datetime(2026, 10, 12, 14, 0, tzinfo=TAIPEI)

    def test_durations(self):
        self.assertEqual(parse_duration("10m"), 600)
        self.assertEqual(parse_duration("1h30m"), 5400)
        self.assertEqual(parse_duration("2天"), 172800)
        self.assertEqual(parse_duration("90秒"), 90)
        self.assertEqual(parse_duration("1小時15分鐘"), 4500)
        self.assertIsNone(parse_duration("10x"))
        self.assertIsNone(parse_duration("abc"))
        self.assertIsNone(parse_duration("0m"))

    def test_clock_times(self):
        self.assertEqual(parse_when("18:30", self.NOW), self.NOW.replace(hour=18, minute=30))
        self.assertEqual(parse_when("09:00", self.NOW).day, 13)        # 已經過了 → 明天
        self.assertEqual(parse_when("明天 9:00", self.NOW), self.NOW.replace(day=13, hour=9))
        self.assertIsNone(parse_when("25:00", self.NOW))


def cards(*ranks):
    return [(r, "♠") for r in ranks]


class BlackjackTest(unittest.TestCase):
    def test_hand_values(self):
        self.assertEqual(hand_value(cards("A", "K")), 21)
        self.assertEqual(hand_value(cards("A", "A", "9")), 21)
        self.assertEqual(hand_value(cards("K", "Q", "5")), 25)
        self.assertEqual(hand_value(cards("A", "5", "K")), 16)

    def deal(self, *ranks):
        # 發牌順序：玩家、玩家、莊家、莊家，之後是補牌（從尾端 pop）
        return list(reversed(cards(*ranks)))

    def test_natural_blackjack_pays_3_to_2(self):
        game = BlackjackGame(1, 100, None, deck=self.deal("A", "K", "9", "7"))
        game.check_naturals()
        self.assertEqual(game.outcome, "blackjack")
        self.assertEqual(game.payout, 250)

    def test_dealer_draws_to_17(self):
        game = BlackjackGame(1, 100, None, deck=self.deal("10", "8", "10", "6", "5"))
        game.stand()
        self.assertEqual(hand_value(game.dealer), 21)
        self.assertEqual(game.outcome, "lose")

    def test_double_down(self):
        game = BlackjackGame(1, 100, None, deck=self.deal("5", "6", "10", "7", "10"))
        game.double()
        self.assertEqual(game.bet, 200)
        self.assertEqual(game.outcome, "win")
        self.assertEqual(game.payout, 400)


    def test_insurance_pays_when_dealer_has_blackjack(self):
        game = BlackjackGame(1, 100, None, deck=self.deal("10", "9", "A", "K"))
        game.begin()
        self.assertTrue(game.insurance_pending)
        self.assertIsNone(game.outcome)        # 莊家先不偷看，等玩家決定保險
        game.resolve_insurance(True)
        self.assertEqual(game.outcome, "dealer_blackjack")
        self.assertEqual(game.staked, 150)
        self.assertEqual(game.payout, 150)     # 主注輸 100，保險 50 賠 2 倍拿回 150 → 打平
        self.assertEqual(game.reveal_steps(), [1])

    def test_insurance_lost_when_dealer_has_no_blackjack(self):
        game = BlackjackGame(1, 100, None, deck=self.deal("10", "9", "A", "6", "10"))
        game.begin()
        game.resolve_insurance(True)
        self.assertIsNone(game.outcome)        # 莊家不是 Blackjack，牌局繼續
        game.stand()                           # 莊家 17 點以上停（A+6 = 17）
        self.assertEqual(game.outcome, "win")
        self.assertEqual(game.payout, 200)     # 保險金沒收
        self.assertEqual(game.payout - game.staked, 50)

    def test_no_insurance_offer_without_ace(self):
        game = BlackjackGame(1, 100, None, deck=self.deal("10", "9", "K", "A"))
        game.begin()
        self.assertFalse(game.insurance_pending)
        self.assertEqual(game.outcome, "dealer_blackjack")   # 明牌 10 點，莊家偷看底牌

    def test_stand_while_insurance_pending_declines_it(self):
        game = BlackjackGame(1, 100, None, deck=self.deal("10", "9", "A", "K"))
        game.begin()
        game.stand()                            # 逾時或後台強制結束
        self.assertFalse(game.insured)
        self.assertEqual(game.outcome, "dealer_blackjack")
        self.assertEqual(game.payout, 0)

    def test_dealer_cards_revealed_one_by_one(self):
        game = BlackjackGame(1, 100, None, deck=self.deal("10", "7", "5", "6", "3", "4"))
        game.stand()                             # 莊家 5+6 → 補 3 → 14 → 補 4 → 18
        self.assertEqual(len(game.dealer), 4)
        self.assertEqual(game.reveal_steps(), [1, 2, 3])
        bust = BlackjackGame(1, 100, None, deck=self.deal("10", "6", "5", "6", "K"))
        bust.hit()
        self.assertEqual(bust.outcome, "bust")
        self.assertEqual(bust.reveal_steps(), [])  # 玩家爆牌直接公布


class BlackjackFlowTest(unittest.IsolatedAsyncioTestCase):
    def _cog(self):
        from unittest.mock import AsyncMock, MagicMock

        from cogs.blackjack import Blackjack
        bot = MagicMock()
        bot.db.hold = AsyncMock(return_value=7)
        bot.db.get_points = AsyncMock(return_value=0)
        bot.db.settle = AsyncMock(return_value=1000)
        return Blackjack(bot)

    def _interaction(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        state = {"done": False}

        async def done(**kwargs):
            state["done"] = True
        user = SimpleNamespace(id=1, display_name="P1", display_avatar=SimpleNamespace(url="https://x/a.png"))
        return SimpleNamespace(
            user=user, channel_id=10, channel=None, message=None,
            response=SimpleNamespace(is_done=lambda: state["done"], edit_message=AsyncMock(side_effect=done),
                                     send_message=AsyncMock(side_effect=done)),
            original_response=AsyncMock(), followup=SimpleNamespace(send=AsyncMock()))

    async def test_play_again_edits_same_message(self):
        from unittest.mock import AsyncMock, patch

        import cogs.blackjack as bj
        cog, inter = self._cog(), self._interaction()
        message = SimpleNamespace(edit=AsyncMock())
        with patch.object(bj, "BlackjackGame", side_effect=lambda *a: BlackjackGame(
                *a, deck=list(reversed(cards("10", "7", "9", "8"))))):
            self.assertTrue(await cog.start(inter, 100, message=message))
        inter.response.edit_message.assert_awaited_once()   # 直接把舊訊息換成新牌局
        inter.response.send_message.assert_not_awaited()
        inter.followup.send.assert_not_awaited()

    async def test_bet_over_limit_is_rejected(self):
        from unittest.mock import patch

        import cogs.blackjack as bj
        cog, inter = self._cog(), self._interaction()
        with patch.object(bj, "send_error") as err:
            self.assertFalse(await cog.start(inter, bj.MAX_BET + 1))
        err.assert_called_once()
        cog.bot.db.hold.assert_not_called()
        self.assertEqual(bj.MAX_BET, 50_000)


class CardDisplayTest(unittest.TestCase):
    def test_black_suits_use_outline_symbols(self):
        from cogs.blackjack import render
        from cogs.poker.cards import Card

        # 實心 ♠ ♣ 在深色背景上看不清楚，顯示時要換成空心符號；紅色花色不變
        self.assertEqual(str(Card("A", "♠")), "A♤")
        self.assertEqual(str(Card("10", "♣")), "10♧")
        self.assertEqual(str(Card("K", "♥")), "K♥")
        self.assertEqual(render([("Q", "♠"), ("7", "♦")]), "`Q♤` `7♦`")

    def test_poker_table_has_no_black_suit_emoji(self):
        from cogs.poker.cards import Card
        from cogs.poker.cog import cards_text

        text = cards_text([Card(r, s) for r, s in [("A", "♠"), ("K", "♣"), ("Q", "♥"), ("J", "♦")]])
        for black in ("♠", "♣"):
            self.assertNotIn(black, text)
        self.assertIn("♤", text)
        self.assertIn("♧", text)


class TicTacToeTest(unittest.TestCase):
    def test_winner_detection(self):
        self.assertEqual(find_winner((X, X, X, 0, 0, 0, 0, 0, 0))[0], X)
        self.assertEqual(find_winner((X, O, X, X, O, O, O, X, X))[0], 3)

    def test_bot_takes_win(self):
        board = (O, O, EMPTY, X, X, EMPTY, EMPTY, EMPTY, EMPTY)
        self.assertEqual(bot_move(board, O, "normal"), 2)

    def test_hard_bot_never_loses(self):
        rng = random.Random(7)
        for _ in range(200):
            board, turn = [EMPTY] * 9, rng.choice([X, O])
            while not find_winner(tuple(board))[0]:
                if turn == O:
                    move = bot_move(tuple(board), O, "hard")
                else:
                    move = rng.choice([i for i in range(9) if board[i] == EMPTY])
                board[move] = turn
                turn = X if turn == O else O
            self.assertNotEqual(find_winner(tuple(board))[0], X)


class MiscGameTest(unittest.TestCase):
    def test_slots_payouts(self):
        self.assertEqual(evaluate(["💎", "💎", "💎"])[0], 100)
        self.assertEqual(evaluate(["🍒", "🍋", "🍒"])[0], 2)
        self.assertEqual(evaluate(["🍒", "🍋", "🔔"])[0], 1)
        self.assertEqual(evaluate(["🍋", "🔔", "⭐"])[0], 0)

    def test_guess_number_narrows_range(self):
        game = GuessGame(1, 1, 100)
        game.answer = 42
        self.assertEqual(game.guess(5, 50), "lower")
        self.assertEqual(game.guess(5, 60), "out")       # 已經超出範圍，不算次數
        self.assertEqual(game.guess(6, 30), "higher")
        self.assertEqual((game.low, game.high, game.attempts), (31, 49, 2))
        self.assertEqual(game.guess(6, 42), "correct")
        self.assertEqual(reward_for(3, 100), 10_000)
        self.assertEqual(reward_for(20, 100), 2_000)



class BoardEmbedTest(unittest.TestCase):
    """開局畫面要能產生，否則題目送不出去、頻道還會卡在「已經有一局」。"""

    def test_guess_number_board(self):
        from types import SimpleNamespace
        from cogs.guess_number import GuessNumber

        embed = GuessNumber(SimpleNamespace()).board_embed(GuessGame(1, 2, 100))
        self.assertTrue(any("元" in f.value for f in embed.fields))

    def test_soup_board(self):
        from types import SimpleNamespace
        from cogs.seatortoise import SeaTortoise, SoupGame

        cog = SeaTortoise(SimpleNamespace(ai=None))
        embed = cog.board_embed(SoupGame(1, 2, cog.bank[0]))
        self.assertTrue(any("元" in f.value for f in embed.fields))


if __name__ == "__main__":
    unittest.main()
