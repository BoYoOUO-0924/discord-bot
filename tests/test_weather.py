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


def town_payload_new_format(name="淡水區"):
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
        {"LocationName": name, "WeatherElement": [
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

    def test_tomorrow_from_evening_query(self):
        from datetime import timedelta
        from cogs.weather import forecast_embed

        tomorrow = NOW.replace(hour=20) + timedelta(days=1)
        f = weather.build_forecast("台北", county_payload(), "臺北市", tomorrow, "臺北市")
        self.assertEqual((f.day.wx, f.day.max_t, f.day.pop), ("晴", 28, 0))
        self.assertIsNone(f.night.pop)   # 36 小時預報還沒涵蓋明晚，顯示「—」而不是出錯
        embed = forecast_embed(f, tomorrow, tomorrow=True)
        self.assertIn("明天 11/1（日）", embed.title)
        self.assertIn("🌙 **晚上**　—", embed.description)

    def test_today_from_late_evening_skips_past_parts(self):
        # 晚上 10 點查今天：預報資料已經不含早上、中午，只列晚上，不能整個查詢失敗
        late = NOW.replace(hour=22)
        payload = town_payload_new_format()
        for element in payload["records"]["Locations"][0]["Location"][1]["WeatherElement"]:
            element["Time"] = [t for t in element["Time"]
                               if (t.get("DataTime") or t.get("StartTime")) >= late.replace(hour=18).isoformat()]
        f = weather.build_forecast("淡水", payload, "淡水區", late, "x")
        self.assertEqual([name for name, _ in f.parts], ["晚上"])

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
            if dataset == weather.COUNTY_36H:
                return county_payload()
            return town_payload_new_format("中正區" if dataset == weather.TOWN_3DAY_TAIPEI else "淡水區")

        with patch.object(weather, "_fetch", fake_fetch):
            forecasts = await weather.fetch_forecasts("KEY", NOW)
        self.assertEqual([f.source for f in forecasts], ["臺北市中正區", "新北市淡水區"])
        self.assertTrue(all(len(f.parts) == 3 for f in forecasts))


class SslContextTest(unittest.TestCase):
    def test_uses_os_verification_with_checks_on(self):
        import ssl
        ctx = weather.ssl_context()
        self.assertEqual(type(ctx).__module__.split(".")[0], "truststore")   # 由作業系統驗證憑證
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)                 # 不是關掉驗證
        self.assertTrue(ctx.check_hostname)


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


class GreetingTest(unittest.TestCase):
    P = weather.Period

    def fc(self, day, night=None):
        return weather.Forecast("x", day, night or self.P("多雲", 20, 25, 10), "x")

    def test_rain(self):
        rainy = [self.fc(self.P("晴", 20, 28, 0)), self.fc(self.P("多雲短暫陣雨", 20, 25, 40))]
        self.assertEqual(weather.sky(rainy), weather.RAIN)
        self.assertIn("有雨", weather.greeting(rainy))
        self.assertEqual(weather.sky([self.fc(self.P("多雲", 20, 25, 60))]), weather.RAIN)

    def test_sunny(self):
        sunny = [self.fc(self.P("晴時多雲", 20, 28, 0)), self.fc(self.P("晴", 20, 28, 10))]
        self.assertEqual(weather.sky(sunny), weather.SUNNY)
        self.assertIn("放晴", weather.greeting(sunny))

    def test_cloudy(self):
        cloudy = [self.fc(self.P("陰時多雲", 20, 25, 20)), self.fc(self.P("多雲", 20, 25, 10))]
        self.assertEqual(weather.sky(cloudy), weather.CLOUDY)
        self.assertIn("星海", weather.greeting(cloudy))


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
            self.assertFalse(cog.is_due(NOW.replace(hour=12)))   # 只傳一次，中午不再傳
            cog.mark_sent(cog.slot_key(NOW, (6, 0)))
            self.assertFalse(cog.is_due(NOW.replace(minute=5)))  # 重啟後不重複傳送

    def test_old_state_file_counts_as_sent(self):
        with tempfile.TemporaryDirectory() as folder:
            cog = self.make_cog(folder)
            with open(cog.state_path, "w", encoding="utf-8") as f:
                f.write(NOW.date().isoformat())
            self.assertFalse(cog.is_due(NOW.replace(hour=7)))

    def test_parse_times(self):
        from cogs.weather import parse_times

        self.assertEqual(parse_times("06:00, 12:00"), [(6, 0), (12, 0)])
        self.assertEqual(parse_times("06:00"), [(6, 0)])
        self.assertEqual(parse_times("07:30"), [(7, 30)])
        self.assertEqual(parse_times("abc"), [])

    def test_webhook_embed_has_no_clothing(self):
        from cogs.weather import forecast_embed

        f = weather.build_forecast("台北", county_payload(), "臺北市", NOW, "臺北市")
        names = [field.name for field in forecast_embed(f, NOW, clothing=False).fields]
        self.assertFalse(any("穿衣" in n for n in names))
        self.assertTrue(any("穿衣" in field.name for field in forecast_embed(f, NOW).fields))

    def test_morning_noon_evening_parts(self):
        f = weather.build_forecast("淡水", town_payload_new_format(), "淡水區", NOW, "x")
        parts = dict(f.parts)
        self.assertEqual(list(parts), ["早上", "中午", "晚上"])
        self.assertEqual((parts["早上"].min_t, parts["早上"].max_t, parts["早上"].pop), (18, 21, 20))
        self.assertEqual((parts["中午"].min_t, parts["中午"].max_t, parts["中午"].pop), (23, 24, 10))
        self.assertEqual((parts["晚上"].min_t, parts["晚上"].max_t, parts["晚上"].wx), (17, 20, "陰"))

    def test_embed_shows_three_parts(self):
        from cogs.weather import forecast_embed

        f = weather.build_forecast("淡水", town_payload_new_format(), "淡水區", NOW, "x")
        embed = forecast_embed(f, NOW, clothing=False)
        self.assertEqual(embed.fields, [])   # 不用並排欄位，手機上才不會排成一長串
        text = embed.description
        self.assertLess(text.index("🌅 **早上**"), text.index("☀️ **中午**"))
        self.assertLess(text.index("☀️ **中午**"), text.index("🌙 **晚上**"))
        self.assertIn("🌡️ 18–21°C　☔ 20%", text)

    def test_embed_renders(self):
        from cogs.weather import forecast_embed

        f = weather.build_forecast("台北", county_payload(), "臺北市", NOW, "臺北市")
        embed = forecast_embed(f, NOW)
        self.assertIn("台北", embed.title)
        self.assertIn("10/31（六）", embed.title)
        self.assertIn("60%", embed.description)


class SendNowTest(unittest.IsolatedAsyncioTestCase):
    async def run_command(self, cog, channel_id=1, day=0):
        from unittest.mock import AsyncMock, patch
        from cogs import weather as weather_cog

        cog.daily_message = AsyncMock(return_value=("早安", ["embed"]))
        interaction = SimpleNamespace(channel_id=channel_id, user=SimpleNamespace())
        with patch.object(weather_cog, "send_error", AsyncMock()) as error,              patch.object(weather_cog, "defer", AsyncMock()) as defer,              patch.object(weather_cog, "respond", AsyncMock()) as respond:
            await cog.weather_command.callback(cog, interaction, day=day, send=True)
        return defer, respond, error

    async def test_anyone_can_send_to_this_channel(self):
        with tempfile.TemporaryDirectory() as folder:
            cog = WeatherCogTest().make_cog(folder)
            defer, respond, error = await self.run_command(cog)
            error.assert_not_awaited()
            self.assertFalse(defer.await_args.kwargs["ephemeral"])           # 公開傳到下指令的頻道
            self.assertEqual(respond.await_args.kwargs["content"], "早安")
            self.assertNotIn("ephemeral", respond.await_args.kwargs)
            self.assertIsNone(cog.last_sent())   # 立即傳送不算今天已傳送

    async def test_send_tomorrow(self):
        with tempfile.TemporaryDirectory() as folder:
            cog = WeatherCogTest().make_cog(folder)
            await self.run_command(cog, day=1)
            self.assertTrue(cog.daily_message.await_args.args[1])

    async def test_same_channel_cooldown(self):
        with tempfile.TemporaryDirectory() as folder:
            cog = WeatherCogTest().make_cog(folder)
            await self.run_command(cog)
            _, respond, error = await self.run_command(cog)
            error.assert_awaited_once()
            respond.assert_not_awaited()
            _, respond, error = await self.run_command(cog, channel_id=2)   # 別的頻道不受影響
            error.assert_not_awaited()

    async def test_webhook_message_has_greeting_and_no_clothing(self):
        from unittest.mock import AsyncMock, MagicMock, patch
        from cogs import weather as weather_cog

        forecast = weather.build_forecast("台北", county_payload(), "臺北市", NOW, "臺北市")
        with tempfile.TemporaryDirectory() as folder:
            cog = WeatherCogTest().make_cog(folder)
            hook = MagicMock(send=AsyncMock())
            with patch.object(weather, "fetch_forecasts", AsyncMock(return_value=[forecast])) as fetch,                  patch.object(weather_cog.discord.Webhook, "from_url", return_value=hook):
                await cog.send_daily(NOW)
            kwargs = hook.send.await_args.kwargs
            self.assertTrue(kwargs["content"].startswith(weather.GREETINGS[weather.RAIN]))
            self.assertIn("今天的天氣預報", kwargs["content"])
            self.assertFalse(any("穿衣" in f.name for e in kwargs["embeds"] for f in e.fields))


class EnvDiagnosticsTest(unittest.TestCase):
    def load(self, files):
        from unittest.mock import patch
        from core.config import load_dotenv

        with tempfile.TemporaryDirectory() as folder:
            for name, content in files.items():
                with open(os.path.join(folder, name), "wb") as f:
                    f.write(content)
            with patch.dict(os.environ, {}, clear=False):
                status = load_dotenv(os.path.join(folder, ".env"))
                values = {k: os.environ.get(k) for k in ("CWA_API_KEY", "WEATHER_WEBHOOK_URL")}
        return status, values

    def test_txt_extension_is_reported(self):
        status, _ = self.load({".env.txt": b"CWA_API_KEY=abc\n"})
        self.assertFalse(status.found)
        self.assertIn(".env.txt", status.problems[0])

    def test_misspelled_key_gets_suggestion(self):
        status, values = self.load({".env": "CAW_API_KEY=abc\n天氣\n".encode("utf-8")})
        self.assertIsNone(values["CWA_API_KEY"])
        self.assertTrue(any("CAW_API_KEY" in p and "CWA_API_KEY" in p for p in status.problems))
        self.assertTrue(any("第 2 行" in p for p in status.problems))

    def test_utf16_file_from_powershell_still_works(self):
        status, values = self.load({".env": "CWA_API_KEY=abc\r\nWEATHER_WEBHOOK_URL=https://x\r\n".encode("utf-16")})
        self.assertEqual(values, {"CWA_API_KEY": "abc", "WEATHER_WEBHOOK_URL": "https://x"})
        self.assertEqual(status.problems, [])

    def test_values_typed_into_example_are_reported(self):
        status, values = self.load({".env.example": b"# CWA_API_KEY=x\nCWA_API_KEY=real-key\nDISCORD_TOKEN=YOUR_DISCORD_BOT_TOKEN\n"})
        self.assertIsNone(values["CWA_API_KEY"])
        self.assertTrue(any(p.startswith("CWA_API_KEY 填在 .env.example") for p in status.problems))

    def test_placeholder_reported(self):
        status, values = self.load({".env": b"CWA_API_KEY=YOUR_CWA_API_KEY\nWEATHER_WEBHOOK_URL=\n"})
        self.assertEqual(len([p for p in status.problems if "還沒有填值" in p]), 2)

    def test_removed_gemini_keys_reported(self):
        status, values = self.load({".env": b"GEMINI_API_KEY=abc\nAI_PROVIDER=gemini\n"})
        self.assertEqual(len([p for p in status.problems if "不再使用" in p]), 2)
        self.assertFalse(any("不是認得的設定名稱" in p for p in status.problems))


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
