"""投票：票數存在資料庫，按鈕在 bot 重啟後仍然有效，可設定自動截止。"""
import logging
import time
from typing import List, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core.db import Poll
from core.timeutil import parse_duration, ts
from core.ui import Color, make_embed, progress_bar, send_error

log = logging.getLogger(__name__)

NUMBER_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
MAX_OPTIONS = 10
MAX_DURATION = 30 * 86400


class PollButton(discord.ui.DynamicItem[discord.ui.Button], template=r"poll:(?P<poll_id>\d+):(?P<action>\d+|end)"):
    def __init__(self, poll_id: int, action: str, *, label: Optional[str] = None, emoji: Optional[str] = None,
                 style: discord.ButtonStyle = discord.ButtonStyle.secondary, row: Optional[int] = None,
                 disabled: bool = False):
        super().__init__(discord.ui.Button(label=label, emoji=emoji, style=style, row=row, disabled=disabled,
                                           custom_id=f"poll:{poll_id}:{action}"))
        self.poll_id, self.action = poll_id, action

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match):
        return cls(int(match["poll_id"]), match["action"])

    async def callback(self, interaction: discord.Interaction):
        cog: Polls = interaction.client.get_cog("Polls")
        await cog.handle_button(interaction, self.poll_id, self.action)


class Polls(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_dynamic_items(PollButton)
        self.close_expired.start()

    async def cog_unload(self):
        self.bot.remove_dynamic_items(PollButton)
        self.close_expired.cancel()

    # ------------------------------------------------------------------ 畫面
    def build_embed(self, poll: Poll, counts: List[int]) -> discord.Embed:
        total = sum(counts)
        top = max(counts) if counts else 0
        lines = []
        for i, (option, count) in enumerate(zip(poll.options, counts)):
            pct = count / total if total else 0
            crown = " 🏆" if poll.closed and count == top and top > 0 else ""
            lines.append(f"{NUMBER_EMOJIS[i]} **{option}**{crown}\n`{progress_bar(pct, 12)}` {count} 票・{pct:.0%}")
        title = f"📊 {'【已結束】' if poll.closed else ''}{poll.question}"
        embed = make_embed(title, "\n".join(lines), Color.MUTED if poll.closed else Color.GOLD)
        info = [f"發起人 <@{poll.author_id}>", f"共 {total} 票"]
        if poll.ends_at and not poll.closed:
            info.append(f"{ts(poll.ends_at, 'R')}截止")
        embed.add_field(name="​", value="・".join(info))
        if not poll.closed:
            embed.set_footer(text="點按鈕投票，再按一次同一個選項可以取消")
        return embed

    def build_view(self, poll: Poll) -> discord.ui.View:
        view = discord.ui.View(timeout=None)
        for i, option in enumerate(poll.options):
            view.add_item(PollButton(poll.id, str(i), label=option[:80], emoji=NUMBER_EMOJIS[i],
                                     row=i // 5, disabled=poll.closed))
        if not poll.closed:
            view.add_item(PollButton(poll.id, "end", label="結束投票", style=discord.ButtonStyle.danger,
                                     row=(len(poll.options) - 1) // 5 + 1))
        return view

    # ------------------------------------------------------------------ 建立
    async def open_modal(self, interaction: discord.Interaction, duration: Optional[str] = None) -> None:
        seconds = None
        if duration:
            seconds = parse_duration(duration)
            if not seconds:
                return await send_error(interaction, f"看不懂「{duration}」這個時長，例如 `30m`、`2h`、`1d`。")
            if seconds > MAX_DURATION:
                return await send_error(interaction, "投票最長只能開 30 天。")
        await interaction.response.send_modal(PollModal(self, seconds))

    async def create(self, interaction: discord.Interaction, question: str, options: List[str],
                     seconds: Optional[int]) -> None:
        ends_at = time.time() + seconds if seconds else None
        poll_id = await self.bot.db.create_poll(interaction.channel_id, interaction.user.id, question, options, ends_at)
        poll = await self.bot.db.get_poll(poll_id)
        await interaction.response.send_message(embed=self.build_embed(poll, [0] * len(options)),
                                                view=self.build_view(poll))
        message = await interaction.original_response()
        await self.bot.db.set_poll_message(poll_id, message.id)

    @app_commands.command(name="poll", description="發起投票，可以設定自動截止")
    @app_commands.describe(duration="多久後自動截止，例如 30m、2h、1d；不填就手動結束")
    @app_commands.guild_only()
    async def poll(self, interaction: discord.Interaction, duration: Optional[str] = None):
        await self.open_modal(interaction, duration)

    # ------------------------------------------------------------------ 互動
    async def handle_button(self, interaction: discord.Interaction, poll_id: int, action: str) -> None:
        poll = await self.bot.db.get_poll(poll_id)
        if not poll:
            return await send_error(interaction, "找不到這個投票。")
        if poll.closed:
            return await send_error(interaction, "這個投票已經結束了。")

        if action == "end":
            is_mod = isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.manage_messages
            if interaction.user.id != poll.author_id and not is_mod:
                return await send_error(interaction, "只有發起人或管理員可以結束投票。")
            await self.bot.db.close_poll(poll_id)
            poll.closed = True
            counts = await self.bot.db.poll_counts(poll_id, len(poll.options))
            return await interaction.response.edit_message(embed=self.build_embed(poll, counts), view=self.build_view(poll))

        option = int(action)
        if not 0 <= option < len(poll.options):
            return await send_error(interaction, "這個選項不存在。")
        result = await self.bot.db.vote(poll_id, interaction.user.id, option)
        counts = await self.bot.db.poll_counts(poll_id, len(poll.options))
        await interaction.response.edit_message(embed=self.build_embed(poll, counts), view=self.build_view(poll))
        feedback = {
            "added": f"✅ 你投給了 **{poll.options[option]}**",
            "changed": f"🔄 已改投 **{poll.options[option]}**",
            "removed": "🗑️ 已取消你的投票",
        }[result]
        await interaction.followup.send(feedback, ephemeral=True)

    @tasks.loop(seconds=30)
    async def close_expired(self):
        for poll in await self.bot.db.expired_polls(time.time()):
            await self.bot.db.close_poll(poll.id)
            poll.closed = True
            if not poll.message_id:
                continue
            counts = await self.bot.db.poll_counts(poll.id, len(poll.options))
            channel = self.bot.get_channel(poll.channel_id)
            if not channel:
                continue
            try:
                await channel.get_partial_message(poll.message_id).edit(
                    embed=self.build_embed(poll, counts), view=self.build_view(poll))
            except discord.HTTPException:
                log.warning("無法更新已截止的投票 #%s", poll.id)

    @close_expired.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()


class PollModal(discord.ui.Modal, title="發起投票"):
    question = discord.ui.TextInput(label="投票題目", placeholder="例如：今晚吃什麼？", max_length=200)
    options = discord.ui.TextInput(label=f"選項（一行一個，2–{MAX_OPTIONS} 個）", style=discord.TextStyle.paragraph,
                                   placeholder="拉麵\n咖哩\n火鍋", max_length=1000)

    def __init__(self, cog: Polls, seconds: Optional[int]):
        super().__init__()
        self.cog, self.seconds = cog, seconds

    async def on_submit(self, interaction: discord.Interaction):
        options = []
        for line in self.options.value.splitlines():
            line = line.strip().lstrip("-•*").strip()
            if line and line not in options:
                options.append(line[:80])
        if not 2 <= len(options) <= MAX_OPTIONS:
            return await send_error(interaction, f"選項需要 2 到 {MAX_OPTIONS} 個（一行一個，重複的會自動合併）。")
        await self.cog.create(interaction, self.question.value.strip(), options, self.seconds)


async def setup(bot):
    await bot.add_cog(Polls(bot))
