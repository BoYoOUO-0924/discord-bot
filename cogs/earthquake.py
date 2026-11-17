"""地震報告：中央氣象署發布新的地震報告時，通知到頻道。

- 各伺服器：管理員用 /settings earthquake 選頻道和震度門檻；看的是伺服器天氣地點所在的縣市
  （沒設定就是台北、新北），那些縣市震度達到門檻（預設 4 級）才通知。
- 舊版 Webhook（選用）：.env 有 EARTHQUAKE_WEBHOOK_URL 或 WEATHER_WEBHOOK_URL 時，台北震度 4 級以上也會傳到那裡。
- 已處理過的報告記在 data/earthquake_seen.txt，重啟不會重複；第一次啟動只記錄、不補送舊報告。
- /地震 查最近 7 天你們縣市有沒有達到門檻的地震。
- 這是地震報告（地震後幾分鐘發布），不是搖晃前的強震即時警報。
"""
import logging
import os
import time
from datetime import datetime, timedelta
from typing import List, Optional, Sequence, Set, Tuple

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from cogs.weather import guild_counties
from core import earthquake as eq
from core.timeutil import now_tw
from core.features import enabled
from core.ui import Color, defer, make_embed, respond, send_error, success_embed

log = logging.getLogger(__name__)

MAX_AGE = timedelta(hours=1)        # 筆電離線很久後，太舊的報告就不補送了
ALERT_COUNTY = "臺北市"
ALERT_LEVEL = "4"                   # 預設門檻；舊版 Webhook 固定看台北這個等級
LEVEL_CHOICES = ("3", "4", "5弱", "5強", "6弱")
RECENT_DAYS = 7                     # /地震 查詢多久以內的大地震
GREETING = "即使大地偶有震顫，也希望你所在的地方始終安穩。"
ERROR_LOG_EVERY = 30 * 60           # 一直連不上時，多久記一次警告
KEEP_SEEN = 200
WEEKDAYS = "一二三四五六日"

COLORS = {"綠": 0x2ECC71, "黃": 0xF1C40F, "橘": 0xE67E22, "紅": 0xE74C3C}
LOCAL_NAMES = {"臺北市": "台北", "新北市": "新北"}


def county_name(county: str) -> str:
    return LOCAL_NAMES.get(county, county.replace("臺", "台"))


def report_color(report: eq.Report) -> int:
    for name, color in COLORS.items():
        if name in report.color:
            return color
    rank = eq.intensity_rank(report.max_intensity)
    return COLORS["紅"] if rank >= 6 else COLORS["橘"] if rank >= 5 else COLORS["黃"] if rank >= 4 else COLORS["綠"]


def fmt_time(dt: Optional[datetime]) -> str:
    if not dt:
        return "—"
    return f"{dt.month}/{dt.day}（{WEEKDAYS[dt.weekday()]}）{dt:%H:%M:%S}"


def local_summary(report: eq.Report, counties: Sequence[str] = eq.LOCAL_COUNTIES) -> str:
    parts = [f"{county_name(c)} {eq.intensity_text(report.local[c])}" for c in counties if c in report.local]
    return "、".join(parts) if parts else "無感"


def report_embed(report: eq.Report, counties: Sequence[str] = eq.LOCAL_COUNTIES) -> discord.Embed:
    magnitude = f"規模 {report.magnitude:.1f}" if report.magnitude is not None else "規模不明"
    embed = discord.Embed(title=f"🌏 地震報告｜{eq.short_location(report.location)} {magnitude}",
                          description=report.content or None, color=report_color(report), url=report.web_url)
    embed.add_field(name="🕒 發生時間", value=fmt_time(report.origin_time), inline=True)
    embed.add_field(name="📈 規模", value=f"{report.magnitude:.1f}" if report.magnitude is not None else "—", inline=True)
    embed.add_field(name="📏 深度", value=f"{report.depth:g} 公里" if report.depth is not None else "—", inline=True)
    embed.add_field(name="📍 震央", value=report.location, inline=False)
    embed.add_field(name="💥 最大震度", value=eq.intensity_text(report.max_intensity), inline=True)
    embed.add_field(name="🏠 " + "／".join(county_name(c) for c in counties), value=local_summary(report, counties),
                    inline=True)
    if report.image_url:
        embed.set_image(url=report.image_url)
    kind = f"第 {report.number} 號顯著有感地震" if report.significant and report.number else (
        "顯著有感地震" if report.significant else "小區域有感地震")
    embed.set_footer(text=f"{kind}・資料來源：中央氣象署")
    return embed


def reaches(report: eq.Report, counties: Sequence[str], level: str) -> bool:
    """這些縣市裡有任何一個震度達到門檻。"""
    return report.rank_among(counties) >= eq.intensity_rank(level)


