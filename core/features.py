"""功能開關：管理後台可以關掉某些功能，可以全部伺服器一起關，也可以只關某個伺服器。

判斷順序：全域關閉 → 關；某伺服器有設定 → 照那個設定；都沒設定 → 開。
大廳（/menu）、說明（/help）、清除訊息（/clear）、伺服器設定（/settings）是基本功能，不能關。
伺服器管理員也可以用 /settings features 自己開關（寫的是同一份設定）。
"""
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

GLOBAL = 0   # 資料庫裡用 guild_id = 0 代表「所有伺服器」


@dataclass(frozen=True)
class Feature:
    key: str
    emoji: str
    name: str
    description: str
    commands: Tuple[str, ...] = ()       # 屬於這個功能的斜線指令（最上層名稱）
    hub_actions: Tuple[str, ...] = ()    # 大廳按鈕
    intents: Tuple[str, ...] = ()        # 嘎哇聊天判斷出的功能
    category: str = "other"              # 分類（CATEGORIES 的 key），後台與 /settings features 依這個分組


# 功能分類：(key, emoji, 名稱)，順序就是顯示順序
CATEGORIES: List[Tuple[str, str, str]] = [
    ("economy", "💰", "經濟"),
    ("games", "🎮", "遊戲"),
    ("tools", "🧰", "生活工具"),
    ("weather", "🌦️", "天氣與防災"),
    ("ai", "🤖", "AI"),
    ("other", "📰", "其他"),
]


FEATURES: List[Feature] = [
    Feature("economy", "💰", "錢包與日常", "簽到、打工、每日任務、轉帳、排行榜、聊天累積存款",
            ("checkin", "work", "quests", "profile", "leaderboard", "give"),
            ("checkin", "work", "quests", "profile", "leaderboard"), ("balance", "checkin", "work", "quests", "leaderboard"),
            category="economy"),
    Feature("blackjack", "🃏", "21點", "跟莊家比點數", ("blackjack",), ("game:blackjack",), category="games"),
    Feature("slots", "🎰", "拉霸機", "三個一樣就中獎", ("slots",), ("game:slots",), category="games"),
    Feature("sicbo", "🎲", "骰寶", "押大小、點數、豹子，多人同桌", ("sicbo",), ("game:sicbo",), category="games"),
    Feature("guess", "🔢", "猜數字", "全頻道一起猜數字", ("guess",), ("game:guess",), category="games"),
    Feature("tictactoe", "⭕", "井字遊戲", "跟朋友或機器人對戰", ("tictactoe",), ("game:tictactoe",), category="games"),
    Feature("poker", "♠️", "德州撲克", "2–8 人德州撲克，可加電腦對手", ("poker",), ("game:poker",), category="games"),
    Feature("seatortoise", "🐢", "海龜湯", "AI 擔任湯主的推理遊戲", ("seatortoise",), ("game:seatortoise",), category="games"),
    Feature("stocks", "📈", "股票模擬", "用錢包的錢模擬買賣台股", ("stock",), ("stocks",), ("stock",), category="economy"),
    Feature("music", "🎵", "音樂", "在語音頻道播放 YouTube", ("play", "music"), ("music",), ("music",), category="tools"),
    Feature("reminders", "⏰", "提醒", "時間到通知你", ("remind", "reminders"), ("remind",), ("remind",), category="tools"),
    Feature("polls", "📊", "投票", "按鈕投票、自動截止", ("poll",), ("poll",), category="tools"),
    Feature("tasks", "📋", "待辦與專案", "任務卡片、專案、每日摘要、週報、GitHub 整合",
            ("task", "project"), ("tasks", "task_add"), ("task",), category="tools"),
    Feature("weather", "🌦️", "天氣", "天氣查詢與每天早上的天氣預報", ("weather",), (), ("weather",), category="weather"),
    Feature("earthquake", "🌏", "地震報告", "地震查詢與大地震自動通報", ("earthquake",), (), ("earthquake",), category="weather"),
    Feature("alerts", "🌀", "颱風豪雨與停班停課", "颱風警報、豪雨特報、停班停課查詢與自動通知", ("alert",), (), ("alert",), category="weather"),
    Feature("catchup", "🧾", "我錯過了什麼", "AI 整理頻道裡沒看到的聊天重點", ("catchup",), (), ("catchup",), category="ai"),
    Feature("memory", "🧠", "AI 記憶", "聊天時慢慢記住每個人的興趣與習慣，/memory 可以查看或刪除",
            ("memory",), (), ("memory",), category="ai"),
    Feature("changelog", "📰", "更新日誌", "看機器人最近更新了哪些功能", ("changelog",), ("changelog",), ("changelog",), category="other"),
    Feature("assistant", "💬", "AI 聊天", "@機器人 用講的叫功能、跟機器人閒聊", category="ai"),
]
FEATURES_BY_KEY: Dict[str, Feature] = {f.key: f for f in FEATURES}
COMMAND_FEATURES: Dict[str, str] = {c: f.key for f in FEATURES for c in f.commands}
HUB_FEATURES: Dict[str, str] = {a: f.key for f in FEATURES for a in f.hub_actions}
INTENT_FEATURES: Dict[str, str] = {i: f.key for f in FEATURES for i in f.intents}


