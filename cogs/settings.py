"""伺服器設定（/settings，中文是 /設定）：伺服器管理員自己調整這個伺服器的設定。

目前有：功能開關、天氣地點與每日預報、颱風豪雨警報與停班停課通知、地震通報。需要「管理伺服器」權限。
功能開關跟管理後台是同一份設定；後台全域關掉的功能，伺服器這邊打不開。
"""
import json
import logging
from typing import List, Optional

import discord
from discord import app_commands
from discord.ext import commands

from cogs.weather import guild_places, parse_clock, place_choices
from core import weather
from core.features import FEATURES, FEATURES_BY_KEY, GLOBAL, features_by_category
from core.ui import BaseView, Color, make_embed, respond, send_error

log = logging.getLogger(__name__)

DEFAULT_TIME = "06:00"


def can_post(channel, me) -> Optional[str]:
    """bot 能不能在這個頻道發訊息；不行就回傳原因。"""
    perms = channel.permissions_for(me)
    if not perms.view_channel or not perms.send_messages:
        return f"我沒有權限在 {channel.mention} 發訊息，請先幫我開權限。"
    if not perms.embed_links:
        return f"我在 {channel.mention} 沒有「嵌入連結」權限，預報卡片會顯示不出來。"
    return None


async def place_autocomplete(interaction: discord.Interaction, current: str):
    return place_choices(current)


def feature_state(bot, guild_id: int) -> tuple:
    """(這個伺服器開著的功能 key, 後台全域關掉的功能 key)。"""
    flags = bot.features
    blocked = {f.key for f in FEATURES if flags.flags.get((GLOBAL, f.key)) is False}
    on = {f.key for f in FEATURES if f.key not in blocked and flags.is_enabled(f.key, guild_id)}
    return on, blocked


class FeatureSelect(discord.ui.Select):
    def __init__(self, on: set, blocked: set):
        # Discord 下拉選單不能分組，就照分類排序、說明前面寫分類
        options = [discord.SelectOption(label=f.name, value=f.key, emoji=f.emoji, default=f.key in on,
                                        description=f"{cat[2]}｜{f.description}"[:100])
                   for cat, items in features_by_category() for f in items if f.key not in blocked]
        super().__init__(placeholder="勾選要開啟的功能（沒勾的會關閉）", min_values=0,
                         max_values=len(options), options=options)

    async def callback(self, interaction: discord.Interaction):
        await self.view.apply(interaction, set(self.values))


class FeatureView(BaseView):
    owner_denied_message = "請自己輸入 /settings features 來設定。"

    def __init__(self, cog: "Settings", guild_id: int, owner_id: int):
        super().__init__(owner_id=owner_id, timeout=300)
        self.cog, self.guild_id = cog, guild_id
        on, blocked = feature_state(cog.bot, guild_id)
        self.add_item(FeatureSelect(on, blocked))

    async def apply(self, interaction: discord.Interaction, wanted: set):
        bot = self.cog.bot
        on, blocked = feature_state(bot, self.guild_id)
        changed = []
        for feature in FEATURES:
            if feature.key in blocked:
                continue
            want = feature.key in wanted
            if want != (feature.key in on):
                # 開啟 = 拿掉伺服器設定（回到預設的開）；關閉 = 記成 False
                await bot.features.set(self.guild_id, feature.key, None if want else False)
                changed.append(f"{'開' if want else '關'} {feature.name}")
        if changed:
            await bot.db.audit("feature", interaction.user.id,
                               f"伺服器 {self.guild_id}（/settings）：{'、'.join(changed)}", None)
            log.info("伺服器 %s 功能開關：%s", self.guild_id, "、".join(changed))
        self.clear_items()
        self.add_item(FeatureSelect(*feature_state(bot, self.guild_id)))
        await interaction.response.edit_message(embed=features_embed(bot, self.guild_id, bool(changed)), view=self)


