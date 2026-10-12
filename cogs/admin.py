"""管理工具。"""
import discord
from discord import app_commands
from discord.ext import commands

from core.ui import success_embed


class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="clear", description="清除這個頻道最近的訊息")
    @app_commands.describe(amount="要清除幾則訊息（1–100）")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)  # 沒權限的人看不到這個指令
    @app_commands.checks.has_permissions(manage_messages=True)
    @app_commands.checks.bot_has_permissions(manage_messages=True, read_message_history=True)
    async def clear(self, interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100] = 10):
        await interaction.response.defer(ephemeral=True, thinking=True)
        deleted = await interaction.channel.purge(limit=amount)
        await interaction.followup.send(embed=success_embed(f"已清除 {len(deleted)} 則訊息。"), ephemeral=True)


async def setup(bot):
    await bot.add_cog(Admin(bot))
