import os
import tempfile
import unittest
from types import SimpleNamespace

from aiohttp.test_utils import TestClient, TestServer

from core.db import Database
from webadmin.server import AdminServer, password_problem

PASSWORD = "correct-horse-battery"
HEADERS = {"X-Requested-With": "gawa-admin"}


class FakeBot:
    def __init__(self, db):
        self.db = db
        self.ai = SimpleNamespace(available=False, configured=False, provider="none", model="", status="未設定")
        self.settings = SimpleNamespace(auto_update=False)
        self.user = None
        self.latency = 0.05
        self.guilds = []
        self.cogs = {}

    def is_ready(self):
        return True

    def is_closed(self):
        return False

    def get_cog(self, name):
        return self.cogs.get(name)

    def get_user(self, user_id):
        return None

    def get_channel(self, channel_id):
        return None


class PasswordRuleTest(unittest.TestCase):
    def test_password_rules(self):
        self.assertIsNotNone(password_problem(None))
        self.assertIsNotNone(password_problem("short"))
        self.assertIsNotNone(password_problem("請改成你自己的密碼"))
        self.assertIsNone(password_problem(PASSWORD))


class AdminApiTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.tmp.name, "test.db"))
        await self.db.connect()
        self.bot = FakeBot(self.db)
        server = AdminServer(self.bot, PASSWORD, "127.0.0.1", 0, os.path.join(self.tmp.name, "bot.log"))
        self.client = TestClient(TestServer(server.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        await self.db.close()
        self.tmp.cleanup()

    async def login(self):
        resp = await self.client.post("/api/login", json={"password": PASSWORD}, headers=HEADERS)
        self.assertEqual(resp.status, 200)

    async def test_requires_login(self):
        self.assertEqual((await self.client.get("/api/overview")).status, 401)
        self.assertEqual((await self.client.get("/api/users")).status, 401)
        # 頁面本身可以開（只有登入畫面），資料 API 才需要登入
        self.assertEqual((await self.client.get("/")).status, 200)

    async def test_wrong_password_and_lockout(self):
        for _ in range(5):
            resp = await self.client.post("/api/login", json={"password": "nope"}, headers=HEADERS)
            self.assertEqual(resp.status, 401)
        resp = await self.client.post("/api/login", json={"password": PASSWORD}, headers=HEADERS)
        self.assertEqual(resp.status, 429)   # 連續失敗後就算密碼對也先鎖住

    async def test_mutations_need_csrf_header(self):
        await self.login()
        resp = await self.client.post("/api/users/1/points", json={"mode": "add", "amount": 5, "reason": "x"})
        self.assertEqual(resp.status, 403)

    async def test_adjust_points_is_logged(self):
        await self.login()
        await self.db.add_points(42, 1000, "初始")
        resp = await self.client.post("/api/users/42/points", json={"mode": "add", "amount": -300, "reason": "違規扣分"},
                                      headers=HEADERS)
        self.assertEqual(await resp.json(), {"ok": True, "old": 1000, "new": 700})
        resp = await self.client.post("/api/users/42/points", json={"mode": "set", "amount": 5000, "reason": "補償"},
                                      headers=HEADERS)
        self.assertEqual((await resp.json())["new"], 5000)
        self.assertEqual(await self.db.get_points(42), 5000)
        # 不能扣到負數；也一定要寫原因
        resp = await self.client.post("/api/users/42/points", json={"mode": "add", "amount": -99999, "reason": "x"},
                                      headers=HEADERS)
        self.assertEqual((await resp.json())["new"], 0)
        resp = await self.client.post("/api/users/42/points", json={"mode": "add", "amount": 5, "reason": " "},
                                      headers=HEADERS)
        self.assertEqual(resp.status, 400)

        txs = await (await self.client.get("/api/users/42/transactions")).json()
        self.assertIn("管理員調整：補償", [t["reason"] for t in txs["items"]])
        audit = await (await self.client.get("/api/audit")).json()
        self.assertTrue(any(a["action"] == "points" and "補償" in a["detail"] for a in audit["items"]))

    async def test_search_and_detail(self):
        await self.login()
        await self.db.add_points(7, 300, "初始")
        await self.db.add_points(8, 900, "初始")
        await self.db.remember_user(7, "小明", None)
        await self.db.remember_user(8, "阿嘎", None)
        data = await (await self.client.get("/api/users?q=小明")).json()
        self.assertEqual([u["id"] for u in data["items"]], ["7"])
        data = await (await self.client.get("/api/users?sort=points")).json()
        self.assertEqual([u["id"] for u in data["items"]], ["8", "7"])
        detail = await (await self.client.get("/api/users/8")).json()
        self.assertEqual((detail["name"], detail["points"]), ("阿嘎", 900))
        self.assertEqual(len(detail["quests"]), 3)
        self.assertEqual((await self.client.get("/api/users/999")).status, 404)

    async def test_streak_and_work_reset(self):
        await self.login()
        await self.db.try_work(5, 1000.0, 3600, 100, "打工")
        resp = await self.client.post("/api/users/5/streak", json={"streak": 12}, headers=HEADERS)
        self.assertEqual(resp.status, 200)
        resp = await self.client.post("/api/users/5/reset-work", headers=HEADERS)
        self.assertEqual(resp.status, 200)
        last_work, _ = await self.db.income_status(5, __import__("datetime").date.today())
        self.assertIsNone(last_work)

    async def test_refund_only_orphan_holds(self):
        await self.login()
        await self.db.add_points(3, 1000, "初始")
        stuck = await self.db.hold(3, 200, "21點")
        busy = await self.db.hold(3, 300, "21點")
        # 模擬一局還在進行的 21 點占用 busy 這筆
        game = SimpleNamespace(hold_id=busy, finished=False, bet=300, channel_id=None)
        self.bot.cogs["Blackjack"] = SimpleNamespace(games={3: game})

        games = await (await self.client.get("/api/games")).json()
        status = {h["id"]: h["in_use"] for h in games["holds"]}
        self.assertEqual(status, {stuck: None, busy: "21點"})
        self.assertEqual((await self.client.post(f"/api/holds/{busy}/refund", headers=HEADERS)).status, 409)
        self.assertEqual((await self.client.post(f"/api/holds/{stuck}/refund", headers=HEADERS)).status, 200)
        self.assertEqual(await self.db.get_points(3), 1000 - 300)

    async def test_overview(self):
        await self.login()
        await self.db.checkin(1, __import__("datetime").date.today())
        data = await (await self.client.get("/api/overview")).json()
        self.assertEqual(data["kpi"]["users"], 1)
        self.assertEqual(len(data["days"]), 14)
        self.assertTrue(data["bot"]["online"])


if __name__ == "__main__":
    unittest.main()
