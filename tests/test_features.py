import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiohttp.test_utils import TestClient, TestServer

from cogs.hub import Hub
from core import catalog
from core.db import Database
from core.features import (FEATURES, GLOBAL, FeatureFlags, feature_for_action, feature_for_command)
from tests.test_webadmin import HEADERS, PASSWORD, FakeBot
from webadmin.server import AdminServer


class MappingTest(unittest.TestCase):
    def test_commands_and_actions(self):
        self.assertEqual(feature_for_command("stock buy"), "stocks")
        self.assertEqual(feature_for_command("project github"), "tasks")
        self.assertIsNone(feature_for_command("help"))
        self.assertIsNone(feature_for_command("menu"))
        self.assertEqual(feature_for_action("game:poker"), "poker")
        self.assertEqual(feature_for_action("quote:台積電"), "stocks")
        self.assertEqual(feature_for_action("task:add|週五|0|報告"), "tasks")
        self.assertEqual(feature_for_action("weather:tomorrow"), "weather")
        self.assertEqual(feature_for_action("checkin"), "economy")
        self.assertIsNone(feature_for_action("help"))

    def test_every_help_command_maps_to_a_feature_or_is_basic(self):
        basic = {"menu", "help", "clear"}
        for _, commands in catalog.HELP_SECTIONS:
            for name, _, _ in commands:
                root = name.split()[0]
                with self.subTest(name=name):
                    self.assertTrue(root in basic or feature_for_command(name), f"/{name} 沒有對應的功能開關")


class FlagsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.tmp.name, "test.db"))
        await self.db.connect()
        self.flags = FeatureFlags(self.db)
        await self.flags.load()

    async def asyncTearDown(self):
        await self.db.close()
        self.tmp.cleanup()

    async def test_precedence_and_persistence(self):
        self.assertTrue(self.flags.is_enabled("poker", 1))           # 預設開
        await self.flags.set(1, "poker", False)
        self.assertFalse(self.flags.is_enabled("poker", 1))
        self.assertTrue(self.flags.is_enabled("poker", 2))           # 別的伺服器不受影響
        await self.flags.set(GLOBAL, "slots", False)
        await self.flags.set(1, "slots", True)
        self.assertFalse(self.flags.is_enabled("slots", 1))          # 全域關閉優先
        self.assertTrue(self.flags.command_enabled("help", 1))       # 基本功能永遠開
        # 重新載入（模擬 bot 重開）設定還在
        again = FeatureFlags(self.db)
        await again.load()
        self.assertFalse(again.is_enabled("poker", 1))
        await again.set(1, "poker", None)                            # 回到預設
        self.assertTrue(again.is_enabled("poker", 1))
        with self.assertRaises(ValueError):
            await again.set(1, "nope", False)

    async def test_hub_blocks_disabled_and_help_hides_it(self):
        await self.flags.set(1, "stocks", False)
        bot = SimpleNamespace(db=self.db, features=self.flags, mention=lambda n: f"/{n}",
                              get_cog=lambda name: SimpleNamespace(send_account=AsyncMock()))
        hub = Hub(bot)
        sent = []
        interaction = SimpleNamespace(guild_id=1, guild=None, user=SimpleNamespace(id=5, display_name="小明", display_avatar=SimpleNamespace(url=None)),
                                      response=SimpleNamespace(is_done=lambda: False,
                                                               send_message=AsyncMock(side_effect=lambda **k: sent.append(k))),
                                      original_response=AsyncMock(), channel=None)
        await hub.handle(interaction, "stocks")
        self.assertIn("已經關閉", sent[0]["embed"].description)
        text = "".join(f.value for f in hub.help_embed(1).fields)
        self.assertNotIn("/stock", text)
        self.assertIn("/stock", "".join(f.value for f in hub.help_embed(2).fields))


class FeatureApiTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.tmp.name, "test.db"))
        await self.db.connect()
        self.bot = FakeBot(self.db)
        self.bot.features = FeatureFlags(self.db)
        server = AdminServer(self.bot, PASSWORD, "127.0.0.1", 0, os.path.join(self.tmp.name, "bot.log"))
        self.client = TestClient(TestServer(server.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        await self.db.close()
        self.tmp.cleanup()

    async def post(self, **body):
        return await self.client.post("/api/features", json=body, headers=HEADERS)

    async def test_get_and_set(self):
        self.assertEqual((await self.client.get("/api/features")).status, 401)
        await self.client.post("/api/login", json={"password": PASSWORD}, headers=HEADERS)
        data = await (await self.client.get("/api/features")).json()
        self.assertEqual(len(data["features"]), len(FEATURES))
        self.assertTrue(all(data["global"].values()))

        self.assertEqual((await self.post(guild="0", feature="music", enabled=False)).status, 200)
        self.assertEqual((await self.post(guild="123", feature="poker", enabled=False)).status, 200)
        data = await (await self.client.get("/api/features")).json()
        self.assertFalse(data["global"]["music"])
        self.assertEqual(data["overrides"], {"123": {"poker": False}})
        self.assertIn("123", [g["id"] for g in data["guilds"]])      # 有設定的伺服器會列出來
        self.assertFalse(self.bot.features.is_enabled("music", 5))     # 立即生效

        await self.post(guild="0", feature="music", enabled=True)      # 全域開回來 = 拿掉設定
        self.assertEqual(await self.db.list_feature_flags(), [r for r in await self.db.list_feature_flags()
                                                              if r["feature"] == "poker"])
        self.assertEqual((await self.post(guild="0", feature="nope", enabled=False)).status, 400)
        self.assertEqual((await self.post(guild="x", feature="music", enabled=False)).status, 400)
        self.assertEqual((await self.client.post("/api/features", json={"guild": "0", "feature": "music",
                                                                         "enabled": False})).status, 403)   # 沒有 CSRF 標頭
        audit = await (await self.client.get("/api/audit")).json()
        self.assertTrue(any(item["action"] == "feature" for item in audit["items"]))


class AssistantGateTest(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_assistant_ignores_and_disabled_feature_refuses(self):
        from tests.test_assistant_cog import BOT_ID, FakeAI, make_cog, make_message
        flags = SimpleNamespace(is_enabled=lambda feature, guild_id=None: feature not in ("assistant",))
        cog, me = make_cog(FakeAI(intent="weather"))
        cog.bot.features = flags
        message = make_message(f"<@{BOT_ID}> 明天下雨嗎", me)
        message.guild = SimpleNamespace(id=1)
        await cog.on_message(message)
        message.reply.assert_not_awaited()                       # 嘎哇聊天整個關掉：不回

        cog, me = make_cog(FakeAI(intent="weather"))
        cog.bot.features = SimpleNamespace(is_enabled=lambda feature, guild_id=None: feature != "weather")
        message = make_message(f"<@{BOT_ID}> 明天下雨嗎", me)
        message.guild = SimpleNamespace(id=1)
        await cog.on_message(message)
        self.assertIn("已經關閉", message.reply.await_args.args[0])   # 天氣關掉：告訴使用者
        self.assertNotIn("view", message.reply.await_args.kwargs)


if __name__ == "__main__":
    unittest.main()
