import discord
from discord.ext import commands
import asyncio
import re

class Reminder(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def parse_time(self, time_str: str) -> int:
        """解析時間字串，返回秒數。範例: 10s, 5m, 1h"""
        time_regex = re.compile(r"(\d+)([smhd])")
        match = time_regex.match(time_str.lower())
        
        if not match:
            return None
        
        amount = int(match.group(1))
        unit = match.group(2)
        
        if unit == 's':
            return amount
        elif unit == 'm':
            return amount * 60
        elif unit == 'h':
            return amount * 3600
        elif unit == 'd':
            return amount * 86400
        return None

    @commands.command(name="remind", aliases=["提醒", "reminder"], help="設定倒數提醒。範例: !remind 10m 泡麵好了")
    async def remind(self, ctx: commands.Context, time_str: str, *, content: str = "時間到囉！"):
        seconds = self.parse_time(time_str)
        
        if seconds is None:
            await ctx.send("⏱️ 時間格式錯誤！請使用數字加單位 (s, m, h, d)。\n範例：`!remind 10m 吃藥`")
            return
            
        if seconds > 86400 * 30: # Limit to 30 days
            await ctx.send("太久了吧！我最多只能記住 30 天內的事。")
            return

        # Confirmation message
        await ctx.send(f"✅ 好的，**{time_str}** 後會提醒你：\n> {content}")
        
        # Start background task (non-blocking sleep)
        # Note: This simple implementation ensures basic reminders but won't survive bot restarts.
        await asyncio.sleep(seconds)
        
        # Trigger Reminder
        embed = discord.Embed(
            title="⏰ 提醒時間到！",
            description=f"{ctx.author.mention} 你剛剛叫我提醒你：\n\n**{content}**",
            color=discord.Color.magenta()
        )
        try:
            await ctx.send(embed=embed)
        except:
            # Fallback if channel inaccessible (though rare effectively)
            pass

async def setup(bot):
    await bot.add_cog(Reminder(bot))
