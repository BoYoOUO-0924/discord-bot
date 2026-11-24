import datetime
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

import bot as bot_module
from core import ui


def interaction(age=0.0):
    created = datetime.datetime.fromtimestamp(time.time() - age, datetime.timezone.utc)
    return SimpleNamespace(created_at=created, extras={}, command=SimpleNamespace(qualified_name="blackjack"), data={})


def not_found(code=10062):
    return discord.NotFound(MagicMock(status=404, reason="Not Found"), {"code": code, "message": "x"})


class TimingTest(unittest.IsolatedAsyncioTestCase):
    def test_only_slow_arrival_is_logged(self):
        with self.assertNoLogs("core.ui", "WARNING"):
            ui.note_arrival(interaction(0.2))
        with self.assertLogs("core.ui", "WARNING") as logs:
            ui.note_arrival(interaction(2.5))
        self.assertIn("互動送達延遲 2.5", logs.output[0])
        self.assertIn("/blackjack", logs.output[0])

    def test_expired_error_tells_network_from_program(self):
        inter = interaction(3.0)
        with self.assertLogs("core.ui", "WARNING"):
            ui.note_arrival(inter)
        wrapped = discord.app_commands.CommandInvokeError(SimpleNamespace(name="blackjack"), not_found())
        with self.assertLogs("core.ui", "WARNING") as logs:
            self.assertFalse(ui.log_interaction_error(inter, wrapped, "指令 /blackjack"))
        self.assertIn("送達延遲 3.0", logs.output[0])
        self.assertIn("送達後 0.0", logs.output[0])
        with self.assertLogs("core.ui", "ERROR"):
            self.assertTrue(ui.log_interaction_error(inter, not_found(10008), "x"))      # 別的 404 照樣回報

    async def test_member_record_runs_in_background(self):
        started = []
        db = SimpleNamespace(touch_member=AsyncMock(), remember_user=AsyncMock())
        client = SimpleNamespace(db=db, features=MagicMock(), command_stats=None, run_background=started.append)
        inter = SimpleNamespace(client=client, command=SimpleNamespace(qualified_name="work"), guild_id=1,
                                type=discord.InteractionType.application_command,
                                user=SimpleNamespace(id=1, display_name="P", display_avatar=SimpleNamespace(url="u")))
        with patch.object(bot_module.app_commands.CommandTree, "__init__", return_value=None):
            tree = bot_module.AppTree.__new__(bot_module.AppTree)
        self.assertTrue(await tree.interaction_check(inter))
        db.touch_member.assert_not_awaited()          # 指令不用等資料庫
        await started[0]
        db.touch_member.assert_awaited_once_with(1, 1)
        db.remember_user.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
