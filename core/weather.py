"""中央氣象署（CWA）開放資料：抓預報、整理成今天白天／晚上的摘要，並給穿衣與帶傘建議。

同時支援 CWA 新舊兩種 JSON 欄位格式（英文代碼 / 中文元素名稱、大小寫不同的鍵），
欄位改版時不容易整個壞掉。這個模組不依賴 Discord，方便測試。
"""
import asyncio
import logging
import ssl
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import aiohttp

from core.timeutil import TAIPEI

log = logging.getLogger(__name__)

API_BASE = "https://opendata.cwa.gov.tw/api/v1/rest/datastore"
COUNTY_36H = "F-C0032-001"          # 各縣市未來 36 小時天氣預報（12 小時一段）
TOWN_3DAY_NEW_TAIPEI = "F-D0047-069"  # 新北市各鄉鎮未來 3 天預報（逐 3 小時）


@dataclass(frozen=True)
class Place:
    label: str
    county: str                       # F-C0032-001 用的縣市名稱
    town: Optional[str] = None        # 有鄉鎮預報時用的區名
    town_dataset: Optional[str] = None


PLACES = [
    Place("台北", "臺北市"),
    Place("淡水", "新北市", "淡水區", TOWN_3DAY_NEW_TAIPEI),
]

# 元素名稱：舊格式是英文代碼，新格式是中文
TEMP = {"t", "溫度"}
MIN_T = {"mint", "最低溫度"}
MAX_T = {"maxt", "最高溫度"}
POP = {"pop", "pop6h", "pop12h", "3小時降雨機率", "6小時降雨機率", "12小時降雨機率"}
WX = {"wx", "天氣現象"}
CI = {"ci", "舒適度指數"}
VALUE_KEYS = ("value", "Weather", "Temperature", "ProbabilityOfPrecipitation", "ComfortIndexDescription",
              "MinTemperature", "MaxTemperature")


class WeatherError(Exception):
    pass


@dataclass
class Period:
    wx: Optional[str]
    min_t: Optional[int]
    max_t: Optional[int]
    pop: Optional[int]
    ci: Optional[str] = None


@dataclass
class Forecast:
    label: str
    day: Period          # 今天 06–18
    night: Period        # 今晚 18–明天 06
    source: str          # 顯示用的資料範圍說明


# ---------------------------------------------------------------------- 解析工具
def _get(data, *names, default=None):
    """不分大小寫取鍵值，新舊格式的鍵名大小寫不同。"""
    if not isinstance(data, dict):
        return default
    lowered = {k.lower(): v for k, v in data.items()}
    for name in names:
        if name.lower() in lowered:
            return lowered[name.lower()]
    return default


def _parse_time(text: Optional[str]) -> Optional[datetime]:
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.strip().replace(" ", "T"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=TAIPEI)


def _value(entry: dict) -> Optional[str]:
    parameter = _get(entry, "parameter")
    if isinstance(parameter, dict):
        return _get(parameter, "parameterName")
    values = _get(entry, "elementValue")
    if isinstance(values, list) and values:
        first = values[0]
        if isinstance(first, dict):
            for key in VALUE_KEYS:
                found = _get(first, key)
                if found not in (None, ""):
                    return str(found)
            return next((str(v) for v in first.values() if v not in (None, "")), None)
        return str(first)
    return None


def _number(text: Optional[str]) -> Optional[int]:
    try:
        return round(float(text))
    except (TypeError, ValueError):
        return None   # 降雨機率沒有資料時會是 " " 或 "-"


Sample = Tuple[datetime, datetime, str]


def _elements(location: dict) -> Dict[str, List[Sample]]:
    """{元素名稱（小寫）: [(開始, 結束, 值), ...]}"""
    result: Dict[str, List[Sample]] = {}
    for element in _get(location, "weatherElement", default=[]) or []:
        name = str(_get(element, "elementName", default="")).strip().lower()
        samples = []
        for entry in _get(element, "time", default=[]) or []:
            start = _parse_time(_get(entry, "startTime", "dataTime"))
            end = _parse_time(_get(entry, "endTime")) or start
            value = _value(entry)
            if start and value is not None:
                samples.append((start, end, value.strip()))
        result[name] = samples
    return result


