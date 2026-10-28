"""自動更新：定期檢查 GitHub，有通過測試的新版本就更新並重新啟動。

只有設定 AUTO_UPDATE=1 時才會啟用（deploy/windows/2_start.bat 會自動設定），
開發用的電腦不會被自動覆蓋。
"""
import asyncio
import logging
import os
import shutil
import time
from typing import Optional

import aiohttp
from discord.ext import commands, tasks

from core import updater

log = logging.getLogger(__name__)

CHECK_MINUTES = 10
MAX_DEFER = 60 * 60   # 有遊戲進行中時最多延後一小時
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Updater(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.deferred_since: Optional[float] = None
        self.last_notice: Optional[str] = None
        self.restart_task: Optional[asyncio.Task] = None

    async def cog_load(self):
        if not self.bot.settings.auto_update:
            log.info("自動更新未啟用（用 deploy/windows/2_start.bat 啟動時才會開啟）")
            return
        if not shutil.which("git"):
            log.warning("找不到 git，無法自動更新。請先安裝 Git：winget install Git.Git")
            return
        if not os.path.isdir(os.path.join(PROJECT_ROOT, ".git")):
            log.warning("專案不是用 git clone 下載的，無法自動更新")
            return
        log.info("自動更新已啟用，每 %d 分鐘檢查一次 GitHub", CHECK_MINUTES)
        self.check_loop.start()

    async def cog_unload(self):
        self.check_loop.cancel()

    def busy(self) -> bool:
        """有牌局或遊戲進行中就先不重啟，免得打斷大家。"""
        return any(getattr(cog, attr, None) for cog in self.bot.cogs.values() for attr in ("games", "lobbies"))

    def _notice(self, level: int, message: str) -> None:
        # 同樣的狀況只記一次，避免每 10 分鐘洗一行紀錄
        if message != self.last_notice:
            log.log(level, message)
            self.last_notice = message

    @tasks.loop(minutes=CHECK_MINUTES)
    async def check_loop(self):
        try:
            await self.check_once()
        except updater.UpdateError as e:
            self._notice(logging.WARNING, f"自動更新檢查失敗：{e}")

    @check_loop.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()

    async def check_once(self) -> None:
        status = await updater.check(PROJECT_ROOT)
        ci = None
        if status.behind and not status.ahead and not status.dirty:
            slug = updater.parse_github_repo(await updater.git(PROJECT_ROOT, "remote", "get-url", "origin"))
            if not slug:
                raise updater.UpdateError("origin 不是 GitHub 網址，無法確認自動測試結果")
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
                ci = await updater.ci_state(session, slug, status.latest)

        action, reason = updater.decide(status, ci)
        if action != "update":
            self._notice(logging.INFO if action in ("current", "wait") else logging.WARNING, f"自動更新：{reason}")
            self.deferred_since = None
            return

        if self.busy():
            self.deferred_since = self.deferred_since or time.time()
            if time.time() - self.deferred_since < MAX_DEFER:
                self._notice(logging.INFO, "自動更新：有遊戲正在進行，等結束後再更新")
                return
            log.warning("自動更新：遊戲進行超過一小時，仍然進行更新（未結算的賭注會自動退還）")

        await updater.apply(PROJECT_ROOT)
        log.info("自動更新：%s → %s（%s），重新啟動中", status.current[:7], status.latest[:7], reason)
        # 關閉 bot 時會卸載這個 cog 並取消 check_loop；如果直接在迴圈裡 await，
        # 關閉流程會跟著被取消，指令都卸載了卻沒斷線、也不會重啟，所以改在獨立的工作裡關閉
        self.restart_task = asyncio.create_task(self.bot.request_restart())


async def setup(bot):
    await bot.add_cog(Updater(bot))
