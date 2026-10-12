"""21 點：每人一局互不干擾，支援加倍下注，Blackjack 賠 3:2。"""
import asyncio
import secrets
from typing import Dict, List, Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands

from core.quests import MIN_QUEST_BET
from core.ui import AmountModal, BaseView, Color, fmt_points, fmt_signed, make_embed, respond, send_error

Card = Tuple[str, str]
RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
SUITS = ["♠", "♥", "♦", "♣"]
DEFAULT_BET = 100


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


def render(cards: List[Card]) -> str:
    return " ".join(f"`{r}{s}`" for r, s in cards)


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
        self.deck = deck or new_deck()
        self.player = [self.deck.pop(), self.deck.pop()]
        self.dealer = [self.deck.pop(), self.deck.pop()]
        self.doubled = False
        self.outcome: Optional[str] = None
        self.channel_id: Optional[int] = None
        self.lock = asyncio.Lock()

    @property
    def finished(self) -> bool:
        return self.outcome is not None

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
        return int(self.bet * OUTCOMES[self.outcome][2]) if self.outcome else 0


class Blackjack(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.games: Dict[int, BlackjackGame] = {}

    # ------------------------------------------------------------------ 畫面
    def build_embed(self, game: BlackjackGame, user: discord.abc.User, balance: Optional[int] = None) -> discord.Embed:
        if game.finished:
            title, color, _ = OUTCOMES[game.outcome]
            dealer = f"{render(game.dealer)}　**{hand_value(game.dealer)}**"
        else:
            title, color = "🃏 21點", Color.GAME
            dealer = f"{render(game.dealer[:1])} `🂠`　**?**"
        embed = make_embed(title, color=color)
        embed.set_author(name=user.display_name, icon_url=user.display_avatar.url)
        embed.add_field(name="莊家", value=dealer, inline=False)
        embed.add_field(name="你的手牌", value=f"{render(game.player)}　**{hand_value(game.player)}**", inline=False)

        if game.finished and game.bet:
            net = game.payout - game.bet
            embed.add_field(name="結算", value=f"賭注 {fmt_points(game.bet)}{'（已加倍）' if game.doubled else ''}"
                                               f"・損益 **{fmt_signed(net)}**・餘額 {fmt_points(balance or 0)}", inline=False)
        elif game.bet:
            embed.set_footer(text=f"賭注 {game.bet:,} 分{'（已加倍）' if game.doubled else ''}")
        else:
            embed.set_footer(text="練習模式（沒有下注）")
        return embed

    # ------------------------------------------------------------------ 流程
    async def start(self, interaction: discord.Interaction, bet: int) -> None:
        user = interaction.user
        current = self.games.get(user.id)
        if current and not current.finished:
            return await send_error(interaction, "你還有一局 21 點沒打完，先把它結束吧！")

        hold_id = None
        if bet > 0:
            hold_id = await self.bot.db.hold(user.id, bet, "21點")
            if hold_id is None:
                balance = await self.bot.db.get_points(user.id)
                return await send_error(interaction, f"積分不足，你目前有 {fmt_points(balance)}。")

        game = BlackjackGame(user.id, bet, hold_id)
        game.channel_id = interaction.channel_id
        self.games[user.id] = game
        game.check_naturals()
        if game.finished:
            balance = await self.finish(game)
            view = PlayAgainView(self, user.id, bet)
            view.message = await respond(interaction, embed=self.build_embed(game, user, balance), view=view)
            return
        view = BlackjackView(self, game, user)
        view.message = await respond(interaction, embed=self.build_embed(game, user), view=view)

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
            "🃏 21點", "賭注（填 0 是練習模式）", self.start, default=DEFAULT_BET))

    @app_commands.command(name="blackjack", description="21點：跟莊家比誰最接近 21 點")
    @app_commands.describe(bet="下注的積分，不填或填 0 是練習模式")
    async def blackjack(self, interaction: discord.Interaction, bet: app_commands.Range[int, 0, 10_000_000] = 0):
        await self.start(interaction, bet)


class BlackjackView(BaseView):
    owner_denied_message = "這不是你的牌局，用 /blackjack 開一局自己的吧！"

    def __init__(self, cog: Blackjack, game: BlackjackGame, user: discord.abc.User):
        super().__init__(owner_id=user.id, timeout=120)
        self.cog, self.game, self.user = cog, game, user
        self.double_btn.disabled = game.bet == 0

    async def _after_action(self, interaction: discord.Interaction) -> None:
        if self.game.finished:
            balance = await self.cog.finish(self.game)
            self.stop()
            view = PlayAgainView(self.cog, self.user.id, self.game.bet // (2 if self.game.doubled else 1))
            view.message = self.message
            await interaction.response.edit_message(embed=self.cog.build_embed(self.game, self.user, balance), view=view)
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
                return await send_error(interaction, f"加倍需要再 {fmt_points(self.game.bet)}，你的積分不夠。")
            self.game.double()
            await self._after_action(interaction)

    async def on_timeout(self) -> None:
        # 玩家離開太久就自動停牌結算，避免賭注一直被扣著
        async with self.game.lock:
            if self.game.finished:
                return
            self.game.stand()
            balance = await self.cog.finish(self.game)
        if self.message:
            embed = self.cog.build_embed(self.game, self.user, balance)
            embed.set_footer(text="閒置太久，已自動停牌")
            try:
                await self.message.edit(embed=embed, view=None)
            except discord.HTTPException:
                pass


class PlayAgainView(BaseView):
    owner_denied_message = "用 /blackjack 開一局自己的吧！"

    def __init__(self, cog: Blackjack, owner_id: int, bet: int):
        super().__init__(owner_id=owner_id, timeout=180)
        self.cog, self.bet = cog, bet
        self.again.label = f"再來一局（{bet:,} 分）" if bet else "再來一局"

    @discord.ui.button(label="再來一局", emoji="🔁", style=discord.ButtonStyle.success)
    async def again(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._clear_buttons()
        await self.cog.start(interaction, self.bet)

    @discord.ui.button(label="換個賭注", emoji="💰", style=discord.ButtonStyle.secondary)
    async def change_bet(self, interaction: discord.Interaction, button: discord.ui.Button):
        async def start(inter: discord.Interaction, amount: int):
            await self._clear_buttons()
            await self.cog.start(inter, amount)
        await interaction.response.send_modal(AmountModal("🃏 21點", "新的賭注（0 是練習模式）", start, default=self.bet))

    async def _clear_buttons(self) -> None:
        self.stop()
        if self.message:
            try:
                await self.message.edit(view=None)
            except discord.HTTPException:
                pass


async def setup(bot):
    await bot.add_cog(Blackjack(bot))
