"""颱風、豪雨等天氣特報與停班停課：自動通知到各伺服器用 /settings alerts 設定的頻道。

- 每 5 分鐘檢查一次；只有至少一個伺服器開啟通知時才會去抓資料。
- 天氣特報只通知伺服器天氣地點所在的縣市；預設只有颱風、豪雨、大雨，可以改成全部特報。
- 颱風警報（發布、更新、解除）每個開啟通知的伺服器都會收到。
- 已經通知過的記在 meta「alerts_sent:伺服器」；第一次檢查只記錄現況、不補發。
- 某個資料來源抓失敗時那一項整個略過，不會被當成「警報解除」。
"""
import json
import logging
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from cogs.weather import guild_counties
from core import alerts
from core.features import enabled
from core.timeutil import now_tw
from core.ui import Color, defer, make_embed, respond

log = logging.getLogger(__name__)

POLL_MINUTES = 5
SENT_KEY = "alerts_sent:{}"
KEEP_SENT = 200
ERROR_LOG_EVERY = 60 * 60
WEEKDAYS = "一二三四五六日"
SOURCE = "資料來源：中央氣象署、行政院人事行政總處"


def fmt_time(dt: Optional[datetime]) -> str:
    if not dt:
        return "—"
    return f"{dt.month}/{dt.day}（{WEEKDAYS[dt.weekday()]}）{dt:%H:%M}"


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1] + "…"


# ---------------------------------------------------------------------- 卡片
def warning_embed(warning: alerts.WeatherWarning, counties: List[str]) -> discord.Embed:
    color = Color.DANGER if any(w in warning.title for w in ("豪雨", "颱風")) else Color.WARNING
    embed = make_embed(f"⚠️ {warning.title}", _cut(warning.text, 2500) or None, color)
    mine = [c for c in counties if c in warning.counties]
    if mine:
        embed.add_field(name="📍 影響你的縣市", value="、".join(mine), inline=False)
    if warning.start or warning.end:
        embed.add_field(name="🕒 有效時間", value=f"{fmt_time(warning.start)} ～ {fmt_time(warning.end)}", inline=False)
    embed.set_footer(text=f"發布：{fmt_time(warning.issued)}・資料來源：中央氣象署")
    return embed


def typhoon_embed(alert: alerts.TyphoonAlert) -> discord.Embed:
    title = f"{'✅' if alert.lifted else '🌀'} {alert.headline}"
    body = "\n\n".join(f"**{name}**\n{text}" for name, text in alert.sections.items() if text)
    embed = make_embed(title, _cut(body, 2500) or None, Color.SUCCESS if alert.lifted else Color.DANGER)
    if alert.url:
        embed.url = alert.url
    if alert.areas and not alert.lifted:
        embed.add_field(name="📍 警戒區域", value=_cut("、".join(alert.areas), 1000), inline=False)
    embed.set_footer(text=f"發布：{fmt_time(alert.effective)}・資料來源：中央氣象署")
    return embed


def closures_embed(closures: Dict[str, str]) -> discord.Embed:
    embed = make_embed("🏫 停班停課通知", color=Color.WARNING)
    for area, text in list(closures.items())[:25]:
        embed.add_field(name=area, value=_cut(text, 1000), inline=False)
    embed.set_footer(text="以各縣市政府公告為準・資料來源：行政院人事行政總處")
    embed.url = alerts.CLOSURE_URL
    return embed


# ---------------------------------------------------------------------- 篩選
def active_warnings(snapshot: alerts.Snapshot, now: datetime, counties: List[str],
                    types) -> List[alerts.WeatherWarning]:
    """還沒過期、種類符合、影響到這些縣市的特報。API 會留著已經過期的特報，要自己濾掉。"""
    wanted = set(counties)
    return [w for w in snapshot.warnings or []
            if (w.end is None or w.end > now) and w.matches(types) and w.counties & wanted]


def guild_items(snapshot: alerts.Snapshot, now: datetime, counties: List[str],
                all_types: bool) -> Optional[List[Tuple[str, discord.Embed]]]:
    """這個伺服器現在該知道的 (去重 key, 卡片)。三個來源都失敗時回傳 None。"""
    if snapshot.warnings is None and snapshot.typhoons is None and snapshot.closures is None:
        return None
    items: List[Tuple[str, discord.Embed]] = []
    for alert in alerts.recent_typhoons(snapshot.typhoons or [], now):
        items.append((f"typhoon|{alert.key}", typhoon_embed(alert)))
    types = None if all_types else alerts.DEFAULT_TYPES
    for warning in active_warnings(snapshot, now, counties, types):
        items.append((f"warning|{warning.key}", warning_embed(warning, counties)))
    closures = alerts.closures_for(snapshot.closures or {}, counties)
    if closures:
        # 內容一變（例如隔天又宣布）就是新的 key，會再通知一次
        key = "closure|" + "|".join(f"{a}={t}" for a, t in sorted(closures.items()))
        items.append((key, closures_embed(closures)))
    return items


