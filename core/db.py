"""SQLite 資料層。

所有積分異動都走這裡，並以單一連線加上 asyncio.Lock 序列化，
避免舊版 JSON 檔「多人同時操作互相覆蓋」的問題。
"""
import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

import aiosqlite

from .timeutil import TAIPEI

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS users (
    user_id        INTEGER PRIMARY KEY,
    points         INTEGER NOT NULL DEFAULT 0,
    streak         INTEGER NOT NULL DEFAULT 0,
    last_checkin   TEXT,                       -- 台灣時間的日期 YYYY-MM-DD
    total_checkins INTEGER NOT NULL DEFAULT 0,
    first_bonus    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS guild_members (
    guild_id INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);
CREATE TABLE IF NOT EXISTS transactions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    amount     INTEGER NOT NULL,
    reason     TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_transactions_user ON transactions (user_id, id DESC);
CREATE TABLE IF NOT EXISTS holds (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    amount     INTEGER NOT NULL,
    reason     TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS game_stats (
    user_id INTEGER NOT NULL,
    game    TEXT NOT NULL,
    played  INTEGER NOT NULL DEFAULT 0,
    won     INTEGER NOT NULL DEFAULT 0,
    net     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, game)
);
CREATE TABLE IF NOT EXISTS reminders (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    channel_id INTEGER,
    content    TEXT NOT NULL,
    due_at     REAL NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders (due_at);
CREATE TABLE IF NOT EXISTS polls (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id INTEGER NOT NULL,
    message_id INTEGER,
    author_id  INTEGER NOT NULL,
    question   TEXT NOT NULL,
    options    TEXT NOT NULL,                  -- JSON 陣列
    closed     INTEGER NOT NULL DEFAULT 0,
    ends_at    REAL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS poll_votes (
    poll_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    option  INTEGER NOT NULL,
    PRIMARY KEY (poll_id, user_id)
);
CREATE TABLE IF NOT EXISTS income (
    user_id     INTEGER PRIMARY KEY,
    last_work   REAL,
    work_count  INTEGER NOT NULL DEFAULT 0,
    chat_day    TEXT,
    chat_earned INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS user_names (
    user_id    INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    avatar_url TEXT,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS stats_daily (
    day    TEXT NOT NULL,                       -- 台灣時間 YYYY-MM-DD
    metric TEXT NOT NULL,
    value  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, metric)
);
CREATE TABLE IF NOT EXISTS soup_played (
    guild_id  INTEGER NOT NULL,
    puzzle_id TEXT NOT NULL,
    played_at REAL NOT NULL,
    PRIMARY KEY (guild_id, puzzle_id)
);
CREATE TABLE IF NOT EXISTS admin_audit (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    action     TEXT NOT NULL,
    user_id    INTEGER,
    detail     TEXT NOT NULL,
    ip         TEXT
);
CREATE TABLE IF NOT EXISTS quest_progress (
    user_id  INTEGER NOT NULL,
    day      TEXT NOT NULL,
    quest    TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    done     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day, quest)
);
"""

CHECKIN_BASE = 100
CHECKIN_FIRST_BONUS = 500
CHECKIN_STREAK_STEP = 20


def checkin_reward(streak: int, first_time: bool) -> Tuple[int, int, int]:
    """回傳 (基礎, 首次加碼, 連續加碼)。"""
    return CHECKIN_BASE, CHECKIN_FIRST_BONUS if first_time else 0, CHECKIN_STREAK_STEP * max(0, streak - 1)


@dataclass
class CheckinResult:
    already: bool
    streak: int
    total_checkins: int
    balance: int
    base: int = 0
    first_bonus: int = 0
    streak_bonus: int = 0
    streak_broken: bool = False

    @property
    def gained(self) -> int:
        return self.base + self.first_bonus + self.streak_bonus


@dataclass
class Profile:
    user_id: int
    points: int
    streak: int               # 已考慮中斷：超過一天沒簽到就是 0
    last_checkin: Optional[date]
    total_checkins: int


@dataclass
class Poll:
    id: int
    channel_id: int
    message_id: Optional[int]
    author_id: int
    question: str
    options: List[str]
    closed: bool
    ends_at: Optional[float]


class Database:
    def __init__(self, path: str):
        self.path = path
        self._conn: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()
        self._members_seen: set = set()
        self._names_seen: Dict[int, Tuple[str, Optional[str]]] = {}

    # ------------------------------------------------------------------ 連線
    async def connect(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        # WAL 模式下用 NORMAL 一樣不會損壞資料庫，但寫入不必每次等硬碟同步，舊電腦上快很多
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if not self._conn:
            raise RuntimeError("資料庫尚未連線")
        return self._conn

    async def _ensure_user(self, user_id: int) -> None:
        await self.conn.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))

    async def _log(self, user_id: int, amount: int, reason: str) -> None:
        if amount:
            await self.conn.execute(
                "INSERT INTO transactions (user_id, amount, reason, created_at) VALUES (?, ?, ?, ?)",
                (user_id, amount, reason, time.time()),
            )

    async def _balance(self, user_id: int) -> int:
        async with self.conn.execute("SELECT points FROM users WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
        return row["points"] if row else 0

    # ------------------------------------------------------------------ 舊資料匯入
    async def migrate_legacy_json(self, data_dir: str) -> int:
        """第一次啟動時匯入舊版 points.json / checkin.json，只執行一次，原檔不會被更動。"""
        async with self._lock:
            async with self.conn.execute("SELECT value FROM meta WHERE key = 'legacy_json_migrated'") as cur:
                if await cur.fetchone():
                    return 0

            def load(name: str) -> Dict:
                path = os.path.join(data_dir, name)
                try:
                    with open(path, encoding="utf-8") as f:
                        return json.load(f)
                except (OSError, ValueError):
                    return {}

            points, checkins = load("points.json"), load("checkin.json")
            imported = 0
            for uid in set(points) | set(checkins):
                if not str(uid).strip().isdigit():
                    continue
                record = checkins.get(uid, {})
                last_date = None
                if record.get("last_checkin_iso"):
                    try:
                        last_date = datetime.fromisoformat(record["last_checkin_iso"]).astimezone(TAIPEI).date().isoformat()
                    except ValueError:
                        pass
                await self.conn.execute(
                    "INSERT OR REPLACE INTO users (user_id, points, streak, last_checkin, total_checkins, first_bonus) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        int(uid),
                        max(0, int(points.get(uid, 0))),
                        int(record.get("consecutive_days", 0)),
                        last_date,
                        int(record.get("consecutive_days", 0)),
                        1 if record.get("first_bonus_received") else 0,
                    ),
                )
                imported += 1
            await self.conn.execute("INSERT INTO meta (key, value) VALUES ('legacy_json_migrated', ?)", (str(time.time()),))
            await self.conn.commit()
            return imported

    # ------------------------------------------------------------------ 積分
    async def touch_member(self, guild_id: int, user_id: int) -> None:
        """記錄使用者出現在哪個伺服器，排行榜只列出同伺服器的人。

        每個指令都會呼叫，所以同一組只在每次啟動後寫一次，避免拖慢指令回應。
        """
        key = (guild_id, user_id)
        if key in self._members_seen:
            return
        async with self._lock:
            await self.conn.execute(
                "INSERT OR IGNORE INTO guild_members (guild_id, user_id) VALUES (?, ?)", key
            )
            await self.conn.commit()
        self._members_seen.add(key)

    async def get_points(self, user_id: int) -> int:
        async with self._lock:
            return await self._balance(user_id)

    async def add_points(self, user_id: int, amount: int, reason: str) -> int:
        async with self._lock:
            await self._ensure_user(user_id)
            await self.conn.execute(
                "UPDATE users SET points = MAX(0, points + ?) WHERE user_id = ?", (amount, user_id)
            )
            await self._log(user_id, amount, reason)
            await self.conn.commit()
            return await self._balance(user_id)

    async def transfer(self, sender: int, receiver: int, amount: int) -> Optional[int]:
        """成功回傳轉出者的新餘額；餘額不足回傳 None。"""
        async with self._lock:
            await self._ensure_user(sender)
            await self._ensure_user(receiver)
            cur = await self.conn.execute(
                "UPDATE users SET points = points - ? WHERE user_id = ? AND points >= ?", (amount, sender, amount)
            )
            if cur.rowcount == 0:
                await self.conn.rollback()
                return None
            await self.conn.execute("UPDATE users SET points = points + ? WHERE user_id = ?", (amount, receiver))
            await self._log(sender, -amount, f"轉帳給 <@{receiver}>")
            await self._log(receiver, amount, f"收到 <@{sender}> 的轉帳")
            await self.conn.commit()
            return await self._balance(sender)

    # ------------------------------------------------------------------ 下注保管
    # 開局時先把賭注從餘額扣下來保管，結算時再一次發放。
    # bot 中途重啟的話，啟動時會把所有保管中的賭注退還。
    async def hold(self, user_id: int, amount: int, reason: str) -> Optional[int]:
        async with self._lock:
            await self._ensure_user(user_id)
            cur = await self.conn.execute(
                "UPDATE users SET points = points - ? WHERE user_id = ? AND points >= ?", (amount, user_id, amount)
            )
            if cur.rowcount == 0:
                await self.conn.rollback()
                return None
            cur = await self.conn.execute(
                "INSERT INTO holds (user_id, amount, reason, created_at) VALUES (?, ?, ?, ?)",
                (user_id, amount, reason, time.time()),
            )
            await self.conn.commit()
            return cur.lastrowid

    async def extend_hold(self, hold_id: int, extra: int) -> bool:
        """加碼（例如 21 點加倍）。餘額不足回傳 False。"""
        async with self._lock:
            async with self.conn.execute("SELECT user_id FROM holds WHERE id = ?", (hold_id,)) as cur:
                row = await cur.fetchone()
            if not row:
                return False
            cur = await self.conn.execute(
                "UPDATE users SET points = points - ? WHERE user_id = ? AND points >= ?", (extra, row["user_id"], extra)
            )
            if cur.rowcount == 0:
                await self.conn.rollback()
                return False
            await self.conn.execute("UPDATE holds SET amount = amount + ? WHERE id = ?", (extra, hold_id))
            await self.conn.commit()
            return True

    async def settle(self, hold_id: int, payout: int, reason: str,
                     game: Optional[str] = None, won: Optional[bool] = None) -> int:
        """結算保管中的賭注：發放 payout，並記錄淨損益與戰績。回傳新餘額。"""
        async with self._lock:
            async with self.conn.execute("SELECT user_id, amount FROM holds WHERE id = ?", (hold_id,)) as cur:
                row = await cur.fetchone()
            if not row:
                raise ValueError(f"找不到保管紀錄 #{hold_id}")
            user_id, amount = row["user_id"], row["amount"]
            await self.conn.execute("DELETE FROM holds WHERE id = ?", (hold_id,))
            await self.conn.execute("UPDATE users SET points = points + ? WHERE user_id = ?", (payout, user_id))
            net = payout - amount
            await self._log(user_id, net, reason)
            if game:
                await self._record_game(user_id, game, won, net)
            await self.conn.commit()
            return await self._balance(user_id)

    async def refund_all_holds(self) -> int:
        async with self._lock:
            async with self.conn.execute("SELECT id, user_id, amount, reason FROM holds") as cur:
                rows = await cur.fetchall()
            for row in rows:
                await self.conn.execute("UPDATE users SET points = points + ? WHERE user_id = ?", (row["amount"], row["user_id"]))
                await self.conn.execute(
                    "INSERT INTO transactions (user_id, amount, reason, created_at) VALUES (?, ?, ?, ?)",
                    (row["user_id"], 0, f"{row['reason']}：bot 重啟，已退還 {row['amount']} 分", time.time()),
                )
            await self.conn.execute("DELETE FROM holds")
            await self.conn.commit()
            return len(rows)

    # ------------------------------------------------------------------ 戰績
    async def _record_game(self, user_id: int, game: str, won: Optional[bool], net: int) -> None:
        await self.conn.execute(
            "INSERT INTO game_stats (user_id, game, played, won, net) VALUES (?, ?, 1, ?, ?) "
            "ON CONFLICT (user_id, game) DO UPDATE SET played = played + 1, won = won + excluded.won, net = net + excluded.net",
            (user_id, game, 1 if won else 0, net),
        )

    async def record_game(self, user_id: int, game: str, won: Optional[bool], net: int = 0) -> None:
        async with self._lock:
            await self._record_game(user_id, game, won, net)
            await self.conn.commit()

    async def game_stats(self, user_id: int) -> List[aiosqlite.Row]:
        async with self._lock:
            async with self.conn.execute(
                "SELECT game, played, won, net FROM game_stats WHERE user_id = ? ORDER BY played DESC", (user_id,)
            ) as cur:
                return await cur.fetchall()

    # ------------------------------------------------------------------ 簽到
    async def checkin(self, user_id: int, today: date) -> CheckinResult:
        async with self._lock:
            await self._ensure_user(user_id)
            async with self.conn.execute(
                "SELECT points, streak, last_checkin, total_checkins, first_bonus FROM users WHERE user_id = ?", (user_id,)
            ) as cur:
                row = await cur.fetchone()

            last = date.fromisoformat(row["last_checkin"]) if row["last_checkin"] else None
            if last == today:
                return CheckinResult(True, row["streak"], row["total_checkins"], row["points"])

            continued = last == today - timedelta(days=1)
            streak = row["streak"] + 1 if continued else 1
            base, first, bonus = checkin_reward(streak, first_time=not row["first_bonus"])
            gained = base + first + bonus
            await self.conn.execute(
                "UPDATE users SET points = points + ?, streak = ?, last_checkin = ?, "
                "total_checkins = total_checkins + 1, first_bonus = 1 WHERE user_id = ?",
                (gained, streak, today.isoformat(), user_id),
            )
            await self._log(user_id, gained, f"每日簽到（連續 {streak} 天）")
            await self.conn.commit()
            return CheckinResult(
                already=False, streak=streak, total_checkins=row["total_checkins"] + 1,
                balance=row["points"] + gained, base=base, first_bonus=first, streak_bonus=bonus,
                streak_broken=last is not None and not continued and row["streak"] > 1,
            )

    async def get_profile(self, user_id: int, today: date) -> Profile:
        async with self._lock:
            async with self.conn.execute(
                "SELECT points, streak, last_checkin, total_checkins FROM users WHERE user_id = ?", (user_id,)
            ) as cur:
                row = await cur.fetchone()
        if not row:
            return Profile(user_id, 0, 0, None, 0)
        last = date.fromisoformat(row["last_checkin"]) if row["last_checkin"] else None
        alive = last is not None and last >= today - timedelta(days=1)
        return Profile(user_id, row["points"], row["streak"] if alive else 0, last, row["total_checkins"])

    async def recent_transactions(self, user_id: int, limit: int = 5) -> List[aiosqlite.Row]:
        async with self._lock:
            async with self.conn.execute(
                "SELECT amount, reason, created_at FROM transactions WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (user_id, limit),
            ) as cur:
                return await cur.fetchall()

    # ------------------------------------------------------------------ 排行榜
    async def leaderboard(self, guild_id: int, by: str, today: date, limit: int = 10) -> List[Tuple[int, int]]:
        """by = 'points' 或 'streak'。回傳 [(user_id, 數值), ...]。"""
        if by == "streak":
            # 中斷的連續簽到不算
            yesterday = (today - timedelta(days=1)).isoformat()
            sql = ("SELECT u.user_id, u.streak AS value FROM users u JOIN guild_members g ON g.user_id = u.user_id "
                   "WHERE g.guild_id = ? AND u.last_checkin >= ? AND u.streak > 0 ORDER BY value DESC, u.user_id LIMIT ?")
            params = (guild_id, yesterday, limit)
        else:
            sql = ("SELECT u.user_id, u.points AS value FROM users u JOIN guild_members g ON g.user_id = u.user_id "
                   "WHERE g.guild_id = ? AND u.points > 0 ORDER BY value DESC, u.user_id LIMIT ?")
            params = (guild_id, limit)
        async with self._lock:
            async with self.conn.execute(sql, params) as cur:
                return [(r["user_id"], r["value"]) for r in await cur.fetchall()]

    async def points_rank(self, guild_id: int, user_id: int) -> Optional[int]:
        async with self._lock:
            async with self.conn.execute(
                "SELECT COUNT(*) + 1 AS rank FROM users u JOIN guild_members g ON g.user_id = u.user_id "
                "WHERE g.guild_id = ? AND u.points > (SELECT points FROM users WHERE user_id = ?)",
                (guild_id, user_id),
            ) as cur:
                row = await cur.fetchone()
            async with self.conn.execute("SELECT points FROM users WHERE user_id = ?", (user_id,)) as cur:
                mine = await cur.fetchone()
        return row["rank"] if mine and mine["points"] > 0 else None

    # ------------------------------------------------------------------ 打工 / 聊天獎勵
    async def try_work(self, user_id: int, now: float, cooldown: float, reward: int,
                       reason: str) -> Tuple[bool, float, int]:
        """冷卻結束才發薪。回傳 (是否成功, 下次可打工的時間, 餘額)。"""
        async with self._lock:
            await self._ensure_user(user_id)
            await self.conn.execute("INSERT OR IGNORE INTO income (user_id) VALUES (?)", (user_id,))
            async with self.conn.execute("SELECT last_work FROM income WHERE user_id = ?", (user_id,)) as cur:
                last = (await cur.fetchone())["last_work"]
            if last and now - last < cooldown:
                return False, last + cooldown, await self._balance(user_id)
            await self.conn.execute(
                "UPDATE income SET last_work = ?, work_count = work_count + 1 WHERE user_id = ?", (now, user_id))
            await self.conn.execute("UPDATE users SET points = points + ? WHERE user_id = ?", (reward, user_id))
            await self._log(user_id, reward, reason)
            await self.conn.commit()
            return True, now + cooldown, await self._balance(user_id)

    async def add_chat_reward(self, user_id: int, day: date, amount: int, daily_cap: int) -> int:
        """發聊天獎勵（每天有上限），回傳實際發出的金額。"""
        async with self._lock:
            await self._ensure_user(user_id)
            await self.conn.execute("INSERT OR IGNORE INTO income (user_id) VALUES (?)", (user_id,))
            async with self.conn.execute("SELECT chat_day, chat_earned FROM income WHERE user_id = ?", (user_id,)) as cur:
                row = await cur.fetchone()
            earned = row["chat_earned"] if row["chat_day"] == day.isoformat() else 0
            granted = max(0, min(amount, daily_cap - earned))
            if granted:
                await self.conn.execute(
                    "UPDATE income SET chat_day = ?, chat_earned = ? WHERE user_id = ?",
                    (day.isoformat(), earned + granted, user_id))
                await self.conn.execute("UPDATE users SET points = points + ? WHERE user_id = ?", (granted, user_id))
                # 聊天獎勵很零碎，不逐筆寫入收支紀錄，只累計每日總額給後台統計
                await self.conn.execute(
                    "INSERT INTO stats_daily (day, metric, value) VALUES (?, 'chat_reward', ?) "
                    "ON CONFLICT (day, metric) DO UPDATE SET value = value + excluded.value",
                    (day.isoformat(), granted))
                await self.conn.commit()
            return granted

    async def income_status(self, user_id: int, day: date) -> Tuple[Optional[float], int]:
        """回傳 (上次打工時間, 今天已領的聊天獎勵)。"""
        async with self._lock:
            async with self.conn.execute("SELECT last_work, chat_day, chat_earned FROM income WHERE user_id = ?",
                                         (user_id,)) as cur:
                row = await cur.fetchone()
        if not row:
            return None, 0
        return row["last_work"], row["chat_earned"] if row["chat_day"] == day.isoformat() else 0

    # ------------------------------------------------------------------ 每日任務
    async def quest_progress(self, user_id: int, day: date) -> Dict[str, Tuple[int, bool]]:
        async with self._lock:
            async with self.conn.execute(
                "SELECT quest, progress, done FROM quest_progress WHERE user_id = ? AND day = ?",
                (user_id, day.isoformat()),
            ) as cur:
                return {r["quest"]: (r["progress"], bool(r["done"])) for r in await cur.fetchall()}

    async def advance_quest(self, user_id: int, day: date, quest: str, increment: int, target: int,
                            reward: int, reason: str) -> bool:
        """增加任務進度；剛好達成時發獎勵並回傳 True（每個任務只會發一次）。"""
        async with self._lock:
            await self.conn.execute(
                "INSERT OR IGNORE INTO quest_progress (user_id, day, quest) VALUES (?, ?, ?)",
                (user_id, day.isoformat(), quest))
            async with self.conn.execute(
                "SELECT progress, done FROM quest_progress WHERE user_id = ? AND day = ? AND quest = ?",
                (user_id, day.isoformat(), quest),
            ) as cur:
                row = await cur.fetchone()
            if row["done"]:
                await self.conn.commit()
                return False
            progress = min(target, row["progress"] + increment)
            completed = progress >= target
            await self.conn.execute(
                "UPDATE quest_progress SET progress = ?, done = ? WHERE user_id = ? AND day = ? AND quest = ?",
                (progress, 1 if completed else 0, user_id, day.isoformat(), quest))
            if completed and reward:
                await self._ensure_user(user_id)
                await self.conn.execute("UPDATE users SET points = points + ? WHERE user_id = ?", (reward, user_id))
                await self._log(user_id, reward, reason)
            await self.conn.commit()
            return completed

    # ------------------------------------------------------------------ 提醒
    async def add_reminder(self, user_id: int, channel_id: Optional[int], content: str, due_at: float) -> int:
        async with self._lock:
            cur = await self.conn.execute(
                "INSERT INTO reminders (user_id, channel_id, content, due_at, created_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, channel_id, content, due_at, time.time()),
            )
            await self.conn.commit()
            return cur.lastrowid

    async def count_reminders(self, user_id: int) -> int:
        async with self._lock:
            async with self.conn.execute("SELECT COUNT(*) AS n FROM reminders WHERE user_id = ?", (user_id,)) as cur:
                return (await cur.fetchone())["n"]

    async def list_reminders(self, user_id: int) -> List[aiosqlite.Row]:
        async with self._lock:
            async with self.conn.execute(
                "SELECT id, channel_id, content, due_at FROM reminders WHERE user_id = ? ORDER BY due_at", (user_id,)
            ) as cur:
                return await cur.fetchall()

    async def due_reminders(self, now: float) -> List[aiosqlite.Row]:
        async with self._lock:
            async with self.conn.execute(
                "SELECT id, user_id, channel_id, content, due_at, created_at FROM reminders WHERE due_at <= ? ORDER BY due_at",
                (now,),
            ) as cur:
                return await cur.fetchall()

    async def delete_reminder(self, reminder_id: int, user_id: Optional[int] = None) -> bool:
        async with self._lock:
            if user_id is None:
                cur = await self.conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
            else:
                cur = await self.conn.execute("DELETE FROM reminders WHERE id = ? AND user_id = ?", (reminder_id, user_id))
            await self.conn.commit()
            return cur.rowcount > 0

    # ------------------------------------------------------------------ 投票
    async def create_poll(self, channel_id: int, author_id: int, question: str,
                          options: List[str], ends_at: Optional[float]) -> int:
        async with self._lock:
            cur = await self.conn.execute(
                "INSERT INTO polls (channel_id, author_id, question, options, ends_at, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (channel_id, author_id, question, json.dumps(options, ensure_ascii=False), ends_at, time.time()),
            )
            await self.conn.commit()
            return cur.lastrowid

    async def set_poll_message(self, poll_id: int, message_id: int) -> None:
        async with self._lock:
            await self.conn.execute("UPDATE polls SET message_id = ? WHERE id = ?", (message_id, poll_id))
            await self.conn.commit()

    @staticmethod
    def _row_to_poll(row) -> Poll:
        return Poll(row["id"], row["channel_id"], row["message_id"], row["author_id"], row["question"],
                    json.loads(row["options"]), bool(row["closed"]), row["ends_at"])

    async def get_poll(self, poll_id: int) -> Optional[Poll]:
        async with self._lock:
            async with self.conn.execute("SELECT * FROM polls WHERE id = ?", (poll_id,)) as cur:
                row = await cur.fetchone()
        return self._row_to_poll(row) if row else None

    async def vote(self, poll_id: int, user_id: int, option: int) -> str:
        """回傳 'added'、'changed' 或 'removed'（再按一次同一個選項就是取消）。"""
        async with self._lock:
            async with self.conn.execute(
                "SELECT option FROM poll_votes WHERE poll_id = ? AND user_id = ?", (poll_id, user_id)
            ) as cur:
                row = await cur.fetchone()
            if row and row["option"] == option:
                await self.conn.execute("DELETE FROM poll_votes WHERE poll_id = ? AND user_id = ?", (poll_id, user_id))
                result = "removed"
            else:
                await self.conn.execute(
                    "INSERT OR REPLACE INTO poll_votes (poll_id, user_id, option) VALUES (?, ?, ?)", (poll_id, user_id, option)
                )
                result = "changed" if row else "added"
            await self.conn.commit()
            return result

    async def poll_counts(self, poll_id: int, option_count: int) -> List[int]:
        async with self._lock:
            async with self.conn.execute(
                "SELECT option, COUNT(*) AS n FROM poll_votes WHERE poll_id = ? GROUP BY option", (poll_id,)
            ) as cur:
                rows = await cur.fetchall()
        counts = [0] * option_count
        for row in rows:
            if 0 <= row["option"] < option_count:
                counts[row["option"]] = row["n"]
        return counts

    async def close_poll(self, poll_id: int) -> bool:
        async with self._lock:
            cur = await self.conn.execute("UPDATE polls SET closed = 1 WHERE id = ? AND closed = 0", (poll_id,))
            await self.conn.commit()
            return cur.rowcount > 0

    async def expired_polls(self, now: float) -> List[Poll]:
        async with self._lock:
            async with self.conn.execute(
                "SELECT * FROM polls WHERE closed = 0 AND ends_at IS NOT NULL AND ends_at <= ?", (now,)
            ) as cur:
                rows = await cur.fetchall()
        return [self._row_to_poll(r) for r in rows]

    # ------------------------------------------------------------------ 使用者名稱（後台搜尋用）
    async def remember_user(self, user_id: int, name: str, avatar_url: Optional[str]) -> None:
        """記下使用者目前的顯示名稱與頭像。名稱沒變就不寫資料庫。"""
        if self._names_seen.get(user_id) == (name, avatar_url):
            return
        async with self._lock:
            await self.conn.execute(
                "INSERT INTO user_names (user_id, name, avatar_url, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (user_id) DO UPDATE SET name = excluded.name, avatar_url = excluded.avatar_url, "
                "updated_at = excluded.updated_at",
                (user_id, name, avatar_url, time.time()))
            await self.conn.commit()
        self._names_seen[user_id] = (name, avatar_url)

    async def get_names(self, user_ids: List[int]) -> Dict[int, Tuple[str, Optional[str]]]:
        if not user_ids:
            return {}
        marks = ",".join("?" * len(user_ids))
        async with self._lock:
            async with self.conn.execute(
                f"SELECT user_id, name, avatar_url FROM user_names WHERE user_id IN ({marks})", list(user_ids)
            ) as cur:
                return {r["user_id"]: (r["name"], r["avatar_url"]) for r in await cur.fetchall()}

    # ------------------------------------------------------------------ 後台：查詢
    USER_SORTS = {"points": "u.points", "streak": "u.streak", "checkins": "u.total_checkins", "games": "games"}

    async def search_users(self, query: str, sort: str, limit: int, offset: int) -> Tuple[int, List[aiosqlite.Row]]:
        order = self.USER_SORTS.get(sort, "u.points")
        query = query.strip()
        where = "WHERE (? = '' OR n.name LIKE ? OR CAST(u.user_id AS TEXT) = ?)"
        params = (query, f"%{query}%", query)
        async with self._lock:
            async with self.conn.execute(
                f"SELECT COUNT(*) AS n FROM users u LEFT JOIN user_names n ON n.user_id = u.user_id {where}", params
            ) as cur:
                total = (await cur.fetchone())["n"]
            async with self.conn.execute(
                "SELECT u.user_id, u.points, u.streak, u.last_checkin, u.total_checkins, n.name, n.avatar_url, "
                "(SELECT COALESCE(SUM(played), 0) FROM game_stats g WHERE g.user_id = u.user_id) AS games "
                f"FROM users u LEFT JOIN user_names n ON n.user_id = u.user_id {where} "
                f"ORDER BY {order} DESC, u.user_id LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ) as cur:
                return total, await cur.fetchall()

    async def user_row(self, user_id: int) -> Optional[aiosqlite.Row]:
        async with self._lock:
            async with self.conn.execute(
                "SELECT u.*, n.name, n.avatar_url FROM users u LEFT JOIN user_names n ON n.user_id = u.user_id "
                "WHERE u.user_id = ?", (user_id,)
            ) as cur:
                return await cur.fetchone()

    async def transactions(self, user_id: int, limit: int, offset: int) -> Tuple[int, List[aiosqlite.Row]]:
        async with self._lock:
            async with self.conn.execute("SELECT COUNT(*) AS n FROM transactions WHERE user_id = ?", (user_id,)) as cur:
                total = (await cur.fetchone())["n"]
            async with self.conn.execute(
                "SELECT id, amount, reason, created_at FROM transactions WHERE user_id = ? "
                "ORDER BY id DESC LIMIT ? OFFSET ?",
                (user_id, limit, offset),
            ) as cur:
                return total, await cur.fetchall()

    async def list_holds(self) -> List[aiosqlite.Row]:
        async with self._lock:
            async with self.conn.execute(
                "SELECT h.id, h.user_id, h.amount, h.reason, h.created_at, n.name FROM holds h "
                "LEFT JOIN user_names n ON n.user_id = h.user_id ORDER BY h.created_at"
            ) as cur:
                return await cur.fetchall()

    # 收支紀錄的原因 → 統計分類
    _CATEGORY_SQL = """CASE
        WHEN reason LIKE '每日簽到%' THEN 'checkin'
        WHEN reason LIKE '打工%' THEN 'work'
        WHEN reason LIKE '每日任務%' THEN 'quest'
        WHEN reason LIKE '猜數字%' OR reason LIKE '海龜湯%' THEN 'game_reward'
        WHEN reason IN ('21點', '拉霸機', '德州撲克') THEN 'game_net'
        WHEN reason LIKE '管理員%' THEN 'admin'
        ELSE 'other' END"""
    # Unix 時間 → 台灣日期
    _DAY_SQL = "date(created_at + 8 * 3600, 'unixepoch')"

    async def stats_overview(self, today: date) -> Dict:
        async with self._lock:
            async def one(sql, params=()):
                async with self.conn.execute(sql, params) as cur:
                    row = await cur.fetchone()
                    return row[0] or 0
            return {
                "users": await one("SELECT COUNT(*) FROM users"),
                "points": await one("SELECT SUM(points) FROM users"),
                "held": await one("SELECT SUM(amount) FROM holds"),
                "checkins_today": await one("SELECT COUNT(*) FROM users WHERE last_checkin = ?", (today.isoformat(),)),
                "active_today": await one(
                    f"SELECT COUNT(DISTINCT user_id) FROM transactions WHERE {self._DAY_SQL} = ?", (today.isoformat(),)),
            }

    async def stats_daily_series(self, today: date, days: int) -> Dict[str, Dict[str, int]]:
        """最近 days 天每天各來源發出的積分與活躍人數。回傳 {日期: {分類: 數值}}。"""
        start = (today - timedelta(days=days - 1)).isoformat()
        series: Dict[str, Dict[str, int]] = {
            (today - timedelta(days=i)).isoformat(): {} for i in range(days - 1, -1, -1)}
        async with self._lock:
            async with self.conn.execute(
                f"SELECT {self._DAY_SQL} AS day, {self._CATEGORY_SQL} AS category, SUM(amount) AS total "
                f"FROM transactions WHERE {self._DAY_SQL} >= ? AND amount > 0 GROUP BY day, category", (start,)
            ) as cur:
                for row in await cur.fetchall():
                    if row["day"] in series:
                        series[row["day"]][row["category"]] = row["total"]
            async with self.conn.execute(
                f"SELECT {self._DAY_SQL} AS day, COUNT(DISTINCT user_id) AS n FROM transactions "
                f"WHERE {self._DAY_SQL} >= ? GROUP BY day", (start,)
            ) as cur:
                for row in await cur.fetchall():
                    if row["day"] in series:
                        series[row["day"]]["active"] = row["n"]
            async with self.conn.execute(
                "SELECT day, value FROM stats_daily WHERE metric = 'chat_reward' AND day >= ?", (start,)
            ) as cur:
                for row in await cur.fetchall():
                    if row["day"] in series:
                        series[row["day"]]["chat"] = row["value"]
        return series

    async def stats_games(self) -> List[aiosqlite.Row]:
        async with self._lock:
            async with self.conn.execute(
                "SELECT game, SUM(played) AS played, SUM(won) AS won, SUM(net) AS net, COUNT(*) AS players "
                "FROM game_stats GROUP BY game ORDER BY played DESC"
            ) as cur:
                return await cur.fetchall()

    # ------------------------------------------------------------------ 後台：管理操作
    async def admin_adjust(self, user_id: int, reason: str, *, delta: Optional[int] = None,
                           set_to: Optional[int] = None) -> Tuple[int, int]:
        """管理員調整積分（加減或直接設定），回傳 (調整前, 調整後)。積分不會低於 0。"""
        async with self._lock:
            await self._ensure_user(user_id)
            old = await self._balance(user_id)
            new = max(0, set_to if set_to is not None else old + (delta or 0))
            await self.conn.execute("UPDATE users SET points = ? WHERE user_id = ?", (new, user_id))
            if new != old:
                await self._log(user_id, new - old, f"管理員調整：{reason}")
            await self.conn.commit()
            return old, new

    async def admin_set_streak(self, user_id: int, streak: int, today: date) -> None:
        """修改連續簽到天數。今天還沒簽到的話，讓他今天簽到時能接續這個天數。"""
        async with self._lock:
            await self._ensure_user(user_id)
            async with self.conn.execute("SELECT last_checkin FROM users WHERE user_id = ?", (user_id,)) as cur:
                last = (await cur.fetchone())["last_checkin"]
            if streak > 0 and last != today.isoformat():
                last = (today - timedelta(days=1)).isoformat()
            await self.conn.execute("UPDATE users SET streak = ?, last_checkin = ? WHERE user_id = ?",
                                    (streak, last, user_id))
            await self.conn.commit()

    async def admin_reset_work(self, user_id: int) -> None:
        async with self._lock:
            await self.conn.execute("UPDATE income SET last_work = NULL WHERE user_id = ?", (user_id,))
            await self.conn.commit()

    async def hold_info(self, hold_id: int) -> Optional[aiosqlite.Row]:
        async with self._lock:
            async with self.conn.execute("SELECT id, user_id, amount, reason FROM holds WHERE id = ?",
                                         (hold_id,)) as cur:
                return await cur.fetchone()

    async def audit(self, action: str, user_id: Optional[int], detail: str, ip: Optional[str]) -> None:
        async with self._lock:
            await self.conn.execute(
                "INSERT INTO admin_audit (created_at, action, user_id, detail, ip) VALUES (?, ?, ?, ?, ?)",
                (time.time(), action, user_id, detail, ip))
            await self.conn.commit()

    async def list_audit(self, limit: int = 100) -> List[aiosqlite.Row]:
        async with self._lock:
            async with self.conn.execute(
                "SELECT a.*, n.name FROM admin_audit a LEFT JOIN user_names n ON n.user_id = a.user_id "
                "ORDER BY a.id DESC LIMIT ?", (limit,)
            ) as cur:
                return await cur.fetchall()

    # ------------------------------------------------------------------ 海龜湯題庫：記錄各伺服器玩過的題目
    async def soup_played(self, guild_id: int) -> Dict[str, float]:
        async with self._lock:
            async with self.conn.execute(
                "SELECT puzzle_id, played_at FROM soup_played WHERE guild_id = ?", (guild_id,)
            ) as cur:
                return {r["puzzle_id"]: r["played_at"] for r in await cur.fetchall()}

    async def mark_soup_played(self, guild_id: int, puzzle_id: str) -> None:
        async with self._lock:
            await self.conn.execute(
                "INSERT INTO soup_played (guild_id, puzzle_id, played_at) VALUES (?, ?, ?) "
                "ON CONFLICT (guild_id, puzzle_id) DO UPDATE SET played_at = excluded.played_at",
                (guild_id, puzzle_id, time.time()))
            await self.conn.commit()
