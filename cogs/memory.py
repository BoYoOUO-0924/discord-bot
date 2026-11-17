"""AI 記憶（/memory）：看機器人聊天時記住了你哪些事，可以刪掉其中一條或全部忘掉。

- 記憶怎麼來的：cogs/assistant.py 閒聊幾句後在背景整理（core/memory.py），只記你自己說的話
- 每個伺服器分開記，只有你自己看得到；選單刪一條、按鈕全部忘掉（要再按一次確認）
- 也可以 @機器人 說「你記得我什麼」「忘掉我的事」，會給你打開這個畫面的按鈕
"""
import logging
from typing import List

import discord
from discord import app_commands
from discord.ext import commands

from core import memory as mem
from core.ui import BaseView, Color, make_embed, respond, send_error

log = logging.getLogger(__name__)


def memory_embed(rows: List, note: str = "") -> discord.Embed:
    if rows:
        lines = "\n".join(f"{i}. {row['content']}" for i, row in enumerate(rows, 1))
        description = f"{note}\n\n{lines}" if note else lines
    else:
        description = (note + "\n\n" if note else "") + "我還沒記住你任何事。多聊幾句，我會慢慢認識你。"
    embed = make_embed("🧠 我記得你的事", description[:4096], Color.PRIMARY)
    embed.set_footer(text=f"{len(rows)} / {mem.MAX_MEMORIES} 條・只有你看得到・聊天時 AI 會自己新增或修改")
    return embed


class MemoryView(BaseView):
    owner_denied_message = "這是別人的記憶，自己用 /memory 看看吧！"

    def __init__(self, cog: "MemoryCog", guild_id: int, owner_id: int, rows: List):
        super().__init__(owner_id=owner_id, timeout=300)
        self.cog = cog
        self.guild_id = guild_id
        self.confirming = False
        if rows:
            select = discord.ui.Select(
                placeholder="選一條要我忘掉的事…",
                options=[discord.SelectOption(label=row["content"][:100], value=str(row["id"]), emoji="🗑️")
                         for row in rows[:25]])
            select.callback = self._make_forget_one(select)
            self.add_item(select)
            self.clear_button = discord.ui.Button(label="全部忘掉", emoji="🧹", style=discord.ButtonStyle.danger)
            self.clear_button.callback = self._clear
            self.add_item(self.clear_button)

    def _make_forget_one(self, select: discord.ui.Select):
        async def callback(interaction: discord.Interaction):
            await self.cog.db.delete_memory(self.guild_id, interaction.user.id, int(select.values[0]))
            await self.cog.refresh(interaction, "好啦，那件事我忘了。")
        return callback

    async def _clear(self, interaction: discord.Interaction):
        if not self.confirming:
            self.confirming = True
            self.clear_button.label = "確定全部忘掉？再按一次"
            return await interaction.response.edit_message(view=self)
        count = await self.cog.db.clear_memories(self.guild_id, interaction.user.id)
        log.info("AI 記憶：%s 清除了 %d 條", interaction.user.id, count)
        await self.cog.refresh(interaction, f"已經忘掉 {count} 件關於你的事。")


class MemoryCog(commands.Cog, name="Memory"):
    def __init__(self, bot):
        self.bot = bot

    @property
    def db(self):
        return self.bot.db

    async def refresh(self, interaction: discord.Interaction, note: str = "") -> None:
        rows = await self.db.list_memories(interaction.guild_id, interaction.user.id)
        view = MemoryView(self, interaction.guild_id, interaction.user.id, rows)
        await interaction.response.edit_message(embed=memory_embed(rows, note), view=view)
        view.message = interaction.message

    async def show(self, interaction: discord.Interaction) -> None:
        if not interaction.guild_id:
            return await send_error(interaction, "這個指令只能在伺服器裡用。")
        rows = await self.db.list_memories(interaction.guild_id, interaction.user.id)
        view = MemoryView(self, interaction.guild_id, interaction.user.id, rows)
        view.message = await respond(interaction, embed=memory_embed(rows), view=view, ephemeral=True)

    @app_commands.command(name="memory", description="看 AI 聊天時記住了你哪些事，可以刪掉（只有你看得到）")
    @app_commands.guild_only()
    async def memory_command(self, interaction: discord.Interaction):
        await self.show(interaction)


async def setup(bot):
    await bot.add_cog(MemoryCog(bot))
