import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from core.instance import acquire_single_instance


class SingleInstanceTest(unittest.TestCase):
    def test_second_copy_is_refused_until_first_exits(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "bot.lock")
            first = acquire_single_instance(path)
            self.assertIsNotNone(first)
            self.assertIsNone(acquire_single_instance(path))   # 第二個 bot 不能啟動
            first.close()                                       # 第一個結束（或當掉）後就能再啟動
            again = acquire_single_instance(path)
            self.assertIsNotNone(again)
            again.close()


class CheckinTest(unittest.IsolatedAsyncioTestCase):
    async def run_checkin(self, already):
        from cogs import economy

        cog = economy.Economy.__new__(economy.Economy)
        result = SimpleNamespace(already=already, streak=3, base=100, first_bonus=0, streak_bonus=20,
                                 gained=120, streak_broken=False, balance=500)
        cog.bot = SimpleNamespace(db=SimpleNamespace(checkin=AsyncMock(return_value=result)))
        interaction = MagicMock()
        interaction.delete_original_response = AsyncMock()
        with patch.object(economy, "defer", AsyncMock()) as defer, patch.object(economy, "respond", AsyncMock()) as respond:
            await cog.do_checkin(interaction)
        return defer, respond, interaction

    async def test_defers_before_touching_database(self):
        defer, respond, interaction = await self.run_checkin(already=False)
        defer.assert_awaited_once()
        self.assertFalse(respond.await_args.kwargs.get("ephemeral", False))   # 簽到成功公開顯示
        interaction.delete_original_response.assert_not_awaited()

    async def test_already_checked_in_is_private(self):
        defer, respond, interaction = await self.run_checkin(already=True)
        interaction.delete_original_response.assert_awaited_once()
        self.assertTrue(respond.await_args.kwargs["ephemeral"])


if __name__ == "__main__":
    unittest.main()
