"""每日任務定義。

各功能在玩家完成動作時呼叫 bot.dispatch("activity", user_id, kind, channel_id, meta)，
任務系統再依這裡的規則累計進度。
"""
import random
from dataclasses import dataclass
from datetime import date
from typing import Callable, Dict, List

DAILY_COUNT = 3
ALL_DONE_BONUS = 200
ALL_DONE_KEY = "__all__"
MIN_QUEST_BET = 50   # 下注太小不算任務進度，避免用 1 分刷任務


@dataclass(frozen=True)
class Quest:
    key: str
    title: str
    target: int
    reward: int
    matches: Callable[[str, Dict], bool]
    needs_ai: bool = False


def _game(name=None, won=None):
    def check(kind: str, meta: Dict) -> bool:
        if kind != "game":
            return False
        if name and meta.get("game") != name:
            return False
        return won is None or meta.get("won") == won
    return check


def _kind(expected: str):
    return lambda kind, meta: kind == expected


QUEST_POOL: List[Quest] = [
    Quest("play3", "玩 3 場遊戲", 3, 150, _game()),
    Quest("win2", "贏 2 場遊戲", 2, 200, _game(won=True)),
    Quest("chat20", "在伺服器聊天 20 則", 20, 100, _kind("chat")),
    Quest("work2", "打工 2 次", 2, 120, _kind("work")),
    Quest("blackjack2", f"玩 2 局 21 點（賭注 {MIN_QUEST_BET} 以上）", 2, 120, _game("blackjack")),
    Quest("slots5", f"拉霸 5 次（賭注 {MIN_QUEST_BET} 以上）", 5, 120, _game("slots")),
    Quest("ttt_win", "井字遊戲贏 1 場（簡單電腦不算）", 1, 120, _game("tictactoe", won=True)),
    Quest("poker5", "德州撲克玩 5 手", 5, 150, _game("poker")),
    Quest("guess5", "猜數字猜 5 次", 5, 80, _kind("guess")),
    Quest("soup3", "海龜湯提問 3 題", 3, 100, _kind("soup_question"), needs_ai=True),
]
QUESTS_BY_KEY = {q.key: q for q in QUEST_POOL}


def daily_quests(user_id: int, day: date, ai_available: bool = True) -> List[Quest]:
    """每個人每天固定抽到同樣的 3 個任務（以使用者與日期當亂數種子）。"""
    pool = [q for q in QUEST_POOL if ai_available or not q.needs_ai]
    return random.Random(f"{user_id}:{day.isoformat()}").sample(pool, DAILY_COUNT)
