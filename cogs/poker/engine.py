"""德州撲克牌局引擎（純邏輯，不依賴 Discord，方便測試）。

處理下注輪、盲注、未跟注退還、邊池（side pot）與攤牌分配。
"""
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from .cards import Card, generate_shuffled_deck
from .evaluate import evaluate_hand

STREET_NAMES = {"preflop": "翻牌前", "flop": "翻牌", "turn": "轉牌", "river": "河牌", "showdown": "攤牌"}


class IllegalAction(Exception):
    """不合法的動作，訊息會直接顯示給玩家。"""


@dataclass
class Seat:
    user_id: int
    name: str
    stack: int
    hole: List[Card] = field(default_factory=list)
    bet: int = 0            # 本輪下注
    contributed: int = 0    # 本手總投入
    folded: bool = False
    all_in: bool = False
    acted: bool = False
    in_hand: bool = False   # 這手有拿到牌
    leaving: bool = False   # 這手結束後離桌
    last_action: str = ""   # 本輪最後的動作，顯示在牌桌上

    @property
    def can_act(self) -> bool:
        return self.in_hand and not self.folded and not self.all_in


@dataclass
class Legal:
    to_call: int
    can_check: bool
    call_amount: int
    can_raise: bool
    min_raise_to: int
    max_raise_to: int


@dataclass
class PotResult:
    amount: int
    winners: List[int]
    hand_name: Optional[str]


@dataclass
class HandResult:
    showdown: bool
    pots: List[PotResult]
    payouts: Dict[int, int]
    hands: Dict[int, Tuple[str, List[Card]]]   # 攤牌時每位玩家的牌型


def build_pots(contributions: Dict[int, int], live: List[int]) -> List[Tuple[int, List[int]]]:
    """依投入金額切出主池與邊池。

    contributions：每位玩家本手總投入（含已棄牌者）
    live：仍在爭奪底池（沒棄牌）的玩家
    回傳 [(金額, 有資格贏的玩家), ...]，由主池到邊池排列。
    """
    levels = sorted({amount for pid, amount in contributions.items() if pid in live and amount > 0})
    pots: List[Tuple[int, List[int]]] = []
    previous = 0
    for level in levels:
        amount = sum(min(c, level) - min(c, previous) for c in contributions.values())
        eligible = [pid for pid in live if contributions.get(pid, 0) >= level]
        if amount:
            pots.append((amount, eligible))
        previous = level
    # 棄牌者投入超過所有存活玩家的部分（理論上會被退還，保險起見併入最後一個池）
    leftover = sum(max(0, c - previous) for c in contributions.values())
    if leftover and pots:
        pots[-1] = (pots[-1][0] + leftover, pots[-1][1])
    return pots


