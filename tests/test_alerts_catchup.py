"""天氣地點、颱風豪雨警報、伺服器設定的功能開關、我錯過了什麼。"""
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from cogs import alerts as alerts_cog
from cogs import catchup as catchup_cog
from cogs import settings as settings_cog
from core import alerts, catchup, weather
from core.db import Database
from core.features import GLOBAL, FeatureFlags
from core.timeutil import TAIPEI

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=TAIPEI)


class PlacesTest(unittest.TestCase):
    def test_search_and_key_round_trip(self):
        found = weather.search_places("淡水")
        self.assertEqual((found[0].county, found[0].town), ("新北市", "淡水區"))
        self.assertTrue(weather.search_places("台北 中正"))           # 台＝臺，多個關鍵字都要符合
        place = weather.place_from_key(weather.place_key(found[0]))
        self.assertEqual(place, found[0])
        self.assertIsNone(weather.place_from_key("火星市|不存在區"))

    def test_duplicate_town_names_get_county_prefix(self):
        taipei = weather.make_place("臺北市", "中正區")
        keelung = weather.make_place("基隆市", "中正區")
        self.assertNotEqual(taipei.label, keelung.label)


def warning(title="豪雨特報", areas=("臺北市",), end=NOW + timedelta(hours=6), issued="2026-11-14 10:00:00"):
    return alerts.WeatherWarning(f"{title}|{issued}", title, [title.replace("特報", "")], None, None, end,
                                 "內容", list(areas))


class AlertsParsingTest(unittest.TestCase):
    def test_area_counties(self):
        self.assertEqual(alerts.area_counties("台北市山區"), {"臺北市"})
        self.assertEqual(alerts.area_counties("恆春半島"), {"屏東縣"})

    def test_parse_closures(self):
        page = """<table><tr><td>縣市名稱</td><td>天然災害停止上班及上課情形</td></tr>
        <tr><td><font>臺北市</font></td><td>今天停止上班、停止上課。<br>明天照常上班</td></tr>
        <tr><td>花蓮縣</td><td>今天停止上課。</td></tr>
        <tr><td colspan=2>備註：以各縣市政府公告為準</td></tr></table>"""
        closures = alerts.parse_closures(page)
        self.assertEqual(closures["臺北市"], "今天停止上班、停止上課。\n明天照常上班")
        self.assertIn("花蓮縣", closures)
        self.assertEqual(list(alerts.closures_for(closures, ["臺北市"])), ["臺北市"])
        self.assertEqual(alerts.parse_closures("<tr><td>無停班停課訊息。</td></tr>"), {})

    def test_guild_items_filters(self):
        snapshot = alerts.Snapshot(warnings=[
            warning(),
            warning("大雨特報", end=NOW - timedelta(hours=1)),       # 過期
            warning("強風特報"),                                       # 預設不通知
            warning("豪雨特報", areas=("花蓮縣",), issued="x"),         # 別的縣市
        ], typhoons=[], closures={})
        keys = [k for k, _ in alerts_cog.guild_items(snapshot, NOW, ["臺北市"], all_types=False)]
        self.assertEqual(keys, ["warning|豪雨特報|2026-11-14 10:00:00"])
        keys = [k for k, _ in alerts_cog.guild_items(snapshot, NOW, ["臺北市"], all_types=True)]
        self.assertEqual(len(keys), 2)
        self.assertIsNone(alerts_cog.guild_items(alerts.Snapshot(), NOW, ["臺北市"], False))  # 全部抓失敗

    def test_fit_embeds(self):
        big = [discord.Embed(description="字" * 2000) for _ in range(5)]
        self.assertEqual(len(alerts_cog.fit_embeds(big)), 2)


class DbTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.tmp.name, "test.db"))
        await self.db.connect()

    async def asyncTearDown(self):
        await self.db.close()
        self.tmp.cleanup()


