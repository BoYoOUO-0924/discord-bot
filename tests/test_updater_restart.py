import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from discord.ext import commands


class UpdaterRestartTest(unittest.IsolatedAsyncioTestCase):
    async def test_restart_from_check_loop_fully_closes_bot(self):
        """關閉 bot 會取消 check_loop；重啟不能跟著被取消，否則指令都卸載了 bot 卻還在線上。"""
        from cogs import updater as updater_cog

        bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
        bot.settings = SimpleNamespace(auto_update=False)
        closed = asyncio.Event()

        async def request_restart():
            await bot.close()
            closed.set()

        bot.request_restart = request_restart
        await bot.load_extension("cogs.updater")
        cog = bot.get_cog("Updater")

        status = SimpleNamespace(branch="main", behind=1, ahead=0, dirty=False, current="a" * 40, latest="b" * 40)
        with patch.object(updater_cog.updater, "check", AsyncMock(return_value=status)), \
             patch.object(updater_cog.updater, "git", AsyncMock(return_value="https://github.com/o/r.git")), \
             patch.object(updater_cog.updater, "ci_state", AsyncMock(return_value="success")), \
             patch.object(updater_cog.updater, "apply", AsyncMock()), \
             patch.object(updater_cog.aiohttp, "ClientSession", MagicMock()):
            cog.check_loop._before_loop = None   # 不等連線，直接跑一次
            cog.check_loop.start()
            await asyncio.wait_for(closed.wait(), timeout=5)

        self.assertTrue(bot.is_closed())
        self.assertEqual(bot.extensions, {})


class UpdateNowTest(unittest.IsolatedAsyncioTestCase):
    def make_cog(self, enabled=True):
        from cogs.updater import Updater

        bot = SimpleNamespace(settings=SimpleNamespace(auto_update=True), request_restart=AsyncMock())
        cog = Updater(bot)
        cog.enabled = enabled
        return cog

    def status(self):
        return SimpleNamespace(current="a" * 40, latest="b" * 40)

    async def test_updates_right_away_even_with_games_running(self):
        from cogs import updater as updater_cog

        cog = self.make_cog()
        cog.busy = lambda: True
        cog.evaluate = AsyncMock(return_value=("update", "有 1 個新版本，自動測試已通過", self.status()))
        with patch.object(updater_cog.updater, "apply", AsyncMock()) as apply:
            message = await cog.update_now()
            await cog.restart_task
        apply.assert_awaited_once()
        cog.bot.request_restart.assert_awaited_once()
        self.assertIn("重新啟動", message)

    async def test_waits_for_ci_then_updates(self):
        from cogs import updater as updater_cog

        cog = self.make_cog()
        results = [("wait", "測試還在跑", self.status()), ("update", "通過", self.status())]
        cog.evaluate = AsyncMock(side_effect=results)
        with patch.object(updater_cog, "CI_POLL_SECONDS", 0),              patch.object(updater_cog.updater, "apply", AsyncMock()) as apply:
            message = await cog.update_now()
            self.assertIn("通過後會立刻更新", message)
            self.assertIn("正在等", await cog.update_now())   # 重複按不會再開一個
            await asyncio.wait_for(cog.ci_wait_task, timeout=5)
        apply.assert_awaited_once()

    async def test_already_current(self):
        cog = self.make_cog()
        cog.evaluate = AsyncMock(return_value=("current", "已經是最新版本", self.status()))
        self.assertEqual(await cog.update_now(), "已經是最新版本")

    async def test_disabled_raises(self):
        from core.updater import UpdateError

        with self.assertRaises(UpdateError):
            await self.make_cog(enabled=False).update_now()


if __name__ == "__main__":
    unittest.main()
