"""我錯過了什麼（/catchup）：AI 整理這個頻道你沒看到的聊天重點，只有你看得到。

- 預設從你在這個頻道最後一次發言之後開始，最多往回 24 小時、500 則；也可以指定最近幾小時。
  剛剛幾分鐘內自己的發言（例如對 @嘎哇 說「我錯過了什麼」）不算，不然會一讀就停。
- 提到你或回覆你的訊息會直接附上連結，不靠 AI。
- AI（Ollama）離線時仍然會列出訊息數、誰在聊、跟你有關的訊息。
"""
import logging
import time
from datetime import timedelta
from typing import Dict, Optional

import discord
from discord import app_commands
from discord.ext import commands

from core import catchup
from core.ai import AIError
from core.persona import profile_of
from core.timeutil import TAIPEI, now_tw
from core.ui import Color, defer, make_embed, respond, send_error

log = logging.getLogger(__name__)

MAX_HOURS = 24
MAX_MESSAGES = 500
COOLDOWN = 60
MAX_MENTIONS = 5
JUST_NOW = timedelta(minutes=3)   # 這麼近的自己發言不當作「上次看到這裡」


def mentions_user(message, user_id: int) -> bool:
    if any(m.id == user_id for m in message.mentions):
        return True
    resolved = getattr(message.reference, "resolved", None) if message.reference else None
    return bool(resolved and getattr(getattr(resolved, "author", None), "id", None) == user_id)


async def collect(channel, user_id: int, hours: Optional[int], now) -> catchup.Missed:
    """從新到舊讀訊息：沒指定小時就讀到你自己的上一則發言為止（剛剛 3 分鐘內的不算）。"""
    after = now - timedelta(hours=min(hours or MAX_HOURS, MAX_HOURS))
    missed = catchup.Missed()
    first = None
    async for message in channel.history(limit=MAX_MESSAGES, after=after, oldest_first=False):
        if message.author.id == user_id:
            if hours is None and message.created_at < now - JUST_NOW:
                break
            continue
        if message.author.bot:
            continue
        text = catchup.message_text(message.content, len(message.attachments), len(message.embeds))
        if not text:
            continue
        missed.lines.append(catchup.Line(message.created_at, message.author.display_name, text))
        if mentions_user(message, user_id) and len(missed.mentions) < MAX_MENTIONS:
            missed.mentions.append(message.jump_url)
        first = message
    missed.lines.reverse()
    missed.mentions.reverse()
    missed.first_url = first.jump_url if first else None
    return missed


def stats_text(missed: catchup.Missed) -> str:
    start = missed.lines[0].when.astimezone(TAIPEI)
    people = "、".join(f"{name}（{n}）" for name, n in list(missed.people.items())[:5])
    jump = f"\n[👉 跳到第一則]({missed.first_url})" if missed.first_url else ""
    return f"{start:%m/%d %H:%M} 之後有 **{len(missed.lines)}** 則訊息\n💬 {people}{jump}"


def result_embed(missed: catchup.Missed, data: Optional[Dict], note: str = "") -> discord.Embed:
    if data:
        points = "\n".join(f"• {p}" for p in data.get("points") or [] if p)
        description = "\n\n".join(x for x in (data.get("summary", "").strip(), points) if x)
    else:
        description = note
    embed = make_embed("🧾 你錯過了這些", description[:4000] or None, Color.PRIMARY)
    for_you = [f"• {x}" for x in (data or {}).get("for_you") or [] if x]
    for_you += [f"• [有人提到你]({url})" for url in missed.mentions]
    if for_you:
        embed.add_field(name="🔔 跟你有關", value="\n".join(for_you)[:1024], inline=False)
    embed.add_field(name="📊 概況", value=stats_text(missed)[:1024], inline=False)
    footer = "AI 整理，可能有遺漏，重要的事請看原文"
    if missed.truncated:
        footer += "・訊息太多，只整理了比較新的部分"
    embed.set_footer(text=footer)
    return embed


class Catchup(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.used: Dict[int, float] = {}

    @app_commands.command(name="catchup", description="AI 幫你整理這個頻道沒看到的聊天重點（只有你看得到）")
    @app_commands.describe(hours="整理最近幾小時（1–24）；不填就從你上次在這裡發言之後開始")
    @app_commands.guild_only()
    async def catchup_command(self, interaction: discord.Interaction,
                              hours: Optional[app_commands.Range[int, 1, MAX_HOURS]] = None):
        channel = interaction.channel
        me = interaction.guild.me if interaction.guild else None
        if me is not None and hasattr(channel, "permissions_for"):
            perms = channel.permissions_for(me)
            if not perms.read_message_history or not perms.view_channel:
                return await send_error(interaction, "我沒有權限讀這個頻道的訊息紀錄。")
        wait = COOLDOWN - (time.time() - self.used.get(interaction.user.id, 0))
        if wait > 0:
            return await send_error(interaction, f"請 {wait:.0f} 秒後再試。")
        self.used[interaction.user.id] = time.time()
        await defer(interaction, ephemeral=True)

        try:
            missed = await collect(channel, interaction.user.id, hours, now_tw())
        except discord.Forbidden:
            return await send_error(interaction, "我沒有權限讀這個頻道的訊息紀錄。")
        if not missed.lines:
            return await respond(interaction, embed=make_embed("🧾 你沒錯過什麼", "這段時間沒有新的聊天。", Color.MUTED),
                                 ephemeral=True)
        if len(missed.lines) < catchup.MIN_MESSAGES:
            return await respond(interaction, embed=result_embed(missed, None, "只有幾則訊息，直接看原文比較快 👇"),
                                 ephemeral=True)

        text, missed.truncated = catchup.transcript(missed.lines)
        data, note = None, ""
        ai = self.bot.ai
        if not ai.available:
            note = "🔌 AI 目前離線（主電腦可能沒開），先列出概況。"
        else:
            try:
                data = await ai.json(catchup.build_prompt(text, interaction.user.display_name,
                                                         profile_of(self.bot, interaction.guild_id).catchup_tone), catchup.SCHEMA,
                                     temperature=0.2, max_tokens=800, num_ctx=8192)
            except AIError as e:
                log.info("我錯過了什麼：AI 整理失敗：%s", e)
                note = "🔌 AI 暫時無法整理，先列出概況。"
        await respond(interaction, embed=result_embed(missed, data, note), ephemeral=True)


async def setup(bot):
    await bot.add_cog(Catchup(bot))
