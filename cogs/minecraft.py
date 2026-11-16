"""Minecraft 伺服器管理（/mc）：在 Discord 開關這台電腦上的 Minecraft 伺服器。

- 背後呼叫控制腳本（.env 的 MINECRAFT_SCRIPT，預設 ~/.local/bin/mc），跟在主機上打 mc start 一樣。
- 指令只會出現在 MINECRAFT_GUILD_IDS 列出的伺服器；沒設定就不啟用。
- /mc status 大家都能看；開、關、重開、下指令、備份、看紀錄需要「管理伺服器」權限。
- 伺服器跑在 tmux 裡。如果 tmux 背景程式還沒在跑，bot 開出來的 tmux 會跟 bot 綁在一起，
  bot 重開（例如自動更新）時 Minecraft 會被直接砍掉、沒有存檔，所以這種情況拒絕開服。
"""
import asyncio
import logging
import os
import re
from typing import Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands

from core.ui import Color, defer, make_embed, respond, send_error

log = logging.getLogger(__name__)

START_TIMEOUT = 11 * 60     # mc start 最多等 10 分鐘
STOP_TIMEOUT = 5 * 60
QUICK_TIMEOUT = 60
MAX_OUTPUT = 1800
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
# tmux send-keys 會把整段剛好是按鍵名稱的字（例如 C-c、Enter）當成按鍵，C-c 會直接砍掉伺服器
_KEY_NAME = re.compile(r"^([CMS]-\S+|\^.|[A-Z][A-Za-z0-9]*)$")


def clean_output(text: str) -> str:
    text = _ANSI.sub("", text or "").replace("\r", "")
    text = text.replace("```", "ˋˋˋ").strip()
    return text if len(text) <= MAX_OUTPUT else "…\n" + text[-MAX_OUTPUT:]


def code_block(text: str) -> str:
    return f"```\n{clean_output(text) or '（沒有輸出）'}\n```"


def check_command(text: str) -> Tuple[Optional[str], Optional[str]]:
    """回傳 (要送出的指令, 錯誤訊息)。"""
    command = " ".join((text or "").split()).lstrip("/")
    if not command:
        return None, "請輸入指令，例如 `list`、`say 大家好`、`op 玩家名稱`。"
    if len(command) > 200:
        return None, "指令太長了。"
    if command.split()[0].lower() == "stop":
        return None, "關服請用 /mc stop，才會先通知玩家並備份。"
    if _KEY_NAME.match(command):
        return None, "這看起來不是 Minecraft 指令。"
    return command, None


