"""賺錢：打工、聊天獎勵、每日任務。"""
import logging
import random
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands

from core.quests import ALL_DONE_BONUS, ALL_DONE_KEY, quests_for
from core.timeutil import next_midnight_tw, today_tw, ts
from core.features import enabled
from core.ui import BaseView, Color, defer, fmt_points, make_embed, progress_bar, respond

log = logging.getLogger(__name__)

WORK_MIN_HOURS, WORK_MAX_HOURS = 1, 8
WORK_DAILY_HOURS = 12     # 每天（台灣時間）最多打工幾小時
WORK_CRIT_CHANCE = 0.1
CHAT_REWARD = 500
CHAT_COOLDOWN = 60        # 聊天獎勵：每分鐘最多一次
CHAT_DAILY_CAP = 20_000
CHAT_MIN_LENGTH = 5


@dataclass(frozen=True)
class Job:
    emoji: str
    text: str
    hours: Tuple[int, int]      # 這份工作可以排幾小時
    hourly: Tuple[int, int]     # 時薪範圍（新台幣）；選幾小時都一樣，平均每小時約 2,000 元


# 每個時數都有好幾種工作可以抽，各時數的平均時薪都在 2,000 元上下（測試會檢查），選長選短不吃虧
JOBS = [
    Job("🐕", "幫鄰居遛狗", (1, 2), (1_500, 2_300)),
    Job("📚", "當家教教國中數學", (1, 3), (1_800, 2_600)),
    Job("💻", "接案幫人修電腦", (1, 4), (1_400, 2_800)),
    Job("🎨", "幫咖啡廳畫黑板菜單", (2, 4), (1_500, 2_700)),
    Job("🛵", "冒雨跑外送", (2, 6), (1_200, 2_600)),
    Job("🎤", "在婚宴上當主持人", (3, 5), (2_000, 2_600)),
    Job("🍢", "在夜市幫忙顧攤", (4, 6), (1_700, 2_100)),
    Job("🧋", "在手搖飲店搖了一百杯珍奶", (4, 8), (1_800, 2_100)),
    Job("🏪", "在便利商店值大夜班", (6, 8), (1_900, 2_200)),
    Job("📦", "在物流倉庫理貨", (6, 8), (1_850, 2_250)),
]


def jobs_for(hours: int) -> List[Job]:
    return [job for job in JOBS if job.hours[0] <= hours <= job.hours[1]]


def work_pay(job: Job, hours: int, crit: bool) -> int:
    reward = random.randint(*job.hourly) * hours // 10 * 10
    return reward * 2 if crit else reward


WORK_HOUR_CHOICES = (1, 2, 4, 8)


class WorkHoursView(BaseView):
    """選打工時數的按鈕（私密訊息）；按下後在頻道公布打工結果，這則就收起來。"""

    def __init__(self, cog: "Earn", origin: discord.Interaction, left: int):
        super().__init__(owner_id=origin.user.id, timeout=120)
        self.cog, self.origin = cog, origin
        for hours in WORK_HOUR_CHOICES:
            style = discord.ButtonStyle.success if hours == WORK_MIN_HOURS else discord.ButtonStyle.secondary
            button = discord.ui.Button(label=f"{hours} 小時", emoji="💼", style=style, disabled=hours > left)
            button.callback = self._make_callback(hours)
            self.add_item(button)
        if left < WORK_MAX_HOURS and left not in WORK_HOUR_CHOICES:
            button = discord.ui.Button(label=f"剩下的 {left} 小時", emoji="⏳", style=discord.ButtonStyle.primary)
            button.callback = self._make_callback(left)
            self.add_item(button)

    def _make_callback(self, hours: int):
        async def callback(interaction: discord.Interaction):
            self.stop()
            await self.cog.do_work(interaction, hours)
            try:
                await self.origin.edit_original_response(
                    embed=make_embed("💼 已開始打工", f"選了 {hours} 小時，結果公布在頻道裡。", Color.MUTED), view=None)
            except discord.HTTPException:
                pass
        return callback


