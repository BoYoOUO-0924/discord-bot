import os
import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from cogs import tasks as cog_mod
from cogs.assistant import buttons_for
from core import assistant as asst
from core import tasks as tk
from core.db import Database
from core.timeutil import TAIPEI

# 2026-11-13 是星期五，下午 3 點
NOW = datetime(2026, 11, 13, 15, 0, tzinfo=TAIPEI)


def at(*args) -> datetime:
    return datetime(*args, tzinfo=TAIPEI)


class ParseDueTest(unittest.TestCase):
    def check(self, text, expected, has_time):
        due = tk.parse_due(text, NOW)
        self.assertIsNotNone(due, text)
        self.assertEqual((due.at, due.has_time), (expected, has_time), text)

    def test_relative_days(self):
        self.check("明天", at(2026, 11, 14, 23, 59), False)
        self.check("後天", at(2026, 11, 15, 23, 59), False)
        self.check("今天", at(2026, 11, 13, 23, 59), False)

    def test_weekdays(self):
        self.check("週五", at(2026, 11, 13, 23, 59), False)        # 今天就是週五
        self.check("週一", at(2026, 11, 16, 23, 59), False)        # 這週一過了 → 下週一
        self.check("下週一", at(2026, 11, 16, 23, 59), False)
        self.check("下週五", at(2026, 11, 20, 23, 59), False)
        self.check("星期日", at(2026, 11, 15, 23, 59), False)
        self.check("禮拜三", at(2026, 11, 18, 23, 59), False)

    def test_dates(self):
        self.check("11/20", at(2026, 11, 20, 23, 59), False)
        self.check("2026-12-01", at(2026, 12, 1, 23, 59), False)
        self.check("1/5", at(2027, 1, 5, 23, 59), False)            # 已經過了 → 明年
        self.check("12月25日", at(2026, 12, 25, 23, 59), False)

    def test_date_with_time(self):
        self.check("明天 18:00", at(2026, 11, 14, 18, 0), True)
        self.check("週五下午3點半", at(2026, 11, 13, 15, 30), True)
        self.check("11/20 9:30", at(2026, 11, 20, 9, 30), True)
        self.check("明天早上8點", at(2026, 11, 14, 8, 0), True)
        self.check("週一前", at(2026, 11, 16, 23, 59), False)

    def test_time_and_duration(self):
        self.check("18:30", at(2026, 11, 13, 18, 30), True)
        self.check("9:00", at(2026, 11, 14, 9, 0), True)           # 過了 → 明天
        self.check("3d", at(2026, 11, 16, 15, 0), True)
        self.check("2小時", at(2026, 11, 13, 17, 0), True)

    def test_invalid(self):
        for text in ("", "隨便", "2/30", "11/20 25:00", "今天 9:00", "2020-01-01", "明天 abc"):
            self.assertIsNone(tk.parse_due(text, NOW), text)

    def test_priority(self):
        self.assertEqual([tk.parse_priority(t) for t in ("高", "中", "低", "", None, "High")], [2, 1, 0, 1, 1, 2])
        self.assertIsNone(tk.parse_priority("超級急"))

    def test_remind_at(self):
        created = NOW.timestamp()
        self.assertEqual(tk.remind_at(created + 3 * 86400, created), created + 2 * 86400)
        self.assertIsNone(tk.remind_at(created + 3600, created))   # 不到 24 小時就不另外提醒


class TaskDatabaseTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.tmp.name, "test.db"))
        await self.db.connect()

    async def asyncTearDown(self):
        await self.db.close()
        self.tmp.cleanup()

    async def test_status_flow(self):
        tid = await self.db.add_task(1, 10, 100, "寫報告")
        self.assertTrue(await self.db.set_task_status(tid, "doing", 100))
        self.assertFalse(await self.db.set_task_status(tid, "doing", 100))   # 狀態沒變
        await self.db.set_task_status(tid, "done", 200)
        task = await self.db.get_task(tid)
        self.assertEqual((task["status"], task["done_by"]), ("done", 200))
        self.assertIsNotNone(task["done_at"])
        await self.db.set_task_status(tid, "todo", 100)
        task = await self.db.get_task(tid)
        self.assertEqual((task["status"], task["done_by"], task["done_at"]), ("todo", None, None))

    async def test_list_scopes_and_order(self):
        a = await self.db.add_task(1, 10, 100, "沒截止")
        b = await self.db.add_task(1, 10, 100, "晚截止", due_at=5000.0)
        c = await self.db.add_task(1, 10, 200, "早截止給100", assignee_id=100, due_at=1000.0)
        d = await self.db.add_task(1, 10, 200, "沒人負責")
        await self.db.add_task(2, 10, 100, "別的伺服器")
        await self.db.set_task_status(d, "doing", 200)
        ids = lambda rows: [r["id"] for r in rows]
        self.assertEqual(ids(await self.db.list_tasks(1)), [d, c, b, a])     # 進行中優先，再依截止時間
        self.assertEqual(ids(await self.db.list_tasks(1, scope="mine", user_id=100)), [c, b, a])
        self.assertEqual(ids(await self.db.list_tasks(1, scope="assigned", user_id=100)), [c])
        self.assertEqual(set(ids(await self.db.list_tasks(1, scope="unassigned"))), {a, b, d})
        await self.db.set_task_status(a, "done", 100)
        self.assertNotIn(a, ids(await self.db.list_tasks(1)))
        self.assertIn(a, ids(await self.db.list_tasks(1, include_done=True)))
        self.assertEqual(await self.db.count_open_tasks(1), 3)

    async def test_notices(self):
        tid = await self.db.add_task(1, 10, 100, "交作業", due_at=10_000.0, remind_at=5_000.0)
        self.assertEqual(await self.db.due_task_notices(4_000.0), [])
        notices = await self.db.due_task_notices(6_000.0)
        self.assertEqual([(k, r["id"]) for k, r in notices], [("remind", tid)])
        await self.db.mark_task_notice(tid, "remind")
        self.assertEqual(await self.db.due_task_notices(6_000.0), [])
        notices = await self.db.due_task_notices(11_000.0)
        self.assertEqual([k for k, _ in notices], ["overdue"])
        await self.db.mark_task_notice(tid, "overdue")
        self.assertEqual(await self.db.due_task_notices(99_000.0), [])
        # 改了截止時間就重新提醒
        await self.db.update_task(tid, due_at=20_000.0, remind_at=15_000.0)
        self.assertEqual([k for k, _ in await self.db.due_task_notices(16_000.0)], ["remind"])
        # 完成的不通知
        await self.db.set_task_status(tid, "done", 100)
        self.assertEqual(await self.db.due_task_notices(99_000.0), [])

    async def test_overdue_after_long_offline_sends_once(self):
        tid = await self.db.add_task(1, 10, 100, "繳費", due_at=10_000.0, remind_at=5_000.0)
        notices = await self.db.due_task_notices(50_000.0)
        self.assertEqual([k for k, _ in notices], ["overdue"])
        await self.db.mark_task_notice(tid, "overdue")
        self.assertEqual(await self.db.due_task_notices(50_000.0), [])

    async def test_update_rejects_unknown_fields_and_delete(self):
        tid = await self.db.add_task(1, 10, 100, "原本")
        with self.assertRaises(ValueError):
            await self.db.update_task(tid, status="done")
        await self.db.update_task(tid, title="改過", priority=2, assignee_id=300)
        task = await self.db.get_task(tid)
        self.assertEqual((task["title"], task["priority"], task["assignee_id"]), ("改過", 2, 300))
        self.assertTrue(await self.db.delete_task(tid))
        self.assertIsNone(await self.db.get_task(tid))


def row(**kw):
    base = dict(id=7, guild_id=1, channel_id=10, message_id=55, title="寫報告", note="", creator_id=100,
                assignee_id=None, priority=1, status="todo", due_at=None, due_has_time=0, created_at=0.0,
                done_at=None, done_by=None)
    base.update(kw)
    return base


class CardTest(unittest.TestCase):
    def labels(self, task):
        return [item.item.label for item in cog_mod.build_view(task).children]

    def test_buttons_follow_status(self):
        self.assertEqual(self.labels(row()), ["開始", "完成", "我來做", "編輯", "指派", "刪除"])
        self.assertEqual(self.labels(row(status="doing", assignee_id=5)), ["完成", "編輯", "指派", "刪除"])
        self.assertEqual(self.labels(row(status="done", assignee_id=5)), ["重新打開", "編輯", "指派", "刪除"])

    def test_permissions(self):
        member = lambda uid, mod=False: SimpleNamespace(id=uid, guild_permissions=SimpleNamespace(manage_messages=mod))
        task = row(assignee_id=200)
        self.assertTrue(cog_mod.can_edit(task, member(100)))
        self.assertTrue(cog_mod.can_edit(task, member(200)))
        self.assertFalse(cog_mod.can_edit(task, member(300)))
        self.assertFalse(cog_mod.can_delete(task, member(200)))   # 負責人不能刪，只有建立者或管理員

    def test_overdue_card_is_red(self):
        embed = cog_mod.build_embed(row(due_at=1.0, due_has_time=1))
        self.assertEqual(embed.color.value, cog_mod.Color.DANGER)
        self.assertIn("已逾期", embed.fields[0].value)


class AssistantTaskTest(unittest.IsolatedAsyncioTestCase):
    def test_buttons(self):
        actions = buttons_for(asst.Intent("task", message="交報告", when="下週五", assignee=42))
        self.assertEqual(actions[0][0], "task:add|下週五|42|交報告")
        self.assertIn("截止", actions[0][1])
        # 看不懂的截止時間就不帶
        self.assertEqual(buttons_for(asst.Intent("task", message="交報告", when="某天"))[0][0], "task:add||0|交報告")
        self.assertEqual([a[0] for a in buttons_for(asst.Intent("task"))], ["task:mine", "task:new"])

    async def test_run_action_creates_task(self):
        from cogs.assistant import Assistant
        tasks_cog = SimpleNamespace(create=AsyncMock(), send_list=AsyncMock(), open_modal=AsyncMock())
        bot = SimpleNamespace(ai=None, user=SimpleNamespace(id=1), settings=SimpleNamespace(assistant_channels=()),
                              get_cog={"Tasks": tasks_cog}.get)
        cog = Assistant(bot)
        interaction = SimpleNamespace()
        await cog.run_action(interaction, "task:add|週五|42|交報告|附件")
        tasks_cog.create.assert_awaited_with(interaction, "交報告|附件", "週五", None, "", 42)
        await cog.run_action(interaction, "task:add||0|買咖啡")
        tasks_cog.create.assert_awaited_with(interaction, "買咖啡", None, None, "", None)
        await cog.run_action(interaction, "task:mine")
        tasks_cog.send_list.assert_awaited_with(interaction, "mine")


if __name__ == "__main__":
    unittest.main()
