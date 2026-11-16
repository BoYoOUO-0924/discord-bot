"""颱風、豪雨等天氣警特報與停班停課：抓資料、整理成通知。不依賴 Discord，方便測試。

資料來源：
- 天氣特報（豪雨、大雨、強風、低溫…）：中央氣象署 W-C0033-002
- 颱風警報：中央氣象署 W-C0034-001（CAP 格式，發布、更新、解除都有）
- 停班停課：行政院人事行政總處「天然災害停止上班及上課情形」網頁（沒有開放 API，解析表格）
"""
import html as html_lib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, Iterable, List, Optional, Set

import aiohttp

from core import weather
from core.timeutil import TAIPEI

log = logging.getLogger(__name__)

WARNINGS_DATASET = "W-C0033-002"
TYPHOON_DATASET = "W-C0034-001"
CLOSURE_URL = "https://www.dgpa.gov.tw/typh/daily/nds.html"

DEFAULT_TYPES = ("颱風", "豪雨", "大雨")   # 預設只通知這些；/settings alerts 可以改成全部特報
TYPHOON_RECENT = timedelta(hours=24)     # 颱風警報只通知 24 小時內發布的
NO_CLOSURE = "無停班停課訊息"

# 特報裡不是縣市名稱的地區 → 所屬縣市
SUBAREAS = {"恆春半島": "屏東縣", "蘭嶼綠島": "臺東縣", "蘭嶼": "臺東縣", "綠島": "臺東縣",
            "金門": "金門縣", "馬祖": "連江縣", "澎湖": "澎湖縣"}


def area_counties(area: str) -> Set[str]:
    """特報、停班停課裡的地區名稱屬於哪些縣市。「臺北市山區」→ {臺北市}；「恆春半島」→ {屏東縣}。"""
    area = (area or "").replace("台", "臺")
    found = {county for county in weather.COUNTIES if county in area}
    found |= {county for name, county in SUBAREAS.items() if name in area}
    return found


def _clean(text: str) -> str:
    lines = [line.strip() for line in (text or "").splitlines()]
    return "\n".join(line for line in lines if line)


def _time(text: Optional[str]) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat((text or "").strip().replace(" ", "T"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=TAIPEI)


# ---------------------------------------------------------------------- 天氣特報
@dataclass
class WeatherWarning:
    key: str                  # 去重用：標題＋發布時間
    title: str                # 例如「豪雨特報」
    phenomena: List[str]
    issued: Optional[datetime]
    start: Optional[datetime]
    end: Optional[datetime]
    text: str
    areas: List[str]

    @property
    def counties(self) -> Set[str]:
        return set().union(*(area_counties(a) for a in self.areas)) if self.areas else set()

    def matches(self, types: Optional[Iterable[str]]) -> bool:
        """types 為 None 代表全部特報都要。"""
        if types is None:
            return True
        words = [self.title, *self.phenomena]
        return any(t in w for t in types for w in words)


def parse_warnings(payload: dict) -> List[WeatherWarning]:
    records = ((payload or {}).get("records") or {}).get("record") or []
    result = []
    for record in records:
        info = record.get("datasetInfo") or {}
        valid = info.get("validTime") or {}
        hazards = ((record.get("hazardConditions") or {}).get("hazards") or {}).get("hazard") or []
        if isinstance(hazards, dict):
            hazards = [hazards]
        phenomena, areas = [], []
        for hazard in hazards:
            hinfo = hazard.get("info") or {}
            if hinfo.get("phenomena"):
                phenomena.append(f"{hinfo.get('phenomena')}{hinfo.get('significance', '')}")
            locations = (hinfo.get("affectedAreas") or {}).get("location") or []
            areas += [loc.get("locationName", "") for loc in locations if loc.get("locationName")]
        title = info.get("datasetDescription") or (phenomena[0] if phenomena else "天氣特報")
        text = _clean((((record.get("contents") or {}).get("content") or {}).get("contentText")) or "")
        issued = info.get("issueTime") or ""
        result.append(WeatherWarning(f"{title}|{issued}", title, phenomena, _time(issued), _time(valid.get("startTime")),
                              _time(valid.get("endTime")), text, list(dict.fromkeys(areas))))
    return result


# ---------------------------------------------------------------------- 颱風警報
@dataclass
class TyphoonAlert:
    key: str
    headline: str             # 例如「海上陸上颱風警報」「解除颱風警報」
    effective: Optional[datetime]
    urgency: str
    sections: Dict[str, str]
    areas: List[str]
    url: str = ""

    @property
    def lifted(self) -> bool:
        return "解除" in self.headline


def parse_typhoon(payload: dict) -> List[TyphoonAlert]:
    infos = ((payload or {}).get("records") or {}).get("info") or []
    if isinstance(infos, dict):
        infos = [infos]
    result = []
    for info in infos:
        if info.get("event") != "颱風":
            continue
        sections = {s.get("title", ""): _clean(s.get("value", ""))
                    for s in ((info.get("description") or {}).get("section") or []) if s.get("title")}
        areas = [a.get("areaDesc", "") for a in info.get("area") or [] if a.get("areaDesc")]
        headline = info.get("headline") or "颱風警報"
        effective = info.get("effective") or ""
        result.append(TyphoonAlert(f"{headline}|{effective}", headline, _time(effective), info.get("urgency", ""),
                                   sections, areas, info.get("web") or ""))
    return result


def recent_typhoons(alerts: List[TyphoonAlert], now: datetime) -> List[TyphoonAlert]:
    return [a for a in alerts if a.effective and now - a.effective <= TYPHOON_RECENT]


# ---------------------------------------------------------------------- 停班停課
_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
_CELL_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.IGNORECASE | re.DOTALL)


