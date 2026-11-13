import os
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from cogs import projects as proj_mod
from cogs.tasks import Tasks
from core import tasks as tk
from core.db import Database
from core.timeutil import TAIPEI

# 2026-11-13 是星期五
FRI = datetime(2026, 11, 13, 17, 5, tzinfo=TAIPEI)


class ScheduleTest(unittest.TestCase):
    def test_digest_window(self):
        at = lambda h, m=0: datetime(2026, 11, 13, h, m, tzinfo=TAIPEI)
        self.assertFalse(tk.digest_due(at(8, 59), None))
        self.assertTrue(tk.digest_due(at(9, 0), None))
        self.assertTrue(tk.digest_due(at(11, 30), "2026-11-12"))
        self.assertFalse(tk.digest_due(at(9, 30), "2026-11-13"))   # 今天發過了
        self.assertFalse(tk.digest_due(at(12, 0), None))           # 太晚不補發

    def test_report_on_friday_evening_once(self):
        self.assertTrue(tk.report_due(FRI, None))
        self.assertFalse(tk.report_due(FRI, tk.week_key(FRI)))
        self.assertFalse(tk.report_due(FRI.replace(hour=16), None))
        self.assertFalse(tk.report_due(FRI + timedelta(days=1), None))   # 週六不補發
        self.assertEqual(tk.week_start(FRI), datetime(2026, 11, 9, tzinfo=TAIPEI))

    def test_progress(self):
        self.assertIn("2/4（50%）", tk.fmt_progress(2, 4))
        self.assertEqual(tk.fmt_progress(0, 0), "還沒有任務")

    def test_fallback_summary(self):
        report = tk.WeekReport("app", done=["a", "b"], created=3, overdue=["c"], stuck=[], next_week=["d"],
                               open_count=4, milestones=[])
        text = tk.fallback_summary(report)
        self.assertIn("完成 2 個", text)
        self.assertIn("1 個已逾期", text)
        self.assertIn("app", tk.report_prompt(report))


class ProjectDatabaseTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "test.db")
        self.db = Database(self.path)
        await self.db.connect()

    async def asyncTearDown(self):
        await self.db.close()
        self.tmp.cleanup()

    async def test_projects(self):
        pid = await self.db.create_project(1, "網站", 10, 100)
        self.assertIsNone(await self.db.create_project(1, "網站", 11, 100))   # 名稱重複
        self.assertIsNotNone(await self.db.create_project(2, "網站", 20, 100))  # 別的伺服器可以同名
        self.assertEqual((await self.db.project_for_channel(1, 10))["id"], pid)
        await self.db.update_project(pid, archived=1)
        self.assertIsNone(await self.db.project_for_channel(1, 10))
        self.assertEqual(await self.db.list_projects(1), [])
        self.assertEqual(len(await self.db.list_projects(1, include_archived=True)), 1)
        self.assertEqual(len(await self.db.list_projects()), 1)   # 全部伺服器、不含封存

    async def test_milestone_progress_and_counts(self):
        pid = await self.db.create_project(1, "網站", 10, 100)
        mid = await self.db.create_milestone(pid, "第一版", None)
        self.assertIsNone(await self.db.create_milestone(pid, "第一版", None))
        a = await self.db.add_task(1, 10, 100, "a", project_id=pid, milestone_id=mid)
        await self.db.add_task(1, 10, 100, "b", project_id=pid, milestone_id=mid)
        await self.db.add_task(1, 10, 100, "c", project_id=pid, due_at=1.0)   # 逾期
        await self.db.set_task_status(a, "done", 100)
        milestone = (await self.db.list_milestones(pid))[0]
        self.assertEqual((milestone["done"], milestone["total"]), (1, 2))
        self.assertEqual(await self.db.project_counts(pid), {"todo": 2, "doing": 0, "done": 1, "overdue": 1})
        self.assertEqual(len(await self.db.list_tasks(1, project_id=pid)), 2)
        await self.db.delete_milestone(mid)
        self.assertIsNone((await self.db.get_task(a))["milestone_id"])

    async def test_week_queries(self):
        pid = await self.db.create_project(1, "網站", 10, 100)
        a = await self.db.add_task(1, 10, 100, "做完的", project_id=pid)
        b = await self.db.add_task(1, 10, 100, "卡住的", project_id=pid)
        await self.db.set_task_status(a, "done", 100)
        await self.db.set_task_status(b, "doing", 100)
        now = time.time()
        done = await self.db.project_tasks_between(pid, "done_at", now - 60, now + 60)
        self.assertEqual([t["id"] for t in done], [a])
        self.assertEqual([t["id"] for t in await self.db.project_stuck_tasks(pid, now + 60)], [b])
        self.assertEqual(await self.db.project_stuck_tasks(pid, now - 60), [])
        with self.assertRaises(ValueError):
            await self.db.project_tasks_between(pid, "title", 0, 1)

    async def test_meta(self):
        self.assertIsNone(await self.db.get_meta("x"))
        await self.db.set_meta("x", "1")
        await self.db.set_meta("x", "2")
        self.assertEqual(await self.db.get_meta("x"), "2")

    async def test_migrates_stage_one_tasks_table(self):
        """第一階段建立的 tasks 表沒有專案欄位，升級後要自動補上，舊資料保留。"""
        await self.db.close()
        old = os.path.join(self.tmp.name, "old.db")
        conn = sqlite3.connect(old)
        conn.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, "
                     "channel_id INTEGER NOT NULL, message_id INTEGER, title TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', "
                     "creator_id INTEGER NOT NULL, assignee_id INTEGER, priority INTEGER NOT NULL DEFAULT 1, "
                     "status TEXT NOT NULL DEFAULT 'todo', due_at REAL, due_has_time INTEGER NOT NULL DEFAULT 0, "
                     "remind_at REAL, reminded INTEGER NOT NULL DEFAULT 0, overdue_sent INTEGER NOT NULL DEFAULT 0, "
                     "created_at REAL NOT NULL, updated_at REAL NOT NULL, done_at REAL, done_by INTEGER)")
        conn.execute("INSERT INTO tasks (guild_id, channel_id, title, creator_id, created_at, updated_at) "
                     "VALUES (1, 10, '舊任務', 100, 0, 0)")
        conn.commit()
        conn.close()
        self.db = Database(old)
        await self.db.connect()
        task = await self.db.get_task(1)
        self.assertEqual((task["title"], task["project_id"]), ("舊任務", None))


