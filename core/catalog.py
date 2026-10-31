"""功能目錄：大廳、指令說明、個人戰績都從這裡讀取，新增功能只要改一個地方。"""
from dataclasses import dataclass
from typing import Dict, List, Tuple


@dataclass(frozen=True)
class GameInfo:
    key: str          # 同時也是斜線指令名稱與戰績代號
    emoji: str
    name: str
    tagline: str
    args: str = ""
    needs_ai: bool = False


GAMES: List[GameInfo] = [
    GameInfo("blackjack", "🃏", "21點", "跟莊家比誰最接近 21 點", "[賭注]"),
    GameInfo("slots", "🎰", "拉霸機", "三個一樣就中獎，最高 100 倍", "賭注"),
    GameInfo("guess", "🔢", "猜數字", "全頻道一起猜，猜中的人拿獎勵", "[範圍]"),
    GameInfo("tictactoe", "⭕", "井字遊戲", "找朋友對戰，或挑戰機器人", "[對手] [難度]"),
    GameInfo("poker", "♠️", "德州撲克", "2–8 人一桌，也能加入電腦對手", "[大盲注] [帶入籌碼] [電腦對手]"),
    GameInfo("seatortoise", "🐢", "海龜湯", "用是非題推理真相，AI 擔任湯主", "[難度]", needs_ai=True),
]
GAMES_BY_KEY: Dict[str, GameInfo] = {g.key: g for g in GAMES}

# 指令說明頁：(分類標題, [(指令名稱, 參數, 說明), ...])
HELP_SECTIONS: List[Tuple[str, List[Tuple[str, str, str]]]] = [
    ("💰 積分與日常", [
        ("checkin", "", "每日簽到領積分，連續簽到加成越來越多"),
        ("work", "", "打工賺 80–200 分，每小時一次，偶爾大成功雙倍"),
        ("quests", "", "每日 3 個任務，全部完成再加碼；在伺服器聊天也會慢慢累積積分"),
        ("profile", "[使用者]", "積分、排名、簽到紀錄與遊戲戰績"),
        ("leaderboard", "", "伺服器積分與連續簽到排行榜"),
        ("give", "對象 金額", "把積分轉給其他人"),
    ]),
    ("🎮 遊戲", [(g.key, g.args, f"{g.emoji} {g.tagline}") for g in GAMES]),
    ("🧰 工具", [
        ("remind", "時間 內容", "設定提醒，例如 `10m`、`1h30m`、`18:30`、`明天 9:00`"),
        ("reminders", "", "查看或取消你的提醒"),
        ("poll", "[時長]", "發起投票，可設定自動截止"),
        ("weather", "", "今天台北、淡水的天氣、降雨機率與穿衣建議（每天早上也會自動傳送）"),
        ("clear", "數量", "清除頻道訊息（管理員）"),
    ]),
    ("🏠 入口", [
        ("menu", "", "打開大廳，用按鈕操作所有功能"),
        ("help", "", "顯示這份說明"),
    ]),
]
