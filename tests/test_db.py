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
        self.assertEqual(first.gained, 600)           # 基礎 100 + 首次 500
        again = await self.db.checkin(1, day)
        self.assertTrue(again.already)
        second = await self.db.checkin(1, day + timedelta(days=1))
        self.assertEqual((second.streak, second.gained), (2, 120))
        broken = await self.db.checkin(1, day + timedelta(days=5))
        self.assertEqual(broken.streak, 1)
        self.assertTrue(broken.streak_broken)
        self.assertEqual(await self.db.get_points(1), 600 + 120 + 100)

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

    async def test_legacy_json_migration(self):
        with open(os.path.join(self.tmp.name, "points.json"), "w", encoding="utf-8") as f:
            json.dump({"111": 1300, "222": 0}, f)
        with open(os.path.join(self.tmp.name, "checkin.json"), "w", encoding="utf-8") as f:
            json.dump({"111": {"last_checkin_iso": "2026-10-12T06:07:06+00:00",
                               "consecutive_days": 3, "first_bonus_received": True}}, f)
        self.assertEqual(await self.db.migrate_legacy_json(self.tmp.name), 2)
        self.assertEqual(await self.db.migrate_legacy_json(self.tmp.name), 0)   # 只匯入一次
        self.assertEqual(await self.db.get_points(111), 1300)
        profile = await self.db.get_profile(111, date(2026, 10, 12))
        self.assertEqual((profile.streak, profile.last_checkin), (3, date(2026, 10, 12)))
        # 已經領過首次獎勵，不會再拿 500
        result = await self.db.checkin(111, date(2026, 10, 13))
        self.assertEqual(result.first_bonus, 0)


if __name__ == "__main__":
    unittest.main()