class FakeAI:
    def __init__(self, available=True, reply="這週進度不錯。"):
        self.available = available
        self.reply = reply
        self.prompts = []

    async def text(self, prompt, **kwargs):
        self.prompts.append(prompt)
        return self.reply


class CogTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.tmp.name, "test.db"))
        await self.db.connect()
        self.ai = FakeAI()
        self.bot = SimpleNamespace(db=self.db, ai=self.ai)
        self.tasks = Tasks(self.bot)
        self.bot.get_cog = {"Tasks": self.tasks}.get
        self.cog = proj_mod.Projects(self.bot)
        self.pid = await self.db.create_project(1, "網站", 10, 100)
        self.project = await self.db.get_project(self.pid)

    async def asyncTearDown(self):
        await self.db.close()
        self.tmp.cleanup()

    async def test_resolve_project(self):
        mid = await self.db.create_milestone(self.pid, "第一版", None)
        other = await self.db.create_project(1, "App", 20, 100)
        self.assertEqual(await self.tasks.resolve_project(1, 10, None, None), (self.pid, None, None))  # 頻道的專案
        self.assertEqual(await self.tasks.resolve_project(1, 99, None, None), (None, None, None))
        self.assertEqual(await self.tasks.resolve_project(1, 99, None, mid), (self.pid, mid, None))   # 里程碑帶出專案
        self.assertIsNotNone((await self.tasks.resolve_project(1, 10, other, mid))[2])                # 里程碑不屬於專案
        self.assertIsNotNone((await self.tasks.resolve_project(2, 10, self.pid, None))[2])           # 別的伺服器

    async def test_digest(self):
        now = datetime.now(TAIPEI)
        self.assertIsNone(await self.cog.build_digest(self.project, now))   # 沒東西就不發
        late = await self.db.add_task(1, 10, 100, "逾期的", project_id=self.pid, due_at=now.timestamp() - 60)
        await self.db.add_task(1, 10, 100, "以後的", project_id=self.pid,
                               due_at=(now + timedelta(days=5)).timestamp())
        embed = await self.cog.build_digest(self.project, now)
        self.assertIn("已逾期（1）", embed.fields[0].name)
        self.assertIn(f"#{late}", embed.fields[0].value)
        self.assertEqual(len(embed.fields), 1)   # 五天後才到期的不列出

    async def test_weekly_report_uses_ai(self):
        a = await self.db.add_task(1, 10, 100, "寫首頁", project_id=self.pid, assignee_id=200)
        await self.db.set_task_status(a, "done", 200)
        await self.db.remember_user(200, "小明", None)
        report, embed, has_content = await self.cog.build_report(self.project, datetime.now(TAIPEI))
        self.assertTrue(has_content)
        self.assertEqual(report.done, ["寫首頁（小明）"])
        self.assertEqual(embed.description, "這週進度不錯。")
        self.assertIn("寫首頁（小明）", self.ai.prompts[0])

    async def test_weekly_report_without_ai(self):
        self.ai.available = False
        _, embed, has_content = await self.cog.build_report(self.project, datetime.now(TAIPEI))
        self.assertFalse(has_content)   # 空專案：排程不會發
        self.assertIn("AI 目前無法使用", embed.description)

    def test_join_lines_truncates(self):
        text = proj_mod.join_lines([f"第 {i} 行" + "x" * 50 for i in range(100)])
        self.assertLessEqual(len(text), proj_mod.FIELD_LIMIT)
        self.assertIn("還有", text)


if __name__ == "__main__":
    unittest.main()
