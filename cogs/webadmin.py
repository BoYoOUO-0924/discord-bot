"""啟動管理後台（網頁）。有設定 ADMIN_PASSWORD 才會啟動。"""
import logging
import os
import socket

from discord.ext import commands

from webadmin.server import AdminServer, password_problem

log = logging.getLogger(__name__)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def lan_addresses() -> list:
    """這台電腦在區域網路的 IP，方便告訴使用者要連哪個網址。"""
    try:
        return sorted({info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
                       if not info[4][0].startswith("127.")})
    except OSError:
        return []


class WebAdmin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.server = None

    async def cog_load(self):
        settings = self.bot.settings
        problem = password_problem(settings.admin_password)
        if problem:
            log.info("管理後台未啟動：%s", problem)
            return
        self.server = AdminServer(self.bot, settings.admin_password, settings.admin_host, settings.admin_port,
                                  os.path.join(PROJECT_ROOT, "logs", "bot.log"))
        try:
            await self.server.start()
        except OSError as e:
            log.error("管理後台無法啟動（連接埠 %s 可能被占用）：%s", settings.admin_port, e)
            self.server = None
            return
        urls = [f"http://{ip}:{settings.admin_port}" for ip in lan_addresses()] or [f"http://localhost:{settings.admin_port}"]
        log.info("管理後台已啟動：%s", "、".join(urls))

    async def cog_unload(self):
        if self.server:
            await self.server.stop()


async def setup(bot):
    await bot.add_cog(WebAdmin(bot))
