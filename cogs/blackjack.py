"""21 點：每人一局互不干擾，支援加倍、分牌、投降與保險，Blackjack 賠 3:2，賭注上限 5 萬。

莊家明牌是 A 時先問要不要買保險（賭注的一半，莊家 Blackjack 賠 2 倍）；
前兩張牌時可以投降（拿回一半賭注，莊家偷看過底牌才行）；前兩張點數一樣可以分牌（最多 4 手，分 A 各補一張就停）；有 A 當 11 點時顯示「軟牌」兩種點數；
結算時莊家的牌一張一張翻開；「再來一局」直接在同一則訊息繼續。
"""
import asyncio
import logging
import secrets
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands

from cogs.poker.cards import card_label
from core.quests import MIN_QUEST_BET
from core.ui import (AmountModal, BaseView, Color, defer, fmt_points, fmt_signed, make_embed, respond, send_error)

log = logging.getLogger(__name__)

Card = Tuple[str, str]
RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
SUITS = ["♠", "♥", "♦", "♣"]
DEFAULT_BET = 10_000
MAX_BET = 50_000          # 每局賭注上限（加倍、分牌、保險另計）
MAX_HANDS = 4             # 分牌最多分成幾手
REVEAL_DELAY = 1.0        # 莊家每翻一張牌的間隔（秒）


