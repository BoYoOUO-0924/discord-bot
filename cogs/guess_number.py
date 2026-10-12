"""猜數字：全頻道一起猜，直接在頻道輸入數字即可，bot 用表情回應大小，不洗版。"""
import asyncio
import math
import random
import time
from typing import Dict, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core.ui import BaseView, Color, make_embed, respond, send_error

IDLE_TIMEOUT = 10 * 60
RANGE_CHOICES = [
    app_commands.Choice(name="1–100（標準）", value=100),
    app_commands.Choice(name="1–1000（困難）", value=1000),
    app_commands.Choice(name="1–10000（地獄）", value=10000),
]


def reward_for(attempts: int, maximum: int) -> int:
    """以二分搜尋的最佳次數為基準給分。"""
    optimal = math.ceil(math.log2(maximum))
    if attempts <= optimal:
        return 100
    if attempts <= optimal * 2:
        return 50
    return 20


def range_bar(low: int, high: int, maximum: int, length: int = 20) -> str:
    start = int((low - 1) / maximum * length)
    end = max(start + 1, math.ceil(high / maximum * length))
    return "░" * start + "█" * (end - start) + "░" * (length - end)


class GuessGame:
    def __init__(self, channel_id: int, host_id: int, maximum: int):
        self.channel_id, self.host_id, self.maximum = channel_id, host_id, maximum
        self.answer = random.randint(1, maximum)
        self.low, self.high = 1, maximum
        self.attempts = 0
        self.players: Dict[int, int] = {}
        self.last_guess: Optional[str] = None
        self.last_activity = time.time()
        self.message: Optional[discord.Message] = None
        self.lock = asyncio.Lock()
        self.over = False

    def guess(self, user_id: int, number: int) -> str:
        """回傳 'out'、'higher'、'lower' 或 'correct'。"""
        if not self.low <= number <= self.high:
            return "out"
        self.attempts += 1
        self.players[user_id] = self.players.get(user_id, 0) + 1
        self.last_activity = time.time()
        if number < self.answer:
            self.low = number + 1
            return "higher"
        if number > self.answer:
            self.high = number - 1
            return "lower"
        self.over = True
        return "correct"


HINTS = {"higher": ("⬆️", "再大一點"), "lower": ("⬇️", "再小一點")}


