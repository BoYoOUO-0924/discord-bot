"""中央氣象署地震報告：抓最新報告、整理成摘要，判斷台北／新北有沒有感覺到。

資料集：
- E-A0015-001 顯著有感地震報告（有編號，規模較大或多處有感）
- E-A0016-001 小區域有感地震報告（規模小、只有附近有感）

注意：這是地震「報告」，通常在地震後幾分鐘才發布，不是搖晃前的地震速報（強震即時警報）。
這個模組不依賴 Discord，方便測試。
"""
import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

import aiohttp

from core.weather import WeatherError, _fetch, _get, _parse_time, ssl_context

log = logging.getLogger(__name__)

SIGNIFICANT = "E-A0015-001"   # 顯著有感地震報告
LOCAL = "E-A0016-001"         # 小區域有感地震報告
DATASETS = (SIGNIFICANT, LOCAL)

# 震度由小到大；5、6 級分強弱
INTENSITY_ORDER = ["0", "1", "2", "3", "4", "5弱", "5強", "6弱", "6強", "7"]
LOCAL_COUNTIES = ("臺北市", "新北市")


class EarthquakeError(WeatherError):
    pass


def normalize_intensity(text) -> Optional[str]:
    """「4級」「５弱」「5-」「6+」→ INTENSITY_ORDER 裡的寫法；看不懂就回傳 None。"""
    if text is None:
        return None
    s = str(text).strip().translate(str.maketrans("０１２３４５６７", "01234567"))
    s = s.replace("級", "").replace(" ", "").replace("-", "弱").replace("+", "強")
    match = re.match(r"([0-7])(弱|強)?", s)
    if not match:
        return None
    level, strength = match.groups()
    if level in ("5", "6"):
        return level + (strength or "弱")
    return level


def intensity_rank(text) -> int:
    level = normalize_intensity(text)
    return INTENSITY_ORDER.index(level) if level else -1


def intensity_text(level: Optional[str]) -> str:
    if not level:
        return "—"
    return level if level.endswith(("弱", "強")) else f"{level} 級"


@dataclass
class Report:
    key: str                         # 用來判斷有沒有傳過
    dataset: str
    number: Optional[str]            # 顯著有感地震的編號
    origin_time: Optional[datetime]
    location: str
    depth: Optional[float]
    magnitude: Optional[float]
    max_intensity: Optional[str]
    content: str
    color: str                       # 綠 / 黃 / 橘 / 紅
    image_url: Optional[str]
    web_url: Optional[str]
    local: Dict[str, str] = field(default_factory=dict)   # {縣市: 震度}

    @property
    def significant(self) -> bool:
        return self.dataset == SIGNIFICANT

    @property
    def local_rank(self) -> int:
        return max((intensity_rank(v) for v in self.local.values()), default=-1)

    @property
    def felt_locally(self) -> bool:
        return self.local_rank >= 1


def _float(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _max_level(levels) -> Optional[str]:
    ranked = [(intensity_rank(v), normalize_intensity(v)) for v in levels if intensity_rank(v) >= 0]
    return max(ranked)[1] if ranked else None


def parse_report(item: dict, dataset: str) -> Report:
    info = _get(item, "EarthquakeInfo", default={}) or {}
    epicenter = _get(info, "Epicenter", default={}) or {}
    magnitude = _get(info, "EarthquakeMagnitude", "Magnitude", default={}) or {}
    origin_text = _get(info, "OriginTime")
    origin = _parse_time(origin_text)

    areas = _get(_get(item, "Intensity", default={}) or {}, "ShakingArea", default=[]) or []
    all_levels, local = [], {}
    for area in areas:
        level = _get(area, "AreaIntensity")
        names = f"{_get(area, 'CountyName', default='')}{_get(area, 'AreaDesc', default='')}".replace("台", "臺")
        all_levels.append(level)
        for county in LOCAL_COUNTIES:
            if county in names and intensity_rank(level) > intensity_rank(local.get(county)):
                local[county] = normalize_intensity(level)
        # 各測站震度：縣市層級沒有標示時用測站補
        for station in _get(area, "EqStation", default=[]) or []:
            station_level = _get(station, "SeismicIntensity")
            all_levels.append(station_level)
            station_county = str(_get(station, "CountyName", default="")).replace("台", "臺")
            for county in LOCAL_COUNTIES:
                if county == station_county and intensity_rank(station_level) > intensity_rank(local.get(county)):
                    local[county] = normalize_intensity(station_level)

    content = str(_get(item, "ReportContent", default="") or "").strip()
    max_intensity = _max_level(all_levels)
    if max_intensity is None:
        found = re.search(r"最大震度[^0-9０-９]*([0-7０-７][弱強級]?)", content)
        max_intensity = normalize_intensity(found.group(1)) if found else None

    number = _get(item, "EarthquakeNo")
    location = str(_get(epicenter, "Location", default="") or "").strip() or "震央位置不明"
    lat, lon = _get(epicenter, "EpicenterLatitude"), _get(epicenter, "EpicenterLongitude")
    return Report(
        key=f"{origin_text}|{lat}|{lon}",
        dataset=dataset,
        number=str(number) if number not in (None, "") else None,
        origin_time=origin,
        location=location,
        depth=_float(_get(info, "FocalDepth", "Depth")),
        magnitude=_float(_get(magnitude, "MagnitudeValue")),
        max_intensity=max_intensity,
        content=content,
        color=str(_get(item, "ReportColor", default="") or ""),
        image_url=_get(item, "ReportImageURI") or None,
        web_url=_get(item, "Web") or None,
        local=local,
    )


def parse_reports(payload: dict, dataset: str) -> List[Report]:
    records = _get(payload, "records", default={}) or {}
    reports = []
    for item in _get(records, "Earthquake", default=[]) or []:
        try:
            reports.append(parse_report(item, dataset))
        except (TypeError, ValueError, AttributeError) as e:
            log.warning("看不懂一筆地震報告，略過：%s", e)
    return reports


def short_location(location: str) -> str:
    """「花蓮縣政府南南東方 25.0 公里 (位於臺灣東部海域)」→「臺灣東部海域」；沒有括號就取縣市名。"""
    inside = re.search(r"[（(]位於(.+?)[)）]", location)
    if inside:
        return inside.group(1).strip()
    county = re.match(r"(.{2,3}?[縣市])", location)
    return county.group(1) if county else location


async def fetch_reports(api_key: str, limit: int = 5) -> List[Report]:
    """兩種地震報告都抓，依發生時間新到舊排序。某一種抓失敗時另一種照常回傳。"""
    if not api_key:
        raise EarthquakeError("沒有設定 CWA_API_KEY")
    timeout = aiohttp.ClientTimeout(total=30)
    connector = aiohttp.TCPConnector(ssl=ssl_context())
    reports, errors = [], []
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        for dataset in DATASETS:
            try:
                payload = await _fetch(session, api_key, dataset, limit=str(limit))
                reports += parse_reports(payload, dataset)
            except (WeatherError, aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
                errors.append(f"{dataset}：{e}")
    if errors and not reports:
        raise EarthquakeError("；".join(errors))
    for error in errors:
        log.warning("地震報告部分資料抓取失敗：%s", error)
    epoch = datetime.min.replace(tzinfo=None)
    reports.sort(key=lambda r: r.origin_time.replace(tzinfo=None) if r.origin_time else epoch, reverse=True)
    return reports