def features_embed(bot, guild_id: int, updated: bool = False) -> discord.Embed:
    on, blocked = feature_state(bot, guild_id)
    description = None
    if blocked:
        description = "🔒 機器人管理員已全域關閉：" + "、".join(FEATURES_BY_KEY[k].name for k in sorted(blocked))
    embed = make_embed("✅ 功能開關已更新" if updated else "🧩 功能開關", description, Color.PRIMARY)
    for (_, emoji, name), items in features_by_category():
        lines = [f"{'✅' if f.key in on else '⬜'} {f.emoji} {f.name}" for f in items if f.key not in blocked]
        if lines:
            embed.add_field(name=f"{emoji} {name}", value="\n".join(lines), inline=True)
    embed.set_footer(text="關閉的功能：指令會提示已關閉、大廳和說明裡也不會出現。/選單 /說明 /設定 不能關")
    return embed


class Settings(commands.Cog):
    settings_group = app_commands.Group(
        name="settings", description="伺服器設定：功能開關、天氣、颱風豪雨停班停課、地震通報（管理員）",
        guild_only=True, default_permissions=discord.Permissions(manage_guild=True))

    def __init__(self, bot):
        self.bot = bot

    async def check(self, interaction: discord.Interaction) -> bool:
        member = interaction.user
        if not (isinstance(member, discord.Member) and member.guild_permissions.manage_guild):
            await send_error(interaction, "只有有「管理伺服器」權限的人可以改設定。")
            return False
        return True

    # ------------------------------------------------------------------ 功能開關
    @settings_group.command(name="features", description="開關這個伺服器的功能，例如不想要遊戲或股票")
    async def features_settings(self, interaction: discord.Interaction):
        if not await self.check(interaction):
            return
        if not getattr(self.bot, "features", None):
            return await send_error(interaction, "功能開關還沒有啟用。")
        view = FeatureView(self, interaction.guild_id, interaction.user.id)
        view.message = await respond(interaction, embed=features_embed(self.bot, interaction.guild_id),
                                     view=view, ephemeral=True)

    # ------------------------------------------------------------------ 天氣
    @settings_group.command(name="weather", description="設定天氣地點（最多 3 個）與每天自動傳送的頻道、時間")
    @app_commands.describe(place1="地點 1，輸入鄉鎮或縣市，例如 淡水、高雄 苓雅", place2="地點 2", place3="地點 3",
                           channel="每天自動傳送天氣預報的頻道", time="幾點傳送，例如 06:00（預設 06:00）",
                           stop="停止每天自動傳送")
    @app_commands.autocomplete(place1=place_autocomplete, place2=place_autocomplete, place3=place_autocomplete)
    async def weather_settings(self, interaction: discord.Interaction, place1: Optional[str] = None,
                               place2: Optional[str] = None, place3: Optional[str] = None,
                               channel: Optional[discord.TextChannel] = None, time: Optional[str] = None,
                               stop: bool = False):
        if not await self.check(interaction):
            return
        db, gid = self.bot.db, interaction.guild_id
        keys = [k for k in (place1, place2, place3) if k]
        if keys:
            places = []
            for key in keys:
                place = weather.place_from_key(key)
                if not place:
                    return await send_error(interaction, f"找不到「{key}」這個地點，請從選單選擇。")
                if place not in places:
                    places.append(place)
            await db.set_guild_setting(gid, "weather.places", json.dumps([weather.place_key(p) for p in places]))
        if time:
            clock = parse_clock(time)
            if not clock:
                return await send_error(interaction, "時間格式請用 `06:00` 這樣的寫法。")
            await db.set_guild_setting(gid, "weather.time", "%02d:%02d" % clock)
        if channel:
            problem = can_post(channel, interaction.guild.me)
            if problem:
                return await send_error(interaction, problem)
            await db.set_guild_setting(gid, "weather.channel", str(channel.id))
        if stop:
            await db.set_guild_setting(gid, "weather.channel", None)
        await respond(interaction, embed=await self.summary(gid, "✅ 天氣設定已更新"), ephemeral=True)
        log.info("伺服器 %s 更新天氣設定", gid)

    # ------------------------------------------------------------------ 警報
    @settings_group.command(name="alerts", description="颱風、豪雨等天氣特報與停班停課，自動通知到指定頻道")
    @app_commands.describe(channel="要通知的頻道", all_types="除了颱風、豪雨、大雨，強風、低溫等所有特報也通知",
                           stop="停止通知")
    async def alerts_settings(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None,
                              all_types: Optional[bool] = None, stop: bool = False):
        if not await self.check(interaction):
            return
        db, gid = self.bot.db, interaction.guild_id
        if channel:
            problem = can_post(channel, interaction.guild.me)
            if problem:
                return await send_error(interaction, problem)
            await db.set_guild_setting(gid, "alerts.channel", str(channel.id))
        if all_types is not None:
            await db.set_guild_setting(gid, "alerts.all_types", "1" if all_types else None)
        if stop:
            await db.set_guild_setting(gid, "alerts.channel", None)
        await respond(interaction, embed=await self.summary(gid, "✅ 警報設定已更新"), ephemeral=True)
        log.info("伺服器 %s 更新警報設定", gid)

    # ------------------------------------------------------------------ 地震
    @settings_group.command(name="earthquake", description="天氣地點所在縣市震度達到門檻時，自動通報地震到指定頻道")
    @app_commands.describe(channel="要通報的頻道", level="你們縣市震度達到多少才通報（預設 4 級）", stop="停止通報")
    @app_commands.choices(level=[app_commands.Choice(name=f"{v} 級" if v.isdigit() else v, value=v)
                                 for v in ("3", "4", "5弱", "5強", "6弱")])
    async def earthquake_settings(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None,
                                  level: Optional[str] = None, stop: bool = False):
        if not await self.check(interaction):
            return
        db, gid = self.bot.db, interaction.guild_id
        if channel:
            problem = can_post(channel, interaction.guild.me)
            if problem:
                return await send_error(interaction, problem)
            await db.set_guild_setting(gid, "earthquake.channel", str(channel.id))
        if level:
            await db.set_guild_setting(gid, "earthquake.level", None if level == "4" else level)
        if stop:
            await db.set_guild_setting(gid, "earthquake.channel", None)
        await respond(interaction, embed=await self.summary(gid, "✅ 地震通報設定已更新"), ephemeral=True)
        log.info("伺服器 %s 更新地震設定", gid)

    # ------------------------------------------------------------------ 查看
    @settings_group.command(name="show", description="查看這個伺服器目前的設定")
    async def show(self, interaction: discord.Interaction):
        if not await self.check(interaction):
            return
        await respond(interaction, embed=await self.summary(interaction.guild_id, "⚙️ 伺服器設定"), ephemeral=True)

    async def summary(self, guild_id: int, title: str) -> discord.Embed:
        settings = await self.bot.db.get_guild_settings(guild_id)
        places = await guild_places(self.bot.db, guild_id)
        custom = "weather.places" in settings
        embed = make_embed(title, color=Color.PRIMARY)
        place_text = "、".join(f"{p.county}{p.town}" for p in places) + ("" if custom else "（預設）")
        channel = settings.get("weather.channel")
        daily = (f"<#{channel}> 每天 {settings.get('weather.time', DEFAULT_TIME)}" if channel else "沒有開啟")
        embed.add_field(name="🌦️ 天氣", value=f"地點：{place_text}\n每日預報：{daily}", inline=False)
        alert_channel = settings.get("alerts.channel")
        types = "所有天氣特報" if settings.get("alerts.all_types") else "颱風、豪雨、大雨"
        counties = "、".join(dict.fromkeys(p.county for p in places))
        alerts = (f"<#{alert_channel}>：{types}、停班停課\n只通知天氣地點所在的縣市（{counties}）"
                  if alert_channel else "沒有開啟")
        embed.add_field(name="🌀 颱風豪雨與停班停課", value=alerts, inline=False)
        quake_channel = settings.get("earthquake.channel")
        level = settings.get("earthquake.level", "4")
        level_text = f"{level} 級" if level.isdigit() else level
        quake = (f"<#{quake_channel}>：{counties} 震度 {level_text}以上" if quake_channel else "沒有開啟")
        embed.add_field(name="🌏 地震通報", value=quake, inline=False)
        if getattr(self.bot, "features", None):
            on, blocked = feature_state(self.bot, guild_id)
            off = [f"{f.emoji} {f.name}" for f in FEATURES if f.key not in on]
            embed.add_field(name="🧩 功能", value=("關閉中：" + "、".join(off)) if off else "全部開啟", inline=False)
        embed.set_footer(text="/settings features 開關功能・/settings weather 改天氣・/settings alerts 改警報・/settings earthquake 改地震")
        return embed


async def setup(bot):
    await bot.add_cog(Settings(bot))
