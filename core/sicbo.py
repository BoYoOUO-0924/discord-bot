"""骰寶（押大小）的規則與結算。不依賴 Discord，方便測試。

賠率一律寫成「X 賠 1」：中獎拿回本金再加 X 倍，所以 payout() 回傳的倍數是 X + 1。
開出豹子（三顆一樣）時，大、小、單、雙全部算輸，這就是莊家的優勢。
"""
import secrets
from dataclasses import dataclass
from itertools import combinations
from typing import Dict, List, Optional, Sequence, Tuple

FACES = "⚀⚁⚂⚃⚄⚅"

# 總和的賠率：越難開出來賠越多
TOTAL_ODDS = {4: 60, 17: 60, 5: 30, 16: 30, 6: 17, 15: 17, 7: 12, 14: 12,
              8: 8, 13: 8, 9: 6, 12: 6, 10: 6, 11: 6}
DOUBLE_ODDS = 10
TRIPLE_ODDS = 180
ANY_TRIPLE_ODDS = 30
COMBO_ODDS = 5

MAX_PER_ROUND = 50_000        # 每人每局所有押注合計的上限
MIN_CHIP = 100


@dataclass(frozen=True)
class Spot:
    key: str
    label: str      # 按鈕與結算顯示，例如「大」「總和 10」
    odds: str       # 「1 賠 1」


def _spots() -> Dict[str, Spot]:
    spots = [Spot("big", "大", "1 賠 1"), Spot("small", "小", "1 賠 1"),
             Spot("odd", "單", "1 賠 1"), Spot("even", "雙", "1 賠 1")]
    spots += [Spot(f"single:{n}", f"單骰 {n}", "1～3 賠 1") for n in range(1, 7)]
    spots += [Spot(f"total:{t}", f"總和 {t}", f"{TOTAL_ODDS[t]} 賠 1") for t in range(4, 18)]
    spots += [Spot(f"combo:{a}-{b}", f"組合 {a}+{b}", f"{COMBO_ODDS} 賠 1") for a, b in combinations(range(1, 7), 2)]
    spots += [Spot(f"double:{n}", f"對子 {n}{n}", f"{DOUBLE_ODDS} 賠 1") for n in range(1, 7)]
    spots += [Spot(f"triple:{n}", f"圍骰 {n}{n}{n}", f"{TRIPLE_ODDS} 賠 1") for n in range(1, 7)]
    spots.append(Spot("any_triple", "全圍", f"{ANY_TRIPLE_ODDS} 賠 1"))
    return {s.key: s for s in spots}


SPOTS: Dict[str, Spot] = _spots()


def is_triple(dice: Sequence[int]) -> bool:
    return dice[0] == dice[1] == dice[2]


def payout(key: str, dice: Sequence[int]) -> int:
    """押 1 元可以拿回多少（含本金）；0 表示輸。"""
    total, triple = sum(dice), is_triple(dice)
    kind, _, arg = key.partition(":")
    if kind == "big":
        return 2 if 11 <= total <= 17 and not triple else 0
    if kind == "small":
        return 2 if 4 <= total <= 10 and not triple else 0
    if kind == "odd":
        return 2 if total % 2 == 1 and not triple else 0
    if kind == "even":
        return 2 if total % 2 == 0 and not triple else 0
    if kind == "single":
        count = list(dice).count(int(arg))
        return 1 + count if count else 0
    if kind == "total":
        return TOTAL_ODDS[int(arg)] + 1 if total == int(arg) else 0
    if kind == "combo":
        a, b = (int(x) for x in arg.split("-"))
        return COMBO_ODDS + 1 if a in dice and b in dice else 0
    if kind == "double":
        return DOUBLE_ODDS + 1 if list(dice).count(int(arg)) >= 2 else 0
    if kind == "triple":
        return TRIPLE_ODDS + 1 if triple and dice[0] == int(arg) else 0
    if kind == "any_triple":
        return ANY_TRIPLE_ODDS + 1 if triple else 0
    raise KeyError(key)


def roll() -> Tuple[int, int, int]:
    return tuple(secrets.randbelow(6) + 1 for _ in range(3))


def faces(dice: Sequence[int]) -> str:
    return " ".join(FACES[d - 1] for d in dice)


def describe(dice: Sequence[int]) -> str:
    """「總點數 12・大・雙」；豹子另外標示。"""
    total = sum(dice)
    if is_triple(dice):
        return f"豹子 {dice[0]}{dice[0]}{dice[0]}・總點數 {total}（大小單雙通殺）"
    size = "大" if total >= 11 else "小"
    parity = "單" if total % 2 else "雙"
    return f"總點數 {total}・{size}・{parity}"


def road_mark(dice: Sequence[int]) -> str:
    """路單上的一格，例如「12大」「豹」。"""
    if is_triple(dice):
        return f"{sum(dice)}豹"
    return f"{sum(dice)}{'大' if sum(dice) >= 11 else '小'}"