def _cell_text(cell: str) -> str:
    cell = re.sub(r"<br\s*/?>", "\n", cell, flags=re.IGNORECASE)
    cell = re.sub(r"<[^>]+>", "", cell)
    return _clean(html_lib.unescape(cell))


def parse_closures(page: str) -> Dict[str, str]:
    """{地區（通常是縣市）: 停班停課情形}；沒有任何停班停課回傳空的 dict。
    表格每一列是「縣市名稱｜情形」，備註列只有一格，會被略過。"""
    closures: Dict[str, str] = {}
    for row in _ROW_RE.findall(page or ""):
        cells = [_cell_text(c) for c in _CELL_RE.findall(row)]
        if len(cells) < 2 or not cells[0] or not cells[1]:
            continue
        if NO_CLOSURE in cells[0] or NO_CLOSURE in cells[1]:
            continue
        closures[cells[0]] = cells[1]
    return closures


def closures_for(closures: Dict[str, str], counties: Iterable[str]) -> Dict[str, str]:
    wanted = set(counties)
    return {area: text for area, text in closures.items() if area_counties(area) & wanted}


# ---------------------------------------------------------------------- 抓資料
@dataclass
class Snapshot:
    """一次輪詢的結果；某個來源失敗時那一項是 None（不要當成「沒有警報」）。"""
    warnings: Optional[List[WeatherWarning]] = None
    typhoons: Optional[List[TyphoonAlert]] = None
    closures: Optional[Dict[str, str]] = None
    errors: List[str] = field(default_factory=list)


async def fetch_snapshot(api_key: str) -> Snapshot:
    snapshot = Snapshot()
    timeout = aiohttp.ClientTimeout(total=30)
    connector = aiohttp.TCPConnector(ssl=weather.ssl_context())
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        if api_key:
            try:
                snapshot.warnings = parse_warnings(await weather._fetch(session, api_key, WARNINGS_DATASET))
            except (weather.WeatherError, aiohttp.ClientError, TimeoutError, ValueError) as e:
                snapshot.errors.append(f"天氣特報：{e}")
            try:
                snapshot.typhoons = parse_typhoon(await weather._fetch(session, api_key, TYPHOON_DATASET))
            except (weather.WeatherError, aiohttp.ClientError, TimeoutError, ValueError) as e:
                snapshot.errors.append(f"颱風警報：{e}")
        try:
            async with session.get(CLOSURE_URL, headers={"User-Agent": "Mozilla/5.0 (gawa-discord-bot)"}) as response:
                if response.status != 200:
                    raise ValueError(f"HTTP {response.status}")
                snapshot.closures = parse_closures(await response.text(errors="replace"))
        except (aiohttp.ClientError, TimeoutError, ValueError) as e:
            snapshot.errors.append(f"停班停課：{e}")
    return snapshot