def is_big(report: eq.Report) -> bool:
    """舊版 Webhook 的門檻：台北震度 4 級以上。"""
    return reaches(report, [ALERT_COUNTY], ALERT_LEVEL)


def alert_text(report: eq.Report, counties: Sequence[str] = eq.LOCAL_COUNTIES) -> str:
    return f"{GREETING}\n-# 🌏 {local_summary(report, counties)}"


async def guild_prefs(db, guild_id: Optional[int]) -> Tuple[List[str], str]:
    """(要看的縣市, 震度門檻)。縣市跟著天氣地點；沒設定就是台北、新北、4 級。"""
    if not guild_id or db is None:
        return list(eq.LOCAL_COUNTIES), ALERT_LEVEL
    level = (await db.get_guild_settings(guild_id)).get("earthquake.level") or ALERT_LEVEL
    return await guild_counties(db, guild_id), level


class Earthquake(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        settings = bot.settings
        self.api_key = settings.cwa_api_key
        self.webhook_url = getattr(settings, "earthquake_webhook_url", None) or settings.weather_webhook_url
        self.state_path = os.path.join(os.path.dirname(settings.database_path), "earthquake_seen.txt")
        self.failing_since: Optional[float] = None
        self.last_error_log = 0.0
        self.guild_sent: Set[Tuple[int, str]] = set()   # 舊版 Webhook 重試時，伺服器不要重複收到

    async def cog_load(self):
        if not self.api_key:
            log.info("地震報告通知未啟用：.env 裡沒有讀到 CWA_API_KEY")
            return
        log.info("地震報告通知已啟用%s", "（含舊版 Webhook）" if self.webhook_url else "")
        self.poll_loop.start()

    async def cog_unload(self):
        self.poll_loop.cancel()

    # ------------------------------------------------------------------ 傳送紀錄
    def seen(self) -> Optional[Set[str]]:
        """已處理過的報告；還沒有紀錄檔（第一次啟動）時回傳 None。"""
        try:
            with open(self.state_path, encoding="utf-8") as f:
                return {line.strip() for line in f if line.strip()}
        except FileNotFoundError:
            return None
        except OSError:
            log.warning("無法讀取地震報告紀錄", exc_info=True)
            return set()

    def mark_seen(self, keys: List[str]) -> None:
        try:
            with open(self.state_path, encoding="utf-8") as f:
                old = [line.strip() for line in f if line.strip()]
        except OSError:
            old = []
        merged = [k for k in old if k not in keys] + list(keys)
        try:
            with open(self.state_path, "w", encoding="utf-8") as f:
                f.write("\n".join(merged[-KEEP_SEEN:]) + "\n")
        except OSError:
            log.warning("無法寫入地震報告紀錄", exc_info=True)

    @staticmethod
    def fresh(report: eq.Report, now: datetime) -> bool:
        return not (report.origin_time and now - report.origin_time > MAX_AGE)

    def worth_sending(self, report: eq.Report, now: datetime) -> bool:
        """舊版 Webhook 要不要傳。"""
        return bool(self.webhook_url) and self.fresh(report, now) and is_big(report)

    # ------------------------------------------------------------------ 傳送
    async def send(self, report: eq.Report) -> None:
        """舊版：用 Webhook 傳送（只看台北）。"""
        if not enabled(self.bot, "earthquake"):
            return   # 管理後台把地震報告全域關掉了（照樣讀取，重新打開時不會補發一堆舊的）
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            webhook = discord.Webhook.from_url(self.webhook_url, session=session)
            await webhook.send(content=alert_text(report), embed=report_embed(report),
                               allowed_mentions=discord.AllowedMentions.none())

    async def send_to_channel(self, channel_id: int, report: eq.Report, counties: Sequence[str]) -> None:
        channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
        await channel.send(content=alert_text(report, counties), embed=report_embed(report, counties),
                           allowed_mentions=discord.AllowedMentions.none())

    async def send_guilds(self, report: eq.Report) -> int:
        """傳給設定了 /settings earthquake、而且縣市震度達到門檻的伺服器。"""
        db = getattr(self.bot, "db", None)
        if db is None:
            return 0
        count = 0
        for guild_id, channel_id in (await db.guilds_with_setting("earthquake.channel")).items():
            if (guild_id, report.key) in self.guild_sent or not enabled(self.bot, "earthquake", guild_id):
                continue
            counties, level = await guild_prefs(db, guild_id)
            if not reaches(report, counties, level):
                continue
            self.guild_sent.add((guild_id, report.key))
            try:
                await self.send_to_channel(int(channel_id), report, counties)
                count += 1
            except (discord.HTTPException, aiohttp.ClientError, TimeoutError) as e:
                log.warning("伺服器 %s 的地震通報傳送失敗：%s", guild_id, e)
        return count

    @tasks.loop(minutes=1)
    async def poll_loop(self):
        try:
            reports = await eq.fetch_reports(self.api_key)
        except (eq.WeatherError, aiohttp.ClientError, TimeoutError, ValueError) as e:
            now = time.time()
            if self.failing_since is None:
                self.failing_since = now
            if now - self.last_error_log >= ERROR_LOG_EVERY:
                self.last_error_log = now
                log.warning("地震報告抓取失敗（之後每 %d 分鐘提醒一次）：%s", ERROR_LOG_EVERY // 60, e)
            return
        if self.failing_since is not None:
            log.info("地震報告恢復連線")
            self.failing_since, self.last_error_log = None, 0.0

        await self.handle(reports, now_tw())

    async def handle(self, reports: List[eq.Report], now: datetime) -> None:
        """處理沒看過的報告（舊到新）。第一次啟動時只記錄現有報告，不補送。"""
        seen = self.seen()
        if seen is None:
            self.mark_seen([r.key for r in reports] or ["(start)"])
            return
        for report in reversed([r for r in reports if r.key not in seen]):
            if self.fresh(report, now):
                sent = await self.send_guilds(report)
                if sent:
                    log.info("已通報地震到 %d 個伺服器：%s %s", sent, report.origin_time, report.location)
            if self.worth_sending(report, now):
                try:
                    await self.send(report)
                except (aiohttp.ClientError, discord.HTTPException, TimeoutError) as e:
                    log.warning("地震報告傳送失敗，下一分鐘重試：%s", e)
                    return   # 沒有記錄成已處理，下一輪會再試
                log.info("已傳送地震報告：%s %s", report.origin_time, report.location)
            self.mark_seen([report.key])   # 不需要傳送的（例如沒感覺的小區域地震）也記下來

    @poll_loop.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------------ 指令
    @app_commands.command(name="earthquake", description="查看最近有沒有影響你們縣市的地震")
    @app_commands.describe(send="管理員：把最新的一筆報告傳到通報頻道（測試用，不論震度）")
    async def earthquake_command(self, interaction: discord.Interaction, send: bool = False):
        if not self.api_key:
            return await send_error(interaction, "還沒有設定中央氣象署 API 金鑰（CWA_API_KEY）。")
        guild_id = getattr(interaction, "guild_id", None)
        db = getattr(self.bot, "db", None)
        channel_id = None
        if send:
            perms = getattr(interaction.user, "guild_permissions", None)
            if not perms or not perms.manage_guild:
                return await send_error(interaction, "只有伺服器管理員可以傳送地震報告。")
            if guild_id and db is not None:
                channel_id = (await db.get_guild_settings(guild_id)).get("earthquake.channel")
            if not channel_id and not self.webhook_url:
                return await send_error(interaction, "還沒有設定通報頻道，請先用 /settings earthquake 選頻道。")
        counties, level = await guild_prefs(db, guild_id)
        await defer(interaction, ephemeral=True)
        try:
            reports = await eq.fetch_reports(self.api_key, limit=20)
            if not reports and send:
                return await send_error(interaction, "目前沒有地震報告。")
            if send:
                if channel_id:
                    await self.send_to_channel(int(channel_id), reports[0], counties)
                else:
                    await self.send(reports[0])
        except (eq.WeatherError, aiohttp.ClientError, discord.HTTPException, TimeoutError, ValueError) as e:
            log.warning("查詢地震報告失敗：%s", e)
            return await send_error(interaction, f"{'傳送' if send else '查詢'}地震報告失敗：{e}")
        if send:
            await respond(interaction, embed=success_embed("已經把最新的地震報告傳到通報頻道了，去看看吧！"),
                          ephemeral=True)
            return
        now = now_tw()
        big = [r for r in reports if reaches(r, counties, level)
               and (not r.origin_time or now - r.origin_time <= timedelta(days=RECENT_DAYS))]
        if big:
            return await respond(interaction, content=alert_text(big[0], counties),
                                 embed=report_embed(big[0], counties), ephemeral=True)
        places = "、".join(county_name(c) for c in counties)
        embed = make_embed("🌿 沒有大地震",
                           f"最近 {RECENT_DAYS} 天{places}沒有震度 {eq.intensity_text(level)}以上的地震。", Color.SUCCESS)
        if reports:
            latest = reports[0]
            magnitude = f"規模 {latest.magnitude:.1f}" if latest.magnitude is not None else "規模不明"
            embed.add_field(name="最新一筆地震報告", value=f"{fmt_time(latest.origin_time)}　"
                            f"{eq.short_location(latest.location)} {magnitude}・{local_summary(latest, counties)}")
        await respond(interaction, embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Earthquake(bot))
