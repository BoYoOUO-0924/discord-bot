"""21 點：每人一局互不干擾，支援加倍與保險，Blackjack 賠 3:2，賭注上限 5 萬。

莊家明牌是 A 時先問要不要買保險（賭注的一半，莊家 Blackjack 賠 2 倍）；
結算時莊家的牌一張一張翻開；「再來一局」直接在同一則訊息繼續。
"""
import asyncio
import secrets
from typing import Dict, List, Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands

from cogs.poker.cards import card_label
from core.quests import MIN_QUEST_BET
from core.ui import AmountModal, BaseView, Color, fmt_points, fmt_signed, make_embed, respond, send_error

Card = Tuple[str, str]
RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
SUITS = ["♠", "♥", "♦", "♣"]
DEFAULT_BET = 10_000
MAX_BET = 50_000          # 每局賭注上限（加倍、保險另計）
REVEAL_DELAY = 1.0        # 莊家每翻一張牌的間隔（秒）


def new_deck() -> List[Card]:
    deck = [(r, s) for s in SUITS for r in RANKS]
    for i in range(len(deck) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        deck[i], deck[j] = deck[j], deck[i]
    return deck


def hand_value(cards: List[Card]) -> int:
    total, aces = 0, 0
    for rank, _ in cards:
        if rank == "A":
            aces += 1
            total += 11
        elif rank in ("J", "Q", "K"):
            total += 10
        else:
            total += int(rank)
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


def is_blackjack(cards: List[Card]) -> bool:
    return len(cards) == 2 and hand_value(cards) == 21


HIDDEN_CARD = "`？`"


def render(cards: List[Card]) -> str:
    return " ".join(f"`{card_label(r, s)}`" for r, s in cards)


# 結果 -> (標題, 顏色, 賠付倍數（含本金）)
OUTCOMES = {
    "blackjack": ("🎉 Blackjack！", Color.GOLD, 2.5),
    "win": ("🎉 你贏了！", Color.SUCCESS, 2),
    "dealer_bust": ("🎉 莊家爆牌，你贏了！", Color.SUCCESS, 2),
    "push": ("🤝 平手，退還賭注", Color.MUTED, 1),
    "lose": ("😢 你輸了", Color.DANGER, 0),
    "bust": ("💥 爆牌了", Color.DANGER, 0),
    "dealer_blackjack": ("😱 莊家 Blackjack", Color.DANGER, 0),
}


class BlackjackGame:
    def __init__(self, player_id: int, bet: int, hold_id: Optional[int], deck: Optional[List[Card]] = None):
        self.player_id, self.bet, self.hold_id = player_id, bet, hold_id
        self.base_bet = bet                  # 開局時的賭注（再來一局用）
        self.deck = deck or new_deck()
        self.player = [self.deck.pop(), self.deck.pop()]
        self.dealer = [self.deck.pop(), self.deck.pop()]
        self.doubled = False
        self.insured = False                 # 有沒有買保險（練習模式也可以買，只是不用錢）
        self.insurance = 0                   # 買保險的金額
        self.insurance_pending = False       # 莊家明牌是 A，等玩家決定要不要買保險
        self.outcome: Optional[str] = None
        self.channel_id: Optional[int] = None
        self.lock = asyncio.Lock()

    @property
    def finished(self) -> bool:
        return self.outcome is not None

    @property
    def offers_insurance(self) -> bool:
        return self.dealer[0][0] == "A"

    @property
    def insurance_cost(self) -> int:
        return self.bet // 2

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
        player_bj, dealer_bj = is_blackjack(self.player), is_blackjack(self.dealer)
        if player_bj and dealer_bj:
            self.outcome = "push"
        elif player_bj:
            self.outcome = "blackjack"
        elif dealer_bj:
            self.outcome = "dealer_blackjack"

    def hit(self) -> None:
        self.player.append(self.deck.pop())
        if hand_value(self.player) > 21:
            self.outcome = "bust"
        elif hand_value(self.player) == 21:
            self.stand()  # 21 點自動停牌

    def stand(self) -> None:
        if self.insurance_pending:          # 還沒決定保險就停牌（逾時、後台強制結束）：當作不買
            self.resolve_insurance(False)
            if self.finished:
                return
        while hand_value(self.dealer) < 17:
            self.dealer.append(self.deck.pop())
        player, dealer = hand_value(self.player), hand_value(self.dealer)
        if dealer > 21:
            self.outcome = "dealer_bust"
        elif player > dealer:
            self.outcome = "win"
        elif player == dealer:
            self.outcome = "push"
        else:
            self.outcome = "lose"

    def double(self) -> None:
        self.bet *= 2
        self.doubled = True
        self.player.append(self.deck.pop())
        if hand_value(self.player) > 21:
            self.outcome = "bust"
        else:
            self.stand()

    @property
    def payout(self) -> int:
        """全部發回的錢（含本金）：主注 + 保險（莊家 Blackjack 時賠 2 倍，連本金拿回 3 倍）。"""
        if not self.outcome:
            return 0
        return int(self.bet * OUTCOMES[self.outcome][2]) + (self.insurance * 3 if self.insurance_won else 0)

    @property
    def staked(self) -> int:
        return self.bet + self.insurance

    def reveal_steps(self) -> List[int]:
        """結算動畫要停留的「莊家亮幾張牌」：先蓋著底牌，再一張一張翻；爆牌或自己 Blackjack 直接公布。"""
        if self.outcome in ("bust", "blackjack"):
            return []
        return list(range(1, len(self.dealer)))


class Blackjack(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.games: Dict[int, BlackjackGame] = {}

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
        # 用標題大小顯示牌面，跟德州撲克「看手牌」一樣清楚
        description = (f"**莊家**　{dealer_value} 點\n# {dealer_cards}\n"
                       f"**你的手牌**　{hand_value(game.player)} 點\n# {render(game.player)}")
        embed = make_embed(title, description, color)
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

        final = game.finished and shown >= len(game.dealer)
        doubled = "（已加倍）" if game.doubled else ""
        if final and game.bet:
            net = game.payout - game.staked
            embed.add_field(name="結算", value=f"賭注 {fmt_points(game.bet)}{doubled}"
                                               f"・損益 **{fmt_signed(net)}**・餘額 {fmt_points(balance or 0)}", inline=False)
        elif game.bet:
            embed.set_footer(text=f"賭注 {game.bet:,} 元{doubled}・上限 {MAX_BET:,} 元")
        else:
            embed.set_footer(text="練習模式（沒有下注）")
        return embed

    async def show(self, interaction: Optional[discord.Interaction], message, **kwargs):
        """更新牌局畫面：互動還沒回應就用互動回應（有原訊息就直接編輯），之後都編輯同一則訊息。"""
        if interaction is not None and not interaction.response.is_done():
            if message is not None:
                await interaction.response.edit_message(**kwargs)
                return message
            return await respond(interaction, **kwargs)
        if message is None:
            return await respond(interaction, **kwargs)
        try:
            await message.edit(**kwargs)
        except discord.HTTPException:
            pass
        return message

    async def play_out(self, game: BlackjackGame, user: discord.abc.User,
                       interaction: Optional[discord.Interaction], message) -> None:
        """結算並播放莊家逐張開牌，最後換成「再來一局」按鈕。"""
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
        if current and not current.finished:
            await send_error(interaction, "你還有一局 21 點沒打完，先把它結束吧！")
            return False

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
        view.message = await self.show(interaction, message, embed=self.build_embed(game, user), view=view)
        return True

    async def finish(self, game: BlackjackGame) -> Optional[int]:
        """結算並回傳新餘額。"""
        self.games.pop(game.player_id, None)
        if game.hold_id is None:
            return await self.bot.db.get_points(game.player_id)
        won = game.outcome in ("blackjack", "win", "dealer_bust")
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
        game.stand()
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
        self.double_btn.disabled = game.bet == 0 or len(game.player) != 2

    async def _after_action(self, interaction: discord.Interaction) -> None:
        if self.game.finished:
            self.stop()
            await self.cog.play_out(self.game, self.user, interaction, self.message or interaction.message)
        else:
            self.double_btn.disabled = True  # 只有前兩張牌時能加倍
            await interaction.response.edit_message(embed=self.cog.build_embed(self.game, self.user), view=self)

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
            if self.game.finished or len(self.game.player) != 2:
                return await send_error(interaction, "只有拿到前兩張牌時可以加倍。")
            if not await self.cog.bot.db.extend_hold(self.game.hold_id, self.game.bet):
                return await send_error(interaction, f"加倍需要再 {fmt_points(self.game.bet)}，你的錢不夠。")
            self.game.double()
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