class GuessNumber(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.games: Dict[int, GuessGame] = {}

    async def cog_load(self):
        self.expire_idle.start()

    async def cog_unload(self):
        self.expire_idle.cancel()

    # ------------------------------------------------------------------ 畫面
    def board_embed(self, game: GuessGame) -> discord.Embed:
        embed = make_embed("🔢 猜數字", f"答案在 **{game.low} ~ {game.high}** 之間\n`{range_bar(game.low, game.high, game.maximum)}`",
                           Color.GAME)
        embed.add_field(name="已猜", value=f"{game.attempts} 次")
        embed.add_field(name="參與", value=f"{len(game.players)} 人")
        embed.add_field(name="猜中可得", value=f"{reward_for(game.attempts + 1, game.maximum)} 分")
        if game.last_guess:
            embed.add_field(name="最近一次", value=game.last_guess, inline=False)
        embed.set_footer(text="直接在頻道輸入數字就能猜！⬆️ 代表答案更大，⬇️ 代表更小")
        return embed

    async def _refresh_board(self, game: GuessGame) -> None:
        if game.message:
            try:
                await game.message.edit(embed=self.board_embed(game))
            except discord.HTTPException:
                pass

    # ------------------------------------------------------------------ 流程
    async def start(self, interaction: discord.Interaction, maximum: int = 100) -> None:
        if not interaction.guild:
            return await send_error(interaction, "猜數字要在伺服器頻道裡玩。")
        existing = self.games.get(interaction.channel_id)
        if existing:
            link = f"\n[跳到遊戲]({existing.message.jump_url})" if existing.message else ""
            return await send_error(interaction, f"這個頻道已經在猜數字了，直接輸入數字加入吧！{link}")
        game = GuessGame(interaction.channel_id, interaction.user.id, maximum)
        self.games[interaction.channel_id] = game
        view = GuessBoardView(self, game)
        game.message = await respond(interaction, embed=self.board_embed(game), view=view)

    async def submit(self, game: GuessGame, user: discord.abc.User, number: int,
                     message: Optional[discord.Message] = None,
                     interaction: Optional[discord.Interaction] = None) -> None:
        async with game.lock:
            if game.over:
                return
            result = game.guess(user.id, number)

            if result == "out":
                if interaction:
                    return await send_error(interaction, f"{number} 不在範圍內，答案在 {game.low} ~ {game.high} 之間。")
                return await self._react(message, "❌")

            self.bot.dispatch("activity", user.id, "guess", game.channel_id, {})

            if result == "correct":
                return await self._win(game, user, number, message, interaction)

            emoji, text = HINTS[result]
            game.last_guess = f"{user.mention} 猜了 **{number}** {emoji} {text}"
            if interaction:
                await interaction.response.send_message(f"{user.mention} 猜了 **{number}** → {emoji} {text}",
                                                        allowed_mentions=discord.AllowedMentions.none())
            else:
                await self._react(message, emoji)
            await self._refresh_board(game)

    async def _react(self, message: Optional[discord.Message], emoji: str) -> None:
        if not message:
            return
        try:
            await message.add_reaction(emoji)
        except discord.HTTPException:
            # 沒有加反應的權限就改用回覆
            await message.reply(emoji, mention_author=False)

    async def _win(self, game: GuessGame, user: discord.abc.User, number: int,
                   message: Optional[discord.Message], interaction: Optional[discord.Interaction]) -> None:
        self.games.pop(game.channel_id, None)
        reward = reward_for(game.attempts, game.maximum)
        balance = await self.bot.db.add_points(user.id, reward, "猜數字猜中")
        await self.bot.db.record_game(user.id, "guess", True, reward)
        self.bot.dispatch("activity", user.id, "game", game.channel_id, {"game": "guess", "won": True})

        embed = make_embed("🎉 猜中了！", f"答案就是 **{number}**！", Color.GOLD)
        embed.set_author(name=user.display_name, icon_url=user.display_avatar.url)
        embed.add_field(name="全頻道共猜", value=f"{game.attempts} 次")
        embed.add_field(name="參與人數", value=f"{len(game.players)} 人")
        embed.add_field(name="獎勵", value=f"+{reward} 分（餘額 {balance:,}）")
        view = AgainView(self, game.maximum)
        if interaction:
            await interaction.response.send_message(embed=embed, view=view)
        else:
            await self._react(message, "🎉")
            await message.reply(embed=embed, view=view, mention_author=False)
        await self._close_board(game, f"✅ 已結束：{user.display_name} 猜中了 {number}")

    async def end(self, game: GuessGame, reason: str) -> None:
        self.games.pop(game.channel_id, None)
        game.over = True
        await self._close_board(game, reason)

    async def _close_board(self, game: GuessGame, reason: str) -> None:
        if not game.message:
            return
        embed = make_embed("🔢 猜數字", f"{reason}\n答案是 **{game.answer}**，共猜了 {game.attempts} 次。", Color.MUTED)
        try:
            await game.message.edit(embed=embed, view=None)
        except discord.HTTPException:
            pass

    async def launch_from_hub(self, interaction: discord.Interaction) -> None:
        await self.start(interaction)

    @app_commands.command(name="guess", description="猜數字：全頻道一起猜，猜中的人拿獎勵")
    @app_commands.describe(max="數字範圍")
    @app_commands.choices(max=RANGE_CHOICES)
    @app_commands.guild_only()
    async def guess(self, interaction: discord.Interaction, max: int = 100):
        await self.start(interaction, max)

    # ------------------------------------------------------------------ 監聽頻道訊息
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        game = self.games.get(message.channel.id)
        content = message.content.strip()
        if not game or not content.isdigit() or len(content) > 6:
            return
        await self.submit(game, message.author, int(content), message=message)

    @tasks.loop(minutes=1)
    async def expire_idle(self):
        now = time.time()
        for game in [g for g in self.games.values() if now - g.last_activity > IDLE_TIMEOUT]:
            await self.end(game, "⏰ 太久沒人猜，遊戲自動結束")


class GuessModal(discord.ui.Modal, title="猜一個數字"):
    number = discord.ui.TextInput(label="你的答案", max_length=6)

    def __init__(self, cog: GuessNumber, game: GuessGame):
        super().__init__()
        self.cog, self.game = cog, game
        self.number.placeholder = f"{game.low} ~ {game.high}"

    async def on_submit(self, interaction: discord.Interaction):
        if not self.number.value.strip().isdigit():
            return await send_error(interaction, "請輸入整數。")
        if self.game.over:
            return await send_error(interaction, "這局已經結束了。")
        await self.cog.submit(self.game, interaction.user, int(self.number.value), interaction=interaction)


class GuessBoardView(BaseView):
    def __init__(self, cog: GuessNumber, game: GuessGame):
        super().__init__(timeout=None)
        self.cog, self.game = cog, game

    @discord.ui.button(label="猜一個數字", emoji="🔢", style=discord.ButtonStyle.primary)
    async def guess_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.game.over:
            return await send_error(interaction, "這局已經結束了。")
        await interaction.response.send_modal(GuessModal(self.cog, self.game))

    @discord.ui.button(label="結束遊戲", emoji="🏳️", style=discord.ButtonStyle.secondary)
    async def stop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        is_mod = isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.manage_messages
        if interaction.user.id != self.game.host_id and not is_mod:
            return await send_error(interaction, "只有開局的人或管理員可以結束遊戲。")
        await interaction.response.defer()
        await self.cog.end(self.game, f"🏳️ {interaction.user.display_name} 結束了遊戲")


class AgainView(BaseView):
    def __init__(self, cog: GuessNumber, maximum: int):
        super().__init__(timeout=300)
        self.cog, self.maximum = cog, maximum

    @discord.ui.button(label="再來一局", emoji="🔁", style=discord.ButtonStyle.success)
    async def again(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.start(interaction, self.maximum)


async def setup(bot):
    await bot.add_cog(GuessNumber(bot))
