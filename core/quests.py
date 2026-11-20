"""每日任務定義。

各功能在玩家完成動作時呼叫 bot.dispatch("activity", user_id, kind, channel_id, meta)，
任務系統再依這裡的規則累計進度。
"""
import random
from dataclasses import dataclass
from datetime import date
from typing import Callable, Collection, Dict, List, Optional

from core.features import enabled

DAILY_COUNT = 3
ALL_DONE_BONUS = 20_000
ALL_DONE_KEY = "__all__"
MIN_QUEST_BET = 5_000   # 下注太小不算任務進度，避免用 1 元刷任務


@dataclass(frozen=True)
class Quest:
    key: str
    title: str
    target: int
    reward: int
    matches: Callable[[str, Dict], bool]
    needs_ai: bool = False
    feature: Optional[str] = None   # 這個功能被全域關掉時就不會抽到（不然任務根本做不到）


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
    Quest("play3", "玩 3 場遊戲", 3, 15_000, _game()),
    Quest("win2", "贏 2 場遊戲", 2, 20_000, _game(won=True)),
    Quest("work2", "打工 2 次", 2, 12_000, _kind("work"), feature="economy"),
    Quest("blackjack2", f"玩 2 局 21 點（賭注 {MIN_QUEST_BET:,} 元以上）", 2, 12_000, _game("blackjack"),
          feature="blackjack"),
    Quest("blackjack_win", f"21 點贏 1 局（賭注 {MIN_QUEST_BET:,} 元以上）", 1, 15_000,
          _game("blackjack", won=True), feature="blackjack"),
    Quest("slots5", f"拉霸 5 次（賭注 {MIN_QUEST_BET:,} 元以上）", 5, 12_000, _game("slots"), feature="slots"),
    Quest("ttt_win", "井字遊戲贏 1 場（簡單電腦不算）", 1, 12_000, _game("tictactoe", won=True), feature="tictactoe"),
    Quest("poker5", "德州撲克玩 5 手", 5, 15_000, _game("poker"), feature="poker"),
    Quest("poker_win3", "德州撲克贏 3 手", 3, 18_000, _game("poker", won=True), feature="poker"),
    Quest("guess_win", "猜數字猜中 1 次", 1, 10_000, _game("guess", won=True), feature="guess"),
    Quest("soup3", "海龜湯提問 3 題", 3, 10_000, _kind("soup_question"), needs_ai=True, feature="seatortoise"),
    Quest("soup_solve", "海龜湯猜中真相 1 次", 1, 25_000, _game("seatortoise", won=True), needs_ai=True,
          feature="seatortoise"),
]
QUESTS_BY_KEY = {q.key: q for q in QUEST_POOL}


def daily_quests(user_id: int, day: date, ai_available: bool = True,
                 disabled: Collection[str] = ()) -> List[Quest]:
    """每個人每天固定抽到同樣的 3 個任務（以使用者與日期當亂數種子）。

    disabled：被全域關掉的功能，屬於這些功能的任務不會抽到。
    """
    pool = [q for q in QUEST_POOL
            if (ai_available or not q.needs_ai) and q.feature not in disabled]
    return random.Random(f"{user_id}:{day.isoformat()}").sample(pool, min(DAILY_COUNT, len(pool)))


def quests_for(bot, user_id: int, day: date) -> List[Quest]:
    """依機器人目前狀態（AI 有沒有設定、哪些功能被全域關掉）抽今天的任務。"""
    disabled = {q.feature for q in QUEST_POOL if q.feature and not enabled(bot, q.feature)}
    return daily_quests(user_id, day, bot.ai.configured, disabled)
