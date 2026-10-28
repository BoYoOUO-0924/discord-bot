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


if __name__ == "__main__":
    unittest.main()
