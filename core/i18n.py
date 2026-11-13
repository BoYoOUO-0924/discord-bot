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
    "earthquake": "地震",
    "play": "播放",
    "music": "音樂",
    # 股票模擬（/股票 底下的子指令）
    "stock": "股票",
    "quote": "查詢",
    "buy": "買進",
    "sell": "賣出",
    "account": "帳戶",
    "orders": "委託",
    "history": "紀錄",
    "rank": "排行",
    # 待辦（/待辦 底下的子指令）
    "task": "待辦",
    "add": "新增",
    "list": "清單",
    "done": "完成",
    # 專案（/專案 底下的子指令）
    "project": "專案",
    "create": "建立",
    "view": "查看",
    "milestone": "里程碑",
    "report": "週報",
    "archive": "封存",
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
    "puzzle": "題目",
    "big_blind": "大盲注",
    "buy_in": "帶入籌碼",
    "bots": "電腦對手",
    "theme": "主題",
    "weather.send": "立即傳送",
    "weather.day": "日期",
    "earthquake.send": "立即傳送",
    "symbol": "股票",
    "shares": "股數",
    "play.query": "歌曲",
    "title": "內容",
    "assignee": "負責人",
    "due": "截止",
    "priority": "優先度",
    "note": "備註",
    "scope": "範圍",
    "include_done": "含已完成",
    "task": "待辦",
    "name": "名稱",
    "channel": "頻道",
    "description": "說明",
    "project": "專案",
    "milestone": "里程碑",
    "include_archived": "含已封存",
    "project milestone.due": "目標日期",
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
