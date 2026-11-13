"""專案管理第三階段：週期任務、完成獎勵、管理後台看板、GitHub 整合。"""
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiohttp.test_utils import TestClient, TestServer

from cogs.github_sync import GitHubSync
from cogs.tasks import Tasks
from core import github as gh
from core import tasks as tk
from core.db import Database
from core.timeutil import TAIPEI
from tests.test_webadmin import HEADERS, PASSWORD, FakeBot
from webadmin.server import AdminServer


def at(*args) -> datetime:
    return datetime(*args, tzinfo=TAIPEI)


class RepeatAndRewardRulesTest(unittest.TestCase):
    def test_next_due(self):
        now = at(2026, 11, 14, 10)   # 週六
        self.assertEqual(tk.next_due(at(2026, 11, 14, 9), "daily", now), at(2026, 11, 15, 9))
        self.assertEqual(tk.next_due(at(2026, 11, 13, 18), "weekdays", now), at(2026, 11, 16, 18))   # 跳過週末
        self.assertEqual(tk.next_due(at(2026, 10, 30, 23, 59), "weekly", now), at(2026, 11, 20, 23, 59))  # 拖了兩週
        self.assertEqual(tk.next_due(at(2026, 1, 31), "monthly", at(2026, 1, 31, 1)), at(2026, 2, 28))   # 月底
        self.assertEqual(tk.next_due(at(2026, 12, 15), "monthly", at(2026, 12, 15, 1)), at(2027, 1, 15))
        with self.assertRaises(ValueError):
            tk.next_due(now, "yearly", now)

    def test_reward_only_when_assigned_by_someone_else(self):
        self.assertEqual(tk.task_reward(2, creator_id=1, assignee_id=2), tk.REWARDS[2])
        self.assertEqual(tk.task_reward(1, creator_id=1, assignee_id=1), 0)   # 自己指派給自己
        self.assertEqual(tk.task_reward(1, creator_id=1, assignee_id=None), 0)

    def test_task_refs(self):
        self.assertEqual(tk.task_refs("修好登入 task#12，順便 待辦 #3、TASK#12"), [12, 3])
        self.assertEqual(tk.task_refs("fix #12"), [])   # 單純的 #12 是 GitHub issue，不算


class FakeChannel:
    def __init__(self):
        self.sent = []
        self.next_id = 1000

    async def send(self, content=None, **kwargs):
        self.sent.append((content, kwargs))
        self.next_id += 1
        return SimpleNamespace(id=self.next_id)

    def get_partial_message(self, message_id):
        return SimpleNamespace(edit=AsyncMock())


class CompletionTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.tmp.name, "test.db"))
        await self.db.connect()
        self.channel = FakeChannel()
        self.bot = SimpleNamespace(db=self.db, get_channel=lambda cid: self.channel)
        self.cog = Tasks(self.bot)

    async def asyncTearDown(self):
        await self.db.close()
        self.tmp.cleanup()

    async def test_reward_paid_once(self):
        tid = await self.db.add_task(1, 10, 100, "寫報告", assignee_id=200, priority=2)
        self.assertTrue(await self.cog.complete(tid, 200))
        self.assertEqual(await self.db.get_points(200), tk.REWARDS[2])
        self.assertIn("獲得", self.channel.sent[-1][0])
        # 重新打開再完成，不會再發
        await self.db.set_task_status(tid, "todo", 200)
        await self.cog.complete(tid, 200)
        self.assertEqual(await self.db.get_points(200), tk.REWARDS[2])
        self.assertFalse(await self.cog.complete(tid, 200))   # 已經完成

    async def test_self_assigned_has_no_reward(self):
        tid = await self.db.add_task(1, 10, 100, "自己的事", assignee_id=100)
        await self.cog.complete(tid, 100)
        self.assertEqual(await self.db.get_points(100), 0)
        self.assertEqual(self.channel.sent, [])

    async def test_daily_cap(self):
        since = tk.day_start(datetime.now(TAIPEI)).timestamp()
        for i in range(tk.REWARD_DAILY_CAP):
            tid = await self.db.add_task(1, 10, 100, f"t{i}", assignee_id=200)
            self.assertEqual((await self.db.reward_task(tid, 200, 5000, since, tk.REWARD_DAILY_CAP, tk.REWARD_REASON))[1],
                             "paid")
        tid = await self.db.add_task(1, 10, 100, "第六個", assignee_id=200)
        self.assertEqual(await self.db.reward_task(tid, 200, 5000, since, tk.REWARD_DAILY_CAP, tk.REWARD_REASON),
                         (0, "capped"))
        self.assertEqual(await self.db.get_points(200), 5000 * tk.REWARD_DAILY_CAP)

    async def test_repeat_spawns_next(self):
        due = (datetime.now(TAIPEI) + timedelta(hours=2)).timestamp()
        tid = await self.db.add_task(1, 10, 100, "倒垃圾", due_at=due, due_has_time=True, repeat="daily",
                                     assignee_id=100)
        await self.cog.complete(tid, 100)
        rows = await self.db.list_tasks(1)
        self.assertEqual(len(rows), 1)
        new = rows[0]
        self.assertEqual((new["title"], new["repeat"], new["due_at"]), ("倒垃圾", "daily", due + 86400))
        self.assertIsNotNone(new["message_id"])                     # 新卡片發出去了
        self.assertIsNone((await self.db.get_task(tid))["repeat"])  # 舊的不再重複
        # 舊的重新打開再完成，不會又多一個
        await self.db.set_task_status(tid, "todo", 100)
        await self.cog.complete(tid, 100)
        self.assertEqual(len(await self.db.list_tasks(1)), 1)


class BoardApiTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.tmp.name, "test.db"))
        await self.db.connect()
        self.bot = FakeBot(self.db)
        self.tasks = SimpleNamespace(complete=AsyncMock(return_value=True), refresh_card=AsyncMock())
        self.bot.cogs["Tasks"] = self.tasks
        server = AdminServer(self.bot, PASSWORD, "127.0.0.1", 0, os.path.join(self.tmp.name, "bot.log"))
        self.client = TestClient(TestServer(server.app))
        await self.client.start_server()
        await self.client.post("/api/login", json={"password": PASSWORD}, headers=HEADERS)

    async def asyncTearDown(self):
        await self.client.close()
        await self.db.close()
        self.tmp.cleanup()

    async def test_board_and_move(self):
        pid = await self.db.create_project(1, "網站", 10, 100)
        a = await self.db.add_task(1, 10, 100, "首頁", project_id=pid, assignee_id=200)
        b = await self.db.add_task(1, 10, 100, "別的", due_at=1.0)
        await self.db.remember_user(200, "小明", None)

        data = await (await self.client.get("/api/board?guild=1")).json()
        self.assertEqual({t["id"] for t in data["tasks"]}, {a, b})
        first = next(t for t in data["tasks"] if t["id"] == a)
        self.assertEqual((first["assignee"]["name"], first["project"]), ("小明", "網站"))
        self.assertTrue(next(t for t in data["tasks"] if t["id"] == b)["overdue"])
        only = await (await self.client.get(f"/api/board?project={pid}")).json()
        self.assertEqual([t["id"] for t in only["tasks"]], [a])

        resp = await self.client.post(f"/api/tasks/{a}/status", json={"status": "doing"}, headers=HEADERS)
        self.assertEqual(resp.status, 200)
        self.assertEqual((await self.db.get_task(a))["status"], "doing")
        self.tasks.refresh_card.assert_awaited()
        await self.client.post(f"/api/tasks/{a}/status", json={"status": "done"}, headers=HEADERS)
        self.tasks.complete.assert_awaited_with(a, 0)   # 完成走共用流程（獎勵、週期任務）

        self.assertEqual((await self.client.post(f"/api/tasks/{a}/status", json={"status": "x"}, headers=HEADERS)).status, 400)
        self.assertEqual((await self.client.post("/api/tasks/999/status", json={"status": "done"}, headers=HEADERS)).status, 404)
        self.assertEqual((await self.client.post(f"/api/tasks/{a}/status", json={"status": "todo"})).status, 403)   # 沒有 CSRF 標頭
        audit = await (await self.client.get("/api/audit")).json()
        self.assertTrue(any(item["action"] == "move_task" for item in audit["items"]))


NOW = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)


def event(eid, kind, action, **payload):
    return {"id": str(eid), "type": kind, "actor": {"login": "boyo"}, "payload": {"action": action, **payload}}


