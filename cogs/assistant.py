"""嘎哇聊天：用講的叫功能，或跟毒舌青蛙嘎哇聊天。AI 用主電腦的 Ollama，資料不上雲。

- 觸發：@嘎哇，或在 .env ASSISTANT_CHANNELS 設定的頻道直接講話
- 叫功能：AI 判斷要用哪個功能（core/assistant.py），嘎哇回一句話並附上按鈕；
  本人按下按鈕才執行，結果跟大廳按鈕完全一樣。AI 不能直接動到錢或資料。
- 閒聊：AI 用嘎哇的個性回話，記得這個頻道最近幾句對話（只存在記憶體，重啟就忘）
- 主電腦關機（Ollama 連不到）時，嘎哇回「大腦在睡覺」，斜線指令照常可以用
"""
import asyncio
import logging
import random
import re
import time
from collections import defaultdict, deque
from typing import Deque, Dict, List, Optional, Tuple

import discord
from discord.ext import commands

from cogs.hub import GAME_COGS
from cogs.reminders import RemindModal
from core import assistant as asst
from core.ai import AIError
from core.catalog import GAMES
from core.timeutil import now_tw, parse_when
from core.ui import BaseView, send_error

log = logging.getLogger(__name__)

USER_COOLDOWN = 5          # 每個人幾秒內只處理一句
SLEEP_NOTICE_EVERY = 60    # 主電腦關機時，同一個頻道多久回一次「在睡覺」
MAX_CONCURRENT = 2         # 同時最多幾個 AI 請求，避免把主電腦塞爆
HISTORY_KEEP = 20

# 叫功能時嘎哇說的話（固定台詞，不用再問一次 AI，回得快也不會亂講）
LINES: Dict[str, List[str]] = {
    "weather": ["要看天氣喔？下雨的話記得告訴我，本蛙最愛下雨天了，嘎！", "天氣自己按啦，我又不是氣象主播……好啦給你按鈕。"],
    "earthquake": ["地震？池塘有晃一下嗎……按這裡看最新的地震報告。", "別緊張，先看看中央氣象署怎麼說，嘎。"],
    "balance": ["又要看錢包？放心，荷葉底下沒有藏你的錢。", "自己的錢自己看，嘎。"],
    "checkin": ["簽到這種小事還要本蛙提醒？按啦。", "每天都要來簽到，不然本蛙會……才沒有想你，嘎！"],
    "work": ["終於想工作了？去抓蚊子……我是說去打工啦！", "勤勞的人類，本蛙勉強給你一個讚，嘎。"],
    "quests": ["今天的任務在這，做完再來跟我炫耀。", "任務清單給你，別又只看不做，嘎。"],
    "leaderboard": ["想知道誰最有錢？反正不是你……開玩笑的啦，自己看。", "排行榜在這，本蛙才不在乎排名，嘎嘎。"],
    "game": ["想玩喔？本蛙陪你，輸了不准哭，嘎！", "好啦好啦，選一個遊戲。"],
    "stock": ["股票的事本蛙不懂，但按這裡看得到報價。", "看盤可以，別把荷葉也押下去，嘎。"],
    "remind": ["要我提醒你？你自己的腦袋呢……好啦，確認一下。", "記性跟金魚一樣，嘎。按一下我就幫你記。"],
    "help": ["本蛙會的可多了，自己看說明。", "指令說明在這，看完別再問，嘎。"],
    "unsupported": ["想得美！這種事本蛙不幫忙，要的話自己用斜線指令。", "嘎？想騙本蛙？沒門，自己動手啦。"],
}
SLEEPING = ["嘎……大腦（主電腦）在睡覺，等它醒來再聊。斜線指令照常可以用喔。",
            "zzz……本蛙的大腦下線了，晚點再來找我，嘎。"]
FAILED = ["嘎？剛剛恍神了，再說一次。", "池塘有點吵沒聽清楚，再講一次好嗎？"]
SUMMONED = "（叫了你一聲）"

WEATHER_DAY = {"today": "今天", "tomorrow": "明天"}


