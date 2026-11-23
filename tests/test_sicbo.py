import asyncio
import os
import tempfile
import unittest
from itertools import product
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import cogs.sicbo as sicbo_cog
from cogs.sicbo import MoreBetsView, SicBo, Table, TableView
from core.db import Database
from core.sicbo import (MAX_PER_ROUND, MAX_PER_SPOT, SPOTS, SicBoRound, describe, payout)

OUTCOMES = list(product(range(1, 7), repeat=3))


def rtp(key):
    return sum(payout(key, d) for d in OUTCOMES) / len(OUTCOMES)


class RulesTest(unittest.TestCase):
    def test_house_edge_matches_macau_table(self):
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
        self.assertIsNone(rnd.check(1, "big", MAX_PER_SPOT - 1_000))
        self.assertIn("最多押", rnd.check(1, "big", MAX_PER_SPOT))
        rnd.add(3, "big", MAX_PER_SPOT)
        rnd.add(3, "small", MAX_PER_SPOT)
        rnd.add(3, "odd", MAX_PER_SPOT)
        rnd.add(3, "even", MAX_PER_SPOT)
        rnd.add(3, "any_triple", MAX_PER_SPOT)
        self.assertIn("每局最多", rnd.check(3, "total:4", 100))
        self.assertEqual(rnd.stake(3), MAX_PER_ROUND)
        self.assertEqual(rnd.clear(1), 1_500)
        self.assertNotIn(1, rnd.bets)


def interaction(user_id=1, name="P1", channel=None):
    state = {"done": False}

    async def done(*args, **kwargs):
        state["done"] = True
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id, display_name=name), channel=channel, channel_id=getattr(channel, "id", None),
        guild_id=None, data={},
        response=SimpleNamespace(is_done=lambda: state["done"], send_message=AsyncMock(side_effect=done),
                                 edit_message=AsyncMock(side_effect=done)),
        original_response=AsyncMock(return_value=SimpleNamespace(id=99)),
        followup=SimpleNamespace(send=AsyncMock()))


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
        kwargs = inter.response.send_message.await_args.kwargs
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

    async def test_limits_and_insufficient_funds(self):
        table, _ = await self.open()
        self.cog.chips[1] = 200_000
        ok, text = await self.cog.place_bet(SimpleNamespace(id=1, display_name="P1"), table, "big")
        self.assertFalse(ok)
        self.assertIn("存款不足", text)
        self.assertEqual(await self.balance(1), 100_000)

    async def test_more_bets_select_places_bet(self):
        table, _ = await self.open()
        view = MoreBetsView(self.cog, table)
        select = view.children[1]                         # 總和
        select._values = ["total:10"]
        with patch.object(type(select), "values", new_callable=lambda: property(lambda s: ["total:10"])):
            inter = interaction(channel=self.channel)
            await select.callback(inter)
        self.assertEqual(table.round.bets[1], {"total:10": 5_000})
        self.assertIsInstance(inter.response.edit_message.await_args.kwargs["view"], MoreBetsView)

    async def test_simulated_rtp_is_below_one(self):
        # 10 萬局隨機押大小，返還率應該接近 97.2%
        import random
        rng = random.Random(7)
        stake = paid = 0
        for _ in range(100_000):
            dice = tuple(rng.randint(1, 6) for _ in range(3))
            stake += 1
            paid += payout(rng.choice(("big", "small", "odd", "even")), dice)
        self.assertTrue(0.96 < paid / stake < 0.985, paid / stake)


if __name__ == "__main__":
    unittest.main()