def fit_embeds(embeds: List[discord.Embed], budget: int = 5000, limit: int = 9) -> List[discord.Embed]:
    """一則訊息的卡片總字數有上限，超過就只放前面幾張。"""
    result, used = [], 0
    for embed in embeds[:limit]:
        if used + len(embed) > budget:
            break
        result.append(embed)
        used += len(embed)
    return result


class Alerts(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.api_key = bot.settings.cwa_api_key
        self.last_error_log = 0.0

    async def cog_load(self):
        if not self.api_key:
            log.info("颱風豪雨特報需要 CWA_API_KEY，目前只會通知停班停課")
        self.poll_loop.start()

    async def cog_unload(self):
        self.poll_loop.cancel()

    # ------------------------------------------------------------------ 紀錄
    async def sent_keys(self, guild_id: int) -> Optional[List[str]]:
        raw = await self.bot.db.get_meta(SENT_KEY.format(guild_id))
        if raw is None:
            return None
        try:
            return list(json.loads(raw))
        except (ValueError, TypeError):
            return []

    async def mark_sent(self, guild_id: int, old: List[str], keys: List[str]) -> None:
        merged = [k for k in old if k not in keys] + keys
        await self.bot.db.set_meta(SENT_KEY.format(guild_id), json.dumps(merged[-KEEP_SENT:], ensure_ascii=False))

    # ------------------------------------------------------------------ 輪詢
    @tasks.loop(minutes=POLL_MINUTES)
    async def poll_loop(self):
        targets = await self.bot.db.guilds_with_setting("alerts.channel")
        if not targets:
            return
        snapshot = await alerts.fetch_snapshot(self.api_key)
        if snapshot.errors and time.time() - self.last_error_log >= ERROR_LOG_EVERY:
            self.last_error_log = time.time()
            log.warning("警報資料抓取失敗（之後每小時提醒一次）：%s", "；".join(snapshot.errors))
        now = now_tw()
        for guild_id, channel_id in targets.items():
            try:
                await self.handle_guild(guild_id, int(channel_id), snapshot, now)
            except Exception:
                log.exception("伺服器 %s 的警報通知處理失敗", guild_id)

    async def handle_guild(self, guild_id: int, channel_id: int, snapshot: alerts.Snapshot, now: datetime) -> int:
        """通知這個伺服器還沒通知過的項目，回傳送出幾張卡片。"""
        settings = await self.bot.db.get_guild_settings(guild_id)
        items = guild_items(snapshot, now, await guild_counties(self.bot.db, guild_id),
                            bool(settings.get("alerts.all_types")))
        if items is None:
            return 0
        sent = await self.sent_keys(guild_id)
        if sent is None:   # 第一次：只記錄現況，不補發
            await self.mark_sent(guild_id, [], [k for k, _ in items] or ["(start)"])
            return 0
        new = [(k, e) for k, e in items if k not in sent]
        if not new:
            return 0
        if not enabled(self.bot, "alerts", guild_id):
            # 功能關閉時也記下來，重新打開時不會補發一堆舊的
            await self.mark_sent(guild_id, sent, [k for k, _ in new])
            return 0
        done: List[str] = []
        try:
            channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
            for key, embed in new:   # 一則一張：Discord 一則訊息所有卡片加起來最多 6000 字
                await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
                done.append(key)
        except (discord.NotFound, discord.Forbidden):
            log.info("伺服器 %s 的警報頻道不存在或沒有權限，略過這次通知", guild_id)
            done = [k for k, _ in new]
        except (discord.HTTPException, aiohttp.ClientError, TimeoutError) as e:
            log.warning("伺服器 %s 的警報通知傳送失敗，下次重試：%s", guild_id, e)
        else:
            log.info("伺服器 %s 已通知 %d 項警報", guild_id, len(new))
        if done:   # 傳送成功的才記錄，失敗的下一輪再送
            await self.mark_sent(guild_id, sent, done)
        return len(done)

    @poll_loop.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------------ 指令
    @app_commands.command(name="alert", description="查看現在的颱風警報、豪雨特報與停班停課")
    async def alert_command(self, interaction: discord.Interaction):
        await defer(interaction, ephemeral=True)
        snapshot = await alerts.fetch_snapshot(self.api_key)
        counties = await guild_counties(self.bot.db, interaction.guild_id)
        items = guild_items(snapshot, now_tw(), counties, all_types=True) or []
        embeds = fit_embeds([e for _, e in items])
        lines = [f"關心的縣市：{'、'.join(counties)}（跟著 /settings weather 的地點）"]
        if snapshot.errors:
            lines.append("⚠️ 部分資料暫時抓不到：" + "；".join(snapshot.errors))
        if len(embeds) < len(items):
            lines.append(f"（內容太多，只顯示前 {len(embeds)} 項）")
        if not items:
            lines.append("✅ 目前沒有颱風警報、天氣特報或停班停課。")
        if interaction.guild_id and not (await self.bot.db.get_guild_settings(interaction.guild_id)).get("alerts.channel"):
            lines.append("💡 管理員可以用 /settings alerts 開啟自動通知。")
        embeds.insert(0, make_embed("🌀 颱風豪雨與停班停課", "\n".join(lines), Color.PRIMARY))
        await respond(interaction, embeds=embeds, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Alerts(bot))
