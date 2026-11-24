"""統一的視覺風格與互動元件。

所有功能共用同一套顏色、錯誤提示與按鈕行為，讓整個 bot 看起來是同一個產品。
"""
import logging
import time
from typing import Optional

import discord

log = logging.getLogger(__name__)


class Color:
    PRIMARY = 0x5865F2   # 一般資訊
    SUCCESS = 0x57F287   # 成功、獲勝
    DANGER = 0xED4245    # 錯誤、落敗
    WARNING = 0xFEE75C   # 提醒、注意
    GOLD = 0xF1C40F      # 大獎、排行榜
    MUTED = 0x99AAB5     # 已結束
    GAME = 0x3498DB      # 遊戲進行中
    TABLE = 0x1F8B4C     # 牌桌綠


def make_embed(title: str, description: Optional[str] = None, color: int = Color.PRIMARY) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=color)


def error_embed(message: str) -> discord.Embed:
    return discord.Embed(description=f"❌ {message}", color=Color.DANGER)


def success_embed(message: str) -> discord.Embed:
    return discord.Embed(description=f"✅ {message}", color=Color.SUCCESS)


def fmt_points(n: int) -> str:
    return f"{n:,} 元"


def fmt_signed(n: int) -> str:
    return f"+{n:,}" if n > 0 else f"{n:,}"


def progress_bar(fraction: float, length: int = 10) -> str:
    fraction = max(0.0, min(1.0, fraction))
    filled = round(fraction * length)
    return "█" * filled + "░" * (length - filled)


async def respond(interaction: discord.Interaction, *, ephemeral: bool = False, **kwargs):
    """不論互動是否已回應過，都能正確送出訊息，並回傳之後可編輯的訊息物件。

    透過互動權杖編輯訊息只在 15 分鐘內有效，所以公開訊息改回傳以 bot 身分編輯的
    PartialMessage，遊戲進行再久都能更新畫面。私密訊息只能用互動權杖編輯。
    """
    if interaction.response.is_done():
        extras = getattr(interaction, "extras", None)
        if extras and extras.pop("private_errors", False) and ephemeral:
            # 公開的「思考中…」補上的第一則訊息會跟著變公開，所以要先刪掉它，私密訊息才會真的私密
            try:
                await interaction.delete_original_response()
            except discord.HTTPException:
                pass
        message = await interaction.followup.send(ephemeral=ephemeral, wait=True, **kwargs)
    else:
        await interaction.response.send_message(ephemeral=ephemeral, **kwargs)
        message = await interaction.original_response()
    channel = interaction.channel
    if not ephemeral and channel is not None and hasattr(channel, "get_partial_message"):
        return channel.get_partial_message(message.id)
    return message


async def defer(interaction: discord.Interaction, *, ephemeral: bool = False, private_errors: bool = False) -> None:
    """先回「思考中…」。Discord 要求 3 秒內回應，要查較多資料的指令先呼叫這個，就有 15 分鐘可以處理。

    之後用 respond() 送出結果即可；ephemeral 必須和最後要送的訊息一致。
    例外：private_errors=True 時先公開「思考中…」，之後 respond(ephemeral=True) 或 send_error 仍會是私密的
    （例如下注指令：成功要公開，存款不足只給本人看）。
    """
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=ephemeral, thinking=True)
        extras = getattr(interaction, "extras", None)
        if private_errors and not ephemeral and extras is not None:
            extras["private_errors"] = True


SLOW_ARRIVAL = 1.0      # 互動送到 bot 超過這個秒數就記下來
UNKNOWN_INTERACTION = 10062


def note_arrival(interaction: discord.Interaction) -> None:
    """記下互動送達的時間；Discord 只等 3 秒，送達就慢的話是網路（或程式卡住），不是指令本身慢。"""
    now = time.time()
    interaction.extras["arrived"] = now
    delay = now - interaction.created_at.timestamp()
    if delay >= SLOW_ARRIVAL:
        log.warning("互動送達延遲 %.2f 秒（%s）", delay, describe_interaction(interaction))


