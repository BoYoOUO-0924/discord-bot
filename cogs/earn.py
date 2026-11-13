"""賺錢：打工、聊天獎勵、每日任務。"""
import logging
import random
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands

from core.quests import ALL_DONE_BONUS, ALL_DONE_KEY, daily_quests
from core.timeutil import next_midnight_tw, today_tw, ts
from core.features import enabled
from core.ui import Color, defer, fmt_points, make_embed, progress_bar, respond

log = logging.getLogger(__name__)

WORK_COOLDOWN = 3600
WORK_CRIT_CHANCE = 0.1
CHAT_REWARD = 500
CHAT_COOLDOWN = 60        # 聊天獎勵：每分鐘最多一次
CHAT_QUEST_COOLDOWN = 10  # 聊天任務：10 秒內的訊息只算一則，避免洗版刷任務
CHAT_DAILY_CAP = 20_000
CHAT_MIN_LENGTH = 5


@dataclass(frozen=True)
class Job:
    emoji: str
    text: str
    hours: int
    pay: Tuple[int, int]   # 這次工作的薪水範圍（新台幣），參考台灣實際行情；基本時薪約 196 元


JOBS = [
    Job("🏪", "在便利商店值大夜班", 8, (1_570, 1_900)),     # 基本時薪＋夜班津貼
    Job("🛵", "冒雨跑外送", 4, (900, 2_200)),               # 一單 40–80 元，雨天有加成
    Job("📚", "當家教教國中數學", 2, (1_000, 1_600)),       # 時薪 500–800
    Job("🍢", "在夜市幫忙顧攤", 5, (1_000, 1_250)),
    Job("🧋", "在手搖飲店搖了一百杯珍奶", 6, (1_180, 1_300)),
    Job("💻", "接案幫人修電腦", 3, (1_500, 3_000)),
    Job("🎤", "在婚宴上當主持人", 4, (5_000, 8_000)),
    Job("🐕", "幫鄰居遛狗", 1, (200, 400)),
    Job("📦", "在物流倉庫理貨", 8, (1_600, 2_000)),
    Job("🎨", "幫咖啡廳畫黑板菜單", 3, (1_500, 3_000)),
]


class Earn(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.chat_reward_at: Dict[int, float] = {}
        self.chat_quest_at: Dict[int, float] = {}

    @property
    def db(self):
        return self.bot.db

    # ------------------------------------------------------------------ 打工
    async def do_work(self, interaction: discord.Interaction) -> None:
        now = time.time()
        job = random.choice(JOBS)
        reward = random.randint(*job.pay) // 10 * 10
        crit = random.random() < WORK_CRIT_CHANCE
        if crit:
            reward *= 2
        ok, next_at, balance = await self.db.try_work(interaction.user.id, now, WORK_COOLDOWN, reward,
                                                      f"打工：{job.text}")
        if not ok:
            embed = make_embed("😮‍💨 還在休息", f"剛下班，{ts(next_at, 'R')}才能再打工。", Color.MUTED)
            return await respond(interaction, embed=embed, ephemeral=True)

        title = "💼 大成功！老闆加碼發紅包" if crit else "💼 打工完成"
        embed = make_embed(title, f"{job.emoji} 你{job.text}（{job.hours} 小時），賺到 **{fmt_points(reward)}**！",
                           Color.GOLD if crit else Color.SUCCESS)
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        embed.add_field(name="⏱️ 換算時薪", value=fmt_points(reward // job.hours))
        embed.add_field(name="💰 錢包餘額", value=fmt_points(balance))
        embed.add_field(name="⏭️ 下次打工", value=ts(next_at, "R"))
        await respond(interaction, embed=embed)
        self.bot.dispatch("activity", interaction.user.id, "work", interaction.channel_id, {})

    @app_commands.command(name="work", description="打工賺錢，薪水依真實行情，每小時可以做一次")
    async def work(self, interaction: discord.Interaction):
        await self.do_work(interaction)

    # ------------------------------------------------------------------ 每日任務
    async def build_quests(self, user: discord.abc.User) -> discord.Embed:
        today = today_tw()
        quests = daily_quests(user.id, today, self.bot.ai.configured)
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
        quests = daily_quests(user_id, today, self.bot.ai.configured)
        finished = []
        for quest in quests:
            if quest.matches(kind, meta):
                if await self.db.advance_quest(user_id, today, quest.key, 1, quest.target, quest.reward,
                                               f"每日任務：{quest.title}"):
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
        if now - self.chat_quest_at.get(user_id, 0) >= CHAT_QUEST_COOLDOWN:
            self.chat_quest_at[user_id] = now
            self.bot.dispatch("activity", user_id, "chat", message.channel.id, {})


async def setup(bot):
    await bot.add_cog(Earn(bot))