def buttons_for(intent: asst.Intent) -> List[Tuple[str, str, str]]:
    """這個功能要給哪些按鈕：(動作, 按鈕文字, emoji)。閒聊和不支援的事沒有按鈕。"""
    name = intent.name
    if name == "weather":
        return [(f"weather:{intent.day}", f"看{WEATHER_DAY[intent.day]}的天氣", "🌦️")]
    if name == "earthquake":
        return [("earthquake", "最新地震報告", "🌏")]
    if name == "balance":
        return [("profile", "我的錢包與戰績", "👤")]
    if name == "checkin":
        return [("checkin", "簽到", "✨")]
    if name == "work":
        return [("work", "打工", "💼")]
    if name == "quests":
        return [("quests", "今天的任務", "🎯")]
    if name == "leaderboard":
        return [("leaderboard", "排行榜", "🏆")]
    if name == "help":
        return [("help", "指令說明", "📖")]
    if name == "stock":
        if intent.stock:
            return [(f"quote:{intent.stock}", f"查 {intent.stock} 的股價", "📈")]
        return [("stocks", "我的股票帳戶", "📈")]
    if name == "remind":
        when = asst.normalize_when(intent.when)
        if intent.message and when and parse_when(when):
            return [(f"remind:{when}|{intent.message}", f"{when} 提醒我：{intent.message}"[:80], "⏰")]
        return [("remind", "設定提醒", "⏰")]
    if name == "game":
        games = [g for g in GAMES if g.key == intent.game] or list(GAMES)
        return [(f"game:{g.key}", g.name, g.emoji) for g in games if g.key in GAME_COGS]
    return []


def strip_mention(content: str, bot_id: int) -> str:
    return re.sub(rf"<@!?{bot_id}>", "", content).strip()