def new_deck() -> List[Card]:
    deck = [(r, s) for s in SUITS for r in RANKS]
    for i in range(len(deck) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        deck[i], deck[j] = deck[j], deck[i]
    return deck


def card_points(rank: str) -> int:
    if rank == "A":
        return 11
    return 10 if rank in ("J", "Q", "K") else int(rank)


def hand_total(cards: List[Card]) -> Tuple[int, bool]:
    """回傳（點數, 是不是軟牌）：軟牌＝有 A 還當 11 點算，再要一張也不會爆。"""
    total = sum(card_points(rank) for rank, _ in cards)
    aces = sum(1 for rank, _ in cards if rank == "A")
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total, aces > 0


def hand_value(cards: List[Card]) -> int:
    return hand_total(cards)[0]


def hand_text(cards: List[Card]) -> str:
    """軟牌顯示兩種點數（A+5 →「6 / 16」），玩家才知道再要一張不會爆。"""
    total, soft = hand_total(cards)
    return f"{total - 10} / {total}" if soft and total < 21 else str(total)


def is_blackjack(cards: List[Card]) -> bool:
    return len(cards) == 2 and hand_value(cards) == 21


HIDDEN_CARD = "`？`"


def render(cards: List[Card]) -> str:
    return " ".join(f"`{card_label(r, s)}`" for r, s in cards)


# 結果 -> (標題, 顏色, 賠付倍數（含本金）)；分牌的整局結果依各手加總，倍數不用
OUTCOMES = {
    "blackjack": ("🎉 Blackjack！", Color.GOLD, 2.5),
    "win": ("🎉 你贏了！", Color.SUCCESS, 2),
    "dealer_bust": ("🎉 莊家爆牌，你贏了！", Color.SUCCESS, 2),
    "push": ("🤝 平手，退還賭注", Color.MUTED, 1),
    "lose": ("😢 你輸了", Color.DANGER, 0),
    "bust": ("💥 爆牌了", Color.DANGER, 0),
    "dealer_blackjack": ("😱 莊家 Blackjack", Color.DANGER, 0),
    "surrender": ("🏳️ 投降，退回一半賭注", Color.MUTED, 0.5),
    "split_win": ("🎉 分牌結算：你贏了！", Color.SUCCESS, None),
    "split_push": ("🤝 分牌結算：打平", Color.MUTED, None),
    "split_lose": ("😢 分牌結算：你輸了", Color.DANGER, None),
}
WIN_OUTCOMES = ("blackjack", "win", "dealer_bust", "split_win")
# 分牌時每一手的結果
HAND_RESULTS = {"win": "✅ 贏", "dealer_bust": "✅ 贏（莊家爆牌）", "push": "🤝 平手", "lose": "❌ 輸", "bust": "💥 爆牌"}


@dataclass
class Hand:
    cards: List[Card]
    bet: int
    doubled: bool = False
    split_aces: bool = False             # 分 A 的手只補一張就停
    done: bool = False
    outcome: Optional[str] = None


class BlackjackGame:
    def __init__(self, player_id: int, bet: int, hold_id: Optional[int], deck: Optional[List[Card]] = None):
        self.player_id, self.hold_id = player_id, hold_id
        self.base_bet = bet                  # 開局時的賭注（再來一局、保險用）
        self.deck = deck or new_deck()
        first = [self.deck.pop(), self.deck.pop()]
        self.dealer = [self.deck.pop(), self.deck.pop()]
        self.hands = [Hand(first, bet)]
        self.active = 0                      # 正在打第幾手
        self.insured = False                 # 有沒有買保險（練習模式也可以買，只是不用錢）
        self.insurance = 0                   # 買保險的金額
        self.insurance_pending = False       # 莊家明牌是 A，等玩家決定要不要買保險
        self.outcome: Optional[str] = None
        self.channel_id: Optional[int] = None
        self.lock = asyncio.Lock()

    # ------------------------------------------------------------ 狀態
    @property
    def finished(self) -> bool:
        return self.outcome is not None

    @property
    def hand(self) -> Hand:
        """正在打的那一手（打完後停在最後一手）。"""
        return self.hands[min(self.active, len(self.hands) - 1)]

    @property
    def player(self) -> List[Card]:
        return self.hand.cards

    @property
    def bet(self) -> int:
        """全部手牌的賭注合計（加倍、分牌都算進去，不含保險）。"""
        return sum(h.bet for h in self.hands)

    @property
    def doubled(self) -> bool:
        return any(h.doubled for h in self.hands)

    @property
    def is_split(self) -> bool:
        return len(self.hands) > 1

    @property
    def offers_insurance(self) -> bool:
        return self.dealer[0][0] == "A"

    @property
    def insurance_cost(self) -> int:
        return self.base_bet // 2

    @property
    def can_act(self) -> bool:
        return not self.finished and not self.insurance_pending

    @property
    def can_double(self) -> bool:
        return self.can_act and len(self.hand.cards) == 2 and not self.hand.split_aces

    @property
    def can_surrender(self) -> bool:
        """晚投降：只有一開始的兩張牌、還沒分牌時可以（莊家沒 Blackjack 才會輪到玩家）。"""
        return self.can_act and not self.is_split and len(self.hand.cards) == 2

    @property
    def can_split(self) -> bool:
        cards = self.hand.cards
        return (self.can_act and len(cards) == 2 and len(self.hands) < MAX_HANDS and not self.hand.split_aces
                and card_points(cards[0][0]) == card_points(cards[1][0]))

    # ------------------------------------------------------------ 開局
    def begin(self) -> None:
        """發完牌：莊家明牌是 A 先問保險，否則直接看有沒有 21 點。"""
        if self.offers_insurance:
            self.insurance_pending = True
        else:
            self.check_naturals()

    def resolve_insurance(self, buy: bool) -> None:
        self.insured = buy
        self.insurance = self.insurance_cost if buy else 0
        self.insurance_pending = False
        self.check_naturals()

    @property
    def insurance_won(self) -> bool:
        return self.insured and is_blackjack(self.dealer)

    def check_naturals(self) -> None:
        """開牌就有 21 點的情況（莊家也會先偷看底牌）。"""
        player_bj, dealer_bj = is_blackjack(self.hands[0].cards), is_blackjack(self.dealer)
        if player_bj and dealer_bj:
            self.outcome = "push"
        elif player_bj:
            self.outcome = "blackjack"
        elif dealer_bj:
            self.outcome = "dealer_blackjack"
        if self.outcome:
            self.hands[0].outcome, self.hands[0].done = self.outcome, True

    # ------------------------------------------------------------ 玩家動作
    def hit(self) -> None:
        hand = self.hand
        hand.cards.append(self.deck.pop())
        value = hand_value(hand.cards)
        if value > 21:
            hand.outcome = "bust"
            self._next_hand()
        elif value == 21:
            self._next_hand()  # 21 點自動停牌

    def stand(self) -> None:
        """停這一手；分牌時換下一手，全部打完才輪到莊家。"""
        if self.insurance_pending:          # 還沒決定保險就停牌（逾時、後台強制結束）：當作不買
            self.resolve_insurance(False)
            if self.finished:
                return
        self._next_hand()

    def stand_all(self) -> None:
        """閒置太久：剩下的每一手都停牌。"""
        while not self.finished:
            self.stand()

    def double(self) -> None:
        hand = self.hand
        hand.bet *= 2
        hand.doubled = True
        hand.cards.append(self.deck.pop())
        if hand_value(hand.cards) > 21:
            hand.outcome = "bust"
        self._next_hand()

    def surrender(self) -> None:
        """放棄這局，拿回一半賭注；莊家不用補牌。"""
        hand = self.hand
        hand.outcome, hand.done = "surrender", True
        self.outcome = "surrender"

    def split(self) -> None:
        """把一對拆成兩手，各押一樣的賭注；第二手輪到時才補牌。"""
        hand = self.hand
        aces = hand.cards[0][0] == "A"
        second = Hand([hand.cards.pop()], hand.bet, split_aces=aces)
        hand.split_aces = aces
        self.hands.insert(self.active + 1, second)
        hand.cards.append(self.deck.pop())
        if aces or hand_value(hand.cards) == 21:
            self._next_hand()

    def _next_hand(self) -> None:
        self.hand.done = True
        self.active += 1
        while self.active < len(self.hands):
            hand = self.hands[self.active]
            if len(hand.cards) == 1:          # 分牌出來的手，輪到才補第二張
                hand.cards.append(self.deck.pop())
            if not hand.split_aces and hand_value(hand.cards) < 21:
                return
            hand.done = True
            self.active += 1
        self._dealer_play()

    def _dealer_play(self) -> None:
        live = [h for h in self.hands if h.outcome != "bust"]
        if live:                              # 全部爆牌就不用補牌
            while hand_value(self.dealer) < 17:
                self.dealer.append(self.deck.pop())
            dealer = hand_value(self.dealer)
            for h in live:
                player = hand_value(h.cards)
                if dealer > 21:
                    h.outcome = "dealer_bust"
                elif player > dealer:
                    h.outcome = "win"
                elif player == dealer:
                    h.outcome = "push"
                else:
                    h.outcome = "lose"
        if not self.is_split:
            self.outcome = self.hands[0].outcome
        else:
            net = self.main_payout - self.bet
            self.outcome = "split_win" if net > 0 else "split_push" if net == 0 else "split_lose"

    # ------------------------------------------------------------ 結算
    @staticmethod
    def hand_payout(hand: Hand) -> int:
        return int(hand.bet * OUTCOMES[hand.outcome][2]) if hand.outcome else 0

    @property
    def main_payout(self) -> int:
        return sum(self.hand_payout(h) for h in self.hands)

    @property
    def payout(self) -> int:
        """全部發回的錢（含本金）：各手主注 + 保險（莊家 Blackjack 時賠 2 倍，連本金拿回 3 倍）。"""
        if not self.outcome:
            return 0
        return self.main_payout + (self.insurance * 3 if self.insurance_won else 0)

    @property
    def staked(self) -> int:
        return self.bet + self.insurance

    def reveal_steps(self) -> List[int]:
        """結算動畫要停留的「莊家亮幾張牌」：先蓋著底牌，再一張一張翻；全部爆牌或自己 Blackjack 直接公布。"""
        if self.outcome == "blackjack" or all(h.outcome == "bust" for h in self.hands):
            return []
        return list(range(1, len(self.dealer)))


class Blackjack(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.games: Dict[int, BlackjackGame] = {}
        self.starting: set = set()      # 正在開局（等 Discord 或資料庫）的玩家，避免連按開出兩局

    # ------------------------------------------------------------------ 畫面
    def build_embed(self, game: BlackjackGame, user: discord.abc.User, balance: Optional[int] = None,
                    shown: Optional[int] = None) -> discord.Embed:
        """shown：結算動畫中莊家亮了幾張牌（None 表示照遊戲狀態顯示）。"""
        if shown is None and game.finished:
            shown = len(game.dealer)
            title, color, _ = OUTCOMES[game.outcome]
        elif shown is not None:
            title, color = "🃏 莊家開牌中…", Color.GAME
        elif game.insurance_pending:
            title, color = "🛡️ 莊家亮出 A，要買保險嗎？", Color.WARNING
        else:
            title, color = "🃏 21點", Color.GAME
        shown = 1 if shown is None else shown
        if shown >= 2:
            dealer_cards, dealer_value = render(game.dealer[:shown]), hand_value(game.dealer[:shown])
        else:
            dealer_cards, dealer_value = f"{render(game.dealer[:1])} {HIDDEN_CARD}", "?"
        final = game.finished and shown >= len(game.dealer)
        # 用標題大小顯示牌面，跟德州撲克「看手牌」一樣清楚
        lines = [f"**莊家**　{dealer_value} 點\n# {dealer_cards}"]
        for i, hand in enumerate(game.hands):
            # 還在決定的手牌才顯示軟牌兩種點數，打完就只顯示最後點數
            value = hand_value(hand.cards) if hand.done or game.finished else hand_text(hand.cards)
            if game.is_split:
                pointer = "👉 " if game.can_act and i == game.active else ""
                notes = [f"{hand.bet:,} 元" if game.bet else "", "已加倍" if hand.doubled else ""]
                if final and hand.outcome:
                    notes.append(HAND_RESULTS.get(hand.outcome, ""))
                note = "・".join(n for n in notes if n)
                name = f"{pointer}**第 {i + 1} 手**　{value} 點" + (f"　{note}" if note else "")
            else:
                name = f"**你的手牌**　{value} 點"
            lines.append(f"{name}\n# {render(hand.cards)}")
        embed = make_embed(title, "\n".join(lines), color)
        embed.set_author(name=user.display_name, icon_url=user.display_avatar.url)

        if game.insurance_pending:
            cost = f"{fmt_points(game.insurance_cost)}" if game.bet else "練習模式不用錢"
            embed.add_field(name="保險", value=f"花賭注的一半（{cost}）買保險：莊家如果是 Blackjack，保險賠 2 倍；"
                                               "不是的話保險金沒收，牌局照常進行。", inline=False)
        elif game.insured:
            if game.insurance_won and shown < len(game.dealer):
                result = "等莊家開牌…"
            elif game.insurance_won:
                result = f"莊家 Blackjack，保險賠 {fmt_points(game.insurance * 2)}" if game.bet else "莊家 Blackjack，保險中了！"
            else:
                result = "莊家沒有 Blackjack，保險金沒收"
            cost = fmt_points(game.insurance) if game.bet else "練習"
            embed.add_field(name="🛡️ 保險", value=f"{cost}・{result}", inline=False)

        doubled = "（已加倍）" if game.doubled and not game.is_split else ""
        bet_name = f"總賭注（{len(game.hands)} 手）" if game.is_split else "賭注"
        if final and game.bet:
            net = game.payout - game.staked
            embed.add_field(name="結算", value=f"{bet_name} {fmt_points(game.bet)}{doubled}"
                                               f"・損益 **{fmt_signed(net)}**・餘額 {fmt_points(balance or 0)}", inline=False)
        elif game.bet:
            embed.set_footer(text=f"{bet_name} {game.bet:,} 元{doubled}・上限 {MAX_BET:,} 元")
        else:
            embed.set_footer(text="練習模式（沒有下注）")
        return embed

    async def show(self, interaction: Optional[discord.Interaction], message, strict: bool = False, **kwargs):
        """更新牌局畫面：互動還沒回應就用互動回應（有原訊息就直接編輯），之後都編輯同一則訊息。

        strict：編輯失敗就拋出錯誤（開局時用，畫面沒出來就要退錢）。
        """
        if interaction is not None and not interaction.response.is_done():
            if message is not None:
                await interaction.response.edit_message(**kwargs)
                return message
            return await respond(interaction, **kwargs)
        if message is None:
            # 送新訊息時 view=None（不要按鈕）不能直接傳，Discord 會拒絕；編輯時才用 None 清掉按鈕
            if kwargs.get("view", ...) is None:
                kwargs.pop("view")
            return await respond(interaction, **kwargs)
        try:
            await message.edit(**kwargs)
        except discord.HTTPException:
            if strict:
                raise
        return message

    @staticmethod
    async def ack(interaction: Optional[discord.Interaction]) -> None:
        """要動資料庫（扣錢、結算）前先回應 Discord：Discord 只等 3 秒，資料庫一慢玩家就會看到「此互動失敗」。

        回應後 show() 會改用編輯原訊息更新畫面，錯誤訊息也會改用 followup 私訊。
        """
        if interaction is not None and not interaction.response.is_done():
            await interaction.response.defer()

    async def play_out(self, game: BlackjackGame, user: discord.abc.User,
                       interaction: Optional[discord.Interaction], message) -> None:
        """結算並播放莊家逐張開牌，最後換成「再來一局」按鈕。"""
        await self.ack(interaction)
        balance = await self.finish(game)
        for shown in game.reveal_steps():
            message = await self.show(interaction, message, embed=self.build_embed(game, user, shown=shown), view=None)
            await asyncio.sleep(REVEAL_DELAY)
        view = PlayAgainView(self, user.id, game.base_bet)
        view.message = await self.show(interaction, message, embed=self.build_embed(game, user, balance), view=view)

    # ------------------------------------------------------------------ 流程
    async def start(self, interaction: discord.Interaction, bet: int, message=None) -> bool:
        """開一局。message 有給就直接把那則訊息換成新牌局（再來一局），不另外發訊息。成功回傳 True。"""
        user = interaction.user
        if bet > MAX_BET:
            await send_error(interaction, f"21 點每局賭注上限是 {fmt_points(MAX_BET)}。")
            return False
        current = self.games.get(user.id)
        if user.id in self.starting or (current and not current.finished):
            await send_error(interaction, "你還有一局 21 點沒打完，先把它結束吧！")
            return False
        self.starting.add(user.id)
        try:
            return await self._start(interaction, bet, message)
        finally:
            self.starting.discard(user.id)

    async def _start(self, interaction: discord.Interaction, bet: int, message) -> bool:
        user = interaction.user
        # 先回應 Discord 再扣錢、登記牌局：回應失敗（逾時）就什麼都不會發生，不會卡住一局
        if message is not None:
            await self.ack(interaction)
        else:
            await defer(interaction, private_errors=True)

        hold_id = None
        if bet > 0:
            hold_id = await self.bot.db.hold(user.id, bet, "21點")
            if hold_id is None:
                balance = await self.bot.db.get_points(user.id)
                await send_error(interaction, f"存款不足，你目前有 {fmt_points(balance)}。")
                return False

        game = BlackjackGame(user.id, bet, hold_id)
        game.channel_id = interaction.channel_id
        self.games[user.id] = game
        game.begin()
        if game.finished:
            # 開牌就有 Blackjack：先讓大家看一眼發到的牌，再翻莊家底牌
            await self.play_out(game, user, interaction, message)
            return True
        view = InsuranceView(self, game, user) if game.insurance_pending else BlackjackView(self, game, user)
        try:
            view.message = await self.show(interaction, message, strict=True,
                                           embed=self.build_embed(game, user), view=view)
        except Exception:
            # 畫面沒出來，玩家按不到按鈕也不會逾時：取消這局並退還賭注
            log.exception("21 點畫面送不出去，取消這局並退還賭注")
            await self.cancel(game)
            await send_error(interaction, "牌桌畫面送不出去，這局已取消，賭注已退還。")
            return False
        return True

    async def cancel(self, game: BlackjackGame) -> None:
        """取消還沒開始打的牌局，全額退還賭注，不算戰績。"""
        self.games.pop(game.player_id, None)
        if game.hold_id is not None:
            await self.bot.db.settle(game.hold_id, game.staked, "21點：畫面送不出去，退還賭注")

    async def finish(self, game: BlackjackGame) -> Optional[int]:
        """結算並回傳新餘額。"""
        self.games.pop(game.player_id, None)
        if game.hold_id is None:
            return await self.bot.db.get_points(game.player_id)
        won = game.outcome in WIN_OUTCOMES
        balance = await self.bot.db.settle(game.hold_id, game.payout, "21點", game="blackjack", won=won)
        if game.bet >= MIN_QUEST_BET:
            self.bot.dispatch("activity", game.player_id, "game", game.channel_id, {"game": "blackjack", "won": won})
        return balance

    async def launch_from_hub(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(AmountModal(
            "🃏 21點", f"賭注（0 是練習模式，上限 {MAX_BET:,}）", self.start, default=DEFAULT_BET, max_value=MAX_BET))

    @app_commands.command(name="blackjack", description="21點：跟莊家比誰最接近 21 點")
    @app_commands.describe(bet=f"下注金額（新台幣，上限 {MAX_BET:,}），不填或填 0 是練習模式")
    async def blackjack(self, interaction: discord.Interaction, bet: app_commands.Range[int, 0, MAX_BET] = 0):
        await self.start(interaction, bet)


async def auto_stand(cog: Blackjack, game: BlackjackGame, user: discord.abc.User, message) -> None:
    """玩家離開太久就自動停牌結算（沒決定保險就當作不買），避免賭注一直被扣著。"""
    async with game.lock:
        if game.finished:
            return
        game.stand_all()
        balance = await cog.finish(game)
    if message:
        embed = cog.build_embed(game, user, balance)
        embed.set_footer(text="閒置太久，已自動停牌")
        try:
            await message.edit(embed=embed, view=None)
        except discord.HTTPException:
            pass


class InsuranceView(BaseView):
    owner_denied_message = "這不是你的牌局，用 /blackjack 開一局自己的吧！"

    def __init__(self, cog: Blackjack, game: BlackjackGame, user: discord.abc.User):
        super().__init__(owner_id=user.id, timeout=120)
        self.cog, self.game, self.user = cog, game, user
        self.buy.label = f"買保險（{game.insurance_cost:,} 元）" if game.bet else "買保險（練習）"

    async def _decide(self, interaction: discord.Interaction, buy: bool) -> None:
        async with self.game.lock:
            if not self.game.insurance_pending:
                return await send_error(interaction, "已經決定過了。")
            cost = self.game.insurance_cost
            if buy and cost:
                await self.cog.ack(interaction)
            if buy and cost and not await self.cog.bot.db.extend_hold(self.game.hold_id, cost):
                return await send_error(interaction, f"買保險需要 {fmt_points(cost)}，你的錢不夠。")
            self.stop()
            self.game.resolve_insurance(buy)
            message = self.message or interaction.message
            if self.game.finished:
                return await self.cog.play_out(self.game, self.user, interaction, message)
            view = BlackjackView(self.cog, self.game, self.user)
            view.message = await self.cog.show(interaction, message, embed=self.cog.build_embed(self.game, self.user),
                                               view=view)

    @discord.ui.button(label="買保險", emoji="🛡️", style=discord.ButtonStyle.primary)
    async def buy(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._decide(interaction, True)

    @discord.ui.button(label="不買", emoji="➡️", style=discord.ButtonStyle.secondary)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._decide(interaction, False)

    async def on_timeout(self) -> None:
        await auto_stand(self.cog, self.game, self.user, self.message)


class BlackjackView(BaseView):
    owner_denied_message = "這不是你的牌局，用 /blackjack 開一局自己的吧！"

    def __init__(self, cog: Blackjack, game: BlackjackGame, user: discord.abc.User):
        super().__init__(owner_id=user.id, timeout=120)
        self.cog, self.game, self.user = cog, game, user
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        # 只有這一手前兩張牌時能加倍；兩張點數一樣才能分牌（練習模式也可以，只是不用錢）
        self.double_btn.disabled = not self.game.can_double
        self.split_btn.disabled = not self.game.can_split
        self.surrender_btn.disabled = not self.game.can_surrender

    async def _pay_extra(self, interaction: discord.Interaction, what: str) -> bool:
        """加倍、分牌要再押這一手的賭注；練習模式不用錢。"""
        cost = self.game.hand.bet
        if not cost:
            return True
        await self.cog.ack(interaction)
        if await self.cog.bot.db.extend_hold(self.game.hold_id, cost):
            return True
        await send_error(interaction, f"{what}需要再 {fmt_points(cost)}，你的錢不夠。")
        return False

    async def _after_action(self, interaction: discord.Interaction) -> None:
        if self.game.finished:
            self.stop()
            await self.cog.play_out(self.game, self.user, interaction, self.message or interaction.message)
        else:
            self._refresh_buttons()
            await self.cog.show(interaction, self.message or interaction.message,
                                embed=self.cog.build_embed(self.game, self.user), view=self)

    @discord.ui.button(label="要牌", emoji="➕", style=discord.ButtonStyle.primary)
    async def hit_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with self.game.lock:
            if self.game.finished:
                return await send_error(interaction, "這局已經結束了。")
            self.game.hit()
            await self._after_action(interaction)

    @discord.ui.button(label="停牌", emoji="✋", style=discord.ButtonStyle.secondary)
    async def stand_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with self.game.lock:
            if self.game.finished:
                return await send_error(interaction, "這局已經結束了。")
            self.game.stand()
            await self._after_action(interaction)

    @discord.ui.button(label="加倍", emoji="✖️", style=discord.ButtonStyle.success)
    async def double_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with self.game.lock:
            if not self.game.can_double:
                return await send_error(interaction, "只有拿到前兩張牌時可以加倍。")
            if not await self._pay_extra(interaction, "加倍"):
                return
            self.game.double()
            await self._after_action(interaction)

    @discord.ui.button(label="分牌", emoji="✂️", style=discord.ButtonStyle.success)
    async def split_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with self.game.lock:
            if not self.game.can_split:
                return await send_error(interaction, f"前兩張點數一樣才能分牌（最多 {MAX_HANDS} 手）。")
            if not await self._pay_extra(interaction, "分牌"):
                return
            self.game.split()
            await self._after_action(interaction)

    @discord.ui.button(label="投降", emoji="🏳️", style=discord.ButtonStyle.danger)
    async def surrender_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with self.game.lock:
            if not self.game.can_surrender:
                return await send_error(interaction, "只有拿到前兩張牌、還沒分牌時可以投降。")
            self.game.surrender()
            await self._after_action(interaction)

    async def on_timeout(self) -> None:
        await auto_stand(self.cog, self.game, self.user, self.message)


class PlayAgainView(BaseView):
    owner_denied_message = "用 /blackjack 開一局自己的吧！"

    def __init__(self, cog: Blackjack, owner_id: int, bet: int):
        super().__init__(owner_id=owner_id, timeout=180)
        self.cog, self.bet = cog, bet
        self.again.label = f"再來一局（{bet:,} 元）" if bet else "再來一局"

    async def _restart(self, interaction: discord.Interaction, bet: int) -> None:
        # 直接在這則訊息開新的一局，不另外發訊息；失敗（錢不夠等）就留著按鈕
        if await self.cog.start(interaction, bet, message=self.message or interaction.message):
            self.stop()

    @discord.ui.button(label="再來一局", emoji="🔁", style=discord.ButtonStyle.success)
    async def again(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._restart(interaction, self.bet)

    @discord.ui.button(label="換個賭注", emoji="💰", style=discord.ButtonStyle.secondary)
    async def change_bet(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AmountModal(
            "🃏 21點", f"新的賭注（0 是練習模式，上限 {MAX_BET:,}）", self._restart,
            default=min(self.bet, MAX_BET), max_value=MAX_BET))


async def setup(bot):
    await bot.add_cog(Blackjack(bot))