class PokerTable:
    def __init__(self, seats: List[Seat], small_blind: int, big_blind: int,
                 deck_factory: Callable[[], List[Card]] = generate_shuffled_deck):
        self.seats = seats
        self.small_blind, self.big_blind = small_blind, big_blind
        self.deck_factory = deck_factory
        self.dealer_id: Optional[int] = None
        self.hand_number = 0
        self.order: List[Seat] = []
        self.board: List[Card] = []
        self.deck: List[Card] = []
        self.street = "preflop"
        self.current_bet = 0
        self.min_raise = big_blind
        self.to_act: Optional[int] = None     # order 裡的索引
        self.result: Optional[HandResult] = None
        self.log: List[str] = []
        self.sb_id: Optional[int] = None
        self.bb_id: Optional[int] = None

    # ------------------------------------------------------------------ 查詢
    @property
    def hand_over(self) -> bool:
        return self.result is not None

    @property
    def pot(self) -> int:
        return sum(s.contributed for s in self.order)

    @property
    def current_seat(self) -> Optional[Seat]:
        if self.hand_over or self.to_act is None:
            return None
        return self.order[self.to_act]

    def seat_of(self, user_id: int) -> Optional[Seat]:
        return next((s for s in self.seats if s.user_id == user_id), None)

    def legal(self, seat: Seat) -> Legal:
        to_call = max(0, self.current_bet - seat.bet)
        max_raise_to = seat.bet + seat.stack
        others_can_act = any(s.can_act for s in self.order if s is not seat)
        return Legal(
            to_call=to_call,
            can_check=to_call == 0,
            call_amount=min(to_call, seat.stack),
            can_raise=max_raise_to > self.current_bet and others_can_act,
            min_raise_to=min(self.current_bet + self.min_raise, max_raise_to),
            max_raise_to=max_raise_to,
        )

    # ------------------------------------------------------------------ 開局
    def _next_index(self, index: int) -> int:
        return (index + 1) % len(self.order)

    def start_hand(self) -> None:
        players = [s for s in self.seats if s.stack > 0 and not s.leaving]
        if len(players) < 2:
            raise IllegalAction("至少要兩位有籌碼的玩家才能開始。")
        self.hand_number += 1
        self.order = players
        self.deck = self.deck_factory()
        self.board, self.log, self.result = [], [], None
        self.street, self.current_bet, self.min_raise = "preflop", 0, self.big_blind
        for seat in self.seats:
            seat.hole, seat.bet, seat.contributed = [], 0, 0
            seat.folded = seat.all_in = seat.acted = False
            seat.last_action = ""
            seat.in_hand = seat in players

        # 移動莊家按鈕到下一位
        ids = [s.user_id for s in self.order]
        if self.dealer_id in ids:
            dealer = self._next_index(ids.index(self.dealer_id))
        else:
            dealer = self._first_after_previous_dealer()
        self.dealer_id = ids[dealer]

        if len(self.order) == 2:   # 單挑：莊家是小盲，翻牌前先行動
            sb, bb = dealer, self._next_index(dealer)
            first = sb
        else:
            sb = self._next_index(dealer)
            bb = self._next_index(sb)
            first = self._next_index(bb)
        self.sb_id, self.bb_id = ids[sb], ids[bb]
        self._pay(self.order[sb], min(self.small_blind, self.order[sb].stack))
        self._pay(self.order[bb], min(self.big_blind, self.order[bb].stack))
        self.current_bet = max(s.bet for s in self.order)
        self.log.append(f"第 {self.hand_number} 手開始，盲注 {self.small_blind}/{self.big_blind}")

        for _ in range(2):
            for seat in self.order:
                seat.hole.append(self.deck.pop())

        self.to_act = first
        if not self.order[first].can_act or self._betting_complete():
            self._advance()

    def _first_after_previous_dealer(self) -> int:
        """上一位莊家已經離桌時，找座位順序上的下一位。"""
        if self.dealer_id is None:
            return 0
        seat_ids = [s.user_id for s in self.seats]
        start = seat_ids.index(self.dealer_id) if self.dealer_id in seat_ids else -1
        for offset in range(1, len(self.seats) + 1):
            candidate = self.seats[(start + offset) % len(self.seats)]
            if candidate in self.order:
                return self.order.index(candidate)
        return 0

    # ------------------------------------------------------------------ 行動
    def _pay(self, seat: Seat, amount: int) -> None:
        amount = min(amount, seat.stack)
        seat.stack -= amount
        seat.bet += amount
        seat.contributed += amount
        if seat.stack == 0:
            seat.all_in = True

    def act(self, user_id: int, action: str, amount: Optional[int] = None) -> None:
        seat = self.current_seat
        if seat is None:
            raise IllegalAction("這手牌已經結束了。")
        if seat.user_id != user_id:
            raise IllegalAction("還沒輪到你喔！")
        legal = self.legal(seat)

        if action == "fold":
            seat.folded = True
            self._record(seat, "棄牌")
        elif action == "check":
            if not legal.can_check:
                raise IllegalAction(f"現在需要跟注 {legal.to_call}，不能過牌。")
            self._record(seat, "過牌")
        elif action == "call":
            if legal.can_check:
                self._record(seat, "過牌")
            else:
                self._pay(seat, legal.call_amount)
                self._record(seat, f"跟注 {legal.call_amount:,}" + ("（All-in）" if seat.all_in else ""))
        elif action in ("raise", "all_in"):
            target = legal.max_raise_to if action == "all_in" else amount
            if target is None:
                raise IllegalAction("請輸入加注金額。")
            if action == "all_in" and (target <= self.current_bet or not legal.can_raise):
                # 籌碼不夠跟注、或其他人都已 All-in 沒人能再跟，這種 All-in 視同跟注
                self._pay(seat, legal.call_amount)
                self._record(seat, f"跟注 {legal.call_amount:,}" + ("（All-in）" if seat.all_in else ""))
            elif target <= self.current_bet:
                raise IllegalAction(f"加注後的總下注必須大於 {self.current_bet}。")
            else:
                if not legal.can_raise:
                    raise IllegalAction("其他玩家都已 All-in，不能再加注了。")
                if target > legal.max_raise_to:
                    raise IllegalAction(f"你最多只能下到 {legal.max_raise_to}。")
                if target < legal.min_raise_to and target != legal.max_raise_to:
                    raise IllegalAction(f"加注後的總下注至少要 {legal.min_raise_to}（或直接 All-in）。")
                raise_size = target - self.current_bet
                if raise_size >= self.min_raise:
                    self.min_raise = raise_size
                self.current_bet = target
                self._pay(seat, target - seat.bet)
                for other in self.order:
                    if other is not seat:
                        other.acted = False
                verb = "All-in" if seat.all_in else "加注到"
                self._record(seat, f"{verb} {target:,}")
        else:
            raise IllegalAction("未知的動作。")

        seat.acted = True
        self._advance()

    def _record(self, seat: Seat, text: str) -> None:
        seat.last_action = text
        self.log.append(f"{seat.name} {text}")

    def _betting_complete(self) -> bool:
        actors = [s for s in self.order if s.can_act]
        if any(s.bet != self.current_bet for s in actors):
            return False
        return len(actors) <= 1 or all(s.acted for s in actors)

    def _advance(self) -> None:
        live = [s for s in self.order if not s.folded]
        if len(live) == 1:
            self._return_uncalled()
            self._finish_uncontested(live[0])
            return
        if self._betting_complete():
            self._next_street()
            return
        index = self.to_act
        for _ in range(len(self.order)):
            index = self._next_index(index)
            if self.order[index].can_act:
                self.to_act = index
                return
        self._next_street()

    def _return_uncalled(self) -> None:
        """最高下注者多出、沒人跟上的部分退還給他。"""
        bets = sorted((s.bet for s in self.order), reverse=True)
        if len(bets) < 2 or bets[0] == bets[1]:
            return
        top = max(self.order, key=lambda s: s.bet)
        excess = bets[0] - bets[1]
        top.bet -= excess
        top.contributed -= excess
        top.stack += excess
        if top.stack > 0:
            top.all_in = False

    def _next_street(self) -> None:
        self._return_uncalled()
        while True:
            for seat in self.order:
                seat.bet, seat.acted = 0, False
                if not seat.folded and not seat.all_in:
                    seat.last_action = ""
            self.current_bet, self.min_raise = 0, self.big_blind
            if self.street == "river":
                self._showdown()
                return
            if self.street == "preflop":
                self.street = "flop"
                self.board += [self.deck.pop() for _ in range(3)]
            else:
                self.street = "turn" if self.street == "flop" else "river"
                self.board.append(self.deck.pop())
            self.log.append(f"— {STREET_NAMES[self.street]}：{' '.join(map(str, self.board))}")

            actors = [s for s in self.order if s.can_act]
            if len(actors) >= 2:
                dealer = next(i for i, s in enumerate(self.order) if s.user_id == self.dealer_id)
                index = dealer
                for _ in range(len(self.order)):
                    index = self._next_index(index)
                    if self.order[index].can_act:
                        self.to_act = index
                        return
            # 剩一人（或沒人）能行動：直接把牌發完

    def _finish_uncontested(self, winner: Seat) -> None:
        pot = self.pot
        winner.stack += pot
        self.to_act = None
        self.result = HandResult(False, [PotResult(pot, [winner.user_id], None)], {winner.user_id: pot}, {})
        self.log.append(f"{winner.name} 贏得底池 {pot}")

    def _showdown(self) -> None:
        self.street = "showdown"
        self.to_act = None
        live = [s for s in self.order if not s.folded]
        scores, hands = {}, {}
        for seat in live:
            rank, kickers, name, best = evaluate_hand(seat.hole, self.board)
            scores[seat.user_id] = (rank, kickers)
            hands[seat.user_id] = (name, best)

        contributions = {s.user_id: s.contributed for s in self.order}
        payouts: Dict[int, int] = {}
        pots: List[PotResult] = []
        # 零頭從莊家左手邊開始分
        dealer = next(i for i, s in enumerate(self.order) if s.user_id == self.dealer_id)
        seat_priority = {self.order[(dealer + 1 + i) % len(self.order)].user_id: i for i in range(len(self.order))}
        for amount, eligible in build_pots(contributions, [s.user_id for s in live]):
            best = max(scores[pid] for pid in eligible)
            winners = sorted((pid for pid in eligible if scores[pid] == best), key=seat_priority.get)
            share, remainder = divmod(amount, len(winners))
            for i, pid in enumerate(winners):
                payouts[pid] = payouts.get(pid, 0) + share + (1 if i < remainder else 0)
            pots.append(PotResult(amount, winners, hands[winners[0]][0]))
        for seat in self.order:
            seat.stack += payouts.get(seat.user_id, 0)
        self.result = HandResult(True, pots, payouts, hands)
        self.log.append("攤牌！")

    def cancel_hand(self) -> None:
        """強制結束牌局時，把這手的投入全部退回。"""
        for seat in self.order:
            seat.stack += seat.contributed
            seat.bet = seat.contributed = 0
        self.to_act = None
        self.result = HandResult(False, [], {}, {})
