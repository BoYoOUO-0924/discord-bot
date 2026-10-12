"""時間相關工具：台灣時區、時間字串解析、Discord 時間戳記。"""
import re
from datetime import date, datetime, timedelta, timezone
from typing import Optional

# 台灣沒有日光節約時間，固定 UTC+8 即可（也省去 Windows 上缺 tzdata 的問題）
TAIPEI = timezone(timedelta(hours=8), "Asia/Taipei")

_UNIT_SECONDS = {
    "s": 1, "sec": 1, "秒": 1,
    "m": 60, "min": 60, "分": 60, "分鐘": 60,
    "h": 3600, "hr": 3600, "小時": 3600, "時": 3600,
    "d": 86400, "day": 86400, "天": 86400, "日": 86400,
}
_DURATION_RE = re.compile(r"(\d+)\s*(分鐘|小時|sec|min|day|hr|[smhd秒分時天日])", re.IGNORECASE)
_CLOCK_RE = re.compile(r"^(今天|明天|後天)?\s*(\d{1,2})[:：](\d{2})$")


def now_tw() -> datetime:
    return datetime.now(TAIPEI)


def today_tw() -> date:
    return now_tw().date()


def next_midnight_tw(now: Optional[datetime] = None) -> datetime:
    now = now or now_tw()
    tomorrow = (now + timedelta(days=1)).date()
    return datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=TAIPEI)


def parse_duration(text: str) -> Optional[int]:
    """把「1h30m」、「90秒」、「2天」這類字串轉成秒數；格式不符回傳 None。"""
    text = text.strip().lower().replace(" ", "")
    if not text:
        return None
    total, consumed = 0, 0
    for match in _DURATION_RE.finditer(text):
        total += int(match.group(1)) * _UNIT_SECONDS[match.group(2).lower()]
        consumed += len(match.group(0))
    # 有無法辨識的字元就視為格式錯誤，避免「10x」被當成 10 秒之類的誤判
    if consumed != len(text) or total <= 0:
        return None
    return total


def parse_when(text: str, now: Optional[datetime] = None) -> Optional[datetime]:
    """解析提醒時間：支援時長（10m、1h30m）與時刻（18:30、明天 9:00）。"""
    now = now or now_tw()
    text = text.strip()

    clock = _CLOCK_RE.match(text)
    if clock:
        day_word, hour, minute = clock.group(1), int(clock.group(2)), int(clock.group(3))
        if hour > 23 or minute > 59:
            return None
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        offset = {"明天": 1, "後天": 2}.get(day_word, 0)
        target += timedelta(days=offset)
        # 沒寫日期且時間已過，就當作明天
        if day_word is None and target <= now:
            target += timedelta(days=1)
        return target if target > now else None

    seconds = parse_duration(text)
    return now + timedelta(seconds=seconds) if seconds else None


def fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    parts = []
    for unit, size in (("天", 86400), ("小時", 3600), ("分", 60), ("秒", 1)):
        if seconds >= size:
            parts.append(f"{seconds // size} {unit}")
            seconds %= size
    return " ".join(parts[:2]) or "0 秒"


def ts(dt_or_unix, style: str = "f") -> str:
    """Discord 時間戳記，會依觀看者的時區與語言自動顯示。style: f 完整、R 相對、t 時刻。"""
    unix = dt_or_unix.timestamp() if isinstance(dt_or_unix, datetime) else dt_or_unix
    return f"<t:{int(unix)}:{style}>"
