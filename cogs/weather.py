"""每日天氣預報：每天早上、中午用 Webhook 傳送台北、淡水的天氣與降雨機率。

資料來自中央氣象署開放資料，需要在 .env 設定 CWA_API_KEY 與 WEATHER_WEBHOOK_URL。
筆電在傳送時間剛好關機或重啟時，開機後會在下一個傳送時間之前補送；同一個時段不會重複傳送。
Webhook 的開頭問候會依天氣（下雨／放晴／陰天）改變；穿衣建議只在 /weather 指令裡顯示。
"""
import logging
import os
import re
import time
from datetime import datetime, timedelta
from typing import List, Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from core import weather
from core.timeutil import TAIPEI, now_tw
from core.ui import Color, defer, respond, send_error, success_embed

log = logging.getLogger(__name__)

DEFAULT_TIMES = "06:00,12:00"
CATCH_UP_HOURS = 6         # 最後一個時段錯過時，最多補送幾小時
RETRY_SECONDS = 10 * 60    # 失敗後隔多久再試
WEEKDAYS = "一二三四五六日"


def parse_clock(text: str) -> Optional[tuple]:
    match = re.fullmatch(r"\s*(\d{1,2})[:：](\d{2})\s*", text or "")
    if not match or int(match.group(1)) > 23 or int(match.group(2)) > 59:
        return None
    return int(match.group(1)), int(match.group(2))


def parse_times(text: str) -> List[tuple]:
    """「06:00,12:00」→ [(6, 0), (12, 0)]；格式錯的部分略過。"""
    times = {parse_clock(part) for part in re.split(r"[,，、\s]+", text or "") if part.strip()}
    return sorted(t for t in times if t)


def slot_label(hour: int) -> str:
    return "早上" if hour < 11 else ("中午" if hour < 14 else ("下午" if hour < 18 else "晚上"))


def fmt_temp(period: weather.Period) -> str:
    if period.min_t is None and period.max_t is None:
        return "—"
    if period.min_t == period.max_t or period.min_t is None or period.max_t is None:
        return f"{period.max_t if period.max_t is not None else period.min_t}°C"
    return f"{period.min_t}–{period.max_t}°C"


def fmt_pop(pop: Optional[int]) -> str:
    return "—" if pop is None else f"{pop}%"


def forecast_embed(forecast: weather.Forecast, date: datetime, tomorrow: bool = False,
                   clothing: bool = True, afternoon: bool = False) -> discord.Embed:
    """clothing：要不要附穿衣建議（Webhook 不附）；afternoon：白天只算中午之後（中午的預報）。"""
    day, night = forecast.day, forecast.night
    part = "下午" if afternoon else "白天"
    emoji, rain_text = weather.rain_advice(day, night)
    top_pop = max([p for p in (day.pop, night.pop) if p is not None], default=0)
    color = Color.PRIMARY if top_pop >= 40 else (Color.WARNING if top_pop < 20 else Color.MUTED)
    date_text = f"{'明天 ' if tomorrow else ''}{date.month}/{date.day}（{WEEKDAYS[date.weekday()]}）"
    embed = discord.Embed(title=f"{emoji} {forecast.label}・{date_text}", color=color)
    embed.description = (f"{part}　" if afternoon else "") + f"**{day.wx or '—'}**　🌡️ {fmt_temp(day)}" + (f"　・　{day.ci}" if day.ci else "")
    embed.add_field(name="☔ 會不會下雨", value=f"**{rain_text}**\n{part} {fmt_pop(day.pop)}・晚上 {fmt_pop(night.pop)}",
                    inline=False)
    if clothing:
        embed.add_field(name="👕 穿衣建議", value=weather.clothing_advice(day, night), inline=False)
    embed.add_field(name="🌙 明晚" if tomorrow else "🌙 今晚", value=f"{night.wx or '—'}　🌡️ {fmt_temp(night)}", inline=False)
    embed.set_footer(text=f"{forecast.source}・資料來源：中央氣象署")
    return embed


