"""大廳與說明：所有功能的入口。"""
import logging

import discord
from discord import app_commands
from discord.ext import commands

from cogs.reminders import RemindModal
from core.catalog import GAMES, HELP_SECTIONS
from core.timeutil import today_tw
from core.ui import Color, fmt_points, make_embed, send_error

log = logging.getLogger(__name__)

GAME_COGS = {
    "blackjack": "Blackjack",
    "slots": "Slots",
    "guess": "GuessNumber",
    "tictactoe": "TicTacToe",
    "poker": "Poker",
    "seatortoise": "SeaTortoise",
}


class HubView(discord.ui.View):
    """大廳按鈕。註冊為永久介面，bot 重啟後舊的大廳訊息仍然能用。"""

    def __init__(self, cog: "Hub"):
        super().__init__(timeout=None)
        self.cog = cog
        row0 = [("checkin", "簽到", "✨", discord.ButtonStyle.success),
                ("work", "打工", "💼", discord.ButtonStyle.success),
                ("quests", "任務", "🎯", discord.ButtonStyle.primary),
                ("profile", "個人資料", "👤", discord.ButtonStyle.primary),
                ("leaderboard", "排行榜", "🏆", discord.ButtonStyle.primary)]
        for key, label, emoji, style in row0:
            self._add(f"hub:{key}", label, emoji, style, 0)
        for i, game in enumerate(GAMES):
            self._add(f"hub:game:{game.key}", game.name, game.emoji, discord.ButtonStyle.secondary, 1 + i // 5)
        tools = [("remind", "提醒", "⏰"), ("poll", "投票", "📊"), ("help", "指令說明", "📖")]
        for key, label, emoji in tools:
            self._add(f"hub:{key}", label, emoji, discord.ButtonStyle.secondary, 3)

    def _add(self, custom_id: str, label: str, emoji: str, style: discord.ButtonStyle, row: int) -> None:
        button = discord.ui.Button(custom_id=custom_id, label=label, emoji=emoji, style=style, row=row)
        button.callback = self._dispatch
        self.add_item(button)

    async def _dispatch(self, interaction: discord.Interaction) -> None:
        await self.cog.handle(interaction, interaction.data["custom_id"].removeprefix("hub:"))

    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        log.exception("大廳按鈕發生錯誤", exc_info=error)
        await send_error(interaction, "操作時發生錯誤，請稍後再試。")


class Hub(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(HubView(self))

    # ------------------------------------------------------------------ 畫面
    async def menu_embed(self, user: discord.abc.User, personal: bool) -> discord.Embed:
        name = self.bot.user.name if self.bot.user else "Bot"
        embed = make_embed(f"🎮 {name} 大廳", color=Color.PRIMARY)
        if personal:
            profile = await self.bot.db.get_profile(user.id, today_tw())
            checked = "✅ 今天已簽到" if profile.last_checkin == today_tw() else "❗ 今天還沒簽到"
            embed.description = f"嗨 {user.mention}！選一個功能開始吧 👇"
            embed.add_field(name="你的狀態",
                            value=f"💰 {fmt_points(profile.points)}・🔥 連續 {profile.streak} 天・{checked}", inline=False)
        else:
            embed.description = "點下方按鈕就能開始，所有結果只會顯示給操作的人或在頻道開局。"
        embed.add_field(name="賺積分", value="✨ 每日簽到・💼 打工（每小時）・🎯 每日任務・💬 聊天也會累積", inline=False)
        games = []
        for game in GAMES:
            unavailable = "（未啟用）" if game.needs_ai and not self.bot.ai.available else ""
            games.append(f"{game.emoji} **{game.name}**{unavailable}　{game.tagline}")
        embed.add_field(name="遊戲", value="\n".join(games), inline=False)
        embed.add_field(name="工具", value="⏰ **提醒**　時間到通知你\n📊 **投票**　按鈕投票、自動截止", inline=False)
        embed.set_footer(text="每個功能也都能用斜線指令操作，輸入 / 看看")
        if self.bot.user:
            embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        return embed

    def help_embed(self) -> discord.Embed:
        embed = make_embed("📖 指令說明", "點藍色的指令可以直接使用，`[ ]` 是可以不填的參數。", Color.PRIMARY)
        for title, commands_ in HELP_SECTIONS:
            lines = []
            for name, args, desc in commands_:
                usage = f"{self.bot.mention(name)} {args}".rstrip()
                lines.append(f"{usage}\n　{desc}")
            embed.add_field(name=title, value="\n".join(lines), inline=False)
        embed.set_footer(text="遊戲贏得的積分可以在 /profile 查看戰績")
        return embed

    # ------------------------------------------------------------------ 按鈕分派
    async def handle(self, interaction: discord.Interaction, action: str) -> None:
        if interaction.guild_id:
            # 按鈕不經過斜線指令的入口，這裡也要記錄，排行榜才找得到人
            await self.bot.db.touch_member(interaction.guild_id, interaction.user.id)
            await self.bot.db.remember_user(interaction.user.id, interaction.user.display_name,
                                            interaction.user.display_avatar.url)
        economy = self.bot.get_cog("Economy")
        if action == "checkin":
            return await economy.do_checkin(interaction)
        if action == "work":
            return await self.bot.get_cog("Earn").do_work(interaction)
        if action == "quests":
            return await self.bot.get_cog("Earn").send_quests(interaction)
        if action == "profile":
            return await economy.send_profile(interaction)
        if action == "leaderboard":
            return await economy.send_leaderboard(interaction, ephemeral=True)
        if action == "help":
            return await interaction.response.send_message(embed=self.help_embed(), ephemeral=True)
        if action == "remind":
            return await interaction.response.send_modal(RemindModal(self.bot.get_cog("Reminders")))
        if action == "poll":
            if not interaction.guild:
                return await send_error(interaction, "投票只能在伺服器裡使用。")
            return await self.bot.get_cog("Polls").open_modal(interaction)
        if action.startswith("game:"):
            key = action.removeprefix("game:")
            cog = self.bot.get_cog(GAME_COGS.get(key, ""))
            if not cog:
                return await send_error(interaction, "這個遊戲目前無法使用。")
            return await cog.launch_from_hub(interaction)
        await send_error(interaction, "這個按鈕已經失效了，請重新打開 /menu。")

    # ------------------------------------------------------------------ 指令
    @app_commands.command(name="menu", description="打開大廳，用按鈕操作所有功能")
    @app_commands.describe(public="公開發在頻道裡讓大家都能用（需要管理訊息權限）")
    async def menu(self, interaction: discord.Interaction, public: bool = False):
        if public:
            if not (isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.manage_messages):
                return await send_error(interaction, "只有管理員可以公開發布大廳。")
            embed = await self.menu_embed(interaction.user, personal=False)
            return await interaction.response.send_message(embed=embed, view=HubView(self))
        embed = await self.menu_embed(interaction.user, personal=True)
        await interaction.response.send_message(embed=embed, view=HubView(self), ephemeral=True)

    @app_commands.command(name="help", description="顯示所有指令的說明")
    async def help(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=self.help_embed(), ephemeral=True)


async def setup(bot):
    await bot.add_cog(Hub(bot))
