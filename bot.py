"""Discord Bot 主程式（斜線指令版）。"""
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

import discord
from discord import app_commands
from discord.ext import commands

from core.ai import create_ai
from core.config import legacy_keys, load_settings
from core.db import Database
from core.instance import acquire_single_instance
from core.i18n import ZhTranslator
from core.ui import send_error

log = logging.getLogger("bot")

EXTENSIONS = [
    "cogs.hub",
    "cogs.economy",
    "cogs.earn",
    "cogs.reminders",
    "cogs.polls",
    "cogs.admin",
    "cogs.blackjack",
    "cogs.slots",
    "cogs.guess_number",
    "cogs.tictactoe",
    "cogs.poker",
    "cogs.seatortoise",
    "cogs.weather",
    "cogs.updater",
    "cogs.webadmin",
]

# 結束代碼 3 代表「已更新，請立刻重新啟動」，deploy/windows/2_start.bat 會依此重啟
RESTART_EXIT_CODE = 3
# 結束代碼 4 代表「已經有另一個 bot 在執行」，啟動腳本不會一直重試
ALREADY_RUNNING_EXIT_CODE = 4


class AppTree(app_commands.CommandTree):
    """所有斜線指令的共同入口：記錄伺服器成員、統一處理錯誤。"""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # 自動完成每打一個字就會觸發一次，不需要記錄
        if interaction.guild_id and interaction.type is discord.InteractionType.application_command:
            db = interaction.client.db
            await db.touch_member(interaction.guild_id, interaction.user.id)
            # 記下名稱，管理後台才能用名字搜尋（名稱沒變就不會寫資料庫）
            await db.remember_user(interaction.user.id, interaction.user.display_name,
                                   interaction.user.display_avatar.url)
        return True

    async def on_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.CommandOnCooldown):
            message = f"指令冷卻中，請 {error.retry_after:.0f} 秒後再試。"
        elif isinstance(error, app_commands.MissingPermissions):
            message = "你沒有使用這個指令的權限。"
        elif isinstance(error, app_commands.BotMissingPermissions):
            missing = "、".join(error.missing_permissions)
            message = f"我缺少這些權限：{missing}，請管理員協助設定。"
        elif isinstance(error, app_commands.NoPrivateMessage):
            message = "這個指令只能在伺服器裡使用。"
        elif isinstance(error, app_commands.CheckFailure):
            message = "目前無法使用這個指令。"
        else:
            log.exception("指令 /%s 發生錯誤", interaction.command.name if interaction.command else "?", exc_info=error)
            message = "執行指令時發生錯誤，請稍後再試。"
        await send_error(interaction, message)


class Bot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True  # 猜數字、海龜湯要讀取頻道訊息
        super().__init__(command_prefix=commands.when_mentioned, intents=intents,
                         help_command=None, tree_cls=AppTree)
        self.settings = load_settings()
        self.db = Database(self.settings.database_path)
        self.ai = create_ai(self.settings)
        self.restart_requested = False

    async def request_restart(self) -> None:
        """關閉 bot 並以 RESTART_EXIT_CODE 結束，讓啟動腳本馬上重新啟動。"""
        self.restart_requested = True
        await self.close()

    async def setup_hook(self) -> None:
        await self.db.connect()
        data_dir = os.path.dirname(self.settings.database_path)
        imported = await self.db.migrate_legacy_json(data_dir)
        if imported:
            log.info("已從舊版 JSON 匯入 %d 位使用者的積分與簽到資料", imported)
        refunded = await self.db.refund_all_holds()
        if refunded:
            log.info("已退還 %d 筆上次未結算的賭注", refunded)
        if self.ai.configured:
            await self.ai.check()   # 實際測試一次，不能用的話海龜湯會顯示「暫停」
        else:
            log.warning("沒有設定 AI（OLLAMA_URL 或 GEMINI_API_KEY），海龜湯會停用")

        await self.tree.set_translator(ZhTranslator())
        for ext in EXTENSIONS:
            await self.load_extension(ext)

        if self.settings.dev_guild_id:
            # 開發用：同步到指定伺服器，指令會立刻出現
            guild = discord.Object(id=self.settings.dev_guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
        else:
            synced = await self.tree.sync()
        self.command_ids = {command.name: command.id for command in synced}
        log.info("已同步 %d 個斜線指令", len(synced))

    def mention(self, name: str) -> str:
        """可點擊的指令連結；還沒同步時退回純文字。"""
        command_id = getattr(self, "command_ids", {}).get(name)
        return f"</{name}:{command_id}>" if command_id else f"`/{name}`"

    async def on_message(self, message: discord.Message) -> None:
        # 只用斜線指令，不處理文字指令（各功能的訊息監聽不受影響）
        return

    async def on_ready(self) -> None:
        log.info("已登入為 %s（%d 個伺服器）", self.user, len(self.guilds))

    async def close(self) -> None:
        await super().close()
        await self.db.close()


def setup_file_logging() -> None:
    """紀錄同時寫進 logs/bot.log（每個 5 MB，保留 3 份），無人看管時也查得到問題。"""
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)
    handler = RotatingFileHandler(os.path.join(log_dir, "bot.log"), maxBytes=5 * 1024 * 1024,
                                  backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("[{asctime}] [{levelname:<8}] {name}: {message}",
                                           "%Y-%m-%d %H:%M:%S", style="{"))
    logging.getLogger().addHandler(handler)


def keep_system_awake() -> None:
    """Windows：bot 執行期間告訴系統「正在工作」，不會因閒置而自動睡眠。

    只擋閒置睡眠；蓋上螢幕或按電源鍵仍依電源設定，見 deploy/windows/4_keep_awake.bat。
    """
    if sys.platform != "win32":
        return
    import ctypes
    ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
    if not ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED):
        log.warning("無法設定防止睡眠")


def main() -> None:
    discord.utils.setup_logging()
    setup_file_logging()
    keep_system_awake()
    try:
        bot = Bot()
        lock = acquire_single_instance(os.path.join(os.path.dirname(bot.settings.database_path), "bot.lock"))
        if lock is None:
            log.critical("已經有另一個 bot 在執行（可能開了兩個 Gawa Bot 視窗），這個不啟動。")
            sys.exit(ALREADY_RUNNING_EXIT_CODE)
        if not bot.settings.discord_token:
            log.critical("找不到 Bot Token：請在 .env 設定 DISCORD_TOKEN（範本見 .env.example）。")
            raise SystemExit(1)
        if legacy_keys:
            log.warning("這些設定還寫在舊的 config.py：%s。請搬到 .env（範本見 .env.example），之後就可以刪掉 config.py",
                        "、".join(legacy_keys))
        bot.run(bot.settings.discord_token, log_handler=None)
    except Exception:
        # 啟動失敗或異常結束也寫進 logs/bot.log，管理後台的「伺服器紀錄」才看得到
        log.critical("bot 異常結束", exc_info=True)
        raise
    if bot.restart_requested:
        sys.exit(RESTART_EXIT_CODE)


if __name__ == "__main__":
    main()
