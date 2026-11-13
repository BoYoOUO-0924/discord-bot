"""GitHub 同步：定時讀綁定了 repo 的專案，把新動態發到專案頻道，commit 提到 task#N 就完成待辦。
綁定 repo 用 /project github（在 cogs/projects.py）。"""
import logging
import time
from typing import Dict

import discord
from discord.ext import commands, tasks

from core import github as gh
from core.features import enabled
from core.ui import Color, make_embed

log = logging.getLogger(__name__)

MAX_LINES = 15


class GitHubSync(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.client = gh.GitHubClient(getattr(bot.settings, "github_token", None))
        self.last_error: Dict[int, str] = {}

    async def cog_load(self):
        self.poll_loop.change_interval(minutes=self.client.poll_minutes)
        self.poll_loop.start()
        mode = "有 GITHUB_TOKEN" if self.client.token else "匿名（只能讀公開 repo）"
        log.info("GitHub 同步已啟用：每 %d 分鐘檢查一次，%s", self.client.poll_minutes, mode)

    async def cog_unload(self):
        self.poll_loop.cancel()
        await self.client.close()

    @tasks.loop(minutes=gh.POLL_MINUTES_ANONYMOUS)
    async def poll_loop(self):
        if time.time() < self.client.rate_limited_until:
            return
        for project in await self.bot.db.list_projects():
            if project["github_repo"] and enabled(self.bot, "tasks", project["guild_id"]):
                await self.sync(project)

    @poll_loop.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()

    async def sync(self, project) -> None:
        state = gh.State.load(project["github_state"])
        try:
            result = await self.client.poll(project["github_repo"], state)
        except gh.GitHubError as e:
            # 同一個錯誤只記一次，不要每 10 分鐘洗一次紀錄
            if self.last_error.get(project["id"]) != str(e):
                log.warning("GitHub 同步「%s」（%s）失敗：%s", project["name"], project["github_repo"], e)
                self.last_error[project["id"]] = str(e)
            return
        self.last_error.pop(project["id"], None)
        await self.bot.db.update_project(project["id"], github_state=state.dump())

        lines = [f"[{n.text}]({n.url})" if n.url else n.text for n in result.notices]
        lines += await self.complete_tasks(project, result.task_refs)
        if lines:
            await self.post(project, lines)

    async def complete_tasks(self, project, refs: Dict[int, str]):
        """commit 提到的待辦：同一個伺服器、還沒完成的才處理。"""
        cog = self.bot.get_cog("Tasks")
        lines = []
        for number, sha in refs.items():
            task = await self.bot.db.get_task(number)
            if not cog or not task or task["guild_id"] != project["guild_id"] or task["status"] == "done":
                continue
            if await cog.complete(number, 0):
                url = f"https://github.com/{project['github_repo']}/commit/{sha}"
                lines.append(f"🔗 commit [{sha}]({url}) 完成了待辦 #{number}「{task['title'][:60]}」")
                log.info("GitHub commit %s 完成待辦 #%d", sha, number)
        return lines

    async def post(self, project, lines) -> None:
        more = len(lines) - MAX_LINES
        text = "\n".join(lines[:MAX_LINES]) + (f"\n…還有 {more} 則" if more > 0 else "")
        embed = make_embed(f"🐙 {project['github_repo']}", text[:4000], Color.PRIMARY)
        try:
            channel = self.bot.get_channel(project["channel_id"]) or await self.bot.fetch_channel(project["channel_id"])
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            log.info("專案「%s」的頻道不存在或沒有權限，略過 GitHub 通知", project["name"])


async def setup(bot):
    await bot.add_cog(GitHubSync(bot))
