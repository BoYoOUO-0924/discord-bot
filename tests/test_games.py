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

    def test_soft_hand_shows_both_totals(self):
        from cogs.blackjack import hand_text
        self.assertEqual(hand_text(cards("A", "5")), "6 / 16")
        self.assertEqual(hand_text(cards("A", "6", "K")), "17")      # A 只能算 1，就不是軟牌
        self.assertEqual(hand_text(cards("A", "K")), "21")
        self.assertEqual(hand_text(cards("A", "A")), "2 / 12")
        self.assertEqual(hand_text(cards("9", "7")), "16")

    def test_split_plays_each_hand_then_dealer(self):
        # 玩家 8 8，莊家 10 7；第一手補 3 → 11 再要 10 → 21 自動停；第二手補 K → 18 停牌
        game = BlackjackGame(1, 100, None, deck=self.deal("8", "8", "10", "7", "3", "10", "K"))
        game.begin()
        self.assertTrue(game.can_split)
        game.split()
        self.assertEqual(len(game.hands), 2)
        self.assertEqual(game.bet, 200)
        self.assertEqual(hand_value(game.player), 11)
        self.assertTrue(game.can_double)                 # 分牌後可以加倍
        game.hit()                                       # 21 點自動換下一手
        self.assertEqual(game.active, 1)
        self.assertEqual(hand_value(game.player), 18)
        self.assertFalse(game.finished)
        game.stand()
        self.assertEqual([h.outcome for h in game.hands], ["win", "win"])
        self.assertEqual(game.outcome, "split_win")
        self.assertEqual(game.payout, 400)

    def test_split_mixed_results(self):
        # 第一手 8+K=18，第二手 8+9=17 加倍補 10 爆牌；莊家 10 7 = 17 → 第一手贏、第二手輸
        game = BlackjackGame(1, 100, None, deck=self.deal("8", "8", "10", "7", "K", "9", "10"))
        game.begin()
        game.split()
        game.stand()
        game.double()
        self.assertEqual([h.outcome for h in game.hands], ["win", "bust"])
        self.assertEqual(game.bet, 300)
        self.assertEqual(game.payout, 200)
        self.assertEqual(game.outcome, "split_lose")

    def test_split_aces_get_one_card_each(self):
        game = BlackjackGame(1, 100, None, deck=self.deal("A", "A", "10", "8", "K", "5"))
        game.split()                                     # 莊家明牌 10，不問保險
        self.assertTrue(game.finished)                   # 分 A 各補一張就停
        self.assertEqual([hand_value(h.cards) for h in game.hands], [21, 16])
        self.assertEqual([h.outcome for h in game.hands], ["win", "lose"])   # 分牌後的 21 不算 Blackjack
        self.assertEqual(game.payout, 200)
        self.assertEqual(game.outcome, "split_push")

    def test_split_rules(self):
        game = BlackjackGame(1, 100, None, deck=self.deal("K", "Q", "9", "7"))
        self.assertTrue(game.can_split)                  # 10 點的牌都可以分
        game = BlackjackGame(1, 100, None, deck=self.deal("9", "8", "9", "7"))
        self.assertFalse(game.can_split)
        game = BlackjackGame(1, 100, None, deck=self.deal("8", "8", "A", "7"))
        game.begin()
        self.assertFalse(game.can_split)                 # 保險還沒決定
        game = BlackjackGame(1, 100, None, deck=self.deal("8", "8", "10", "7", "8", "8", "8", "8", "8"))
        for _ in range(3):
            game.split()
        self.assertEqual(len(game.hands), 4)
        self.assertFalse(game.can_split)                 # 最多 4 手

    def test_surrender_returns_half_bet(self):
        game = BlackjackGame(1, 100, None, deck=self.deal("10", "6", "10", "7"))
        game.begin()
        self.assertTrue(game.can_surrender)
        game.surrender()
        self.assertEqual(game.outcome, "surrender")
        self.assertEqual(game.payout, 50)
        self.assertEqual(len(game.dealer), 2)           # 莊家不用補牌
        self.assertEqual(game.reveal_steps(), [1])      # 還是會翻開底牌給玩家看

    def test_surrender_only_on_first_two_cards(self):
        game = BlackjackGame(1, 100, None, deck=self.deal("2", "3", "10", "7", "4"))
        game.hit()
        self.assertFalse(game.can_surrender)
        game = BlackjackGame(1, 100, None, deck=self.deal("8", "8", "10", "7", "3"))
        game.split()
        self.assertFalse(game.can_surrender)            # 分牌後不能投降
        game = BlackjackGame(1, 100, None, deck=self.deal("10", "6", "A", "7"))
        game.begin()
        self.assertFalse(game.can_surrender)            # 保險還沒決定（莊家還沒偷看）

    def test_practice_mode_can_double(self):
        game = BlackjackGame(1, 0, None, deck=self.deal("5", "6", "10", "7", "10"))
        self.assertTrue(game.can_double)
        game.double()
        self.assertEqual(game.outcome, "win")
        self.assertEqual(game.payout, 0)

    def test_stand_all_finishes_every_hand(self):
        game = BlackjackGame(1, 100, None, deck=self.deal("8", "8", "10", "7", "2", "3"))
        game.split()
        game.stand_all()
        self.assertTrue(game.finished)
        self.assertTrue(all(h.outcome for h in game.hands))

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
                                     send_message=AsyncMock(side_effect=done), defer=AsyncMock(side_effect=done)),
            original_response=AsyncMock(), followup=SimpleNamespace(send=AsyncMock()))

    async def test_play_again_edits_same_message(self):
        from unittest.mock import AsyncMock, patch

        import cogs.blackjack as bj
        cog, inter = self._cog(), self._interaction()
        message = SimpleNamespace(edit=AsyncMock())
        with patch.object(bj, "BlackjackGame", side_effect=lambda *a: BlackjackGame(
                *a, deck=list(reversed(cards("10", "7", "9", "8"))))):
            self.assertTrue(await cog.start(inter, 100, message=message))
        inter.response.defer.assert_awaited_once()   # 扣賭注前先回應 Discord
        message.edit.assert_awaited_once()           # 直接把舊訊息換成新牌局
        inter.response.send_message.assert_not_awaited()
        inter.followup.send.assert_not_awaited()

    async def test_play_again_button_answers_discord_before_holding_bet(self):
        from unittest.mock import AsyncMock, patch

        import cogs.blackjack as bj
        cog, inter = self._cog(), self._interaction()
        order = []
        state = {"done": False}

        async def defer(**kwargs):
            order.append("defer")
            state["done"] = True

        async def hold(*args):
            order.append("hold")
            return 7
        inter.response.is_done = lambda: state["done"]
        inter.response.defer = AsyncMock(side_effect=defer)
        cog.bot.db.hold = AsyncMock(side_effect=hold)
        message = SimpleNamespace(edit=AsyncMock())
        view = bj.PlayAgainView(cog, 1, 100)
        view.message = message
        with patch.object(bj, "BlackjackGame", side_effect=lambda *a: BlackjackGame(
                *a, deck=list(reversed(cards("10", "7", "9", "8"))))):
            await view._restart(inter, 100)
        self.assertEqual(order, ["defer", "hold"])   # 先回應 Discord，3 秒限制就不會超過
        message.edit.assert_awaited()                 # 回應後改用編輯原訊息顯示新牌局
        inter.followup.send.assert_not_awaited()

    async def test_command_natural_blackjack_shows_result(self):
        """/blackjack 莊家開牌就 Blackjack：要播開牌動畫，送新訊息時不能帶 view=None。"""
        from unittest.mock import AsyncMock, patch

        import cogs.blackjack as bj
        cog, inter = self._cog(), self._interaction()
        sent = SimpleNamespace(edit=AsyncMock())

        async def followup(**kwargs):
            self.assertFalse("view" in kwargs and kwargs["view"] is None)
            return sent
        inter.followup.send = AsyncMock(side_effect=followup)
        with patch.object(bj, "REVEAL_DELAY", 0), patch.object(bj, "BlackjackGame", side_effect=lambda *a: BlackjackGame(
                *a, deck=list(reversed(cards("10", "9", "K", "A"))))):
            self.assertTrue(await cog.start(inter, 100))
        inter.followup.send.assert_awaited_once()   # 只發一則訊息
        sent.edit.assert_awaited()                  # 之後的開牌動畫與結果都編輯同一則
        cog.bot.db.settle.assert_awaited_once()

    async def test_split_button_extends_bet_and_shows_hands(self):
        from unittest.mock import AsyncMock, patch

        import cogs.blackjack as bj
        cog, inter = self._cog(), self._interaction()
        cog.bot.db.extend_hold = AsyncMock(return_value=True)
        sent = SimpleNamespace(edit=AsyncMock())
        inter.followup.send = AsyncMock(return_value=sent)
        with patch.object(bj, "BlackjackGame", side_effect=lambda *a: BlackjackGame(
                *a, deck=list(reversed(cards("8", "8", "10", "7", "2", "3"))))):
            self.assertTrue(await cog.start(inter, 100))
        game = cog.games[1]
        user = inter.user
        view = bj.BlackjackView(cog, game, user)
        self.assertFalse(view.split_btn.disabled)
        click = self._interaction()
        click.message = sent
        await view.split_btn.callback(click)
        cog.bot.db.extend_hold.assert_awaited_once_with(7, 100)
        self.assertEqual(len(game.hands), 2)
        embed = cog.build_embed(game, user)
        self.assertIn("第 1 手", embed.description)
        self.assertIn("👉", embed.description)
        self.assertIn("總賭注", embed.footer.text)

    async def test_command_not_enough_money_error_stays_private(self):
        from unittest.mock import AsyncMock, patch

        import cogs.blackjack as bj
        cog, inter = self._cog(), self._interaction()
        inter.extras = {}
        inter.delete_original_response = AsyncMock()
        cog.bot.db.hold = AsyncMock(return_value=None)
        self.assertFalse(await cog.start(inter, 100))
        inter.response.defer.assert_awaited_once()             # 先回應再扣錢
        inter.delete_original_response.assert_awaited_once()   # 公開的「思考中…」要刪掉，錯誤才會是私密的
        self.assertTrue(inter.followup.send.await_args.kwargs["ephemeral"])
        self.assertEqual((cog.games, cog.starting), ({}, set()))

    def _not_found(self):
        from unittest.mock import MagicMock

        import discord
        return discord.NotFound(MagicMock(status=404, reason="Not Found"), {"code": 10062, "message": "Unknown interaction"})

    async def test_expired_interaction_takes_no_money_and_leaves_no_game(self):
        from unittest.mock import AsyncMock

        import discord
        for message in (None, SimpleNamespace(edit=AsyncMock())):     # 指令、再來一局
            cog, inter = self._cog(), self._interaction()
            inter.response.defer = AsyncMock(side_effect=self._not_found())
            with self.assertRaises(discord.NotFound):
                await cog.start(inter, 100, message=message)
            cog.bot.db.hold.assert_not_awaited()
            self.assertEqual((cog.games, cog.starting), ({}, set()))

    async def test_board_not_shown_cancels_and_refunds(self):
        from unittest.mock import AsyncMock, patch

        import cogs.blackjack as bj
        for again in (False, True):
            cog, inter = self._cog(), self._interaction()
            message = SimpleNamespace(edit=AsyncMock(side_effect=self._not_found())) if again else None
            inter.followup.send = AsyncMock(side_effect=[None] if again else [self._not_found(), None])
            with patch.object(bj, "BlackjackGame", side_effect=lambda *a: BlackjackGame(
                    *a, deck=list(reversed(cards("10", "7", "9", "8"))))), self.assertLogs("cogs.blackjack", "ERROR"):
                self.assertFalse(await cog.start(inter, 100, message=message))
            self.assertEqual(cog.games, {})
            self.assertEqual(cog.bot.db.settle.await_args.args[:2], (7, 100))     # 全額退還
            self.assertNotIn("game", cog.bot.db.settle.await_args.kwargs)         # 不算戰績

    async def test_double_start_only_opens_one_game(self):
        import asyncio
        from unittest.mock import AsyncMock, patch

        import cogs.blackjack as bj
        cog, first, second = self._cog(), self._interaction(), self._interaction()
        gate, defer_first = asyncio.Event(), first.response.defer

        async def slow(**kwargs):
            await gate.wait()
            await defer_first(**kwargs)
        first.response.defer = AsyncMock(side_effect=slow)
        with patch.object(bj, "BlackjackGame", side_effect=lambda *a: BlackjackGame(
                *a, deck=list(reversed(cards("10", "7", "9", "8"))))):
            task = asyncio.create_task(cog.start(first, 100))
            await asyncio.sleep(0)
            self.assertFalse(await cog.start(second, 100))
            gate.set()
            self.assertTrue(await task)
        cog.bot.db.hold.assert_awaited_once()

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