# 押法說明：(標題, 內容)；/sicbo 的「押法說明」按鈕直接顯示
RULES: List[Tuple[str, str]] = [
    ("🎲 怎麼玩",
     "莊家搖 3 顆骰子，你押開出來的結果。下注時間結束後就不能再押或撤回，開獎時大家一起結算。\n"
     "「X 賠 1」＝中獎時拿回本金，再加 X 倍。例如押 1,000 元在 1 賠 1，中了拿回 2,000 元。\n"
     "⚠️ **開出豹子（三顆一樣）時，大、小、單、雙全部算輸。**"),
    ("大／小（1 賠 1）",
     "大：總點數 11～17　小：總點數 4～10\n遇到豹子算輸。"),
    ("單／雙（1 賠 1）",
     "總點數是單數或雙數。遇到豹子算輸。"),
    ("單骰（1～3 賠 1）",
     "押 1～6 其中一個點數，開出 1 顆賠 1 倍、2 顆賠 2 倍、3 顆賠 3 倍。"),
    ("總和（6～60 賠 1）",
     "押 3 顆骰子加起來剛好是某個數字：\n"
     "4、17 → 60 倍　5、16 → 30 倍　6、15 → 17 倍　7、14 → 12 倍\n"
     "8、13 → 8 倍　9、12 → 6 倍　10、11 → 6 倍"),
    (f"兩骰組合（{COMBO_ODDS} 賠 1）",
     "押兩個不同的點數（例如 1+2），兩個點數都有開出來就中，共 15 種組合。"),
    (f"對子（{DOUBLE_ODDS} 賠 1）",
     "押一個點數，至少開出 2 顆就中（開出 3 顆也算）。"),
    (f"圍骰（{TRIPLE_ODDS} 賠 1）",
     "指定豹子：3 顆都是你押的點數。"),
    (f"全圍（{ANY_TRIPLE_ODDS} 賠 1）",
     "任何豹子都中，不管是哪個點數。"),
    ("💰 下注限額",
     f"每人每局所有押注加起來最多 {MAX_PER_ROUND:,} 元，每注至少 {MIN_CHIP:,} 元。\n"
     "按押注按鈕會用你選的籌碼面額押一注，可以連按疊加；用「換籌碼」改面額。"),
]


class SicBoRound:
    """一局的押注。金額扣款（保管）由 cog 處理，這裡只記帳與算錢。"""

    def __init__(self, number: int):
        self.number = number
        self.bets: Dict[int, Dict[str, int]] = {}
        self.holds: Dict[int, int] = {}
        self.names: Dict[int, str] = {}

    def stake(self, user_id: int) -> int:
        return sum(self.bets.get(user_id, {}).values())

    @property
    def total(self) -> int:
        return sum(self.stake(u) for u in self.bets)

    def check(self, user_id: int, key: str, amount: int) -> Optional[str]:
        """超過限額回傳錯誤訊息。"""
        if key not in SPOTS:
            return "沒有這種押法。"
        if amount < MIN_CHIP:
            return f"每注至少 {MIN_CHIP:,} 元。"
        stake = self.stake(user_id)
        if stake + amount > MAX_PER_ROUND:
            return (f"每局合計最多押 {MAX_PER_ROUND:,} 元，你已經押了 {stake:,} 元，"
                    f"還能押 {MAX_PER_ROUND - stake:,} 元（這注是 {amount:,} 元）。")
        return None

    def add(self, user_id: int, key: str, amount: int, name: str = "") -> None:
        spots = self.bets.setdefault(user_id, {})
        spots[key] = spots.get(key, 0) + amount
        if name:
            self.names[user_id] = name

    def clear(self, user_id: int) -> int:
        """撤回這個人的所有押注，回傳金額。"""
        self.names.pop(user_id, None)
        return sum(self.bets.pop(user_id, {}).values())

    def spot_totals(self) -> List[Tuple[str, int, int]]:
        """各押法的 (押法, 人數, 總金額)，依金額由大到小。"""
        totals: Dict[str, List[int]] = {}
        for spots in self.bets.values():
            for key, amount in spots.items():
                entry = totals.setdefault(key, [0, 0])
                entry[0] += 1
                entry[1] += amount
        return sorted(((k, n, a) for k, (n, a) in totals.items()), key=lambda x: -x[2])

    def settle(self, dice: Sequence[int]) -> Dict[int, Tuple[int, int, List[Tuple[str, int, int]]]]:
        """每個人的 (本金, 拿回金額, [(押法, 押注, 拿回)])。"""
        results = {}
        for user_id, spots in self.bets.items():
            lines = [(key, amount, amount * payout(key, dice)) for key, amount in spots.items()]
            results[user_id] = (sum(a for _, a, _ in lines), sum(r for _, _, r in lines), lines)
        return results