async def run_script(script: str, *args: str, timeout: float = QUICK_TIMEOUT) -> Tuple[int, str]:
    """執行控制腳本，回傳 (結束代碼, 輸出)。逾時回傳 -1。"""
    proc = await asyncio.create_subprocess_exec(script, *args, stdin=asyncio.subprocess.DEVNULL,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return -1, "（等太久了，指令可能還在背景執行，請稍後用 /mc status 確認）"
    return proc.returncode, out.decode("utf-8", "replace")


async def tmux_server_ok() -> Tuple[bool, str]:
    """tmux 背景程式有在跑，而且不是 bot 自己開出來的（不然 bot 重開會連 Minecraft 一起砍掉）。"""
    try:
        proc = await asyncio.create_subprocess_exec("tmux", "display-message", "-p", "#{pid}",
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(), 10)
    except (OSError, asyncio.TimeoutError):
        return False, "找不到 tmux。"
    pid = out.decode().strip()
    if proc.returncode != 0 or not pid.isdigit():
        return False, ("主機上的 tmux 背景程式沒有在跑（平常開機時 playit 會把它開起來）。"
                       "請先在主機上執行一次 `mc start`。")
    try:
        with open(f"/proc/{pid}/cgroup", encoding="utf-8") as f:
            if "gawa-bot" in f.read():
                return False, "tmux 是 bot 自己開的，bot 重開時會把 Minecraft 一起關掉。請在主機上關掉 tmux 後重新執行 `mc start`。"
    except OSError:
        pass
    return True, ""


def is_admin(interaction: discord.Interaction) -> bool:
    perms = getattr(interaction.user, "guild_permissions", None)
    return bool(perms and perms.manage_guild)


class Minecraft(commands.GroupCog, group_name="mc", group_description="Minecraft 伺服器：開關、狀態、下指令、備份"):
    def __init__(self, bot, script: str):
        self.bot = bot
        self.script = script
        self.busy: Optional[str] = None   # 正在進行的開服／關服動作，同時只能一個

    async def admin_only(self, interaction: discord.Interaction) -> bool:
        if not is_admin(interaction):
            await send_error(interaction, "只有有「管理伺服器」權限的人可以操作 Minecraft 伺服器。")
            return False
        return True

    async def audit(self, interaction: discord.Interaction, action: str) -> None:
        log.info("%s（%s）執行 /mc %s", interaction.user, interaction.user.id, action)
        try:
            await self.bot.db.audit("minecraft", interaction.user.id, f"/mc {action}", None)
        except Exception:
            log.warning("寫入稽核紀錄失敗", exc_info=True)

    async def long_action(self, interaction: discord.Interaction, action: str, title: str, timeout: float) -> None:
        """開服、關服、重開、備份：同時只能做一件，結果公開傳到頻道。"""
        if not await self.admin_only(interaction):
            return
        if self.busy:
            return await send_error(interaction, f"正在{self.busy}，請等它完成。")
        if action in ("start", "restart"):
            ok, reason = await tmux_server_ok()
            if not ok:
                return await send_error(interaction, reason)
        self.busy = title
        try:
            await defer(interaction)
            await self.audit(interaction, action)
            code, out = await run_script(self.script, action, timeout=timeout)
        finally:
            self.busy = None
        good = code == 0
        embed = make_embed(f"{'✅' if good else '⚠️'} Minecraft・{title}", code_block(out),
                           Color.SUCCESS if good else Color.WARNING)
        embed.set_footer(text=f"由 {interaction.user.display_name} 操作")
        try:
            await respond(interaction, embed=embed)
        except discord.HTTPException:   # 開服太久，互動逾時了：直接傳到頻道
            if interaction.channel:
                await interaction.channel.send(embed=embed)

    @app_commands.command(name="status", description="伺服器開著沒、幾個人在線、最新備份")
    async def status(self, interaction: discord.Interaction):
        await defer(interaction)
        code, out = await run_script(self.script, "status")
        busy = f"\n⏳ 正在{self.busy}…" if self.busy else ""
        color = Color.SUCCESS if "🟢" in out else Color.MUTED
        await respond(interaction, embed=make_embed("⛏️ Minecraft 伺服器", code_block(out) + busy, color))

    @app_commands.command(name="start", description="開伺服器（管理員，約需幾分鐘）")
    async def start(self, interaction: discord.Interaction):
        await self.long_action(interaction, "start", "開服", START_TIMEOUT)

    @app_commands.command(name="stop", description="關伺服器：通知玩家 → 存檔 → 關閉 → 備份（管理員）")
    async def stop(self, interaction: discord.Interaction):
        await self.long_action(interaction, "stop", "關服", STOP_TIMEOUT)

    @app_commands.command(name="restart", description="重開伺服器（管理員）")
    async def restart(self, interaction: discord.Interaction):
        await self.long_action(interaction, "restart", "重開", STOP_TIMEOUT + START_TIMEOUT)

    @app_commands.command(name="backup", description="立刻備份一次（管理員）")
    async def backup(self, interaction: discord.Interaction):
        await self.long_action(interaction, "backup", "備份", STOP_TIMEOUT)

    @app_commands.command(name="cmd", description="在伺服器控制台下指令，例如 list、say 大家好、op 玩家（管理員）")
    @app_commands.describe(command="Minecraft 指令（不用加 /）")
    async def cmd(self, interaction: discord.Interaction, command: str):
        if not await self.admin_only(interaction):
            return
        text, error = check_command(command)
        if error:
            return await send_error(interaction, error)
        await defer(interaction, ephemeral=True)
        await self.audit(interaction, f"cmd {text}")
        code, out = await run_script(self.script, "cmd", text)
        await respond(interaction, embed=make_embed(f"⌨️ {text}", code_block(out), Color.PRIMARY), ephemeral=True)

    @app_commands.command(name="log", description="看伺服器最近的紀錄（管理員）")
    @app_commands.describe(lines="要看幾行（預設 30）")
    async def log_command(self, interaction: discord.Interaction, lines: app_commands.Range[int, 5, 100] = 30):
        if not await self.admin_only(interaction):
            return
        await defer(interaction, ephemeral=True)
        code, out = await run_script(self.script, "log", str(lines))
        await respond(interaction, embed=make_embed("📜 伺服器紀錄", code_block(out), Color.MUTED), ephemeral=True)


async def setup(bot):
    settings = bot.settings
    script = getattr(settings, "minecraft_script", None)
    guild_ids = getattr(settings, "minecraft_guild_ids", ())
    if not guild_ids or not script or not os.access(script, os.X_OK):
        log.info("Minecraft 管理未啟用：%s", "沒有設定 MINECRAFT_GUILD_IDS" if not guild_ids
                 else f"找不到可執行的控制腳本 {script}")
        return
    await bot.add_cog(Minecraft(bot, script), guilds=[discord.Object(id=g) for g in guild_ids])
    log.info("Minecraft 管理已啟用（%d 個伺服器）", len(guild_ids))