class AlertsCogTest(DbTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.channel = SimpleNamespace(send=AsyncMock())
        self.bot = SimpleNamespace(db=self.db, settings=SimpleNamespace(cwa_api_key="k"),
                                   get_channel=lambda cid: self.channel)
        self.cog = alerts_cog.Alerts(self.bot)

    async def test_first_run_silent_then_only_new(self):
        first = alerts.Snapshot(warnings=[warning()], typhoons=[], closures={})
        self.assertEqual(await self.cog.handle_guild(1, 9, first, NOW), 0)
        self.channel.send.assert_not_awaited()
        self.assertEqual(await self.cog.handle_guild(1, 9, first, NOW), 0)        # 同一則不重複

        second = alerts.Snapshot(warnings=[warning(), warning("大雨特報")], typhoons=None,
                                 closures={"臺北市": "今天停止上班、停止上課。"})
        self.assertEqual(await self.cog.handle_guild(1, 9, second, NOW), 2)
        self.assertEqual(self.channel.send.await_count, 2)
        self.assertEqual(await self.cog.handle_guild(1, 9, second, NOW), 0)

    async def test_failed_send_retries_and_disabled_marks_silently(self):
        await self.cog.handle_guild(1, 9, alerts.Snapshot(warnings=[], typhoons=[], closures={}), NOW)
        snapshot = alerts.Snapshot(warnings=[warning()], typhoons=[], closures={})
        self.channel.send = AsyncMock(side_effect=discord.HTTPException(SimpleNamespace(status=500, reason=""), ""))
        self.assertEqual(await self.cog.handle_guild(1, 9, snapshot, NOW), 0)
        self.channel.send = AsyncMock()
        self.assertEqual(await self.cog.handle_guild(1, 9, snapshot, NOW), 1)     # 下一輪重送

        self.bot.features = SimpleNamespace(is_enabled=lambda feature, gid=None: False)
        await self.cog.handle_guild(1, 9, alerts.Snapshot(warnings=[warning("大雨特報")], typhoons=[], closures={}), NOW)
        self.channel.send.assert_awaited_once()
        self.assertIn("warning|大雨特報|2026-11-14 10:00:00", await self.cog.sent_keys(1))


def fake_interaction(guild_id=1, user_id=5, manage=True):
    sent = []
    async def send_message(**kwargs):
        sent.append(kwargs)
    async def edit_message(**kwargs):
        sent.append(kwargs)
    member = SimpleNamespace(id=user_id, display_name="小明", guild_permissions=SimpleNamespace(manage_guild=manage))
    return SimpleNamespace(guild_id=guild_id, user=member, sent=sent,
                           response=SimpleNamespace(is_done=lambda: False, send_message=send_message, defer=AsyncMock(),
                                                    edit_message=edit_message),
                           original_response=AsyncMock())


class SettingsFeaturesTest(DbTestCase):
    async def test_toggle_features(self):
        flags = FeatureFlags(self.db)
        await flags.load()
        await flags.set(GLOBAL, "music", False)
        bot = SimpleNamespace(db=self.db, features=flags)
        on, blocked = settings_cog.feature_state(bot, 1)
        self.assertEqual(blocked, {"music"})
        self.assertIn("poker", on)

        view = settings_cog.FeatureView(settings_cog.Settings(bot), 1, 5)
        values = {o.value for o in view.children[0].options}
        self.assertNotIn("music", values)                     # 全域關掉的不能選
        interaction = fake_interaction()
        await view.apply(interaction, values - {"poker", "stocks"})
        self.assertFalse(flags.is_enabled("poker", 1))
        self.assertTrue(flags.is_enabled("poker", 2))
        self.assertIn("已更新", interaction.sent[-1]["embed"].title)
        await view.apply(fake_interaction(), values)          # 再打開 = 拿掉設定
        self.assertTrue(flags.is_enabled("poker", 1))
        self.assertNotIn((1, "poker"), flags.flags)
        self.assertTrue(any("德州撲克" in r["detail"] for r in await self.db.list_audit()))


def message(mid, author_id, name, content, minutes, bot=False, mentions=(), reply_to=None):
    return SimpleNamespace(
        id=mid, author=SimpleNamespace(id=author_id, display_name=name, bot=bot), content=content,
        created_at=(NOW - timedelta(minutes=minutes)).astimezone(timezone.utc), attachments=[], embeds=[],
        mentions=[SimpleNamespace(id=m) for m in mentions],
        reference=SimpleNamespace(resolved=SimpleNamespace(author=SimpleNamespace(id=reply_to))) if reply_to else None,
        jump_url=f"https://discord.com/channels/1/2/{mid}")


class FakeChannel:
    def __init__(self, messages):
        self.messages = messages   # 新到舊

    def history(self, **kwargs):
        async def gen():
            for m in self.messages[:kwargs.get("limit", 100)]:
                yield m
        return gen()


class CatchupTest(unittest.IsolatedAsyncioTestCase):
    def test_text_helpers(self):
        self.assertEqual(catchup.message_text("  hi\n there "), "hi there")
        self.assertEqual(catchup.message_text("", attachments=2), "[附件]")
        self.assertEqual(len(catchup.message_text("字" * 500)), catchup.MAX_LINE)
        lines = [catchup.Line(NOW, "A", "x" * 50) for _ in range(10)]
        text, cut = catchup.transcript(lines, limit=200)
        self.assertTrue(cut)
        self.assertLessEqual(len(text), 200)
        self.assertFalse(catchup.transcript(lines[:1])[1])

    async def test_collect_stops_at_my_last_message(self):
        channel = FakeChannel([
            message(7, 5, "小明", "@嘎哇 我錯過了什麼", 0),          # 剛剛叫功能的那句不算「上次看到這裡」
            message(6, 7, "阿華", "@小明 晚上要不要吃火鍋", 1, mentions=(5,)),
            message(5, 8, "機器人", "遊戲結果", 2, bot=True),
            message(4, 8, "小美", "好啊", 3, reply_to=5),
            message(3, 7, "阿華", "我先訂位", 4),
            message(2, 5, "小明", "我先下線", 10),
            message(1, 7, "阿華", "更早的訊息", 20),
        ])
        missed = await catchup_cog.collect(channel, 5, None, NOW)
        self.assertEqual([l.text for l in missed.lines], ["我先訂位", "好啊", "@小明 晚上要不要吃火鍋"])
        self.assertEqual(len(missed.mentions), 2)
        self.assertTrue(missed.first_url.endswith("/3"))
        missed = await catchup_cog.collect(channel, 5, 1, NOW)       # 指定小時：不在自己的訊息停下
        self.assertEqual(len(missed.lines), 4)

        embed = catchup_cog.result_embed(missed, {"summary": "在約吃火鍋", "points": ["阿華訂位"],
                                                   "for_you": ["阿華問你要不要吃火鍋"]})
        self.assertIn("在約吃火鍋", embed.description)
        self.assertIn("阿華問你", embed.fields[0].value)
        offline = catchup_cog.result_embed(missed, None, "AI 離線")
        self.assertEqual(offline.description, "AI 離線")
        self.assertIn("阿華（3）", offline.fields[-1].value)

    async def test_command_uses_ai(self):
        channel = FakeChannel([message(i, 7, "阿華", f"訊息{i}", i) for i in range(1, 6)])
        ai = SimpleNamespace(available=True, json=AsyncMock(return_value={"summary": "整理", "points": [], "for_you": []}))
        cog = catchup_cog.Catchup(SimpleNamespace(ai=ai))
        interaction = fake_interaction()
        interaction.channel, interaction.guild = channel, None
        interaction.followup = SimpleNamespace(send=AsyncMock())
        await catchup_cog.Catchup.catchup_command.callback(cog, interaction, None)
        prompt = ai.json.await_args.args[0]
        self.assertIn("訊息1", prompt)
        self.assertEqual(ai.json.await_args.kwargs["num_ctx"], 8192)


class HelpSizeTest(unittest.TestCase):
    def test_help_fields_fit(self):
        from core.catalog import HELP_SECTIONS
        for title, commands in HELP_SECTIONS:
            value = "\n".join(f"</{n}:123456789012345678> {a}\n　{d}" for n, a, d in commands)
            self.assertLessEqual(len(value), 1024, title)


if __name__ == "__main__":
    unittest.main()
