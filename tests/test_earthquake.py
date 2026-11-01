import os
import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from core import earthquake as eq
from core.timeutil import TAIPEI

NOW = datetime(2026, 11, 1, 8, 5, tzinfo=TAIPEI)


def quake(time="2026-11-01 08:00:12", location="花蓮縣政府南南東方 25.0 公里 (位於臺灣東部海域)",
          magnitude=5.8, areas=None, number=115045, color="黃色"):
    if areas is None:
        areas = [
            {"AreaDesc": "最大震度4級地區", "CountyName": "花蓮縣", "AreaIntensity": "4級",
             "EqStation": [{"StationName": "和平", "CountyName": "花蓮縣", "SeismicIntensity": "4級"}]},
            {"AreaDesc": "最大震度2級地區", "CountyName": "臺北市、宜蘭縣", "AreaIntensity": "2級"},
            {"AreaDesc": "最大震度1級地區", "CountyName": "新北市", "AreaIntensity": "1級"},
        ]
    return {
        "EarthquakeNo": number, "ReportColor": color,
        "ReportContent": "11/01-08:00花蓮縣政府南南東方25.0公里位於臺灣東部海域發生規模5.8有感地震，最大震度花蓮縣和平4級。",
        "ReportImageURI": "https://scweb.cwa.gov.tw/x.png", "Web": "https://scweb.cwa.gov.tw/details",
        "EarthquakeInfo": {"OriginTime": time, "FocalDepth": 15.5,
                           "Epicenter": {"Location": location, "EpicenterLatitude": 23.77, "EpicenterLongitude": 121.67},
                           "EarthquakeMagnitude": {"MagnitudeType": "芮氏規模", "MagnitudeValue": magnitude}},
        "Intensity": {"ShakingArea": areas},
    }


BIG = [{"CountyName": "臺北市", "AreaIntensity": "4級"}, {"CountyName": "宜蘭縣", "AreaIntensity": "5弱"}]


def payload(*items):
    return {"success": "true", "records": {"datasetDescription": "地震報告", "Earthquake": list(items)}}


class ParseTest(unittest.TestCase):
    def test_intensity(self):
        self.assertEqual(eq.normalize_intensity("4級"), "4")
        self.assertEqual(eq.normalize_intensity("５弱"), "5弱")
        self.assertEqual(eq.normalize_intensity("6強"), "6強")
        self.assertEqual(eq.normalize_intensity("5"), "5弱")
        self.assertIsNone(eq.normalize_intensity("無"))
        self.assertGreater(eq.intensity_rank("5強"), eq.intensity_rank("5弱"))
        self.assertEqual(eq.intensity_text("3"), "3 級")
        self.assertEqual(eq.intensity_text("6弱"), "6弱")

    def test_parse_report(self):
        [r] = eq.parse_reports(payload(quake()), eq.SIGNIFICANT)
        self.assertEqual(r.magnitude, 5.8)
        self.assertEqual(r.depth, 15.5)
        self.assertEqual(r.max_intensity, "4")
        self.assertEqual(r.local, {"臺北市": "2", "新北市": "1"})
        self.assertTrue(r.felt_locally and r.significant)
        self.assertEqual(r.number, "115045")
        self.assertEqual(r.origin_time, datetime(2026, 11, 1, 8, 0, 12, tzinfo=TAIPEI))
        self.assertEqual(eq.short_location(r.location), "臺灣東部海域")

    def test_lowercase_keys_and_missing_areas(self):
        item = {"earthquakeInfo": {"originTime": "2026-11-01 07:00:00",
                                   "epicenter": {"location": "宜蘭縣政府東方 10.0 公里"}},
                "reportContent": "最大震度宜蘭縣南澳3級。"}
        [r] = eq.parse_reports(payload(item), eq.LOCAL)
        self.assertEqual(r.max_intensity, "3")
        self.assertFalse(r.felt_locally)
        self.assertEqual(eq.short_location(r.location), "宜蘭縣")


def report_title(report):
    from cogs.earthquake import report_embed
    return report_embed(report).title


