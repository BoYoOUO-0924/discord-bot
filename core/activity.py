"""即時活動統計（只存在記憶體）：每分鐘幾個指令、今天總共幾個、最近的指令事件。

給管理後台首頁的即時圖表與「指令飛進核心」的動畫用，bot 重開就歸零。
這個模組不依賴 Discord，方便測試。
"""
import time
from collections import deque
from typing import Deque, Dict, List, Optional, Tuple

from core.timeutil import today_tw

MINUTES = 60          # 圖表顯示最近幾分鐘
EVENTS_KEEP = 200


class Activity:
    def __init__(self, clock=time.time):
        self.clock = clock
        self.minutes: Dict[int, int] = {}                     # 第幾分鐘（unix // 60）→ 指令數
        self.events: Deque[Tuple[int, float, Optional[int], str]] = deque(maxlen=EVENTS_KEEP)
        self.seq = 0
        self.day = today_tw()
        self.today = 0

    def record(self, guild_id: Optional[int], name: str = "") -> None:
        now = self.clock()
        if today_tw() != self.day:
            self.day, self.today = today_tw(), 0
        minute = int(now // 60)
        self.minutes[minute] = self.minutes.get(minute, 0) + 1
        for old in [m for m in self.minutes if m < minute - MINUTES * 2]:
            del self.minutes[old]
        self.today += 1
        self.seq += 1
        self.events.append((self.seq, now, guild_id, name[:40]))

    def per_minute(self) -> List[int]:
        """最近 MINUTES 分鐘每分鐘的指令數，舊到新，最後一個是這一分鐘。"""
        current = int(self.clock() // 60)
        return [self.minutes.get(current - i, 0) for i in range(MINUTES - 1, -1, -1)]

    def since(self, seq: int) -> List[Dict]:
        return [{"seq": s, "at": at, "guild": str(g) if g else None, "command": n}
                for s, at, g, n in self.events if s > seq]