def _find_location(payload: dict, name: str) -> dict:
    records = _get(payload, "records", default={})
    candidates = list(_get(records, "location", default=[]) or [])
    for group in _get(records, "locations", default=[]) or []:
        candidates += _get(group, "location", default=[]) or []
    for location in candidates:
        if _get(location, "locationName") == name:
            return location
    raise WeatherError(f"預報資料裡找不到「{name}」")


def _in_window(sample: Sample, start: datetime, end: datetime) -> bool:
    s, e, _ = sample
    if s == e:   # 時間點資料（逐時溫度）
        return start <= s < end
    return s < end and e > start


def summarize(elements: Dict[str, List[Sample]], start: datetime, end: datetime) -> Period:
    def pick(names):
        return [v for name in names for (s, e, v) in elements.get(name, []) if _in_window((s, e, v), start, end)]

    temps = [n for n in map(_number, pick(TEMP)) if n is not None]
    mins = [n for n in map(_number, pick(MIN_T)) if n is not None] or temps
    maxs = [n for n in map(_number, pick(MAX_T)) if n is not None] or temps
    pops = [n for n in map(_number, pick(POP)) if n is not None]
    wx = [v for v in pick(WX) if v]
    ci = [v for v in pick(CI) if v and not v.isdigit()]
    return Period(
        wx=Counter(wx).most_common(1)[0][0] if wx else None,
        min_t=min(mins) if mins else None,
        max_t=max(maxs) if maxs else None,
        pop=max(pops) if pops else None,
        ci=ci[0] if ci else None,
    )


def windows(day: datetime) -> Tuple[Tuple[datetime, datetime], Tuple[datetime, datetime]]:
    """那一天的白天 06–18、晚上 18–隔天 06。"""
    six = day.astimezone(TAIPEI).replace(hour=6, minute=0, second=0, microsecond=0)
    return (six, six + timedelta(hours=12)), (six + timedelta(hours=12), six + timedelta(hours=24))


def build_forecast(label: str, payload: dict, location_name: str, day: datetime, source: str) -> Forecast:
    """day：要預報的那一天（今天或明天）。"""
    elements = _elements(_find_location(payload, location_name))
    (ds, de), (ns, ne) = windows(day)
    daytime, night = summarize(elements, ds, de), summarize(elements, ns, ne)
    if daytime.max_t is None and daytime.pop is None:
        raise WeatherError(f"「{location_name}」沒有 {day.month}/{day.day} 白天的預報資料")
    return Forecast(label, daytime, night, source)


# ---------------------------------------------------------------------- 建議
def rain_advice(day: Period, night: Period) -> Tuple[str, str]:
    """回傳 (表情, 說明)。"""
    pops = [p for p in (day.pop, night.pop) if p is not None]
    top = max(pops) if pops else None
    rainy_wx = any(p.wx and "雨" in p.wx for p in (day, night))
    if top is None:
        return ("❔", "沒有降雨機率資料")
    if top >= 70:
        return ("☔", "很可能會下雨，出門一定要帶傘")
    if top >= 40 or (rainy_wx and top >= 30):
        return ("🌂", "有機會下雨，建議帶傘")
    if top >= 20:
        return ("🌥️", "降雨機率不高，包包可以放把折傘備用")
    return ("☀️", "應該不會下雨，不用帶傘")