class ActionView(BaseView):
    owner_denied_message = "這是別人叫嘎哇做的事，你自己 @嘎哇 說一次吧！"

    def __init__(self, cog: "Assistant", owner_id: int, actions: List[Tuple[str, str, str]]):
        super().__init__(owner_id=owner_id, timeout=300)
        self.cog = cog
        for i, (action, label, emoji) in enumerate(actions[:10]):
            button = discord.ui.Button(label=label, emoji=emoji, style=discord.ButtonStyle.primary, row=i // 5)
            button.callback = self._make_callback(action)
            self.add_item(button)

    def _make_callback(self, action: str):
        async def callback(interaction: discord.Interaction):
            await self.cog.run_action(interaction, action)
        return callback


class Assistant(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.channels = set(getattr(bot.settings, "assistant_channels", ()) or ())
        self.history: Dict[int, Deque[Tuple[str, str]]] = defaultdict(lambda: deque(maxlen=HISTORY_KEEP))
        self.last_message: Dict[int, float] = {}
        self.sleep_notice: Dict[int, float] = {}
        self.slots = asyncio.Semaphore(MAX_CONCURRENT)

    async def cog_load(self):
        where = f"@嘎哇，或在 {len(self.channels)} 個指定頻道直接講話" if self.channels else "@嘎哇"
        log.info("嘎哇聊天已啟用：%s", where)

    @property
    def ai(self):
        return self.bot.ai

    # ------------------------------------------------------------------ 判斷要不要回
    def addressed_text(self, message: discord.Message) -> Optional[str]:
        """對嘎哇說的話；不是在跟嘎哇說話就回傳 None。"""
        me = self.bot.user
        if not me or message.author.bot or not message.guild:
            return None
        replied_to_me = (message.reference and isinstance(message.reference.resolved, discord.Message)
                         and message.reference.resolved.author.id == me.id)
        if me in message.mentions or replied_to_me:
            return strip_mention(message.content, me.id) or SUMMONED
        if message.channel.id in self.channels:
            text = message.content.strip()
            return text or None
        return None

    def belongs_to_game(self, message: discord.Message, text: str) -> bool:
        """海龜湯提問、猜數字的數字交給遊戲處理，嘎哇不插嘴。"""
        soup = self.bot.get_cog("SeaTortoise")
        if soup and message.channel.id in soup.games and text.endswith(("?", "？")):
            return True
        guess = self.bot.get_cog("GuessNumber")
        return bool(guess and message.channel.id in guess.games and text.isdigit())

    # ------------------------------------------------------------------ 收到訊息
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        text = self.addressed_text(message)
        if text is None or self.belongs_to_game(message, text):
            return
        now = time.time()
        if now - self.last_message.get(message.author.id, 0) < USER_COOLDOWN:
            return await self._react(message, "⏳")
        self.last_message[message.author.id] = now

        if not self.ai.available:
            return await self._sleeping(message)
        try:
            async with self.slots, message.channel.typing():
                await self.respond(message, text)
        except AIError as e:
            log.info("嘎哇聊天：AI 無法回應（%s）", e)
            if not self.ai.available:
                return await self._sleeping(message)
            await self._reply(message, random.choice(FAILED))
        except discord.HTTPException:
            log.warning("嘎哇聊天：回覆失敗", exc_info=True)

    async def respond(self, message: discord.Message, text: str) -> None:
        name = message.author.display_name
        intent = (asst.Intent("chat") if text == SUMMONED
                  else await asst.classify(self.ai, text, name))
        history = self.history[message.channel.id]
        actions = buttons_for(intent)
        if intent.name in LINES and (actions or intent.name == "unsupported"):
            reply = random.choice(LINES[intent.name])
            view = ActionView(self, message.author.id, actions) if actions else None
            sent = await self._reply(message, reply, view=view)
            if view:
                view.message = sent
        else:
            reply = await asst.chat(self.ai, list(history), text, name) or random.choice(FAILED)
            await self._reply(message, reply)
        history.append((name, text))
        history.append(("嘎哇", reply))
        log.info("嘎哇聊天：%s → %s", text[:40], asst.describe(intent))

    async def _reply(self, message: discord.Message, content: str, view: Optional[discord.ui.View] = None):
        kwargs = {"view": view} if view else {}
        return await message.reply(content, mention_author=False,
                                   allowed_mentions=discord.AllowedMentions.none(), **kwargs)

    async def _sleeping(self, message: discord.Message) -> None:
        now = time.time()
        if now - self.sleep_notice.get(message.channel.id, 0) < SLEEP_NOTICE_EVERY:
            return await self._react(message, "💤")
        self.sleep_notice[message.channel.id] = now
        try:
            await self._reply(message, random.choice(SLEEPING))
        except discord.HTTPException:
            pass

    @staticmethod
    async def _react(message: discord.Message, emoji: str) -> None:
        try:
            await message.add_reaction(emoji)
        except discord.HTTPException:
            pass

    # ------------------------------------------------------------------ 按下按鈕
    async def run_action(self, interaction: discord.Interaction, action: str) -> None:
        """執行按鈕對應的功能。跟大廳按鈕走同一條路，結果只有按的人看得到。"""
        kind, _, arg = action.partition(":")
        hub = self.bot.get_cog("Hub")
        if kind in ("checkin", "work", "quests", "profile", "leaderboard", "help", "stocks", "game") and hub:
            return await hub.handle(interaction, action)
        if kind == "weather":
            cog = self.bot.get_cog("Weather")
            if cog:
                return await cog.weather_command.callback(cog, interaction, 1 if arg == "tomorrow" else 0, False)
        if kind == "earthquake":
            cog = self.bot.get_cog("Earthquake")
            if cog:
                return await cog.earthquake_command.callback(cog, interaction, False)
        if kind == "quote":
            cog = self.bot.get_cog("Stocks")
            if cog:
                return await cog.quote_command.callback(cog, interaction, arg)
        if kind == "remind":
            cog = self.bot.get_cog("Reminders")
            if cog and arg:
                when, _, content = arg.partition("|")
                return await cog.create(interaction, when, content)
            if cog:
                return await interaction.response.send_modal(RemindModal(cog))
        await send_error(interaction, "這個功能目前無法使用。")


async def setup(bot):
    await bot.add_cog(Assistant(bot))