def features_by_category() -> List[Tuple[Tuple[str, str, str], List[Feature]]]:
    """[(分類, 這個分類的功能)]，照 CATEGORIES 順序，跳過沒有功能的分類。"""
    groups = [(c, [f for f in FEATURES if f.category == c[0]]) for c in CATEGORIES]
    return [(c, items) for c, items in groups if items]


def feature_for_command(name: str) -> Optional[str]:
    """斜線指令（可以是「stock buy」這種子指令）屬於哪個功能；基本指令回傳 None。"""
    return COMMAND_FEATURES.get(name.split()[0]) if name else None


def feature_for_action(action: str) -> Optional[str]:
    """大廳或嘎哇的按鈕動作屬於哪個功能，例如「game:poker」「quote:台積電」「task:mine」。"""
    if action in HUB_FEATURES:
        return HUB_FEATURES[action]
    kind = action.split(":", 1)[0]
    if kind == "game":
        return action.split(":", 2)[1] if action.count(":") else None
    return {"quote": "stocks", "remind": "reminders", "task": "tasks", "weather": "weather",
            "earthquake": "earthquake", "alert": "alerts", "catchup": "catchup", "memory": "memory",
            "profile": "economy"}.get(kind, HUB_FEATURES.get(kind))


class FeatureFlags:
    """功能開關的記憶體快取；寫入時同時存資料庫。"""

    def __init__(self, db):
        self.db = db
        self.flags: Dict[Tuple[int, str], bool] = {}

    async def load(self) -> None:
        self.flags = {(row["guild_id"], row["feature"]): bool(row["enabled"])
                      for row in await self.db.list_feature_flags()}

    def is_enabled(self, feature: Optional[str], guild_id: Optional[int] = None) -> bool:
        if not feature:
            return True
        if self.flags.get((GLOBAL, feature)) is False:
            return False
        if guild_id:
            return self.flags.get((guild_id, feature), True)
        return True

    def command_enabled(self, name: str, guild_id: Optional[int]) -> bool:
        return self.is_enabled(feature_for_command(name), guild_id)

    def action_enabled(self, action: str, guild_id: Optional[int]) -> bool:
        return self.is_enabled(feature_for_action(action), guild_id)

    async def set(self, guild_id: int, feature: str, enabled: Optional[bool]) -> None:
        """enabled 為 None：拿掉這個伺服器的設定，回到預設（跟著全域）。"""
        if feature not in FEATURES_BY_KEY:
            raise ValueError(feature)
        await self.db.set_feature_flag(guild_id, feature, enabled)
        if enabled is None:
            self.flags.pop((guild_id, feature), None)
        else:
            self.flags[(guild_id, feature)] = enabled

    def overrides(self) -> Dict[int, Dict[str, bool]]:
        result: Dict[int, Dict[str, bool]] = {}
        for (guild_id, feature), enabled in self.flags.items():
            result.setdefault(guild_id, {})[feature] = enabled
        return result


def disabled_message(feature: Optional[str]) -> str:
    info = FEATURES_BY_KEY.get(feature or "")
    name = f"{info.emoji} {info.name}" if info else "這個功能"
    return f"「{name}」在這個伺服器已經關閉了。"


def enabled(bot, feature: Optional[str], guild_id: Optional[int] = None) -> bool:
    """給各功能用的捷徑；bot 沒有 features（例如測試用的假 bot）時一律當作開啟。"""
    flags = getattr(bot, "features", None)
    return flags.is_enabled(feature, guild_id) if flags else True
