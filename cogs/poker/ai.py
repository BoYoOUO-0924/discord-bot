"""德州撲克電腦玩家。

用蒙地卡羅模擬估算勝率，再依底池賠率與個性決定動作。不會偷看任何人的底牌。
"""
import random
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .cards import Card
from .engine import PokerTable, Seat
from .evaluate import evaluate_hand


@dataclass(frozen=True)
class Personality:
    name: str
    style: str
    margin: float       # 跟注時要求比底池賠率多出的勝率，越高越保守
    aggression: float   # 牌好時選擇加注（而非跟注）的傾向
    bluff: float        # 牌差時偷雞的機率
    sims: int           # 模擬次數，越多判斷越準


PERSONALITIES: List[Personality] = [
    Personality("🤖 穩健阿伯", "保守，只玩好牌", 0.08, 0.35, 0.02, 250),
    Personality("🤖 衝動小陳", "愛加注、常常詐唬", -0.04, 0.80, 0.15, 200),
    Personality("🤖 冷靜小美", "攻守平衡", 0.02, 0.55, 0.06, 300),
    Personality("🤖 賭神阿嘎", "算得最準，最難對付", 0.00, 0.65, 0.09, 500),
]


def estimate_equity(hole: List[Card], board: List[Card], opponents: int, sims: int,
                    rng: random.Random) -> float:
    """模擬把牌發完，估算對上 opponents 位對手時的勝率（平手按人數分）。"""
    known = {(c.rank, c.suit) for c in hole + board}
    deck = [Card(r, s) for s in Card.SUITS for r in Card.RANKS if (r, s) not in known]
    need = 5 - len(board)
    total = 0.0
    for _ in range(sims):
        draw = rng.sample(deck, need + 2 * opponents)
        full_board = board + draw[:need]
        mine = evaluate_hand(hole, full_board)[:2]
        opp_scores = [evaluate_hand(draw[need + 2 * i: need + 2 * i + 2], full_board)[:2] for i in range(opponents)]
        best = max(opp_scores)
        if mine > best:
            total += 1
        elif mine == best:
            total += 1 / (1 + opp_scores.count(best))
    return total / sims


def decide(table: PokerTable, seat: Seat, personality: Personality,
           rng: Optional[random.Random] = None) -> Tuple[str, Optional[int]]:
    """回傳 (動作, 加注到的總額)，動作是 fold / check / call / raise / all_in。"""
    rng = rng or random.Random()
    legal = table.legal(seat)
    opponents = max(1, sum(1 for s in table.order if s is not seat and not s.folded))
    sims = max(60, personality.sims // opponents)
    equity = estimate_equity(seat.hole, table.board, opponents, sims, rng) + rng.uniform(-0.04, 0.04)

    pot = table.pot
    base = 1 / (opponents + 1)              # 平均勝率
    very_strong = equity >= min(0.85, base + 0.45)
    strong = equity >= base + 0.2

    def raise_to(fraction: float) -> Tuple[str, Optional[int]]:
        if not legal.can_raise:
            return ("check", None) if legal.can_check else ("call", None)
        target = table.current_bet + max(table.min_raise, int(pot * fraction))
        target = max(legal.min_raise_to, min(legal.max_raise_to, target))
        # 剩下的籌碼不多了就乾脆 All-in
        if target >= legal.max_raise_to * 0.8:
            return "all_in", None
        return "raise", target

    if legal.can_check:
        if very_strong or (strong and rng.random() < personality.aggression):
            return raise_to(rng.uniform(0.5, 1.0))
        if rng.random() < personality.bluff:
            return raise_to(0.5)
        return "check", None

    pot_odds = legal.call_amount / (pot + legal.call_amount)
    if very_strong and rng.random() < personality.aggression + 0.2:
        return raise_to(rng.uniform(0.75, 1.2))
    if strong and rng.random() < personality.aggression * 0.5:
        return raise_to(rng.uniform(0.5, 0.8))
    if equity >= pot_odds + personality.margin:
        return "call", None
    if rng.random() < personality.bluff * 0.5 and legal.call_amount <= pot * 0.3:
        return "call", None
    return "fold", None
