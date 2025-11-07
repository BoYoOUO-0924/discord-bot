import discord
from discord.ext import commands

class Utils(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="clear", help="清除指定數量的訊息（預設10）。需要管理訊息權限。")
    @commands.has_permissions(manage_messages=True)
    @commands.guild_only()
    async def clear(self, ctx: commands.Context, amount: int = 10):
        """Clears a specified number of messages in the channel."""
        # Add 1 to the amount to include the command message itself
        limit = amount + 1
        try:
            deleted = await ctx.channel.purge(limit=limit)
            await ctx.send(f"成功清除了 {len(deleted) - 1} 則訊息。", delete_after=5)
        except discord.Forbidden:
            await ctx.send("我沒有權限在此頻道中刪除訊息。", delete_after=10)
        except discord.HTTPException as e:
            await ctx.send(f"清除訊息時發生錯誤：{e}", delete_after=10)

async def setup(bot):
    await bot.add_cog(Utils(bot))
