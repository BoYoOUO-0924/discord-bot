"""地震報告：中央氣象署發布新的地震報告時，用 Webhook 傳送到頻道。

- 只有台北（臺北市）震度 4 級以上才主動傳送；/地震 指令可以查最近有沒有這種大地震。
- 需要 .env 的 CWA_API_KEY，以及 EARTHQUAKE_WEBHOOK_URL（沒設定時用 WEATHER_WEBHOOK_URL）。
- 已傳送過的報告記在 data/earthquake_seen.txt，重啟不會重複傳送；第一次啟動只記錄、不補送舊報告。
- 這是地震報告（地震後幾分鐘發布），不是搖晃前的強震即時警報。
"""
import logging
import os
import time
from datetime import datetime, timedelta
from typing import List, Optional, Set

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from core import earthquake as eq
from core.timeutil import now_tw
from core.features import enabled
from core.ui import Color, defer, make_embed, respond, send_error, success_embed

log = logging.getLogger(__name__)

MAX_AGE = timedelta(hours=1)        # 筆電離線很久後，太舊的報告就不補送了
ALERT_COUNTY = "臺北市"
ALERT_LEVEL = "4"                   # 台北震度達到這個等級才主動通報
RECENT_DAYS = 7                     # /地震 查詢多久以內的大地震
GREETING = "即使大地偶有震顫，也希望你所在的地方始終安穩。"
ERROR_LOG_EVERY = 30 * 60           # 一直連不上時，多久記一次警告
KEEP_SEEN = 200
WEEKDAYS = "一二三四五六日"

COLORS = {"綠": 0x2ECC71, "黃": 0xF1C40F, "橘": 0xE67E22, "紅": 0xE74C3C}
LOCAL_NAMES = {"臺北市": "台北", "新北市": "新北"}


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


def local_summary(report: eq.Report) -> str:
    parts = [f"{LOCAL_NAMES[c]} {eq.intensity_text(report.local[c])}" for c in eq.LOCAL_COUNTIES if c in report.local]
    return "、".join(parts) if parts else "無感"


def report_embed(report: eq.Report) -> discord.Embed:
    magnitude = f"規模 {report.magnitude:.1f}" if report.magnitude is not None else "規模不明"
    embed = discord.Embed(title=f"🌏 地震報告｜{eq.short_location(report.location)} {magnitude}",
                          description=report.content or None, color=report_color(report), url=report.web_url)
    embed.add_field(name="🕒 發生時間", value=fmt_time(report.origin_time), inline=True)
    embed.add_field(name="📈 規模", value=f"{report.magnitude:.1f}" if report.magnitude is not None else "—", inline=True)
    embed.add_field(name="📏 深度", value=f"{report.depth:g} 公里" if report.depth is not None else "—", inline=True)
    embed.add_field(name="📍 震央", value=report.location, inline=False)
    embed.add_field(name="💥 最大震度", value=eq.intensity_text(report.max_intensity), inline=True)
    embed.add_field(name="🏠 台北／新北", value=local_summary(report), inline=True)
    if report.image_url:
        embed.set_image(url=report.image_url)
    kind = f"第 {report.number} 號顯著有感地震" if report.significant and report.number else (
        "顯著有感地震" if report.significant else "小區域有感地震")
    embed.set_footer(text=f"{kind}・資料來源：中央氣象署")
    return embed


def is_big(report: eq.Report) -> bool:
    """台北震度達到通報門檻。"""
    return report.rank_in(ALERT_COUNTY) >= eq.INTENSITY_ORDER.index(ALERT_LEVEL)


def alert_text(report: eq.Report) -> str:
    return f"{GREETING}\n-# 🌏 {local_summary(report)}"


