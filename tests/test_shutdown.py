"""自動更新與後台「立即更新」的重新啟動：關閉時資料庫一定要關好，不然程式會卡住退不出去。"""
import asyncio
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock

import bot as bot_module
from core.db import Database


class RestartTest(unittest.TestCase):
    def run_until_restart(self, background: bool):
        """真的跑 Client.__aexit__、Bot.close 和 asyncio.run，只把 Discord 連線換成假的。"""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        bot = bot_module.Bot()
        bot.db = Database(os.path.join(tmp.name, "t.db"))

        async def main():
            gate = asyncio.Event()

            async def ws_close(code=1000):
                gate.set()

            async def login(token):
                await bot._async_setup_hook()
                await bot.db.connect()
                self.connection = bot.db._conn
                if background:
                    bot.run_background(bot.watch_loop_lag())

            async def connect(*, reconnect=True):
                bot.ws = MagicMock(close=ws_close)
                self.restart = asyncio.create_task(bot.request_restart())   # 和 cogs/updater.py 一樣放在獨立的工作
                await gate.wait()

            bot.login, bot.connect, bot.http.close = login, connect, AsyncMock()
            async with bot:
                await bot.start("x")

        try:
            asyncio.run(main())
            self.assertTrue(bot.restart_requested)
            self.assertIsNone(bot.db._conn)
            self.connection._thread.join(5)
            self.assertFalse(self.connection._thread.is_alive())
        finally:
            self.connection.stop()      # 失敗時也要讓執行緒結束，測試才不會跟著卡住

    def test_database_closed_with_background_task(self):
        self.run_until_restart(True)

    def test_database_closed_without_background_task(self):
        self.run_until_restart(False)


if __name__ == "__main__":
    unittest.main()
