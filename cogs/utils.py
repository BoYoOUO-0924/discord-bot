import discord
from discord.ext import commands

class Utils(commands.Cog):

    @commands.command(name="eval", aliases=["exec", "py"], hidden=True)
    @commands.is_owner()
    async def _eval(self, ctx, *, body: str):
        """Executes a code block."""
        import io
        import sys
        import textwrap
        from contextlib import redirect_stdout

        # Clean up code block
        if body.startswith("```") and body.endswith("```"):
            body = "\n".join(body.split("\n")[1:-1])
        else:
            body = body.strip("` \n")

        env = {
            'bot': self.bot,
            'ctx': ctx,
            'channel': ctx.channel,
            'author': ctx.author,
            'guild': ctx.guild,
            'message': ctx.message,
            '_': self._last_result
        }
        env.update(globals())

        stdout = io.StringIO()
        
        to_compile = f'async def func():\n{textwrap.indent(body, "  ")}'

        try:
            exec(to_compile, env)
        except Exception as e:
            return await ctx.send(f'```py\n{e.__class__.__name__}: {e}\n```')

        func = env['func']
        try:
            with redirect_stdout(stdout):
                ret = await func()
        except Exception as e:
            value = stdout.getvalue()
            await ctx.send(f'```py\n{value}{e.__class__.__name__}: {e}\n```')
        else:
            value = stdout.getvalue()
            try:
                await ctx.message.add_reaction('\u2705')
            except: pass

            if ret is None:
                if value:
                    await ctx.send(f'```py\n{value}\n```')
            else:
                self._last_result = ret
                await ctx.send(f'```py\n{value}{ret}\n```')

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._last_result = None

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
