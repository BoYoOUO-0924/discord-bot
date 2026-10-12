"""拉霸機。"""
import asyncio
import random

import discord
from discord import app_commands
from discord.ext import commands

from core.ui import AmountModal, BaseView, Color, fmt_points, fmt_signed, make_embed, respond, send_error

SYMBOLS = ["💎", "7️⃣", "⭐", "🍀", "🔔", "🍇", "🍒", "🍋"]
WEIGHTS = [2, 4, 6, 8, 10, 15, 25, 30]
TRIPLE_PAYOUTS = {"💎": 100, "7️⃣": 50, "⭐": 25, "🍀": 15, "🔔": 10, "🍇": 7, "🍒": 5, "🍋": 3}
DEFAULT_BET = 50


def spin() -> list:
    return random.choices(SYMBOLS, weights=WEIGHTS, k=3)


def evaluate(reels: list) -> tuple:
    """回傳 (賠率倍數, 說明)。"""
    if reels[0] == reels[1] == reels[2]:
        multiplier = TRIPLE_PAYOUTS[reels[0]]
        text = f"三個 {reels[0]}！{multiplier} 倍！"
        return multiplier, (f"🎉 JACKPOT！{text}" if multiplier >= 50 else text)
    cherries = reels.count("🍒")
    if cherries == 2:
        return 2, "兩顆櫻桃，2 倍！"
    if cherries == 1:
        return 1, "一顆櫻桃，退還賭注"
    return 0, "可惜沒中，再接再厲！"


def payout_table() -> str:
    lines = [f"{s}{s}{s}　**{m} 倍**" for s, m in TRIPLE_PAYOUTS.items()]
    lines += ["🍒 任兩顆　**2 倍**", "🍒 任一顆　**退還賭注**"]
    return "\n".join(lines)


class Slots(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _frame(self, user, bet: int, reels: list, status: str, color: int) -> discord.Embed:
        embed = make_embed("🎰 拉霸機", f"# {' │ '.join(reels)}\n{status}", color)
        embed.set_author(name=f"{user.display_name} 下注 {bet:,} 分", icon_url=user.display_avatar.url)
        return embed

    async def play(self, interaction: discord.Interaction, bet: int) -> None:
        user = interaction.user
        hold_id = await self.bot.db.hold(user.id, bet, "拉霸機")
        if hold_id is None:
            balance = await self.bot.db.get_points(user.id)
            return await send_error(interaction, f"積分不足，你目前有 {fmt_points(balance)}。")

        message = await respond(interaction, embed=self._frame(user, bet, ["❓"] * 3, "*滾輪轉動中…*", Color.GAME))
        final = spin()
        # 三個滾輪依序停下，製造一點期待感
        for stopped in range(3):
            await asyncio.sleep(0.7)
            reels = final[:stopped + 1] + [random.choice(SYMBOLS) for _ in range(2 - stopped)]
            await message.edit(embed=self._frame(user, bet, reels, "*滾輪轉動中…*", Color.GAME))

        multiplier, text = evaluate(final)
        payout = bet * multiplier
        balance = await self.bot.db.settle(hold_id, payout, "拉霸機", game="slots", won=payout > bet)
        color = Color.GOLD if multiplier >= 10 else Color.SUCCESS if payout > bet else Color.MUTED if payout == bet else Color.DANGER
        embed = self._frame(user, bet, final, f"**{text}**", color)
        embed.set_footer(text=f"損益 {fmt_signed(payout - bet)}・餘額 {balance:,} 分")
        view = SlotsView(self, user.id, bet)
        await message.edit(embed=embed, view=view)
        view.message = message

    async def launch_from_hub(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(AmountModal("🎰 拉霸機", "賭注", self.play, default=DEFAULT_BET, min_value=1))

    @app_commands.command(name="slots", description="拉霸機：三個一樣就中獎，最高 100 倍")
    @app_commands.describe(bet="下注的積分")
    @app_commands.checks.cooldown(1, 3.0)
    async def slots(self, interaction: discord.Interaction, bet: app_commands.Range[int, 1, 10_000_000]):
        await self.play(interaction, bet)


class SlotsView(BaseView):
    owner_denied_message = "這不是你的拉霸機，用 /slots 玩一台吧！"

    def __init__(self, cog: Slots, owner_id: int, bet: int):
        super().__init__(owner_id=owner_id, timeout=180)
        self.cog, self.bet = cog, bet
        self.again.label = f"再轉一次（{bet:,} 分）"

    @discord.ui.button(label="再轉一次", emoji="🔄", style=discord.ButtonStyle.primary)
    async def again(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(view=None)
        await self.cog.play(interaction, self.bet)

    @discord.ui.button(label="賠率表", emoji="📋", style=discord.ButtonStyle.secondary)
    async def table(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=make_embed("📋 拉霸機賠率表", payout_table()), ephemeral=True)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # 賠率表大家都能看
        if interaction.data.get("custom_id") == self.table.custom_id:
            return True
        return await super().interaction_check(interaction)


async def setup(bot):
    await bot.add_cog(Slots(bot))
