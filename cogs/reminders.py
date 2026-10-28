"""提醒：存在資料庫裡，bot 重啟也不會遺失。"""
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import List

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core.timeutil import fmt_duration, now_tw, parse_when, ts
from core.ui import BaseView, Color, defer, make_embed, respond, send_error, success_embed

log = logging.getLogger(__name__)

MAX_PER_USER = 25
MAX_DAYS = 365
PRESETS = ["10m", "30m", "1h", "3h", "明天 09:00", "20:00"]
FORMAT_HELP = "支援的格式：`10m`、`1h30m`、`2d`、`90秒`、`18:30`、`明天 9:00`"


class Reminders(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.deliver_due.start()

    async def cog_unload(self):
        self.deliver_due.cancel()

    # ------------------------------------------------------------------ 建立
    async def create(self, interaction: discord.Interaction, when: str, content: str) -> None:
        now = now_tw()
        due = parse_when(when, now)
        if not due:
            return await send_error(interaction, f"看不懂「{when}」這個時間。\n{FORMAT_HELP}")
        if due - now > timedelta(days=MAX_DAYS):
            return await send_error(interaction, f"最多只能設定 {MAX_DAYS} 天內的提醒。")
        if await self.bot.db.count_reminders(interaction.user.id) >= MAX_PER_USER:
            return await send_error(interaction, f"你已經有 {MAX_PER_USER} 個提醒了，先用 `/reminders` 取消一些吧。")

        channel_id = interaction.channel_id if interaction.guild else None
        reminder_id = await self.bot.db.add_reminder(interaction.user.id, channel_id, content, due.timestamp())
        embed = make_embed("⏰ 提醒已設定", f"> {content}", Color.SUCCESS)
        embed.add_field(name="提醒時間", value=f"{ts(due, 'F')}（{ts(due, 'R')}）")
        embed.set_footer(text="時間到會在這個頻道提及你；用 /reminders 可以查看或取消")
        view = CancelReminderView(self, interaction.user.id, reminder_id)
        view.message = await respond(interaction, embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="remind", description="設定提醒，時間到會通知你")
    @app_commands.describe(when="多久後或幾點，例如 10m、1h30m、18:30、明天 9:00", message="要提醒的內容")
    async def remind(self, interaction: discord.Interaction, when: str,
                     message: app_commands.Range[str, 1, 500]):
        await self.create(interaction, when, message)

    @remind.autocomplete("when")
    async def when_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        now = now_tw()
        choices = []
        candidates = ([current] if current else []) + [p for p in PRESETS if p != current]
        for text in candidates:
            due = parse_when(text, now)
            if due:
                label = f"{text}　→　{due:%m/%d %H:%M} 提醒（{fmt_duration((due - now).total_seconds())}後）"
                choices.append(app_commands.Choice(name=label[:100], value=text[:100]))
        if current and not parse_when(current, now):
            choices.insert(0, app_commands.Choice(name=f"⚠️ 格式不對：{current}"[:100], value=current[:100]))
        return choices[:25]

    # ------------------------------------------------------------------ 查看 / 取消
    @app_commands.command(name="reminders", description="查看或取消你的提醒")
    async def reminders(self, interaction: discord.Interaction):
        await defer(interaction, ephemeral=True)
        rows = await self.bot.db.list_reminders(interaction.user.id)
        if not rows:
            return await respond(interaction, embed=make_embed("⏰ 你的提醒", "目前沒有任何提醒。用 `/remind` 新增一個吧！"),
                                 ephemeral=True)
        lines = [f"`#{r['id']}` {ts(r['due_at'], 'R')}・{r['content'][:60]}" for r in rows]
        embed = make_embed(f"⏰ 你的提醒（{len(rows)}/{MAX_PER_USER}）", "\n".join(lines))
        embed.set_footer(text="從下方選單選擇要取消的提醒")
        view = ReminderListView(self, interaction.user.id, rows)
        view.message = await respond(interaction, embed=embed, view=view, ephemeral=True)

    # ------------------------------------------------------------------ 送達
    @tasks.loop(seconds=10)
    async def deliver_due(self):
        for row in await self.bot.db.due_reminders(time.time()):
            try:
                await self._deliver(row)
            except Exception:
                log.exception("送出提醒 #%s 失敗", row["id"])
            await self.bot.db.delete_reminder(row["id"])

    @deliver_due.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()

    async def _deliver(self, row) -> None:
        embed = make_embed("⏰ 提醒時間到！", f"> {row['content']}", Color.WARNING)
        late = time.time() - row["due_at"]
        footer = "設定於"
        if late > 60:
            footer = f"bot 離線期間到期，延遲 {fmt_duration(late)} 送達・設定於"
        embed.set_footer(text=footer)
        embed.timestamp = datetime.fromtimestamp(row["created_at"], tz=timezone.utc)
        mention = f"<@{row['user_id']}>"
        allowed = discord.AllowedMentions(users=True, everyone=False, roles=False)

        if row["channel_id"]:
            channel = self.bot.get_channel(row["channel_id"])
            try:
                channel = channel or await self.bot.fetch_channel(row["channel_id"])
                await channel.send(content=mention, embed=embed, allowed_mentions=allowed)
                return
            except (discord.Forbidden, discord.NotFound):
                pass  # 頻道沒了或沒權限，改用私訊
        user = self.bot.get_user(row["user_id"]) or await self.bot.fetch_user(row["user_id"])
        await user.send(embed=embed)


class RemindModal(discord.ui.Modal, title="設定提醒"):
    when = discord.ui.TextInput(label="時間", placeholder="10m、1h30m、18:30、明天 9:00", max_length=30)
    content = discord.ui.TextInput(label="提醒內容", style=discord.TextStyle.paragraph, max_length=500)

    def __init__(self, cog: Reminders):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.create(interaction, self.when.value, self.content.value)


class CancelReminderView(BaseView):
    def __init__(self, cog: Reminders, owner_id: int, reminder_id: int):
        super().__init__(owner_id=owner_id, timeout=600)
        self.cog, self.reminder_id = cog, reminder_id

    @discord.ui.button(label="取消這個提醒", style=discord.ButtonStyle.secondary, emoji="🗑️")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        removed = await self.cog.bot.db.delete_reminder(self.reminder_id, interaction.user.id)
        self.disable_all()
        embed = success_embed("已取消提醒。") if removed else make_embed("這個提醒已經送出或取消了。", color=Color.MUTED)
        await interaction.response.edit_message(embed=embed, view=self)


class ReminderListView(BaseView):
    def __init__(self, cog: Reminders, owner_id: int, rows):
        super().__init__(owner_id=owner_id, timeout=300)
        self.cog = cog
        select = discord.ui.Select(
            placeholder="選擇要取消的提醒（可複選）", min_values=1, max_values=min(len(rows), 25),
            options=[discord.SelectOption(label=f"#{r['id']} {r['content'][:80]}", value=str(r["id"]),
                                          description=f"{fmt_duration(max(0, r['due_at'] - time.time()))}後") for r in rows[:25]],
        )
        select.callback = self.on_select
        self.add_item(select)

    async def on_select(self, interaction: discord.Interaction):
        values = interaction.data.get("values", [])
        removed = 0
        for value in values:
            removed += await self.cog.bot.db.delete_reminder(int(value), interaction.user.id)
        self.disable_all()
        await interaction.response.edit_message(embed=success_embed(f"已取消 {removed} 個提醒。"), view=self)


async def setup(bot):
    await bot.add_cog(Reminders(bot))
