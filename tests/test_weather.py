import os
import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from core import weather
from core.timeutil import TAIPEI

NOW = datetime(2026, 10, 31, 6, 0, tzinfo=TAIPEI)


def county_payload():
    """F-C0032-001（36 小時縣市預報）的格式。"""
    def element(name, values):
        slots = [("2026-10-31 06:00:00", "2026-10-31 18:00:00"), ("2026-10-31 18:00:00", "2026-11-01 06:00:00"),
                 ("2026-11-01 06:00:00", "2026-11-01 18:00:00")]
        return {"elementName": name, "time": [{"startTime": s, "endTime": e, "parameter": {"parameterName": v}}
                                              for (s, e), v in zip(slots, values)]}
    return {"success": "true", "records": {"location": [{
        "locationName": "臺北市",
        "weatherElement": [element("Wx", ["多雲短暫陣雨", "陰時多雲", "晴"]), element("PoP", ["60", "30", "0"]),
                           element("MinT", ["21", "19", "20"]), element("MaxT", ["27", "22", "28"]),
                           element("CI", ["舒適至悶熱", "舒適", "舒適"])],
    }]}}


def town_payload_new_format():
    """F-D0047-069 新版格式：鍵名大寫、元素名稱中文、數值放在具名欄位。"""
    temps = [{"DataTime": f"2026-10-31T{h:02d}:00:00+08:00", "ElementValue": [{"Temperature": str(t)}]}
             for h, t in ((6, 18), (9, 21), (12, 24), (15, 23), (18, 20), (21, 17))]
    pops = [{"StartTime": f"2026-10-31T{h:02d}:00:00+08:00", "EndTime": f"2026-10-31T{h + 3:02d}:00:00+08:00",
             "ElementValue": [{"ProbabilityOfPrecipitation": p}]} for h, p in ((6, "10"), (9, "20"), (12, "10"),
                                                                               (15, "-"), (18, "10"), (21, "0"))]
    wx = [{"StartTime": f"2026-10-31T{h:02d}:00:00+08:00", "EndTime": f"2026-10-31T{h + 3:02d}:00:00+08:00",
           "ElementValue": [{"Weather": w, "WeatherCode": "02"}]} for h, w in ((6, "多雲"), (9, "晴"), (12, "多雲"),
                                                                                (18, "陰"))]
    return {"success": "true", "records": {"Locations": [{"Location": [
        {"LocationName": "板橋區", "WeatherElement": []},
        {"LocationName": "淡水區", "WeatherElement": [
            {"ElementName": "溫度", "Time": temps},
            {"ElementName": "3小時降雨機率", "Time": pops},
            {"ElementName": "天氣現象", "Time": wx},
        ]},
    ]}]}}


class WeatherParseTest(unittest.TestCase):
    def test_county_forecast(self):
        f = weather.build_forecast("台北", county_payload(), "臺北市", NOW, "臺北市")
        self.assertEqual((f.day.wx, f.day.min_t, f.day.max_t, f.day.pop, f.day.ci),
                         ("多雲短暫陣雨", 21, 27, 60, "舒適至悶熱"))
        self.assertEqual((f.night.pop, f.night.min_t), (30, 19))

    def test_town_forecast_new_format(self):
        f = weather.build_forecast("淡水", town_payload_new_format(), "淡水區", NOW, "新北市淡水區")
        self.assertEqual((f.day.min_t, f.day.max_t, f.day.pop), (18, 24, 20))   # "-" 視為沒有資料
        self.assertEqual(f.night.min_t, 17)
        self.assertIn(f.day.wx, ("多雲", "晴"))

    def test_missing_location(self):
        with self.assertRaises(weather.WeatherError):
            weather.build_forecast("淡水", county_payload(), "淡水區", NOW, "")


class FetchTest(unittest.IsolatedAsyncioTestCase):
    async def test_town_failure_falls_back_and_other_places_still_sent(self):
        from unittest.mock import patch

        async def fake_fetch(session, api_key, dataset, **params):
            if dataset == weather.COUNTY_36H:
                return county_payload()   # 只有臺北市，沒有新北市
            raise weather.WeatherError("HTTP 500")

        with patch.object(weather, "_fetch", fake_fetch), self.assertLogs("core.weather", "WARNING"):
            forecasts = await weather.fetch_forecasts("KEY", NOW)
        self.assertEqual([f.label for f in forecasts], ["台北"])

    async def test_town_forecast_used_when_available(self):
        from unittest.mock import patch

        async def fake_fetch(session, api_key, dataset, **params):
            return county_payload() if dataset == weather.COUNTY_36H else town_payload_new_format()

        with patch.object(weather, "_fetch", fake_fetch):
            forecasts = await weather.fetch_forecasts("KEY", NOW)
        self.assertEqual([f.source for f in forecasts], ["臺北市", "新北市淡水區"])


