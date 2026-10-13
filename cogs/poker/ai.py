"""德州撲克電腦玩家。

翻牌前依起手牌排名與個性的入局比例決定要不要玩；翻牌後用蒙地卡羅模擬估算勝率，
再依底池賠率決定動作。不會偷看任何人的底牌。
"""
import random
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .cards import Card
from .engine import Legal, PokerTable, Seat
from .evaluate import evaluate_hand


@dataclass(frozen=True)
class Personality:
    name: str
    style: str
    vpip: float         # 翻牌前願意入局的起手牌比例（前百分之幾）
    pfr: float          # 翻牌前主動加注的起手牌比例
    margin: float       # 翻牌後跟注時要求比底池賠率多出的勝率，越高越保守
    aggression: float   # 翻牌後牌好時選擇加注（而非跟注）的傾向
    bluff: float        # 牌差時偷雞的機率
    sims: int           # 翻牌後的模擬次數，越多判斷越準


# 入局比例設得很鬆：7 位電腦平均每手約 5 位跟進看翻牌，牌局比較熱鬧
PERSONALITIES: List[Personality] = [
    Personality("🤖 穩健阿伯", "保守，只玩好牌", 0.62, 0.06, 0.06, 0.35, 0.02, 250),
    Personality("🤖 衝動小陳", "什麼牌都想玩、常常詐唬", 0.95, 0.14, -0.06, 0.80, 0.15, 200),
    Personality("🤖 冷靜小美", "攻守平衡", 0.78, 0.08, 0.00, 0.55, 0.06, 300),
    Personality("🤖 賭神阿嘎", "算得最準，最難對付", 0.72, 0.10, 0.00, 0.65, 0.09, 500),
]

# ---------------------------------------------------------------------- 起手牌排名
_CHEN_HIGH = {"A": 10, "K": 8, "Q": 7, "J": 6}


def chen_score(high: str, low: str, suited: bool) -> float:
    """Chen 公式：常見的起手牌快速評分法，分數越高越好。"""
    hi, lo = Card.RANK_VALUES[high], Card.RANK_VALUES[low]
    score = _CHEN_HIGH.get(high, (hi + 2) / 2)
    if hi == lo:
        return max(5.0, score * 2)
    if suited:
        score += 2
    gap = hi - lo - 1
    score -= {0: 0, 1: 1, 2: 2, 3: 4}.get(gap, 5)
    if gap <= 1 and hi < Card.RANK_VALUES["Q"]:
        score += 1   # 小連張有機會湊順子
    return score


def _build_preflop_table() -> Dict[Tuple[str, str, bool], float]:
    """把 169 種起手牌依組合數加權排序，對應到「排在前百分之幾」。"""
    classes = []
    ranks = Card.RANKS
    for i, high in enumerate(ranks):
        for low in ranks[:i + 1]:
            if high == low:
                classes.append((high, low, False, 6))
            else:
                classes.append((high, low, True, 4))
                classes.append((high, low, False, 12))
    classes.sort(key=lambda c: (chen_score(c[0], c[1], c[2]), Card.RANK_VALUES[c[0]], Card.RANK_VALUES[c[1]]),
                 reverse=True)
    table, cumulative = {}, 0
    for high, low, suited, combos in classes:
        cumulative += combos
        table[(high, low, suited)] = cumulative / 1326
    return table


PREFLOP_RANK = _build_preflop_table()


def preflop_rank(hole: List[Card]) -> float:
    """起手牌排在全部組合的前百分之幾（AA ≈ 0.005，72 不同花 = 1.0）。"""
    a, b = sorted(hole, key=lambda c: c.value(), reverse=True)
    return PREFLOP_RANK[(a.rank, b.rank, a.suit == b.suit and a.rank != b.rank)]


# ---------------------------------------------------------------------- 勝率模擬
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


# ---------------------------------------------------------------------- 決策
def _raise_to(table: PokerTable, legal: Legal, target: int) -> Tuple[str, Optional[int]]:
    if not legal.can_raise:
        return ("check", None) if legal.can_check else ("call", None)
    target = max(legal.min_raise_to, min(legal.max_raise_to, target))
    # 剩下的籌碼不多了就乾脆 All-in
    if target >= legal.max_raise_to * 0.8:
        return "all_in", None
    return "raise", target


def _preflop(table: PokerTable, seat: Seat, personality: Personality, legal: Legal,
             rng: random.Random) -> Tuple[str, Optional[int]]:
    bb = table.big_blind
    rank = preflop_rank(seat.hole) + rng.uniform(-0.03, 0.03)
    # 面對加注時收緊範圍：小加注幾乎照跟，被 All-in 時只剩頂端的牌會跟
    factor = min(1.0, (4 * bb / legal.call_amount) ** 0.5) if legal.call_amount > bb else 1.0
    if seat.bet > 0 and legal.call_amount <= 10 * bb:
        factor = factor ** 0.5   # 已經投入籌碼、加注又不大，比較捨不得棄牌
    play_range, raise_range = personality.vpip * factor, personality.pfr * factor

    if rank <= raise_range and legal.can_raise:
        # 加注金額刻意不加大，讓底池維持多人參與
        target = int(table.current_bet * 2.5) if table.current_bet > bb else bb * 3
        return _raise_to(table, legal, target)
    if rank <= play_range:
        return ("check", None) if legal.can_check else ("call", None)
    if legal.can_check:
        return "check", None
    if rng.random() < personality.bluff * 0.5 and legal.call_amount <= bb:
        return "call", None
    return "fold", None


def decide(table: PokerTable, seat: Seat, personality: Personality,
           rng: Optional[random.Random] = None) -> Tuple[str, Optional[int]]:
    """回傳 (動作, 加注到的總額)，動作是 fold / check / call / raise / all_in。"""
    rng = rng or random.Random()
    legal = table.legal(seat)
    if not table.board:
        return _preflop(table, seat, personality, legal, rng)

    # 翻牌後：勝率只對上還沒棄牌的對手，和底池賠率的計算基準一致
    opponents = max(1, sum(1 for s in table.order if s is not seat and not s.folded))
    sims = max(60, personality.sims // opponents)
    equity = estimate_equity(seat.hole, table.board, opponents, sims, rng) + rng.uniform(-0.04, 0.04)

    pot = table.pot
    base = 1 / (opponents + 1)              # 平均勝率
    very_strong = equity >= min(0.85, base + 0.45)
    strong = equity >= base + 0.2

    def raise_by(fraction: float) -> Tuple[str, Optional[int]]:
        return _raise_to(table, legal, table.current_bet + max(table.min_raise, int(pot * fraction)))

    if legal.can_check:
        if very_strong or (strong and rng.random() < personality.aggression):
            return raise_by(rng.uniform(0.5, 1.0))
        if rng.random() < personality.bluff:
            return raise_by(0.5)
        return "check", None

    pot_odds = legal.call_amount / (pot + legal.call_amount)
    if very_strong and rng.random() < personality.aggression + 0.2:
        return raise_by(rng.uniform(0.75, 1.2))
    if strong and rng.random() < personality.aggression * 0.5:
        return raise_by(rng.uniform(0.5, 0.8))
    if equity >= pot_odds + personality.margin:
        return "call", None
    if rng.random() < personality.bluff * 0.5 and legal.call_amount <= pot * 0.3:
        return "call", None
    return "fold", None
