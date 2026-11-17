"""管理後台：伺服器設定、音樂、股票、Minecraft 頁面的 API。"""
import asyncio
from datetime import date
import json
import os
import stat
import tempfile
import unittest
from types import SimpleNamespace

from aiohttp.test_utils import TestClient, TestServer

from core.db import Database
from core.music import Track
from tests.test_webadmin import HEADERS, PASSWORD, FakeBot
from webadmin.server import AdminServer


class Perms:
    def __init__(self, ok=True):
        self.view_channel = self.send_messages = self.embed_links = ok


class Channel:
    def __init__(self, cid, name, ok=True):
        self.id, self.name, self.ok = cid, name, ok

    def permissions_for(self, me):
        return Perms(self.ok)


class Guild:
    def __init__(self):
        self.id, self.name, self.member_count = 77, "讀書會", 12
        self.me = object()
        self.text_channels = [Channel(1, "一般"), Channel(2, "公告", ok=False)]


class FakeMusic:
    def __init__(self, guild):
        voice = SimpleNamespace(channel=SimpleNamespace(name="語音", members=[SimpleNamespace(display_name="小明", bot=False)]),
                                is_playing=lambda: True, is_paused=lambda: False)
        queue = SimpleNamespace(tracks=[Track("晴天", "https://youtu.be/x", 269, 1, "小明")], index=0,
                                has_next=False, has_prev=False)
        queue.current = queue.tracks[0]
        self.players = {guild.id: SimpleNamespace(guild=guild, voice=voice, queue=queue, playing=True, paused=False, loading=False)}
        self.calls = []

    async def control(self, guild, action):
        self.calls.append(action)
        return "已暫停"


class PagesApiTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.tmp.name, "t.db"))
        await self.db.connect()
        self.bot = FakeBot(self.db)
        self.guild = Guild()
        self.bot.guilds = [self.guild]
        self.bot.get_guild = lambda gid: self.guild if gid == self.guild.id else None
        server = AdminServer(self.bot, PASSWORD, "127.0.0.1", 0, os.path.join(self.tmp.name, "bot.log"))
        self.client = TestClient(TestServer(server.app))
        await self.client.start_server()
        await self.client.post("/api/login", json={"password": PASSWORD}, headers=HEADERS)

    async def asyncTearDown(self):
        await self.client.close()
        await self.db.close()
        self.tmp.cleanup()

    async def post(self, path, body):
        return await self.client.post(path, json=body, headers=HEADERS)

    async def test_guild_settings(self):
        data = await (await self.client.get("/api/guilds/77/settings")).json()
        self.assertFalse(data["custom_places"])
        self.assertEqual([c["problem"] for c in data["channels"]], [None, "沒有發訊息權限"])
        places = (await (await self.client.get("/api/places?q=淡水")).json())["items"]
        self.assertTrue(places)

        resp = await self.post("/api/guilds/77/settings", {"places": [places[0]["key"]], "weather_channel": "1",
                                                           "weather_time": "07:30", "alerts_channel": "1", "alerts_all": True})
        self.assertEqual(resp.status, 200)
        saved = await self.db.get_guild_settings(77)
        self.assertEqual(json.loads(saved["weather.places"]), [places[0]["key"]])
        self.assertEqual((saved["weather.channel"], saved["weather.time"], saved["alerts.all_types"]), ("1", "07:30", "1"))

        self.assertEqual((await self.post("/api/guilds/77/settings", {"weather_channel": "2"})).status, 400)   # 沒權限的頻道
        self.assertEqual((await self.post("/api/guilds/77/settings", {"weather_time": "25:00"})).status, 400)
        self.assertEqual((await self.post("/api/guilds/77/settings", {"places": ["火星|不存在"]})).status, 400)
        await self.post("/api/guilds/77/settings", {"places": [], "alerts_channel": None})
        saved = await self.db.get_guild_settings(77)
        self.assertNotIn("weather.places", saved)
        self.assertNotIn("alerts.channel", saved)

        # 地震通報：頻道與門檻，預設 4 級不存
        self.assertEqual(data["earthquake"]["level"], "4")
        self.assertIn({"value": "5弱", "label": "5弱"}, data["earthquake"]["levels"])
        await self.post("/api/guilds/77/settings", {"earthquake_channel": "1", "earthquake_level": "5弱"})
        saved = await self.db.get_guild_settings(77)
        self.assertEqual((saved["earthquake.channel"], saved["earthquake.level"]), ("1", "5弱"))
        self.assertEqual((await self.post("/api/guilds/77/settings", {"earthquake_level": "9"})).status, 400)
        await self.post("/api/guilds/77/settings", {"earthquake_channel": None, "earthquake_level": "4"})
        saved = await self.db.get_guild_settings(77)
        self.assertNotIn("earthquake.channel", saved)
        self.assertNotIn("earthquake.level", saved)
        self.assertEqual((await self.client.get("/api/guilds/5/settings")).status, 404)
        audit = await (await self.client.get("/api/audit")).json()
        self.assertTrue(any(a["action"] == "guild_settings" for a in audit["items"]))

    async def test_music(self):
        self.assertFalse((await (await self.client.get("/api/music")).json())["enabled"])
        music = FakeMusic(self.guild)
        self.bot.cogs["Music"] = music
        data = await (await self.client.get("/api/music")).json()
        player = data["players"][0]
        self.assertEqual((player["state"], player["current"]["title"], player["listeners"]), ("playing", "晴天", ["小明"]))
        self.assertEqual((await self.post("/api/music/77/control", {"action": "pause"})).status, 200)
        self.assertEqual(music.calls, ["pause"])
        self.assertEqual((await self.post("/api/music/77/control", {"action": "boom"})).status, 400)

    async def test_stocks_overview_and_cancel(self):
        await self.db.remember_user(1, "阿傑", "")
        await self.db.admin_adjust(1, "測試", set_to=100000)
        order, error = await self.db.stock_queue(1, "buy", "2330", "台積電", 10, date(2026, 11, 18), 5000)
        self.assertIsNone(error)
        data = await (await self.client.get("/api/stocks")).json()
        self.assertFalse(data["available"])
        self.assertEqual(data["summary"]["pending"], 1)
        pending = data["pending"][0]
        self.assertEqual((pending["user"]["name"], pending["symbol"]), ("阿傑", "2330"))
        resp = await self.post(f"/api/stocks/orders/{pending['id']}/cancel", {})
        self.assertEqual(resp.status, 200)
        self.assertEqual((await self.post(f"/api/stocks/orders/{pending['id']}/cancel", {})).status, 404)
        data = await (await self.client.get("/api/stocks")).json()
        self.assertEqual(data["summary"]["pending"], 0)
        self.assertEqual(data["history"][0]["status"], "cancelled")
        self.assertIsNotNone(order)

    async def test_minecraft(self):
        self.assertFalse((await (await self.client.get("/api/minecraft")).json())["enabled"])
        script = os.path.join(self.tmp.name, "mc")
        with open(script, "w") as f:
            f.write('#!/bin/sh\nif [ "$1" = status ]; then echo "🟢 開著 2 人在線"; else echo "ran $*"; fi\n')
        os.chmod(script, os.stat(script).st_mode | stat.S_IEXEC)
        self.bot.cogs["Minecraft"] = SimpleNamespace(script=script, busy=None)
        data = await (await self.client.get("/api/minecraft")).json()
        self.assertTrue(data["online"])
        out = await (await self.post("/api/minecraft/cmd", {"command": "say hi"})).json()
        self.assertIn("ran cmd say hi", out["output"])
        self.assertEqual((await self.post("/api/minecraft/cmd", {"command": "stop"})).status, 400)
        self.assertIn("ran log 30", (await (await self.client.get("/api/minecraft/log?lines=30")).json())["output"])
        self.assertEqual((await self.post("/api/minecraft/action", {"action": "backup"})).status, 200)
        for _ in range(50):
            await asyncio.sleep(0.05)
            if (await (await self.client.get("/api/minecraft")).json()).get("last"):
                break
        last = (await (await self.client.get("/api/minecraft")).json())["last"]
        self.assertEqual((last["action"], last["ok"]), ("備份", True))
        self.assertEqual((await self.post("/api/minecraft/action", {"action": "rm"})).status, 400)


if __name__ == "__main__":
    unittest.main()