class AdviceTest(unittest.TestCase):
    def P(self, low, high, pop, wx="多雲"):
        return weather.Period(wx, low, high, pop)

    def test_rain_levels(self):
        self.assertEqual(weather.rain_advice(self.P(20, 25, 80), self.P(18, 20, 10))[0], "☔")
        self.assertEqual(weather.rain_advice(self.P(20, 25, 40), self.P(18, 20, 10))[0], "🌂")
        self.assertEqual(weather.rain_advice(self.P(20, 25, 10), self.P(18, 20, 0))[0], "☀️")

    def test_clothing(self):
        self.assertIn("短袖", weather.clothing_advice(self.P(27, 33, 0), self.P(26, 28, 0)))
        cold = weather.clothing_advice(self.P(8, 12, 0), self.P(7, 9, 0))
        self.assertIn("厚外套", cold)
        swing = weather.clothing_advice(self.P(15, 26, 60), self.P(14, 18, 20))
        self.assertIn("溫差", swing)
        self.assertIn("防水", swing)


class WeatherCogTest(unittest.TestCase):
    def make_cog(self, folder, time_text="06:00"):
        from cogs.weather import Weather

        settings = SimpleNamespace(cwa_api_key="k", weather_webhook_url="u", weather_time=time_text,
                                   database_path=os.path.join(folder, "bot.db"))
        return Weather(MagicMock(settings=settings))

    def test_sends_once_per_day_and_catches_up(self):
        with tempfile.TemporaryDirectory() as folder:
            cog = self.make_cog(folder)
            self.assertFalse(cog.is_due(NOW.replace(hour=5, minute=59)))
            self.assertTrue(cog.is_due(NOW))
            self.assertTrue(cog.is_due(NOW.replace(hour=9)))     # 早上開機補送
            self.assertFalse(cog.is_due(NOW.replace(hour=13)))   # 太晚就不補了
            cog.mark_sent(NOW.date().isoformat())
            self.assertFalse(cog.is_due(NOW.replace(minute=5)))  # 重啟後不重複傳送

    def test_embed_renders(self):
        from cogs.weather import forecast_embed

        f = weather.build_forecast("台北", county_payload(), "臺北市", NOW, "臺北市")
        embed = forecast_embed(f, NOW)
        self.assertIn("台北", embed.title)
        self.assertIn("10/31（六）", embed.title)
        self.assertTrue(any("60%" in field.value for field in embed.fields))


class SendNowTest(unittest.IsolatedAsyncioTestCase):
    async def run_command(self, manage_guild):
        from unittest.mock import AsyncMock, patch
        from cogs import weather as weather_cog

        with tempfile.TemporaryDirectory() as folder:
            cog = WeatherCogTest().make_cog(folder)
            cog.send_daily = AsyncMock()
            interaction = SimpleNamespace(user=SimpleNamespace(guild_permissions=SimpleNamespace(manage_guild=manage_guild)))
            with patch.object(weather_cog, "send_error", AsyncMock()) as error, \
                 patch.object(weather_cog, "defer", AsyncMock()), patch.object(weather_cog, "respond", AsyncMock()):
                await cog.weather_command.callback(cog, interaction, send=True)
            self.assertIsNone(cog.last_sent())   # 測試傳送不算今天已傳送
            return cog.send_daily, error

    async def test_admin_can_send_now(self):
        send, error = await self.run_command(True)
        send.assert_awaited_once()
        error.assert_not_awaited()

    async def test_non_admin_cannot_send(self):
        send, error = await self.run_command(False)
        send.assert_not_awaited()
        error.assert_awaited_once()


class SettingsSourceTest(unittest.TestCase):
    def test_env_first_then_legacy_config_py(self):
        from unittest.mock import patch
        from core import config

        legacy = SimpleNamespace(TEST_KEY_A="from-config", TEST_KEY_B="from-config", TEST_KEY_C="YOUR_PLACEHOLDER")
        with patch.object(config, "_legacy_config", legacy), patch.object(config, "legacy_keys", []), \
             patch.dict(os.environ, {"TEST_KEY_A": "from-env"}):
            self.assertEqual(config._read("TEST_KEY_A"), "from-env")      # .env / 環境變數優先
            self.assertEqual(config._read("TEST_KEY_B"), "from-config")   # 舊 config.py 仍可讀
            self.assertEqual(config._read("TEST_KEY_C", "default"), "default")
            self.assertEqual(config.legacy_keys, ["TEST_KEY_B"])          # 會提醒搬到 .env


class DotenvTest(unittest.TestCase):
    def test_load_dotenv(self):
        from core.config import load_dotenv

        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, ".env")
            with open(path, "w", encoding="utf-8") as f:
                f.write('# 註解\nTEST_WEATHER_A="abc"\nexport TEST_WEATHER_B = x=y\n\nTEST_WEATHER_C=keep\n')
            os.environ["TEST_WEATHER_C"] = "existing"
            load_dotenv(path)
            self.assertEqual(os.environ.pop("TEST_WEATHER_A"), "abc")
            self.assertEqual(os.environ.pop("TEST_WEATHER_B"), "x=y")
            self.assertEqual(os.environ.pop("TEST_WEATHER_C"), "existing")


if __name__ == "__main__":
    unittest.main()
