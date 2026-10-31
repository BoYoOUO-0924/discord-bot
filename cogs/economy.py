"""積分系統：簽到、個人資料、排行榜、轉帳。"""
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from cogs.earn import CHAT_DAILY_CAP, WORK_COOLDOWN
from core.catalog import GAMES_BY_KEY
from core.db import checkin_reward
from core.timeutil import next_midnight_tw, today_tw, ts
from core.ui import BaseView, Color, defer, fmt_points, fmt_signed, make_embed, respond, send_error

MEDALS = ["🥇", "🥈", "🥉"]


class Economy(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @property
    def db(self):
        return self.bot.db

    # ------------------------------------------------------------------ 簽到
    async def do_checkin(self, interaction: discord.Interaction) -> None:
        # 先回「思考中」：舊電腦寫資料庫偶爾會超過 Discord 的 3 秒限制（Unknown interaction）
        await defer(interaction)
        result = await self.db.checkin(interaction.user.id, today_tw())
        next_reset = ts(next_midnight_tw(), "R")

        if result.already:
            embed = make_embed("📅 今天已經簽到過了", color=Color.MUTED)
            embed.add_field(name="🔥 連續簽到", value=f"{result.streak} 天")
            embed.add_field(name="⏭️ 下次簽到", value=next_reset)
            # 已簽到只需要讓本人知道：收掉公開的「思考中」，改用私密訊息回覆
            try:
                await interaction.delete_original_response()
            except discord.HTTPException:
                pass
            return await respond(interaction, embed=embed, ephemeral=True)

        parts = [f"基礎 +{result.base}"]
        if result.first_bonus:
            parts.append(f"首次簽到 +{result.first_bonus}")
        if result.streak_bonus:
            parts.append(f"連續加成 +{result.streak_bonus}")
        tomorrow = sum(checkin_reward(result.streak + 1, first_time=False))

        embed = make_embed("✨ 簽到成功！", f"## +{result.gained:,} 分\n" + "・".join(parts), Color.SUCCESS)
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        streak_text = f"{result.streak} 天"
        if result.streak_broken:
            streak_text += "（中斷後重新開始）"
        embed.add_field(name="🔥 連續簽到", value=streak_text)
        embed.add_field(name="💰 目前積分", value=fmt_points(result.balance))
        embed.add_field(name="⏭️ 明天再簽", value=f"+{tomorrow:,} 分")
        embed.set_footer(text="每天台灣時間 00:00 換日，斷簽會重新計算連續天數")
        await respond(interaction, embed=embed)

    @app_commands.command(name="checkin", description="每日簽到領積分，連續簽到加成越來越多")
    async def checkin(self, interaction: discord.Interaction):
        await self.do_checkin(interaction)

    # ------------------------------------------------------------------ 個人資料
    async def build_profile(self, member: discord.abc.User, guild: Optional[discord.Guild]) -> discord.Embed:
        today = today_tw()
        profile = await self.db.get_profile(member.id, today)
        rank = await self.db.points_rank(guild.id, member.id) if guild else None

        embed = make_embed(f"👤 {member.display_name}", color=Color.PRIMARY)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="💰 積分", value=fmt_points(profile.points))
        embed.add_field(name="🏆 伺服器排名", value=f"第 {rank} 名" if rank else "—")
        checked_today = profile.last_checkin == today
        embed.add_field(name="📅 今日簽到", value="✅ 已簽到" if checked_today else "❗ 尚未簽到")
        embed.add_field(name="🔥 連續簽到", value=f"{profile.streak} 天")
        embed.add_field(name="🗓️ 累計簽到", value=f"{profile.total_checkins} 天")
        last_work, chat_earned = await self.db.income_status(member.id, today)
        if last_work and time.time() - last_work < WORK_COOLDOWN:
            work_text = f"{ts(last_work + WORK_COOLDOWN, 'R')}可打工"
        else:
            work_text = "現在可以打工"
        embed.add_field(name="💼 今日收入", value=f"{work_text}\n聊天獎勵 {chat_earned}/{CHAT_DAILY_CAP}")

        stats = await self.db.game_stats(member.id)
        if stats:
            lines = []
            for row in stats:
                info = GAMES_BY_KEY.get(row["game"])
                name = f"{info.emoji} {info.name}" if info else row["game"]
                rate = row["won"] / row["played"] * 100 if row["played"] else 0
                line = f"{name}：{row['played']} 場・勝率 {rate:.0f}%"
                if row["net"]:
                    line += f"・損益 {fmt_signed(row['net'])}"
                lines.append(line)
            embed.add_field(name="🎮 遊戲戰績", value="\n".join(lines), inline=False)

        recent = await self.db.recent_transactions(member.id)
        if recent:
            lines = []
            for row in recent:
                amount = f"`{fmt_signed(row['amount']):>7}`" if row["amount"] else "`     ±0`"
                lines.append(f"{amount} {row['reason']}・{ts(row['created_at'], 'R')}")
            embed.add_field(name="🧾 最近紀錄", value="\n".join(lines), inline=False)
        return embed

    async def send_profile(self, interaction: discord.Interaction, member: Optional[discord.abc.User] = None) -> None:
        member = member or interaction.user
        if member.bot:
            return await send_error(interaction, "機器人沒有個人資料喔。")
        await defer(interaction, ephemeral=True)   # 要查好幾種資料，先回應避免超過 3 秒
        embed = await self.build_profile(member, interaction.guild)
        view = None
        if member.id == interaction.user.id:
            profile = await self.db.get_profile(member.id, today_tw())
            if profile.last_checkin != today_tw():
                view = QuickCheckinView(self, member.id)
        await respond(interaction, embed=embed, view=view or discord.utils.MISSING, ephemeral=True)

    @app_commands.command(name="profile", description="查看積分、排名、簽到紀錄與遊戲戰績")
    @app_commands.describe(user="要查看的人，不填就是自己")
    async def profile(self, interaction: discord.Interaction, user: Optional[discord.User] = None):
        await self.send_profile(interaction, user)

    # ------------------------------------------------------------------ 排行榜
    async def build_leaderboard(self, guild: discord.Guild, viewer_id: int, by: str) -> discord.Embed:
        today = today_tw()
        rows = await self.db.leaderboard(guild.id, by, today)
        title = "🏆 積分排行榜" if by == "points" else "🔥 連續簽到排行榜"
        embed = make_embed(title, color=Color.GOLD)
        if not rows:
            embed.description = "目前還沒有人上榜，快用 `/checkin` 簽到吧！"
            return embed
        lines = []
        for i, (user_id, value) in enumerate(rows):
            badge = MEDALS[i] if i < 3 else f"`{i + 1:>2}.`"
            value_text = fmt_points(value) if by == "points" else f"{value} 天"
            me = " ← 你" if user_id == viewer_id else ""
            lines.append(f"{badge} <@{user_id}>　**{value_text}**{me}")
        embed.description = "\n".join(lines)
        if by == "points" and viewer_id not in [uid for uid, _ in rows]:
            rank = await self.db.points_rank(guild.id, viewer_id)
            points = await self.db.get_points(viewer_id)
            embed.set_footer(text=f"你目前第 {rank} 名・{points:,} 分" if rank else "你還沒有積分，簽到就能上榜")
        return embed

    async def send_leaderboard(self, interaction: discord.Interaction, *, ephemeral: bool = False) -> None:
        if not interaction.guild:
            return await send_error(interaction, "排行榜只能在伺服器裡查看。")
        await defer(interaction, ephemeral=ephemeral)
        embed = await self.build_leaderboard(interaction.guild, interaction.user.id, "points")
        view = LeaderboardView(self)
        view.message = await respond(interaction, embed=embed, view=view, ephemeral=ephemeral)

    @app_commands.command(name="leaderboard", description="伺服器積分與連續簽到排行榜")
    @app_commands.guild_only()
    async def leaderboard(self, interaction: discord.Interaction):
        await self.send_leaderboard(interaction)

    # ------------------------------------------------------------------ 轉帳
    @app_commands.command(name="give", description="把積分轉給其他人")
    @app_commands.describe(user="要轉給誰", amount="轉出的積分")
    @app_commands.guild_only()
    async def give(self, interaction: discord.Interaction, user: discord.Member,
                   amount: app_commands.Range[int, 1, 10_000_000]):
        if user.bot or user.id == interaction.user.id:
            return await send_error(interaction, "不能轉給自己或機器人。")
        balance = await self.db.transfer(interaction.user.id, user.id, amount)
        if balance is None:
            current = await self.db.get_points(interaction.user.id)
            return await send_error(interaction, f"積分不足，你目前有 {fmt_points(current)}。")
        embed = make_embed("💸 轉帳成功", f"{interaction.user.mention} 轉了 **{fmt_points(amount)}** 給 {user.mention}",
                           Color.SUCCESS)
        embed.set_footer(text=f"你的餘額：{balance:,} 分")
        await interaction.response.send_message(embed=embed)


class QuickCheckinView(BaseView):
    def __init__(self, cog: Economy, owner_id: int):
        super().__init__(owner_id=owner_id, timeout=300)
        self.cog = cog

    @discord.ui.button(label="現在簽到", emoji="✨", style=discord.ButtonStyle.success)
    async def checkin(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.do_checkin(interaction)


class LeaderboardView(BaseView):
    def __init__(self, cog: Economy):
        super().__init__(timeout=300)
        self.cog = cog

    async def _switch(self, interaction: discord.Interaction, by: str):
        # 每個人按都會看到包含自己排名的版本
        await defer(interaction, ephemeral=True)
        embed = await self.cog.build_leaderboard(interaction.guild, interaction.user.id, by)
        await respond(interaction, embed=embed, ephemeral=True)

    @discord.ui.button(label="積分", emoji="💰", style=discord.ButtonStyle.secondary)
    async def by_points(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._switch(interaction, "points")

    @discord.ui.button(label="連續簽到", emoji="🔥", style=discord.ButtonStyle.secondary)
    async def by_streak(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._switch(interaction, "streak")


async def setup(bot):
    await bot.add_cog(Economy(bot))