class Earthquake(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        settings = bot.settings
        self.api_key = settings.cwa_api_key
        self.webhook_url = getattr(settings, "earthquake_webhook_url", None) or settings.weather_webhook_url
        self.state_path = os.path.join(os.path.dirname(settings.database_path), "earthquake_seen.txt")
        self.failing_since: Optional[float] = None
        self.last_error_log = 0.0

    async def cog_load(self):
        if not self.api_key or not self.webhook_url:
            missing = [name for name, value in (("CWA_API_KEY", self.api_key),
                                                ("EARTHQUAKE_WEBHOOK_URL 或 WEATHER_WEBHOOK_URL", self.webhook_url))
                       if not value]
            log.info("地震報告通知未啟用：.env 裡沒有讀到 %s", "、".join(missing))
            return
        log.info("地震報告通知已啟用")
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
    def worth_sending(report: eq.Report, now: datetime) -> bool:
        if report.origin_time and now - report.origin_time > MAX_AGE:
            return False
        return is_big(report)

    # ------------------------------------------------------------------ 傳送
    async def send(self, report: eq.Report) -> None:
        if not enabled(self.bot, "earthquake"):
            return   # 管理後台把地震報告全域關掉了（照樣讀取，重新打開時不會補發一堆舊的）
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            webhook = discord.Webhook.from_url(self.webhook_url, session=session)
            await webhook.send(content=alert_text(report), embed=report_embed(report),
                               allowed_mentions=discord.AllowedMentions.none())

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
        """傳送沒處理過的報告（舊到新）。第一次啟動時只記錄現有報告，不補送。"""
        seen = self.seen()
        if seen is None:
            self.mark_seen([r.key for r in reports] or ["(start)"])
            return
        for report in reversed([r for r in reports if r.key not in seen]):
            if self.worth_sending(report, now):
                try:
                    await self.send(report)
                except (aiohttp.ClientError, discord.HTTPException, TimeoutError) as e:
                    log.warning("地震報告傳送失敗，下一分鐘重試：%s", e)
                    return   # 沒有記錄成已處理，下一輪會再試
                log.info("已傳送地震報告：%s %s", report.origin_time, report.location)
            self.mark_seen([report.key])   # 不需要傳送的（例如台北無感的小區域地震）也記下來

    @poll_loop.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------------ 指令
    @app_commands.command(name="earthquake", description="查看中央氣象署最新的地震報告")
    @app_commands.describe(send="管理員：把最新的一筆報告用 Webhook 傳送到頻道（測試用，不論震度）")
    async def earthquake_command(self, interaction: discord.Interaction, send: bool = False):
        if not self.api_key:
            return await send_error(interaction, "還沒有設定中央氣象署 API 金鑰（CWA_API_KEY）。")
        if send:
            perms = getattr(interaction.user, "guild_permissions", None)
            if not perms or not perms.manage_guild:
                return await send_error(interaction, "只有伺服器管理員可以傳送地震報告。")
            if not self.webhook_url:
                return await send_error(interaction, "還沒有設定 Webhook（EARTHQUAKE_WEBHOOK_URL）。")
        await defer(interaction, ephemeral=True)
        try:
            reports = await eq.fetch_reports(self.api_key, limit=20)
            if not reports and send:
                return await send_error(interaction, "目前沒有地震報告。")
            if send:
                await self.send(reports[0])
        except (eq.WeatherError, aiohttp.ClientError, discord.HTTPException, TimeoutError, ValueError) as e:
            log.warning("查詢地震報告失敗：%s", e)
            return await send_error(interaction, f"{'傳送' if send else '查詢'}地震報告失敗：{e}")
        if send:
            await respond(interaction, embed=success_embed("已經用 Webhook 傳送最新的地震報告了，去頻道看看吧！"),
                          ephemeral=True)
            return
        now = now_tw()
        big = [r for r in reports if is_big(r)
               and (not r.origin_time or now - r.origin_time <= timedelta(days=RECENT_DAYS))]
        if big:
            return await respond(interaction, content=alert_text(big[0]), embed=report_embed(big[0]), ephemeral=True)
        embed = make_embed("🌿 沒有大地震", f"最近 {RECENT_DAYS} 天台北沒有震度 {eq.intensity_text(ALERT_LEVEL)}以上的地震。",
                           Color.SUCCESS)
        if reports:
            latest = reports[0]
            magnitude = f"規模 {latest.magnitude:.1f}" if latest.magnitude is not None else "規模不明"
            embed.add_field(name="最新一筆地震報告", value=f"{fmt_time(latest.origin_time)}　"
                            f"{eq.short_location(latest.location)} {magnitude}・台北 "
                            f"{eq.intensity_text(latest.local.get(ALERT_COUNTY)) if ALERT_COUNTY in latest.local else '無感'}")
        await respond(interaction, embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Earthquake(bot))
