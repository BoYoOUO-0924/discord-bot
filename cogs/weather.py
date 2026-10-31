"""每日天氣預報：每天早上用 Webhook 傳送台北、淡水的天氣、降雨機率與穿衣建議。

資料來自中央氣象署開放資料，需要在 .env（或 config.py）設定 CWA_API_KEY 與 WEATHER_WEBHOOK_URL。
筆電在傳送時間剛好關機或重啟時，開機後中午前會補送；同一天不會重複傳送。
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
from core.ui import Color, defer, respond, send_error

log = logging.getLogger(__name__)

CATCH_UP_UNTIL_HOUR = 12   # 錯過傳送時間時，最晚補送到中午
RETRY_SECONDS = 10 * 60    # 失敗後隔多久再試
WEEKDAYS = "一二三四五六日"


def parse_clock(text: str) -> Optional[tuple]:
    match = re.fullmatch(r"\s*(\d{1,2})[:：](\d{2})\s*", text or "")
    if not match or int(match.group(1)) > 23 or int(match.group(2)) > 59:
        return None
    return int(match.group(1)), int(match.group(2))


def fmt_temp(period: weather.Period) -> str:
    if period.min_t is None and period.max_t is None:
        return "—"
    if period.min_t == period.max_t or period.min_t is None or period.max_t is None:
        return f"{period.max_t if period.max_t is not None else period.min_t}°C"
    return f"{period.min_t}–{period.max_t}°C"


def fmt_pop(pop: Optional[int]) -> str:
    return "—" if pop is None else f"{pop}%"


def forecast_embed(forecast: weather.Forecast, now: datetime) -> discord.Embed:
    day, night = forecast.day, forecast.night
    emoji, rain_text = weather.rain_advice(day, night)
    top_pop = max([p for p in (day.pop, night.pop) if p is not None], default=0)
    color = Color.PRIMARY if top_pop >= 40 else (Color.WARNING if top_pop < 20 else Color.MUTED)
    date_text = f"{now.month}/{now.day}（{WEEKDAYS[now.weekday()]}）"
    embed = discord.Embed(title=f"{emoji} {forecast.label}・{date_text}", color=color)
    embed.description = f"**{day.wx or '—'}**　🌡️ {fmt_temp(day)}" + (f"　・　{day.ci}" if day.ci else "")
    embed.add_field(name="☔ 會不會下雨", value=f"**{rain_text}**\n白天 {fmt_pop(day.pop)}・晚上 {fmt_pop(night.pop)}",
                    inline=False)
    embed.add_field(name="👕 穿衣建議", value=weather.clothing_advice(day, night), inline=False)
    embed.add_field(name="🌙 今晚", value=f"{night.wx or '—'}　🌡️ {fmt_temp(night)}", inline=False)
    embed.set_footer(text=f"{forecast.source}・資料來源：中央氣象署")
    return embed


class Weather(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        settings = bot.settings
        self.api_key = settings.cwa_api_key
        self.webhook_url = settings.weather_webhook_url
        self.send_at = parse_clock(settings.weather_time) or (6, 0)
        self.state_path = os.path.join(os.path.dirname(settings.database_path), "weather_last_sent.txt")
        self.last_attempt = 0.0

    async def cog_load(self):
        if not self.api_key or not self.webhook_url:
            log.info("每日天氣預報未啟用（需要在 .env 設定 CWA_API_KEY 與 WEATHER_WEBHOOK_URL）")
            return
        if not parse_clock(self.bot.settings.weather_time):
            log.warning("WEATHER_TIME 格式錯誤（%s），改用 06:00", self.bot.settings.weather_time)
        log.info("每日天氣預報已啟用，每天 %02d:%02d 傳送", *self.send_at)
        self.daily_loop.start()

    async def cog_unload(self):
        self.daily_loop.cancel()

    # ------------------------------------------------------------------ 傳送紀錄
    def last_sent(self) -> Optional[str]:
        try:
            with open(self.state_path, encoding="utf-8") as f:
                return f.read().strip() or None
        except OSError:
            return None

    def mark_sent(self, day: str) -> None:
        try:
            with open(self.state_path, "w", encoding="utf-8") as f:
                f.write(day)
        except OSError:
            log.warning("無法寫入天氣預報傳送紀錄", exc_info=True)

    def is_due(self, now: datetime) -> bool:
        send_time = now.replace(hour=self.send_at[0], minute=self.send_at[1], second=0, microsecond=0)
        deadline = max(send_time + timedelta(hours=1), now.replace(hour=CATCH_UP_UNTIL_HOUR, minute=0, second=0))
        return send_time <= now < deadline and self.last_sent() != now.date().isoformat()

    # ------------------------------------------------------------------ 傳送
    async def build_embeds(self, now: datetime) -> List[discord.Embed]:
        forecasts = await weather.fetch_forecasts(self.api_key, now)
        return [forecast_embed(f, now) for f in forecasts]

    async def send_daily(self, now: datetime) -> None:
        embeds = await self.build_embeds(now)
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            webhook = discord.Webhook.from_url(self.webhook_url, session=session)
            await webhook.send(content="☀️ **早安！今天的天氣預報**", embeds=embeds, username="天氣預報",
                               allowed_mentions=discord.AllowedMentions.none())

    @tasks.loop(minutes=1)
    async def daily_loop(self):
        now = now_tw()
        if not self.is_due(now) or time.time() - self.last_attempt < RETRY_SECONDS:
            return
        self.last_attempt = time.time()
        try:
            await self.send_daily(now)
        except (weather.WeatherError, aiohttp.ClientError, discord.HTTPException, TimeoutError, ValueError) as e:
            log.warning("每日天氣預報傳送失敗，%d 分鐘後重試：%s", RETRY_SECONDS // 60, e)
            return
        self.mark_sent(now.date().isoformat())
        log.info("已傳送 %s 的天氣預報", now.date())

    @daily_loop.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------------ 指令
    @app_commands.command(name="weather", description="查看今天台北、淡水的天氣、降雨機率與穿衣建議")
    async def weather_command(self, interaction: discord.Interaction):
        if not self.api_key:
            return await send_error(interaction, "還沒有設定中央氣象署 API 金鑰（CWA_API_KEY）。")
        await defer(interaction, ephemeral=True)
        now = datetime.now(TAIPEI)
        try:
            embeds = await self.build_embeds(now)
        except (weather.WeatherError, aiohttp.ClientError, TimeoutError, ValueError) as e:
            log.warning("查詢天氣失敗：%s", e)
            return await send_error(interaction, f"查詢天氣失敗：{e}")
        await respond(interaction, embeds=embeds, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Weather(bot))