def clothing_advice(day: Period, night: Period) -> str:
    lows = [t for t in (day.min_t, night.min_t) if t is not None]
    highs = [t for t in (day.max_t, night.max_t) if t is not None]
    if not lows and not highs:
        return "沒有溫度資料"
    low = min(lows or highs)
    high = max(highs or lows)

    if high >= 30:
        base = "天氣炎熱，穿短袖、透氣排汗的衣物，注意防曬並多喝水"
    elif high >= 26:
        base = "穿短袖就可以"
    elif high >= 22:
        base = "短袖或薄長袖"
    elif high >= 18:
        base = "長袖加一件薄外套"
    elif high >= 14:
        base = "穿毛衣或夾克等外套"
    elif high >= 10:
        base = "天氣冷，穿厚外套並做好保暖"
    else:
        base = "非常冷，羽絨外套、圍巾、手套都帶上"

    tips = [base]
    if high - low >= 8 and high >= 18:
        tips.append(f"早晚溫差大（{low}–{high}°C），多帶一件外套方便穿脫")
    elif low < 18 <= high:
        tips.append("早晚偏涼，出門可以帶件薄外套")
    if any(p.pop is not None and p.pop >= 40 for p in (day, night)):
        tips.append("可能下雨，穿防水的鞋子比較好")
    return "；".join(tips) + "。"


# ---------------------------------------------------------------------- 抓資料
_ssl_context: Optional[ssl.SSLContext] = None


def ssl_context() -> ssl.SSLContext:
    """連中央氣象署用的 SSL 設定：交給作業系統驗證憑證。

    opendata.cwa.gov.tw 沒有送出中繼憑證，Python 內建的驗證會失敗
    （unable to get local issuer certificate）。Windows 的驗證跟瀏覽器一樣會自動補抓缺少的
    中繼憑證，所以用 truststore 改由系統驗證；驗證仍然完整，不是關掉檢查。
    """
    global _ssl_context
    if _ssl_context is None:
        try:
            import truststore
            _ssl_context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        except ImportError:
            log.warning("沒有安裝 truststore，改用 Python 內建的憑證驗證")
            _ssl_context = ssl.create_default_context()
    return _ssl_context


async def _fetch(session: aiohttp.ClientSession, api_key: str, dataset: str, **params) -> dict:
    query = {"Authorization": api_key, "format": "JSON", **params}
    try:
        async with session.get(f"{API_BASE}/{dataset}", params=query) as response:
            if response.status in (401, 403):
                raise WeatherError("CWA API 金鑰無效或沒有權限，請確認 CWA_API_KEY")
            if response.status != 200:
                raise WeatherError(f"CWA API 回應 HTTP {response.status}")
            data = await response.json(content_type=None)
    except aiohttp.ClientSSLError as e:
        raise WeatherError(f"中央氣象署的憑證驗證失敗：{e}") from e
    if str(_get(data, "success", default="true")).lower() != "true":
        raise WeatherError(f"CWA API 回傳失敗：{_get(data, 'message', default=data)}")
    return data


async def fetch_forecasts(api_key: str, day: datetime, places: List[Place] = PLACES) -> List[Forecast]:
    """day：要預報的那一天。36 小時縣市預報在任何時間查詢都會涵蓋明天白天，鄉鎮預報涵蓋 3 天。"""
    if not api_key:
        raise WeatherError("沒有設定 CWA_API_KEY")
    timeout = aiohttp.ClientTimeout(total=30)
    connector = aiohttp.TCPConnector(ssl=ssl_context())
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        county = await _fetch(session, api_key, COUNTY_36H)
        town_data: Dict[str, dict] = {}
        forecasts, errors = [], []
        for place in places:
            if place.town:
                try:
                    if place.town_dataset not in town_data:
                        town_data[place.town_dataset] = await _fetch(session, api_key, place.town_dataset)
                    forecasts.append(build_forecast(place.label, town_data[place.town_dataset], place.town, day,
                                                    f"{place.county}{place.town}"))
                    continue
                except (WeatherError, aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
                    # 鄉鎮預報抓不到時，退回用縣市預報
                    log.warning("%s 的鄉鎮預報無法使用（%s），改用%s預報", place.label, e, place.county)
            try:
                forecasts.append(build_forecast(place.label, county, place.county, day,
                                                place.county + ("（全市預報）" if place.town else "")))
            except WeatherError as e:
                # 某個地點壞掉時，其他地點照常傳送
                log.warning("%s 的天氣預報無法使用：%s", place.label, e)
                errors.append(f"{place.label}：{e}")
        if not forecasts:
            raise WeatherError("；".join(errors))
        return forecasts