class CogTest(unittest.IsolatedAsyncioTestCase):
    def make_cog(self, folder):
        from cogs.earthquake import Earthquake

        settings = SimpleNamespace(cwa_api_key="k", weather_webhook_url="w", earthquake_webhook_url=None,
                                   database_path=os.path.join(folder, "bot.db"))
        cog = Earthquake(MagicMock(settings=settings))
        cog.send = AsyncMock()
        return cog

    def reports(self, *items, dataset=eq.SIGNIFICANT):
        return eq.parse_reports(payload(*items), dataset)

    async def test_first_run_records_without_sending(self):
        with tempfile.TemporaryDirectory() as folder:
            cog = self.make_cog(folder)
            self.assertEqual(cog.webhook_url, "w")   # 沒設定時用天氣的 Webhook
            old = self.reports(quake())
            await cog.handle(old, NOW)
            cog.send.assert_not_awaited()
            await cog.handle(old, NOW)
            cog.send.assert_not_awaited()
            new = self.reports(quake(time="2026-11-01 08:03:00", areas=BIG)) + old
            await cog.handle(new, NOW)
            cog.send.assert_awaited_once()
            await cog.handle(new, NOW)   # 不重複傳送
            cog.send.assert_awaited_once()

    async def test_filters(self):
        with tempfile.TemporaryDirectory() as folder:
            cog = self.make_cog(folder)
            await cog.handle([], NOW)
            taipei_3 = self.reports(quake(time="2026-11-01 08:01:00", areas=[
                {"CountyName": "臺北市", "AreaIntensity": "3級"}, {"CountyName": "花蓮縣", "AreaIntensity": "6弱"}]))
            new_taipei_5 = self.reports(quake(time="2026-11-01 08:01:30", areas=[
                {"CountyName": "新北市", "AreaIntensity": "5弱"}]))   # 只有新北大，台北無感
            taipei_4 = self.reports(quake(time="2026-11-01 08:02:00", areas=BIG), dataset=eq.LOCAL)
            too_old = self.reports(quake(time="2026-11-01 05:00:00", areas=BIG))
            await cog.handle(taipei_3 + new_taipei_5 + taipei_4 + too_old, NOW)
            self.assertEqual(cog.send.await_count, 1)
            self.assertEqual(cog.send.await_args.args[0].key, taipei_4[0].key)
            self.assertEqual(len(cog.seen()), 5)   # 沒傳的也記下來（含第一次啟動的標記）

    async def test_failed_send_is_retried(self):
        import discord

        with tempfile.TemporaryDirectory() as folder:
            cog = self.make_cog(folder)
            await cog.handle([], NOW)
            report = self.reports(quake(areas=BIG))
            cog.send.side_effect = discord.HTTPException(MagicMock(status=500), "boom")
            await cog.handle(report, NOW)
            cog.send.side_effect = None
            await cog.handle(report, NOW)
            self.assertEqual(cog.send.await_count, 2)

    def test_embed_and_alert(self):
        from cogs.earthquake import alert_text, report_embed

        [r] = self.reports(quake())
        embed = report_embed(r)
        self.assertIn("臺灣東部海域 規模 5.8", embed.title)
        self.assertIn("第 115045 號", embed.footer.text)
        self.assertTrue(any("台北 2 級、新北 1 級" in f.value for f in embed.fields))
        self.assertTrue(alert_text(r).startswith("即使大地偶有震顫，也希望你所在的地方始終安穩。"))
        self.assertIn("台北 2 級", alert_text(r))

    async def test_send_uses_webhook(self):
        from cogs import earthquake as cog_module
        from cogs.earthquake import Earthquake

        with tempfile.TemporaryDirectory() as folder:
            cog = self.make_cog(folder)
            hook = MagicMock(send=AsyncMock())
            with patch.object(cog_module.discord.Webhook, "from_url", return_value=hook):
                await Earthquake.send(cog, self.reports(quake())[0])
            self.assertIn("embed", hook.send.await_args.kwargs)

    async def run_query(self, reports):
        from cogs import earthquake as cog_module

        with tempfile.TemporaryDirectory() as folder:
            cog = self.make_cog(folder)
            with patch.object(cog_module.eq, "fetch_reports", AsyncMock(return_value=reports)), \
                 patch.object(cog_module, "now_tw", return_value=NOW), \
                 patch.object(cog_module, "defer", AsyncMock()), \
                 patch.object(cog_module, "respond", AsyncMock()) as respond:
                await cog.earthquake_command.callback(cog, SimpleNamespace())
            return respond.await_args.kwargs

    async def test_query_says_none_without_big_quake(self):
        kwargs = await self.run_query(self.reports(quake()))
        self.assertIn("沒有", kwargs["embed"].title)
        self.assertIn("4 級以上", kwargs["embed"].description)
        self.assertIn("台北 2 級", kwargs["embed"].fields[0].value)
        kwargs = await self.run_query([])
        self.assertIn("沒有", kwargs["embed"].title)

    async def test_query_shows_big_quake(self):
        big = self.reports(quake(time="2026-10-30 10:00:00", areas=BIG))
        kwargs = await self.run_query(self.reports(quake()) + big)
        self.assertEqual(kwargs["embed"].title, report_title(big[0]))
        self.assertIn("即使大地", kwargs["content"])

    async def test_command_non_admin_cannot_send(self):
        from cogs import earthquake as cog_module

        with tempfile.TemporaryDirectory() as folder:
            cog = self.make_cog(folder)
            interaction = SimpleNamespace(user=SimpleNamespace(guild_permissions=SimpleNamespace(manage_guild=False)))
            with patch.object(cog_module, "send_error", AsyncMock()) as error, \
                 patch.object(cog_module.eq, "fetch_reports", AsyncMock()) as fetch:
                await cog.earthquake_command.callback(cog, interaction, send=True)
            error.assert_awaited_once()
            fetch.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