class GitHubParsingTest(unittest.TestCase):
    def test_normalize_repo(self):
        for text in ("BoYoOUO-0924/discord-bot", "https://github.com/BoYoOUO-0924/discord-bot",
                     "https://github.com/BoYoOUO-0924/discord-bot.git", "https://github.com/BoYoOUO-0924/discord-bot/"):
            self.assertEqual(gh.normalize_repo(text), "BoYoOUO-0924/discord-bot", text)
        for text in ("", "discord-bot", "a/b/c", "https://gitlab.com/a/b"):
            self.assertIsNone(gh.normalize_repo(text), text)

    def test_events(self):
        events = [
            event(5, "PullRequestEvent", "closed", pull_request={"number": 7, "title": "沒合併", "merged": False}),
            event(4, "PullRequestEvent", "closed", pull_request={"number": 6, "title": "加看板", "merged": True}),
            event(3, "IssuesEvent", "opened", issue={"number": 2, "title": "按鈕壞了", "html_url": "u"}),
            event(2, "WatchEvent", "started"),
            event(1, "IssuesEvent", "opened", issue={"number": 1, "title": "舊的"}),
        ]
        texts = [n.text for n in gh.event_notices(events, last_id=1)]
        self.assertEqual(len(texts), 2)
        self.assertIn("新 issue #2", texts[0])     # 舊到新
        self.assertIn("PR 已合併 #6", texts[1])

    def test_failed_runs(self):
        runs = [
            {"id": 3, "status": "in_progress", "conclusion": None},
            {"id": 2, "status": "completed", "conclusion": "failure", "name": "tests", "head_branch": "main",
             "created_at": "2026-11-14T11:00:00Z"},
            {"id": 1, "status": "completed", "conclusion": "failure", "created_at": "2026-11-10T00:00:00Z"},  # 太舊
            {"id": 0, "status": "completed", "conclusion": "success", "created_at": "2026-11-14T11:00:00Z"},
        ]
        notices = gh.failed_runs(runs, seen=[], now=NOW)
        self.assertEqual(len(notices), 1)
        self.assertIn("tests", notices[0].text)
        self.assertEqual(gh.failed_runs(runs, seen=[2], now=NOW), [])

    def test_state_roundtrip(self):
        state = gh.State(event_id=9, runs_seen=list(range(150)), initialized=True)
        loaded = gh.State.load(state.dump())
        self.assertEqual((loaded.event_id, len(loaded.runs_seen), loaded.initialized), (9, gh.SEEN_KEEP, True))
        self.assertEqual(gh.State.load("壞掉的 json").event_id, 0)


class FakeGitHub(gh.GitHubClient):
    def __init__(self):
        super().__init__(token=None)
        self.events, self.runs, self.commits = [], [], []

    async def get(self, path, **params):
        if path.endswith("/events"):
            return self.events
        if path.endswith("/actions/runs"):
            return {"workflow_runs": self.runs}
        return self.commits


def commit(sha, message, date="2026-11-14T11:30:00Z"):
    return {"sha": sha, "commit": {"message": message, "committer": {"date": date}}}


class GitHubPollTest(unittest.IsolatedAsyncioTestCase):
    async def test_first_poll_only_records_position(self):
        client = FakeGitHub()
        client.events = [event(10, "IssuesEvent", "opened", issue={"number": 1, "title": "舊"})]
        client.commits = [commit("a" * 40, "task#1")]
        state = gh.State()
        result = await client.poll("o/r", state, NOW)
        self.assertEqual((result.notices, result.task_refs), ([], {}))
        self.assertEqual(state.event_id, 10)

        client.events.insert(0, event(11, "IssuesEvent", "opened", issue={"number": 2, "title": "新"}))
        client.commits.insert(0, commit("b" * 40, "完成登入 task#5", "2026-11-14T11:40:00Z"))
        result = await client.poll("o/r", state, NOW)
        self.assertEqual(len(result.notices), 1)
        self.assertEqual(result.task_refs, {5: "bbbbbbb"})   # 舊 commit 的 task#1 不會再處理
        self.assertEqual(state.commit_since, "2026-11-14T11:40:00Z")

    async def test_sync_completes_task_in_same_guild_only(self):
        tmp = tempfile.TemporaryDirectory()
        db = Database(os.path.join(tmp.name, "test.db"))
        await db.connect()
        try:
            pid = await db.create_project(1, "網站", 10, 100)
            mine = await db.add_task(1, 10, 100, "登入頁")
            other = await db.add_task(2, 20, 100, "別的伺服器")
            await db.update_project(pid, github_repo="o/r", github_state=gh.State(initialized=True).dump())
            tasks_cog = SimpleNamespace(complete=AsyncMock(return_value=True))
            channel = FakeChannel()
            bot = SimpleNamespace(db=db, settings=SimpleNamespace(github_token=None), get_channel=lambda cid: channel,
                                  get_cog={"Tasks": tasks_cog}.get)
            sync = GitHubSync(bot)
            sync.client = FakeGitHub()
            sync.client.commits = [commit("c" * 40, f"task#{mine} task#{other} task#999")]
            await sync.sync(await db.get_project(pid))
            tasks_cog.complete.assert_awaited_once_with(mine, 0)
            self.assertIn(f"完成了待辦 #{mine}", channel.sent[0][1]["embed"].description)
            state = json.loads((await db.get_project(pid))["github_state"])
            self.assertIn("c" * 40, state["commits_seen"])
        finally:
            await db.close()
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
