import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

import cogs.earn as earn
from cogs.earn import JOBS, WORK_DAILY_HOURS, WORK_MAX_HOURS, WORK_MIN_HOURS, Earn, WorkHoursView, jobs_for, work_pay
from core.db import Database
from core.timeutil import today_tw


class JobTableTest(unittest.TestCase):
    def test_every_hour_has_jobs_and_fair_pay(self):
        for hours in range(WORK_MIN_HOURS, WORK_MAX_HOURS + 1):
            jobs = jobs_for(hours)
            self.assertGreaterEqual(len(jobs), 2, hours)
            # 每個時數的平均時薪都要差不多，選長選短不吃虧
            average = sum(sum(job.hourly) / 2 for job in jobs) / len(jobs)
            self.assertTrue(1_900 <= average <= 2_150, (hours, average))

    def test_pay_scales_with_hours(self):
        job = JOBS[0]
        with patch.object(earn.random, "randint", return_value=2_000):
            self.assertEqual(work_pay(job, 1, False), 2_000)
            self.assertEqual(work_pay(job, 2, False), 4_000)
            self.assertEqual(work_pay(job, 2, True), 8_000)       # 大成功整班翻倍


def interaction(user_id=1):
    state = {"done": False}

    async def done(**kwargs):
        state["done"] = True
    send = AsyncMock()
    user = SimpleNamespace(id=user_id, display_name="P1", display_avatar=SimpleNamespace(url="https://x/a.png"))
    return SimpleNamespace(
        user=user, channel_id=10, channel=None, guild_id=None, extras={}, delete_original_response=AsyncMock(),
        response=SimpleNamespace(is_done=lambda: state["done"], send_message=AsyncMock(side_effect=done),
                                 defer=AsyncMock(side_effect=done)),
        original_response=AsyncMock(), followup=SimpleNamespace(send=send),
        edit_original_response=AsyncMock())


class WorkFlowTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.tmp.name, "test.db"))
        await self.db.connect()
        self.bot = MagicMock()
        self.bot.db = self.db
        self.cog = Earn(self.bot)

    async def asyncTearDown(self):
        await self.db.close()
        self.tmp.cleanup()

    def sent(self, inter):
        sent = inter.followup.send if inter.followup.send.await_args else inter.response.send_message
        return sent.await_args.kwargs

    async def test_expired_interaction_is_not_paid(self):
        inter = interaction()
        inter.response.defer = AsyncMock(side_effect=discord.NotFound(
            MagicMock(status=404, reason="Not Found"), {"code": 10062, "message": "Unknown interaction"}))
        with self.assertRaises(discord.NotFound):
            await self.cog.do_work(inter, 8)
        self.assertEqual((await self.db.income_status(1, today_tw()))[1], 0)     # 沒發薪也沒算時數

    async def test_blocked_message_is_private(self):
        with patch.object(earn.time, "time", return_value=1000.0):
            await self.cog.do_work(interaction(), 8)
            inter = interaction()
            await self.cog.do_work(inter, 1)
        inter.delete_original_response.assert_awaited_once()       # 刪掉公開的「思考中…」
        self.assertTrue(self.sent(inter)["ephemeral"])

    async def test_default_is_one_hour(self):
        inter = interaction()
        with patch.object(earn.time, "time", return_value=1000.0):
            await self.cog.work.callback(self.cog, inter)
        until, used, _ = await self.db.income_status(1, today_tw())
        self.assertEqual((until, used), (1000.0 + 3600, 1))
        self.assertFalse(self.sent(inter)["ephemeral"])
        self.bot.dispatch.assert_called_once_with("activity", 1, "work", 10, {"hours": 1})

    async def test_eight_hours_then_still_working(self):
        with patch.object(earn.time, "time", return_value=1000.0):
            await self.cog.do_work(interaction(), 8)
        inter = interaction()
        with patch.object(earn.time, "time", return_value=1000.0 + 7 * 3600):
            await self.cog.do_work(inter, 1)
        self.assertTrue(self.sent(inter)["ephemeral"])
        self.assertIn("還在上班", self.sent(inter)["embed"].title)
        self.assertEqual((await self.db.income_status(1, today_tw()))[1], 8)

    async def test_last_shift_shortened_to_daily_limit(self):
        now = 1000.0
        with patch.object(earn.time, "time", return_value=now):
            await self.cog.do_work(interaction(), 8)
        inter = interaction()
        with patch.object(earn.time, "time", return_value=now + 8 * 3600):
            await self.cog.do_work(inter, 8)                       # 今天只剩 4 小時
        self.assertIn("只剩 4 小時", self.sent(inter)["embed"].description)
        until, used, _ = await self.db.income_status(1, today_tw())
        self.assertEqual((until, used), (now + 12 * 3600, WORK_DAILY_HOURS))
        inter = interaction()
        with patch.object(earn.time, "time", return_value=now + 12 * 3600):
            await self.cog.do_work(inter, 1)
        self.assertIn("今天做夠了", self.sent(inter)["embed"].title)

    async def test_picker_disables_hours_over_limit(self):
        with patch.object(earn.time, "time", return_value=1000.0):
            await self.cog.do_work(interaction(), 8)
        inter = interaction()
        with patch.object(earn.time, "time", return_value=1000.0 + 8 * 3600):
            await self.cog.ask_work_hours(inter)
        view = self.sent(inter)["view"]
        self.assertTrue(self.sent(inter)["ephemeral"])
        labels = {b.label: b.disabled for b in view.children}
        self.assertEqual(labels, {"1 小時": False, "2 小時": False, "4 小時": False, "8 小時": True})

    async def test_picker_button_starts_work(self):
        origin = interaction()
        view = WorkHoursView(self.cog, origin, 3)
        labels = [b.label for b in view.children]
        self.assertIn("剩下的 3 小時", labels)
        click = interaction()
        with patch.object(earn.time, "time", return_value=1000.0):
            await view.children[1].callback(click)                   # 2 小時
        self.assertEqual((await self.db.income_status(1, today_tw()))[1], 2)
        origin.edit_original_response.assert_awaited_once()          # 選單收起來
