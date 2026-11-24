import os
import random
import tempfile
import unittest
from itertools import product
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

import cogs.sicbo as sicbo_cog
from cogs.sicbo import MoreBetsView, RulesView, SicBo, TableView
from core.db import Database
from core.sicbo import MAX_PER_ROUND, SPOTS, SicBoRound, describe, payout

OUTCOMES = list(product(range(1, 7), repeat=3))


def rtp(key):
    return sum(payout(key, d) for d in OUTCOMES) / len(OUTCOMES)


class RulesTest(unittest.TestCase):
    def test_house_edge_of_current_odds(self):
        expected = {"big": 0.0278, "small": 0.0278, "odd": 0.0278, "even": 0.0278, "single:3": 0.0787,
                    "total:4": 0.1528, "total:17": 0.1528, "total:5": 0.1389, "total:6": 0.1667, "total:7": 0.0972,
                    "total:8": 0.125, "total:9": 0.1898, "total:10": 0.125, "total:11": 0.125,
                    "combo:1-2": 0.1667, "double:4": 0.1852, "triple:5": 0.162, "any_triple": 0.1389}
        for key, edge in expected.items():
            self.assertAlmostEqual(1 - rtp(key), edge, places=3, msg=key)
        # 每個押法莊家都有優勢，沒有穩賺的
        for key in SPOTS:
            self.assertLess(rtp(key), 1, key)

    def test_triple_kills_big_small_odd_even(self):
        for dice in ((1, 1, 1), (4, 4, 4), (6, 6, 6)):
            for key in ("big", "small", "odd", "even"):
                self.assertEqual(payout(key, dice), 0, (key, dice))
            self.assertEqual(payout("any_triple", dice), 31)
            self.assertEqual(payout(f"triple:{dice[0]}", dice), 181)
        self.assertIn("豹子", describe((4, 4, 4)))
        self.assertEqual(describe((3, 4, 5)), "總點數 12・大・雙")

    def test_single_die_counts(self):
        self.assertEqual(payout("single:2", (2, 5, 6)), 2)
        self.assertEqual(payout("single:2", (2, 2, 6)), 3)
        self.assertEqual(payout("single:2", (2, 2, 2)), 4)
        self.assertEqual(payout("single:2", (1, 5, 6)), 0)
        self.assertEqual(payout("double:2", (2, 2, 2)), 11)     # 三顆也算對子
        self.assertEqual(payout("combo:1-6", (6, 3, 1)), 6)
        self.assertEqual(payout("combo:1-6", (6, 3, 3)), 0)

    def test_round_settle_and_limits(self):
        rnd = SicBoRound(1)
        rnd.add(1, "big", 1_000, "A")
        rnd.add(1, "total:12", 500)
        rnd.add(2, "small", 2_000, "B")
        results = rnd.settle((3, 4, 5))
        self.assertEqual(results[1][:2], (1_500, 2_000 + 500 * 7))
        self.assertEqual(results[2][:2], (2_000, 0))
        self.assertEqual(rnd.spot_totals()[0], ("small", 1, 2_000))
        # 單局所有押注合計最多 5 萬，不管分散在幾種押法
        self.assertEqual(MAX_PER_ROUND, 50_000)
        self.assertIsNone(rnd.check(1, "triple:6", 48_500))
        self.assertIn("還能押 48,500", rnd.check(1, "triple:6", 48_501))
        self.assertIsNone(rnd.check(3, "big", 50_000))          # 別人的押注不影響
        self.assertEqual(rnd.clear(1), 1_500)
        self.assertNotIn(1, rnd.bets)


def not_found():
    return discord.NotFound(MagicMock(status=404, reason="Not Found"), {"code": 10062, "message": "Unknown interaction"})


def interaction(user_id=1, name="P1", channel=None):
    state = {"done": False}

    async def done(*args, **kwargs):
        state["done"] = True
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id, display_name=name), channel=channel, channel_id=getattr(channel, "id", None),
        guild_id=None, data={}, extras={}, delete_original_response=AsyncMock(), edit_original_response=AsyncMock(),
        response=SimpleNamespace(is_done=lambda: state["done"], send_message=AsyncMock(side_effect=done),
                                 edit_message=AsyncMock(side_effect=done), defer=AsyncMock(side_effect=done)),
        original_response=AsyncMock(return_value=SimpleNamespace(id=99)),
        followup=SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=99))))


class TableFlowTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.tmp.name, "test.db"))
        await self.db.connect()
        for uid in (1, 2):
            await self.db.add_points(uid, 100_000, "測試")
        self.bot = MagicMock()
        self.bot.db = self.db
        self.cog = SicBo(self.bot)
        self.message = MagicMock(edit=AsyncMock(), jump_url="https://discord/x")
        self.channel = MagicMock(id=10, send=AsyncMock(return_value=self.message))
        self.channel.get_partial_message = MagicMock(return_value=self.message)
        patcher = patch.object(sicbo_cog.asyncio, "create_task", side_effect=lambda coro: coro.close() or MagicMock())
        patcher.start()
        self.addCleanup(patcher.stop)

    async def asyncTearDown(self):
        await self.db.close()
        self.tmp.cleanup()

    async def open(self):
        inter = interaction(channel=self.channel)
        await self.cog.open_table(inter)
        return self.cog.tables[10], inter

    async def balance(self, uid):
        return await self.db.get_points(uid)

    async def test_open_table_posts_betting_message(self):
        table, inter = await self.open()
        self.assertEqual((table.phase, table.number), ("betting", 1))
        kwargs = inter.followup.send.await_args.kwargs
        self.assertIsInstance(kwargs["view"], TableView)
        self.assertIn("開放下注", kwargs["embed"].description)

    async def test_bet_holds_money_and_stacks(self):
        table, _ = await self.open()
        user = SimpleNamespace(id=1, display_name="P1")
        ok, text = await self.cog.place_bet(user, table, "big")
        self.assertTrue(ok, text)
        ok, _ = await self.cog.place_bet(user, table, "big")
        self.assertTrue(ok)
        self.assertEqual(table.round.bets[1], {"big": 10_000})
        self.assertEqual(await self.balance(1), 90_000)
        self.assertEqual(len(await self.db.list_holds()), 1)        # 同一局只用一筆保管

    async def test_bet_after_close_must_wait_for_next_round(self):
        table, _ = await self.open()
        await self.cog.place_bet(SimpleNamespace(id=1, display_name="P1"), table, "big")
        self.assertTrue(await self.cog.close_round(table))
        ok, text = await self.cog.place_bet(SimpleNamespace(id=2, display_name="P2"), table, "small")
        self.assertFalse(ok)
        self.assertIn("下一局", text)
        self.assertEqual(await self.balance(2), 100_000)
        ok, text = await self.cog.withdraw(1, table)                   # 停止下注後也不能撤回
        self.assertFalse(ok)
        # /sicbo 也只會叫人等下一局，不會再開一桌
        inter = interaction(user_id=2, channel=self.channel)
        await self.cog.open_table(inter)
        self.assertIn("下一局", inter.response.send_message.await_args.kwargs["embed"].description)
        self.assertIs(self.cog.tables[10], table)

    async def test_reveal_settles_everyone(self):
        table, _ = await self.open()
        p1, p2 = SimpleNamespace(id=1, display_name="P1"), SimpleNamespace(id=2, display_name="P2")
        await self.cog.place_bet(p1, table, "big")
        await self.cog.place_bet(p1, table, "total:12")
        self.cog.chips[2] = 10_000
        await self.cog.place_bet(p2, table, "small")
        await self.cog.close_round(table)
        await self.cog.reveal(table, (3, 4, 5))
        self.assertEqual(await self.balance(1), 100_000 - 10_000 + 10_000 + 35_000)
        self.assertEqual(await self.balance(2), 90_000)
        self.assertEqual(await self.db.list_holds(), [])
        self.assertEqual(table.phase, "break")
        self.assertEqual(list(table.history), [(3, 4, 5)])
        stats = {r["game"]: (r["played"], r["won"]) for r in await self.db.game_stats(1)}
        self.assertEqual(stats["sicbo"], (1, 1))
        self.bot.dispatch.assert_any_call("activity", 1, "game", 10, {"game": "sicbo", "won": True})
        embed = self.message.edit.await_args.kwargs["embed"]
        self.assertIn("大", embed.description)
        # 下一局發新訊息
        await self.cog.start_round(table)
        self.assertEqual((table.number, table.phase), (2, "betting"))
        self.channel.send.assert_awaited()

    async def test_withdraw_refunds(self):
        table, _ = await self.open()
        await self.cog.place_bet(SimpleNamespace(id=1, display_name="P1"), table, "odd")
        ok, text = await self.cog.withdraw(1, table)
        self.assertTrue(ok, text)
        self.assertEqual(await self.balance(1), 100_000)
        self.assertNotIn(1, table.round.bets)

    async def test_empty_round_closes_table(self):
        table, _ = await self.open()
        self.assertFalse(await self.cog.close_round(table))
        self.assertNotIn(10, self.cog.tables)
        self.assertIn("沒人下注", self.message.edit.await_args.kwargs["embed"].description)

    async def test_shutdown_refunds_open_bets(self):
        table, _ = await self.open()
        await self.cog.place_bet(SimpleNamespace(id=1, display_name="P1"), table, "even")
        await self.cog.shutdown(table, "管理員收桌")
        self.assertEqual(await self.balance(1), 100_000)
        self.assertEqual(await self.db.list_holds(), [])
        await self.cog.reveal(table, (1, 2, 3))              # 收桌後就不會再結算
        self.assertEqual(await self.balance(1), 100_000)

    async def test_insufficient_funds(self):
        table, _ = await self.open()
        before = await self.balance(1)
        await self.db.add_points(1, 3_000 - before, "測試")
        ok, text = await self.cog.place_bet(SimpleNamespace(id=1, display_name="P1"), table, "big")
        self.assertFalse(ok)
        self.assertIn("存款不足", text)
        self.assertEqual(await self.balance(1), 3_000)
        self.assertEqual(table.round.bets, {})

    async def test_more_bets_select_places_bet(self):
        table, _ = await self.open()
        view = MoreBetsView(self.cog, table)
        select = view.children[1]                         # 總和
        select._values = ["total:10"]
        with patch.object(type(select), "values", new_callable=lambda: property(lambda s: ["total:10"])):
            inter = interaction(channel=self.channel)
            await select.callback(inter)
        self.assertEqual(table.round.bets[1], {"total:10": 5_000})
        self.assertIsInstance(inter.edit_original_response.await_args.kwargs["view"], MoreBetsView)

    async def test_expired_interaction_leaves_no_table(self):
        inter = interaction(channel=self.channel)
        inter.response.defer = AsyncMock(side_effect=not_found())
        with self.assertRaises(discord.NotFound):
            await self.cog.open_table(inter)
        self.assertEqual(self.cog.tables, {})

    async def test_board_not_shown_removes_table(self):
        inter = interaction(channel=self.channel)
        inter.followup.send = AsyncMock(side_effect=[not_found(), None])
        with self.assertLogs("cogs.sicbo", "ERROR"):
            await self.cog.open_table(inter)
        self.assertEqual(self.cog.tables, {})
        await self.open()                                       # 馬上可以再開
        self.assertIn(10, self.cog.tables)

    async def test_expired_bet_takes_no_money(self):
        table, _ = await self.open()
        inter = interaction(channel=self.channel)
        inter.response.defer = AsyncMock(side_effect=not_found())
        with self.assertRaises(discord.NotFound):
            await self.cog.bet_from(inter, table, "big")
        self.assertEqual(await self.balance(1), 100_000)

    async def test_round_limit_over_many_spots(self):
        table, _ = await self.open()
        user = SimpleNamespace(id=1, display_name="P1")
        self.cog.chips[1] = 25_000
        for key in ("big", "total:12"):
            self.assertTrue((await self.cog.place_bet(user, table, key))[0])
        ok, text = await self.cog.place_bet(user, table, "odd")
        self.assertFalse(ok)
        self.assertIn("合計最多押 50,000", text)
        self.assertEqual(await self.balance(1), 50_000)

    async def test_rules_stay_available_while_rolling_and_after_result(self):
        table, _ = await self.open()
        await self.cog.place_bet(SimpleNamespace(id=1, display_name="P1"), table, "big")
        await self.cog.close_round(table)
        self.assertIsInstance(self.message.edit.await_args.kwargs["view"], RulesView)
        await self.cog.reveal(table, (1, 2, 3))
        self.assertIsInstance(self.message.edit.await_args.kwargs["view"], RulesView)
        await self.cog.cog_load()                               # 註冊成永久介面
        view = self.bot.add_view.call_args.args[0]
        self.assertTrue(view.is_persistent())
        inter = interaction()
        await view.rules.callback(inter)
        self.assertIn("押法說明", inter.response.send_message.await_args.kwargs["embed"].title)

    async def test_countdown_text_moves(self):
        table, _ = await self.open()
        texts = []
        for left in (30, 15, 4):
            with patch.object(sicbo_cog.time, "time", return_value=table.closes_at - left):
                texts.append(self.cog.betting_embed(table).description)
            self.assertIn(f"剩 **{left}** 秒", texts[-1])
            self.assertNotIn(":R>", texts[-1])
        self.assertEqual(len(set(texts)), 3)

    async def test_tick_edits_until_phase_ends(self):
        table, _ = await self.open()
        clock = [table.closes_at - 30]
        slept = []

        async def sleep(seconds):
            slept.append(seconds)
            clock[0] += seconds
        with patch.object(sicbo_cog.time, "time", side_effect=lambda: clock[0]), \
                patch.object(sicbo_cog.asyncio, "sleep", side_effect=sleep):
            await self.cog.tick(table, "betting", table.closes_at)
        shown = [c.kwargs["embed"].description for c in self.message.edit.await_args_list]
        for left, text in zip((25, 20, 15, 10, 8, 6, 4, 2), shown, strict=True):
            self.assertIn(f"剩 **{left}** 秒", text)
        self.assertTrue(all(s >= 1.9 for s in slept), slept)        # 不會太密集被限速

    async def test_late_countdown_does_not_overwrite_result(self):
        table, _ = await self.open()
        await self.cog.place_bet(SimpleNamespace(id=1, display_name="P1"), table, "big")
        await self.cog.close_round(table)
        edits = self.message.edit.await_count
        self.assertFalse(await self.cog.refresh(table, "betting", 1))
        self.assertEqual(self.message.edit.await_count, edits)
        await self.cog.reveal(table, (1, 2, 3))
        with patch.object(sicbo_cog.time, "time", return_value=table.next_at - 4):
            self.assertTrue(await self.cog.refresh(table, "break", 1))
        embed = self.message.edit.await_args.kwargs["embed"]
        self.assertIn("剩 **4** 秒後開放下注", embed.fields[-1].value)
        self.assertEqual(embed.fields[0].name, "結算")                # 開獎結果還在

    async def test_chip_amount_is_shown(self):
        table, _ = await self.open()
        self.assertIn("5,000", table.view.chip.label)
        self.cog.chips[1] = 10_000
        _, text = await self.cog.place_bet(SimpleNamespace(id=1, display_name="P1"), table, "big")
        self.assertIn("目前籌碼 10,000", text)


class SimulationTest(unittest.TestCase):
    def test_random_bets_on_every_spot(self):
        """10 萬局隨機押全部押法，返還率要落在理論值的 4 個標準誤差內。"""
        rng, keys, n = random.Random(7), list(SPOTS), 100_000
        outcomes = [payout(k, d) for k in keys for d in OUTCOMES]
        mean = sum(outcomes) / len(outcomes)
        variance = sum((x - mean) ** 2 for x in outcomes) / len(outcomes)
        paid = sum(payout(rng.choice(keys), tuple(rng.randint(1, 6) for _ in range(3))) for _ in range(n))
        self.assertAlmostEqual(paid / n, mean, delta=4 * (variance / n) ** 0.5)
        self.assertTrue(0.8 < mean < 1, mean)


if __name__ == "__main__":
    unittest.main()
