"""斜線指令名稱的中文化。

指令本身用英文命名（打字方便），Discord 介面語言是中文的使用者會看到這裡對應的中文名稱。
說明文字直接用中文撰寫，不需要翻譯。
"""
from typing import Optional

from discord import Locale, app_commands
from discord.app_commands import TranslationContextLocation as Loc

COMMAND_NAMES = {
    "menu": "選單",
    "help": "說明",
    "checkin": "簽到",
    "profile": "個人資料",
    "leaderboard": "排行榜",
    "give": "轉帳",
    "work": "打工",
    "quests": "每日任務",
    "remind": "提醒",
    "reminders": "我的提醒",
    "poll": "投票",
    "clear": "清除訊息",
    "blackjack": "21點",
    "slots": "拉霸機",
    "guess": "猜數字",
    "tictactoe": "井字遊戲",
    "poker": "德州撲克",
    "seatortoise": "海龜湯",
    "weather": "天氣",
}

# 同名參數在不同指令可能有不同意思，所以用「指令.參數」當 key，找不到再退回只看參數名
PARAMETER_NAMES = {
    "user": "使用者",
    "give.user": "對象",
    "give.amount": "金額",
    "clear.amount": "數量",
    "when": "時間",
    "message": "內容",
    "duration": "時長",
    "public": "公開",
    "bet": "賭注",
    "max": "範圍上限",
    "opponent": "對手",
    "difficulty": "難度",
    "big_blind": "大盲注",
    "buy_in": "帶入籌碼",
    "bots": "電腦對手",
    "theme": "主題",
    "weather.send": "立即傳送",
}


class ZhTranslator(app_commands.Translator):
    async def translate(self, string: app_commands.locale_str, locale: Locale,
                        context: app_commands.TranslationContextTypes) -> Optional[str]:
        if locale not in (Locale.taiwan_chinese, Locale.chinese):
            return None
        if context.location is Loc.command_name:
            return COMMAND_NAMES.get(string.message)
        if context.location is Loc.parameter_name:
            command = getattr(context.data, "command", None)
            key = f"{command.qualified_name}.{string.message}" if command else string.message
            return PARAMETER_NAMES.get(key) or PARAMETER_NAMES.get(string.message)
        return None