class Weather(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        settings = bot.settings
        self.api_key = settings.cwa_api_key
        self.webhook_url = settings.weather_webhook_url
        self.send_times = parse_times(settings.weather_time) or parse_times(DEFAULT_TIMES)
        self.state_path = os.path.join(os.path.dirname(settings.database_path), "weather_last_sent.txt")
        self.last_attempt = 0.0

    async def cog_load(self):
        if not self.api_key or not self.webhook_url:
            missing = [name for name, value in (("CWA_API_KEY", self.api_key),
                                                ("WEATHER_WEBHOOK_URL", self.webhook_url)) if not value]
            log.info("每日天氣預報未啟用：.env 裡沒有讀到 %s", "、".join(missing))
            return
        if not parse_times(self.bot.settings.weather_time):
            log.warning("WEATHER_TIME 格式錯誤（%s），改用 %s", self.bot.settings.weather_time, DEFAULT_TIMES)
        log.info("每日天氣預報已啟用，每天 %s 傳送", "、".join("%02d:%02d" % t for t in self.send_times))
        self.daily_loop.start()

    async def cog_unload(self):
        self.daily_loop.cancel()

    # ------------------------------------------------------------------ 傳送紀錄
    def sent_slots(self) -> List[str]:
        """已傳送的時段，每行一個「2026-11-01 06:00」。舊版只記日期，視為當天第一個時段已傳送。"""
        try:
            with open(self.state_path, encoding="utf-8") as f:
                lines = [line.strip() for line in f if line.strip()]
        except OSError:
            return []
        first = "%02d:%02d" % self.send_times[0]
        return [f"{line} {first}" if len(line) == 10 else line for line in lines]

    def last_sent(self) -> Optional[str]:
        slots = self.sent_slots()
        return slots[-1] if slots else None

    def mark_sent(self, slot: str) -> None:
        slots = [s for s in self.sent_slots() if s != slot] + [slot]
        try:
            with open(self.state_path, "w", encoding="utf-8") as f:
                f.write("\n".join(slots[-20:]) + "\n")
        except OSError:
            log.warning("無法寫入天氣預報傳送紀錄", exc_info=True)

    def due_slot(self, now: datetime) -> Optional[tuple]:
        """現在該傳送、而且還沒傳送過的時段 (時, 分)；錯過時在下一個時段之前補送。"""
        sent = set(self.sent_slots())
        for i, (hour, minute) in enumerate(self.send_times):
            start = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if i + 1 < len(self.send_times):
                h, m = self.send_times[i + 1]
                deadline = now.replace(hour=h, minute=m, second=0, microsecond=0)
            else:
                deadline = min(start + timedelta(hours=CATCH_UP_HOURS),
                               now.replace(hour=23, minute=59, second=59, microsecond=0))
            if start <= now < deadline and self.slot_key(now, (hour, minute)) not in sent:
                return hour, minute
        return None

    def is_due(self, now: datetime) -> bool:
        return self.due_slot(now) is not None

    @staticmethod
    def slot_key(now: datetime, slot: tuple) -> str:
        return f"{now.date().isoformat()} {slot[0]:02d}:{slot[1]:02d}"

    # ------------------------------------------------------------------ 傳送
    async def fetch(self, now: datetime, tomorrow: bool = False) -> tuple:
        """回傳 (那一天, 是否只算下午, 預報列表)。今天過了中午就只看下午到晚上。"""
        day = now + timedelta(days=1) if tomorrow else now
        afternoon = not tomorrow and now.hour >= 12
        forecasts = await weather.fetch_forecasts(self.api_key, day, start_hour=12 if afternoon else 6)
        return day, afternoon, forecasts

    async def build_embeds(self, now: datetime, tomorrow: bool = False) -> List[discord.Embed]:
        day, afternoon, forecasts = await self.fetch(now, tomorrow)
        return [forecast_embed(f, day, tomorrow, afternoon=afternoon) for f in forecasts]

    async def send_daily(self, now: datetime, tomorrow: bool = False) -> None:
        day, afternoon, forecasts = await self.fetch(now, tomorrow)
        embeds = [forecast_embed(f, day, tomorrow, clothing=False, afternoon=afternoon) for f in forecasts]
        heading = "明天的天氣預報" if tomorrow else f"{slot_label(now.hour)}的天氣預報"
        content = f"{weather.greeting(forecasts)}\n-# 📅 {day.month}/{day.day}（{WEEKDAYS[day.weekday()]}）{heading}"
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            webhook = discord.Webhook.from_url(self.webhook_url, session=session)
            await webhook.send(content=content, embeds=embeds,
                               allowed_mentions=discord.AllowedMentions.none())

    @tasks.loop(minutes=1)
    async def daily_loop(self):
        now = now_tw()
        slot = self.due_slot(now)
        if slot is None or time.time() - self.last_attempt < RETRY_SECONDS:
            return
        self.last_attempt = time.time()
        try:
            await self.send_daily(now)
        except (weather.WeatherError, aiohttp.ClientError, discord.HTTPException, TimeoutError, ValueError) as e:
            log.warning("每日天氣預報傳送失敗，%d 分鐘後重試：%s", RETRY_SECONDS // 60, e)
            return
        self.mark_sent(self.slot_key(now, slot))
        log.info("已傳送 %s 的天氣預報", self.slot_key(now, slot))

    @daily_loop.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------------ 指令
    @app_commands.command(name="weather", description="查看台北、淡水今天或明天的天氣、降雨機率與穿衣建議")
    @app_commands.describe(day="要看哪一天，預設今天",
                           send="管理員：立刻用 Webhook 傳送到頻道（不影響每天的自動傳送）")
    @app_commands.choices(day=[app_commands.Choice(name="今天", value=0), app_commands.Choice(name="明天", value=1)])
    async def weather_command(self, interaction: discord.Interaction, day: int = 0, send: bool = False):
        tomorrow = day == 1
        if not self.api_key:
            return await send_error(interaction, "還沒有設定中央氣象署 API 金鑰（CWA_API_KEY）。")
        if send:
            perms = getattr(interaction.user, "guild_permissions", None)
            if not perms or not perms.manage_guild:
                return await send_error(interaction, "只有伺服器管理員可以立刻傳送預報。")
            if not self.webhook_url:
                return await send_error(interaction, "還沒有設定 Webhook（WEATHER_WEBHOOK_URL）。")
        await defer(interaction, ephemeral=True)
        now = datetime.now(TAIPEI)
        try:
            if send:
                await self.send_daily(now, tomorrow)
            else:
                embeds = await self.build_embeds(now, tomorrow)
        except (weather.WeatherError, aiohttp.ClientError, discord.HTTPException, TimeoutError, ValueError) as e:
            log.warning("查詢天氣失敗：%s", e)
            return await send_error(interaction, f"{'傳送' if send else '查詢'}天氣失敗：{e}")
        if send:
            await respond(interaction, embed=success_embed(f"已經用 Webhook 傳送{'明天' if tomorrow else '今天'}的天氣預報了，去頻道看看吧！"), ephemeral=True)
        else:
            await respond(interaction, embeds=embeds, ephemeral=True)

async def setup(bot):
    await bot.add_cog(Weather(bot))
