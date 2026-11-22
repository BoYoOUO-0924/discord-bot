import json
import os
import tempfile
import unittest
from datetime import date, timedelta

from core.db import Database


class DatabaseTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.tmp.name, "test.db"))
        await self.db.connect()

    async def asyncTearDown(self):
        await self.db.close()
        self.tmp.cleanup()

    async def test_checkin_streak(self):
        day = date(2026, 10, 1)
        first = await self.db.checkin(1, day)
        self.assertEqual(first.gained, 60_000)        # 基礎 10,000 + 首次 50,000
        again = await self.db.checkin(1, day)
        self.assertTrue(again.already)
        second = await self.db.checkin(1, day + timedelta(days=1))
        self.assertEqual((second.streak, second.gained), (2, 12_000))
        broken = await self.db.checkin(1, day + timedelta(days=5))
        self.assertEqual(broken.streak, 1)
        self.assertTrue(broken.streak_broken)
        self.assertEqual(await self.db.get_points(1), 60_000 + 12_000 + 10_000)

    async def test_profile_streak_resets_after_gap(self):
        day = date(2026, 10, 1)
        await self.db.checkin(1, day)
        await self.db.checkin(1, day + timedelta(days=1))
        self.assertEqual((await self.db.get_profile(1, day + timedelta(days=2))).streak, 2)
        self.assertEqual((await self.db.get_profile(1, day + timedelta(days=3))).streak, 0)

    async def test_hold_and_settle(self):
        await self.db.add_points(1, 1000, "test")
        hold = await self.db.hold(1, 300, "21點")
        self.assertEqual(await self.db.get_points(1), 700)
        self.assertIsNone(await self.db.hold(1, 5000, "21點"))
        self.assertTrue(await self.db.extend_hold(hold, 300))
        self.assertEqual(await self.db.get_points(1), 400)
        balance = await self.db.settle(hold, 1200, "21點", game="blackjack", won=True)
        self.assertEqual(balance, 1600)
        stats = await self.db.game_stats(1)
        self.assertEqual((stats[0]["played"], stats[0]["won"], stats[0]["net"]), (1, 1, 600))

    async def test_refund_holds_on_restart(self):
        await self.db.add_points(1, 500, "test")
        await self.db.hold(1, 200, "德州撲克")
        self.assertEqual(await self.db.refund_all_holds(), 1)
        self.assertEqual(await self.db.get_points(1), 500)

    async def test_transfer(self):
        await self.db.add_points(1, 100, "test")
        self.assertIsNone(await self.db.transfer(1, 2, 500))
        self.assertEqual(await self.db.transfer(1, 2, 40), 60)
        self.assertEqual(await self.db.get_points(2), 40)

    async def test_leaderboard_is_per_guild(self):
        for uid, pts in [(1, 300), (2, 500), (3, 900)]:
            await self.db.add_points(uid, pts, "test")
        await self.db.touch_member(10, 1)
        await self.db.touch_member(10, 2)
        await self.db.touch_member(20, 3)
        board = await self.db.leaderboard(10, "points", date(2026, 10, 1))
        self.assertEqual(board, [(2, 500), (1, 300)])
        self.assertEqual(await self.db.points_rank(10, 1), 2)

    async def test_touch_member_writes_once(self):
        await self.db.touch_member(10, 1)
        await self.db.conn.execute("DELETE FROM guild_members")
        await self.db.touch_member(10, 1)   # 已經記錄過，不會再寫一次資料庫
        async with self.db.conn.execute("SELECT COUNT(*) AS n FROM guild_members") as cur:
            self.assertEqual((await cur.fetchone())["n"], 0)
        await self.db.touch_member(10, 2)   # 新的人照常寫入
        async with self.db.conn.execute("SELECT COUNT(*) AS n FROM guild_members") as cur:
            self.assertEqual((await cur.fetchone())["n"], 1)

    async def test_votes_toggle(self):
        poll = await self.db.create_poll(1, 1, "Q", ["a", "b"], None)
        self.assertEqual(await self.db.vote(poll, 5, 0), "added")
        self.assertEqual(await self.db.vote(poll, 5, 1), "changed")
        self.assertEqual(await self.db.poll_counts(poll, 2), [0, 1])
        self.assertEqual(await self.db.vote(poll, 5, 1), "removed")
        self.assertEqual(await self.db.poll_counts(poll, 2), [0, 0])

    async def test_reminders(self):
        rid = await self.db.add_reminder(1, 99, "泡麵", 1000.0)
        await self.db.add_reminder(1, 99, "之後", 5000.0)
        due = await self.db.due_reminders(2000.0)
        self.assertEqual([r["id"] for r in due], [rid])
        self.assertFalse(await self.db.delete_reminder(rid, user_id=2))
        self.assertTrue(await self.db.delete_reminder(rid, user_id=1))

    async def test_work_cooldown(self):
        ok, next_at, balance = await self.db.try_work(1, 1000.0, 3600, 150, "打工")
        self.assertEqual((ok, next_at, balance), (True, 4600.0, 150))
        ok, next_at, balance = await self.db.try_work(1, 2000.0, 3600, 150, "打工")
        self.assertEqual((ok, next_at, balance), (False, 4600.0, 150))
        ok, _, balance = await self.db.try_work(1, 4600.0, 3600, 100, "打工")
        self.assertEqual((ok, balance), (True, 250))

    async def test_work_hours_cooldown_and_daily_cap(self):
        day, h = date(2026, 11, 1), 3600
        ok, next_at, _ = await self.db.try_work(1, 0.0, 8 * h, 1000, "打工", day=day, hours=8, daily_cap=12)
        self.assertEqual((ok, next_at), (True, 8.0 * h))                 # 選 8 小時就冷卻 8 小時
        ok, next_at, _ = await self.db.try_work(1, 7.0 * h, h, 1000, "打工", day=day, hours=1, daily_cap=12)
        self.assertEqual((ok, next_at), (False, 8.0 * h))
        ok, next_at, _ = await self.db.try_work(1, 8.0 * h, 5 * h, 1000, "打工", day=day, hours=5, daily_cap=12)
        self.assertEqual((ok, next_at), (False, None))                   # 8 + 5 超過每天 12 小時
        ok, _, balance = await self.db.try_work(1, 8.0 * h, 4 * h, 1000, "打工", day=day, hours=4, daily_cap=12)
        self.assertEqual((ok, balance), (True, 2000))
        self.assertEqual(await self.db.income_status(1, day), (12.0 * h, 12, 0))
        tomorrow = day + timedelta(days=1)
        self.assertEqual((await self.db.income_status(1, tomorrow))[1], 0)   # 隔天時數歸零
        ok, _, _ = await self.db.try_work(1, 12.0 * h, h, 1000, "打工", day=tomorrow, hours=1, daily_cap=12)
        self.assertTrue(ok)
        await self.db.admin_reset_work(1)
        self.assertEqual(await self.db.income_status(1, tomorrow), (None, 0, 0))

    async def test_old_income_table_migrates_cooldown(self):
        import sqlite3
        await self.db.close()
        path = os.path.join(self.tmp.name, "old.db")
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE income (user_id INTEGER PRIMARY KEY, last_work REAL, "
                     "work_count INTEGER NOT NULL DEFAULT 0, chat_day TEXT, chat_earned INTEGER NOT NULL DEFAULT 0)")
        conn.execute("INSERT INTO income (user_id, last_work, work_count) VALUES (1, 1000.0, 3)")
        conn.commit()
        conn.close()
        self.db = Database(path)
        await self.db.connect()
        self.assertEqual(await self.db.income_status(1, date(2026, 11, 1)), (4600.0, 0, 0))  # 舊資料照 1 小時冷卻

    async def test_chat_reward_daily_cap(self):
        day = date(2026, 10, 1)
        granted = [await self.db.add_chat_reward(1, day, 30, 100) for _ in range(5)]
        self.assertEqual(granted, [30, 30, 30, 10, 0])
        self.assertEqual(await self.db.add_chat_reward(1, day + timedelta(days=1), 30, 100), 30)
        self.assertEqual(await self.db.get_points(1), 130)

    async def test_quest_rewards_once(self):
        day = date(2026, 10, 1)
        results = [await self.db.advance_quest(1, day, "play3", 1, 3, 150, "任務") for _ in range(5)]
        self.assertEqual(results, [False, False, True, False, False])
        self.assertEqual(await self.db.get_points(1), 150)
        self.assertEqual(await self.db.quest_progress(1, day), {"play3": (3, True)})

    async def test_legacy_json_migration(self):
        with open(os.path.join(self.tmp.name, "points.json"), "w", encoding="utf-8") as f:
            json.dump({"111": 1300, "222": 0}, f)
        with open(os.path.join(self.tmp.name, "checkin.json"), "w", encoding="utf-8") as f:
            json.dump({"111": {"last_checkin_iso": "2026-10-12T06:07:06+00:00",
                               "consecutive_days": 3, "first_bonus_received": True}}, f)
        self.assertEqual(await self.db.migrate_legacy_json(self.tmp.name), 2)
        self.assertEqual(await self.db.migrate_legacy_json(self.tmp.name), 0)   # 只匯入一次
        self.assertEqual(await self.db.get_points(111), 130_000)   # 舊檔是積分，1 分 = 100 元
        profile = await self.db.get_profile(111, date(2026, 10, 12))
        self.assertEqual((profile.streak, profile.last_checkin), (3, date(2026, 10, 12)))
        # 已經領過首次獎勵，不會再拿 50,000
        result = await self.db.checkin(111, date(2026, 10, 13))
        self.assertEqual(result.first_bonus, 0)


if __name__ == "__main__":
    unittest.main()