def describe_interaction(interaction: discord.Interaction) -> str:
    command = getattr(interaction, "command", None)
    if command is not None:
        return f"/{command.qualified_name}"
    data = getattr(interaction, "data", None) or {}
    return f"按鈕或表單 {data.get('custom_id', '?')}"


def timing(interaction: discord.Interaction) -> str:
    """「送達延遲 0.21 秒，送達後 2.95 秒」：分辨是網路慢還是程式慢。"""
    created, now = interaction.created_at.timestamp(), time.time()
    arrived = interaction.extras.get("arrived")
    if arrived is None:
        return f"建立後 {now - created:.2f} 秒"
    return f"送達延遲 {arrived - created:.2f} 秒，送達後 {now - arrived:.2f} 秒"


def is_expired(error: BaseException) -> bool:
    """互動已經失效（沒在 3 秒內回應）。指令的錯誤會被包一層，要看原本的錯誤。"""
    error = getattr(error, "original", error)
    return isinstance(error, discord.NotFound) and error.code == UNKNOWN_INTERACTION


def log_interaction_error(interaction: discord.Interaction, error: Exception, where: str) -> bool:
    """記錄互動錯誤。逾時的互動只記一行加上計時，並回傳 False（回覆也送不出去了）。"""
    if is_expired(error):
        log.warning("%s 回應逾時（10062）：%s，%s", where, describe_interaction(interaction), timing(interaction),
                    exc_info=error)
        return False
    log.error("%s 發生錯誤", where, exc_info=error)
    return True


async def send_error(interaction: discord.Interaction, message: str) -> None:
    """錯誤一律只讓當事人看到，不洗頻道。"""
    try:
        await respond(interaction, embed=error_embed(message), ephemeral=True)
    except discord.HTTPException:
        log.warning("無法送出錯誤訊息：%s", message)


class BaseView(discord.ui.View):
    """所有按鈕介面的基底。

    - owner_id：只有擁有者能操作，其他人按會收到私密提示
    - 逾時自動停用所有按鈕，避免留下按了沒反應的介面
    - 未預期的錯誤會回覆友善訊息並記錄到主控台
    """

    owner_denied_message = "這不是你的介面喔！"

    def __init__(self, *, owner_id: Optional[int] = None, timeout: Optional[float] = 180):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self.message: Optional[discord.Message] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.owner_id is not None and interaction.user.id != self.owner_id:
            await send_error(interaction, self.owner_denied_message)
            return False
        return True

    def disable_all(self) -> None:
        for item in self.children:
            if hasattr(item, "disabled"):
                item.disabled = True

    async def on_timeout(self) -> None:
        self.disable_all()
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        if log_interaction_error(interaction, error, f"介面操作（{type(self).__name__}）"):
            await send_error(interaction, "操作時發生錯誤，請稍後再試。")


class AmountModal(discord.ui.Modal):
    """輸入金額的通用表單，送出後呼叫 callback(interaction, amount)。"""

    def __init__(self, title: str, label: str, callback, *, default: Optional[int] = None,
                 min_value: int = 0, max_value: int = 10**9, placeholder: Optional[str] = None):
        super().__init__(title=title)
        self._callback = callback
        self.min_value, self.max_value = min_value, max_value
        self.amount = discord.ui.TextInput(
            label=label, default=str(default) if default is not None else None,
            placeholder=placeholder or f"{min_value} 以上的整數", max_length=12,
        )
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        text = self.amount.value.replace(",", "").strip()
        if not text.isdigit():
            return await send_error(interaction, "請輸入整數金額。")
        value = int(text)
        if not self.min_value <= value <= self.max_value:
            return await send_error(interaction, f"金額需介於 {self.min_value:,} 到 {self.max_value:,} 之間。")
        await self._callback(interaction, value)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        if log_interaction_error(interaction, error, "表單"):
            await send_error(interaction, "處理表單時發生錯誤，請稍後再試。")
