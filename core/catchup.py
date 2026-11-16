"""「我錯過了什麼」：把頻道裡沒看到的聊天整理成重點。不依賴 Discord 物件的細節，方便測試。"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from core.timeutil import TAIPEI

MAX_LINE = 200         # 單則訊息最多取幾個字
MAX_TRANSCRIPT = 6000  # 給 AI 的聊天紀錄最多幾個字（太長只留最新的）
MIN_MESSAGES = 3       # 少於這個數量不用 AI，直接叫使用者自己看

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "points": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
        "for_you": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
    },
    "required": ["summary", "points", "for_you"],
}


@dataclass
class Line:
    when: datetime
    author: str
    text: str


@dataclass
class Missed:
    lines: List[Line] = field(default_factory=list)
    mentions: List[str] = field(default_factory=list)   # 提到你或回覆你的訊息連結
    first_url: Optional[str] = None
    truncated: bool = False

    @property
    def people(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for line in self.lines:
            counts[line.author] = counts.get(line.author, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


def message_text(content: str, attachments: int = 0, embeds: int = 0) -> str:
    text = " ".join((content or "").split())
    extras = (["[附件]"] * min(attachments, 1)) + (["[連結預覽]"] * min(embeds, 1) if not text else [])
    text = " ".join([text, *extras]).strip()
    return text if len(text) <= MAX_LINE else text[:MAX_LINE - 1] + "…"


def transcript(lines: List[Line], limit: int = MAX_TRANSCRIPT) -> tuple:
    """回傳 (文字, 有沒有因為太長被截掉)。保留最新的訊息。"""
    out: List[str] = []
    used = 0
    for line in reversed(lines):
        row = f"{line.when.astimezone(TAIPEI):%H:%M} {line.author}：{line.text}"
        if used + len(row) + 1 > limit:
            return "\n".join(reversed(out)), True
        out.append(row)
        used += len(row) + 1
    return "\n".join(reversed(out)), False


def build_prompt(text: str, reader: str) -> str:
    return f"""你是 Discord 聊天整理助手。下面是 {reader} 沒看到的頻道聊天紀錄（時間 名字：內容）。
請用繁體中文整理，讓 {reader} 十秒內知道發生了什麼：
- summary：一兩句話講整體在聊什麼
- points：最多 6 點重點，每點一句話，寫出誰說了／決定了什麼；有約定時間、地點、要做的事一定要寫
- for_you：跟 {reader} 有關、需要 {reader} 回應或注意的事（有人問、找或指派事情給 {reader}）；沒有就給空陣列
只根據紀錄內容，不要編造；閒聊、貼圖、打招呼可以省略。

聊天紀錄：
{text}"""
