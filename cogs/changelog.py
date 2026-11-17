"""更新日誌（/changelog）：看機器人更新了哪些功能，從最新的開始，用按鈕翻頁。

- 內容來自 git commit 紀錄（core/changelog.py），每頁 5 筆
- 只有自己看得到；按鈕只有叫出來的人能按，5 分鐘沒動作就停用
- 也可以從大廳的「更新日誌」按鈕，或 @嘎哇 最近更新了什麼 打開
"""
import logging
from typing import List, Optional

import discord
from discord import app_commands
from discord.ext import commands

from core import changelog as cl
from core.ui import BaseView, Color, make_embed, send_error

log = logging.getLogger(__name__)


def page_embed(entries: List[cl.Entry], page: int, repo: Optional[str] = None) -> discord.Embed:
    total_pages = cl.page_count(len(entries))
    page = max(0, min(page, total_pages - 1))
    items = cl.page_slice(entries, page)
    if items:
        description = "\n\n".join(cl.format_entry(e, repo) for e in items)
    else:
        description = "目前還沒有任何更新紀錄。"
    embed = make_embed("📰 更新日誌", description[:4096], Color.PRIMARY)
    embed.set_footer(text=f"第 {page + 1} / {total_pages} 頁・共 {len(entries)} 筆更新・最新的在最前面")
    return embed


class ChangelogView(BaseView):
    owner_denied_message = "這是別人打開的更新日誌，自己用 /changelog 打開一份吧！"

    def __init__(self, entries: List[cl.Entry], repo: Optional[str], owner_id: int, page: int = 0):
        super().__init__(owner_id=owner_id, timeout=300)
        self.entries = entries
        self.repo = repo
        self.total = cl.page_count(len(entries))
        self.page = max(0, min(page, self.total - 1))
        self.refresh()

    def embed(self) -> discord.Embed:
        return page_embed(self.entries, self.page, self.repo)

    def refresh(self) -> None:
        first, last = self.page == 0, self.page >= self.total - 1
        self.newest.disabled = first
        self.previous.disabled = first
        self.next.disabled = last
        self.oldest.disabled = last
        self.indicator.label = f"{self.page + 1} / {self.total}"

    async def go(self, interaction: discord.Interaction, page: int) -> None:
        self.page = max(0, min(page, self.total - 1))
        self.refresh()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(emoji="⏮️", style=discord.ButtonStyle.secondary)
    async def newest(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.go(interaction, 0)

    @discord.ui.button(label="上一頁", emoji="◀️", style=discord.ButtonStyle.primary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.go(interaction, self.page - 1)

    @discord.ui.button(label="1 / 1", style=discord.ButtonStyle.secondary, disabled=True)
    async def indicator(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass

    @discord.ui.button(label="下一頁", emoji="▶️", style=discord.ButtonStyle.primary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.go(interaction, self.page + 1)

    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary)
    async def oldest(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.go(interaction, self.total - 1)


class Changelog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.log = cl.Changelog()

    async def send(self, interaction: discord.Interaction, page: int = 0) -> None:
        """打開更新日誌（斜線指令、大廳按鈕、嘎哇按鈕都走這裡）。結果只有自己看得到。"""
        try:
            entries = await self.log.get()
        except cl.ChangelogError as e:
            log.warning("更新日誌：%s", e.__cause__ or e)
            return await send_error(interaction, str(e))
        view = ChangelogView(entries, self.log.repo, interaction.user.id, page)
        await interaction.response.send_message(embed=view.embed(), view=view, ephemeral=True)
        view.message = await interaction.original_response()

    @app_commands.command(name="changelog", description="看機器人更新了哪些功能（最新的在最前面）")
    @app_commands.describe(page="從第幾頁開始看，不填就是最新的一頁")
    async def changelog_command(self, interaction: discord.Interaction,
                                page: Optional[app_commands.Range[int, 1, 1000]] = None):
        await self.send(interaction, (page or 1) - 1)


async def setup(bot):
    await bot.add_cog(Changelog(bot))