class Earn(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.chat_reward_at: Dict[int, float] = {}

    @property
    def db(self):
        return self.bot.db

    # ------------------------------------------------------------------ 打工
    async def _work_blocked(self, interaction: discord.Interaction, now: float) -> Optional[int]:
        """還在上班或今天時數滿了就回覆說明並回傳 None；可以打工就回傳 (今天已打工時數)。"""
        until, used, _ = await self.db.income_status(interaction.user.id, today_tw())
        if until and now < until:
            embed = make_embed("😮‍💨 還在上班", f"這次的班還沒下班，{ts(until, 'R')}才能再打工。", Color.MUTED)
        elif used >= WORK_DAILY_HOURS:
            embed = make_embed("😴 今天做夠了", f"今天已經打工 {WORK_DAILY_HOURS} 小時，"
                                             f"{ts(next_midnight_tw().timestamp(), 'R')}再來吧。", Color.MUTED)
        else:
            return used
        await respond(interaction, embed=embed, ephemeral=True)
        return None

    async def ask_work_hours(self, interaction: discord.Interaction) -> None:
        """大廳、嘎哇的「打工」按鈕：先問要打工幾小時。"""
        used = await self._work_blocked(interaction, time.time())
        if used is None:
            return
        left = WORK_DAILY_HOURS - used
        embed = make_embed("💼 要打工幾小時？", f"開始打工就發薪，做完才能再打工。今天還可以打工 **{left} 小時**。"
                                              f"\n（也可以用 `/work 小時` 選 {WORK_MIN_HOURS}～{WORK_MAX_HOURS} 小時）",
                           Color.PRIMARY)
        view = WorkHoursView(self, interaction, left)
        await respond(interaction, embed=embed, view=view, ephemeral=True)

    async def do_work(self, interaction: discord.Interaction, hours: int = WORK_MIN_HOURS) -> None:
        """開始打工就發薪，之後要等選的時數過了才能再打工；每天最多 WORK_DAILY_HOURS 小時。"""
        now, today = time.time(), today_tw()
        hours = max(WORK_MIN_HOURS, min(WORK_MAX_HOURS, hours))
        used = await self._work_blocked(interaction, now)
        if used is None:
            return
        left = WORK_DAILY_HOURS - used
        shortened = hours > left
        hours = min(hours, left)

        job = random.choice(jobs_for(hours))
        crit = random.random() < WORK_CRIT_CHANCE
        reward = work_pay(job, hours, crit)
        ok, next_at, balance = await self.db.try_work(interaction.user.id, now, hours * 3600, reward,
                                                      f"打工：{job.text}（{hours} 小時）", day=today, hours=hours,
                                                      daily_cap=WORK_DAILY_HOURS)
        if not ok:  # 同時按了兩次
            embed = make_embed("😮‍💨 還在上班", "這次的班還沒下班，等一下再來。", Color.MUTED)
            return await respond(interaction, embed=embed, ephemeral=True)

        title = "💼 大成功！老闆加碼發紅包" if crit else "💼 打工完成"
        text = f"{job.emoji} 你{job.text}（{hours} 小時），賺到 **{fmt_points(reward)}**！"
        if shortened:
            text += f"\n今天只剩 {hours} 小時可以打工，幫你排了 {hours} 小時的班。"
        embed = make_embed(title, text, Color.GOLD if crit else Color.SUCCESS)
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        embed.add_field(name="⏱️ 換算時薪", value=fmt_points(reward // hours))
        embed.add_field(name="💰 錢包餘額", value=fmt_points(balance))
        embed.add_field(name="⏭️ 下次打工", value=ts(next_at, "R"))
        embed.set_footer(text=f"今天已打工 {used + hours}/{WORK_DAILY_HOURS} 小時・/work 可以選 "
                              f"{WORK_MIN_HOURS}～{WORK_MAX_HOURS} 小時")
        await respond(interaction, embed=embed)
        self.bot.dispatch("activity", interaction.user.id, "work", interaction.channel_id, {"hours": hours})

    @app_commands.command(name="work", description=f"打工賺錢：選 {WORK_MIN_HOURS}～{WORK_MAX_HOURS} 小時，"
                                                   f"做完才能再打工，每天最多 {WORK_DAILY_HOURS} 小時")
    @app_commands.describe(hours=f"要打工幾小時（{WORK_MIN_HOURS}～{WORK_MAX_HOURS}），不填是 1 小時")
    async def work(self, interaction: discord.Interaction,
                   hours: app_commands.Range[int, WORK_MIN_HOURS, WORK_MAX_HOURS] = WORK_MIN_HOURS):
        await self.do_work(interaction, hours)

    # ------------------------------------------------------------------ 每日任務
    async def build_quests(self, user: discord.abc.User) -> discord.Embed:
        today = today_tw()
        quests = quests_for(self.bot, user.id, today)
        progress = await self.db.quest_progress(user.id, today)
        lines, done_count = [], 0
        for quest in quests:
            value, done = progress.get(quest.key, (0, False))
            done_count += done
            mark = "✅" if done else "▫️"
            bar = progress_bar(value / quest.target, 8)
            lines.append(f"{mark} **{quest.title}**　+{quest.reward:,}\n　`{bar}` {value}/{quest.target}")
        bonus_done = progress.get(ALL_DONE_KEY, (0, False))[1]
        lines.append(f"\n{'✅' if bonus_done else '🎁'} **全部完成獎勵**　+{ALL_DONE_BONUS:,}（{done_count}/{len(quests)}）")
        embed = make_embed("🎯 每日任務", "\n".join(lines), Color.PRIMARY)
        embed.set_author(name=user.display_name, icon_url=user.display_avatar.url)
        embed.set_footer(text="完成後自動發放獎勵・每天台灣時間 00:00 換新任務")
        embed.add_field(name="⏭️ 換新任務", value=ts(next_midnight_tw(), "R"))
        return embed

    async def send_quests(self, interaction: discord.Interaction) -> None:
        await defer(interaction, ephemeral=True)
        await respond(interaction, embed=await self.build_quests(interaction.user), ephemeral=True)

    @app_commands.command(name="quests", description="查看今天的每日任務與進度")
    async def quests(self, interaction: discord.Interaction):
        await self.send_quests(interaction)

    @commands.Cog.listener()
    async def on_activity(self, user_id: int, kind: str, channel_id: Optional[int], meta: dict):
        if user_id <= 0:   # 撲克電腦玩家
            return
        today = today_tw()
        quests = quests_for(self.bot, user_id, today)
        finished = []
        for quest in quests:
            if quest.matches(kind, meta):
                if await self.db.advance_quest(user_id, today, quest.key, quest.amount(meta), quest.target,
                                               quest.reward, f"每日任務：{quest.title}"):
                    finished.append(f"「{quest.title}」+{quest.reward:,}")
        if not finished:
            return
        progress = await self.db.quest_progress(user_id, today)
        if all(progress.get(q.key, (0, False))[1] for q in quests):
            if await self.db.advance_quest(user_id, today, ALL_DONE_KEY, 1, 1, ALL_DONE_BONUS, "每日任務全部完成"):
                finished.append(f"🎁 全部完成 +{ALL_DONE_BONUS:,}")
        channel = self.bot.get_channel(channel_id) if channel_id else None
        if channel:
            try:
                await channel.send(f"🎯 <@{user_id}> 完成每日任務：{'、'.join(finished)}",
                                   allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                pass

    # ------------------------------------------------------------------ 聊天獎勵
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        if len(message.content.strip()) < CHAT_MIN_LENGTH:
            return
        if not enabled(self.bot, "economy", message.guild.id):
            return
        now, user_id = time.time(), message.author.id
        if now - self.chat_reward_at.get(user_id, 0) >= CHAT_COOLDOWN:
            self.chat_reward_at[user_id] = now
            await self.db.add_chat_reward(user_id, today_tw(), CHAT_REWARD, CHAT_DAILY_CAP)
            await self.db.touch_member(message.guild.id, user_id)
            await self.db.remember_user(user_id, message.author.display_name, message.author.display_avatar.url)


async def setup(bot):
    await bot.add_cog(Earn(bot))
