import unittest
from types import SimpleNamespace

from core.overview import feature_overview, format_overview


def settings(**kw):
    base = dict(cwa_api_key=None, twfolio_db=None, earthquake_webhook_url=None, weather_webhook_url=None,
                admin_password=None, admin_port=8080, minecraft_guild_ids=())
    base.update(kw)
    return SimpleNamespace(**base)


class OverviewTest(unittest.TestCase):
    def test_minimal_setup_explains_what_is_missing(self):
        text = format_overview(feature_overview(settings(), ai_configured=False))
        self.assertIn("✅ 錢包、遊戲", text)
        self.assertIn("缺 CWA_API_KEY", text)
        self.assertIn("缺 OLLAMA_URL", text)
        self.assertIn("➖ 股票模擬", text)

    def test_configured(self):
        rows = feature_overview(settings(cwa_api_key="k", admin_password="p", minecraft_guild_ids=(1,)), True)
        ok = {name: good for good, name, _ in rows}
        self.assertTrue(ok["天氣、地震、颱風豪雨特報"])
        self.assertTrue(ok["Minecraft 管理（進階）"])
        self.assertIn("localhost:8080", format_overview(rows))


if __name__ == "__main__":
    unittest.main()
