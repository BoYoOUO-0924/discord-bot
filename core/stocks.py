"""股票模擬交易的行情與規則：twfolio 的每日資料、證交所即時行情、手續費與交易稅、成交規則。

- 股票清單、歷史行情、本益比、交易日曆來自 twfolio 的 SQLite，一律唯讀開啟，不會寫入。
- 盤中報價來自證交所網站的即時行情（mis.twse.com.tw），大約 5 秒更新一次。
  這不是證交所正式公開的 API，查不到時退回 twfolio 的最新收盤價。
- 手續費與交易稅的費率讀 twfolio 的 config.yaml，跟真實帳戶的計算方式一致。

成交規則（避免「已經知道收盤價還能用舊價格買」的時間差）：
- 盤中（交易日 9:00–13:30）：用當下的即時價立刻成交，買進用最佳賣價、賣出用最佳買價。
- 其他時間：先委託，用下一個交易日的開盤價成交；下單時沒人知道這個價格。

這個模組不依賴 Discord，方便測試。
"""
import asyncio
import logging
import os
import re
import time as _time
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import ROUND_FLOOR, Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import aiohttp
import aiosqlite

from core.timeutil import TAIPEI
from core.weather import ssl_context

log = logging.getLogger(__name__)

BOARD_LOT = 1000                      # 一張 = 1,000 股
SESSION_OPEN = time(9, 0)
SESSION_CLOSE = time(13, 30)
QUEUE_MARGIN = Decimal("1.1")         # 委託買進預扣：參考價 × 1.1（台股單日漲幅上限 10%）

MIS_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
MIS_BATCH = 50                        # 一次請求最多查幾檔
LIVE_TTL = 10                         # 同一檔 10 秒內不重複查，避免被證交所擋
SPARK = "▁▂▃▄▅▆▇█"


class MarketError(Exception):
    pass


# ---------------------------------------------------------------------- 手續費與交易稅
def _floor(value: Decimal) -> int:
    """券商手續費與證交稅都是元以下無條件捨去。"""
    return int(value.quantize(Decimal(1), rounding=ROUND_FLOOR))


@dataclass(frozen=True)
class Fees:
    """預設值與 twfolio 的 config.yaml 相同，讀不到設定檔時使用。"""
    fee_rate: Decimal = Decimal("0.001425")
    discount: Decimal = Decimal("1")
    min_fee_board_lot: int = 20
    min_fee_odd_lot: int = 1
    tax_rate_stock: Decimal = Decimal("0.003")
    tax_rate_etf: Decimal = Decimal("0.001")

    def fee(self, gross: int, shares: int) -> int:
        """手續費 = 成交金額 × 費率 × 折扣（捨去），且不低於最低手續費（零股另有較低的下限）。"""
        minimum = self.min_fee_odd_lot if shares < BOARD_LOT else self.min_fee_board_lot
        return max(_floor(Decimal(gross) * self.fee_rate * self.discount), minimum)

    def sell_tax(self, gross: int, is_etf: bool) -> int:
        """證券交易稅，只在賣出時收：股票 0.3%、ETF 0.1%。"""
        return _floor(Decimal(gross) * (self.tax_rate_etf if is_etf else self.tax_rate_stock))


_FEE_KEYS = {
    "fee_rate": Decimal, "discount": Decimal, "min_fee_board_lot": int, "min_fee_odd_lot": int,
    "tax_rate_stock": Decimal, "tax_rate_etf": Decimal,
}


def load_fees(path: Optional[str]) -> Fees:
    """讀 twfolio config.yaml 的 fees 區塊。只需要幾個數字，不必為此安裝 YAML 套件。"""
    if not path:
        return Fees()
    try:
        with open(path, encoding="utf-8-sig") as f:
            lines = f.read().splitlines()
    except OSError:
        log.info("讀不到 twfolio 設定檔 %s，手續費使用預設費率", path)
        return Fees()
    values, inside = {}, False
    for line in lines:
        if re.match(r"^fees:\s*(#.*)?$", line):
            inside = True
            continue
        if inside and re.match(r"^\S", line):    # 下一個頂層區塊
            break
        match = re.match(r"^\s+(\w+):\s*([0-9.]+)", line) if inside else None
        if match and match.group(1) in _FEE_KEYS:
            try:
                values[match.group(1)] = _FEE_KEYS[match.group(1)](match.group(2))
            except (ValueError, InvalidOperation):
                log.warning("twfolio 設定檔的 fees.%s 看不懂，使用預設值", match.group(1))
    return Fees(**values)


def gross_amount(price: Decimal, shares: int) -> int:
    return _floor(price * shares)


def buy_total(fees: Fees, price: Decimal, shares: int) -> Tuple[int, int]:
    """買進：回傳 (成交金額, 手續費)，總共要付兩者相加。"""
    gross = gross_amount(price, shares)
    return gross, fees.fee(gross, shares)


def sell_net(fees: Fees, price: Decimal, shares: int, is_etf: bool) -> Tuple[int, int, int]:
    """賣出：回傳 (成交金額, 手續費, 交易稅)，實拿 = 成交金額 - 手續費 - 交易稅。"""
    gross = gross_amount(price, shares)
    return gross, fees.fee(gross, shares), fees.sell_tax(gross, is_etf)


def queue_reserve(fees: Fees, ref_price: Decimal, shares: int) -> int:
    """委託買進要先扣住的現金：開盤價最多比參考價高 10%，成交後多扣的會退回。"""
    gross = gross_amount(ref_price * QUEUE_MARGIN, shares)
    return gross + fees.fee(gross, shares)


# ---------------------------------------------------------------------- 報價
@dataclass
class Quote:
    symbol: str
    name: str
    price: Optional[Decimal]          # 最新成交價（盤後就是收盤價）
    prev_close: Optional[Decimal]
    trade_date: date
    open: Optional[Decimal] = None
    high: Optional[Decimal] = None
    low: Optional[Decimal] = None
    limit_up: Optional[Decimal] = None
    limit_down: Optional[Decimal] = None
    bid: Optional[Decimal] = None     # 最佳買價
    ask: Optional[Decimal] = None     # 最佳賣價
    volume_lots: Optional[int] = None
    updated: Optional[datetime] = None
    live: bool = False                # True = 證交所即時行情；False = twfolio 每日收盤資料

    @property
    def change(self) -> Optional[Decimal]:
        if self.price is None or self.prev_close is None:
            return None
        return self.price - self.prev_close

    @property
    def change_pct(self) -> Optional[Decimal]:
        if self.change is None or not self.prev_close:
            return None
        return self.change / self.prev_close * 100

    @property
    def reference(self) -> Optional[Decimal]:
        """估算用的參考價：最新成交價，沒有的話用昨收。"""
        return self.price if self.price is not None else self.prev_close


def _dec(value) -> Optional[Decimal]:
    if value in (None, "", "-"):
        return None
    text = str(value).strip()
    if "." in text:
        text = text.rstrip("0").rstrip(".")   # 證交所給「2330.0000」，存進紀錄時寫成「2330」
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    return number if number > 0 else None


def _first(levels) -> Optional[Decimal]:
    """五檔報價「2335.0000_2340.0000_…_」取第一檔。"""
    if not levels:
        return None
    return _dec(str(levels).split("_")[0])


def parse_mis(item: dict) -> Optional[Quote]:
    try:
        trade_date = datetime.strptime(item["d"], "%Y%m%d").date()
    except (KeyError, ValueError):
        return None
    updated = None
    try:
        clock = datetime.strptime(item.get("t", ""), "%H:%M:%S").time()
        updated = datetime.combine(trade_date, clock, TAIPEI)
    except ValueError:
        pass
    volume = item.get("v")
    return Quote(
        symbol=str(item.get("c", "")).strip(),
        name=str(item.get("n", "")).strip(),
        price=_dec(item.get("z")),
        prev_close=_dec(item.get("y")),
        trade_date=trade_date,
        open=_dec(item.get("o")),
        high=_dec(item.get("h")),
        low=_dec(item.get("l")),
        limit_up=_dec(item.get("u")),
        limit_down=_dec(item.get("w")),
        bid=_first(item.get("b")),
        ask=_first(item.get("a")),
        volume_lots=int(volume) if str(volume or "").isdigit() else None,
        updated=updated,
        live=True,
    )


class LiveQuotes:
    """證交所即時行情，加上短暫快取。"""

    def __init__(self, ttl: float = LIVE_TTL):
        self.ttl = ttl
        self._cache: Dict[str, Tuple[float, Quote]] = {}
        self._lock = asyncio.Lock()

    async def _request(self, symbols: List[str]) -> List[dict]:
        params = {"ex_ch": "|".join(f"tse_{s}.tw" for s in symbols), "json": "1", "delay": "0"}
        timeout = aiohttp.ClientTimeout(total=10)
        connector = aiohttp.TCPConnector(ssl=ssl_context())
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://mis.twse.com.tw/stock/index.jsp"}
        async with aiohttp.ClientSession(timeout=timeout, connector=connector, headers=headers) as session:
            async with session.get(MIS_URL, params=params) as resp:
                if resp.status != 200:
                    raise MarketError(f"即時行情回應 HTTP {resp.status}")
                payload = await resp.json(content_type=None)
        if not isinstance(payload, dict) or payload.get("rtmessage") not in (None, "OK"):
            raise MarketError(f"即時行情回應異常：{payload.get('rtmessage') if isinstance(payload, dict) else payload}")
        return payload.get("msgArray") or []

    async def get(self, symbols: Iterable[str]) -> Dict[str, Quote]:
        """查不到的代號不會出現在結果裡；整批失敗會丟 MarketError 或 aiohttp 的錯誤。"""
        wanted = sorted({s for s in symbols if s})
        now = _time.monotonic()
        result = {s: q for s, (at, q) in self._cache.items() if s in wanted and now - at < self.ttl}
        missing = [s for s in wanted if s not in result]
        if not missing:
            return result
        async with self._lock:
            for i in range(0, len(missing), MIS_BATCH):
                for item in await self._request(missing[i:i + MIS_BATCH]):
                    quote = parse_mis(item)
                    if quote and quote.symbol:
                        self._cache[quote.symbol] = (_time.monotonic(), quote)
                        result[quote.symbol] = quote
        return result


# ---------------------------------------------------------------------- twfolio 資料（唯讀）
@dataclass
class Security:
    symbol: str
    name: str
    is_etf: bool
    industry: Optional[str] = None


@dataclass
class Bar:
    trade_date: date
    open: Optional[Decimal]
    close: Optional[Decimal]


class Twfolio:
    """twfolio 的 SQLite，用 mode=ro 開啟，就算程式寫錯也不會改到資料。"""

    def __init__(self, path: Optional[str]):
        self.path = path
        self._conn: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        return bool(self.path) and os.path.exists(self.path)

    async def _db(self) -> aiosqlite.Connection:
        if self._conn is None:
            if not self.available:
                raise MarketError(f"找不到 twfolio 資料庫：{self.path}")
            self._conn = await aiosqlite.connect(Path(self.path).resolve().as_uri() + "?mode=ro", uri=True)
            self._conn.row_factory = aiosqlite.Row
        return self._conn

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def _all(self, sql: str, params: tuple = ()) -> List[aiosqlite.Row]:
        async with self._lock:
            conn = await self._db()
            async with conn.execute(sql, params) as cur:
                return await cur.fetchall()

    async def _one(self, sql: str, params: tuple = ()) -> Optional[aiosqlite.Row]:
        rows = await self._all(sql, params)
        return rows[0] if rows else None

    async def search(self, text: str, limit: int = 25) -> List[Security]:
        """代號開頭或名稱包含關鍵字；代號完全相同的排最前面。"""
        text = text.strip()
        if not text:
            sql = ("SELECT symbol, name, security_type, industry FROM securities "
                   "WHERE symbol IN ('2330', '0050', '2317', '2454', '0056', '00878', '2412', '2881') ORDER BY symbol")
            rows = await self._all(sql)
        else:
            rows = await self._all(
                "SELECT symbol, name, security_type, industry FROM securities "
                "WHERE symbol LIKE ? || '%' OR name LIKE '%' || ? || '%' "
                "ORDER BY symbol = ? DESC, symbol LIKE ? || '%' DESC, length(symbol), symbol LIMIT ?",
                (text, text, text, text, limit))
        return [Security(r["symbol"], r["name"], r["security_type"] == "ETF", r["industry"]) for r in rows]

    async def security(self, symbol: str) -> Optional[Security]:
        row = await self._one("SELECT symbol, name, security_type, industry FROM securities WHERE symbol = ?",
                              (symbol,))
        return Security(row["symbol"], row["name"], row["security_type"] == "ETF", row["industry"]) if row else None

    async def resolve(self, text: str) -> Optional[Security]:
        """使用者輸入「2330」「2330 台積電」或「台積電」都找得到。"""
        text = text.strip()
        code = text.split()[0] if text else ""
        found = await self.security(code)
        if found:
            return found
        matches = await self.search(text, limit=2)
        exact = [s for s in matches if s.name == text]
        if exact:
            return exact[0]
        return matches[0] if len(matches) == 1 else None

    async def closes(self, symbol: str, days: int = 30) -> List[Bar]:
        """最近 days 個有收盤價的交易日，舊到新。"""
        rows = await self._all(
            "SELECT trade_date, open, close FROM daily_prices WHERE symbol = ? AND close IS NOT NULL "
            "ORDER BY trade_date DESC LIMIT ?", (symbol, days))
        return [Bar(date.fromisoformat(r["trade_date"]), _dec(r["open"]), _dec(r["close"])) for r in reversed(rows)]

    async def range_52w(self, symbol: str, today: date) -> Tuple[Optional[Decimal], Optional[Decimal]]:
        since = (today - timedelta(days=365)).isoformat()
        rows = await self._all(
            "SELECT low, high FROM daily_prices WHERE symbol = ? AND trade_date >= ? AND close IS NOT NULL",
            (symbol, since))
        lows = [d for d in (_dec(r["low"]) for r in rows) if d is not None]
        highs = [d for d in (_dec(r["high"]) for r in rows) if d is not None]
        return (min(lows) if lows else None), (max(highs) if highs else None)

    async def valuation(self, symbol: str) -> Tuple[Optional[Decimal], Optional[Decimal]]:
        """最新的 (本益比, 殖利率 %)；ETF 沒有這份資料。"""
        row = await self._one("SELECT pe_ratio, dividend_yield FROM daily_valuations WHERE symbol = ? "
                              "ORDER BY trade_date DESC LIMIT 1", (symbol,))
        if not row:
            return None, None
        yield_ = row["dividend_yield"]
        return _dec(row["pe_ratio"]), (Decimal(str(yield_)) if yield_ not in (None, "") else None)

    async def latest_quote(self, symbol: str) -> Optional[Quote]:
        """用每日資料組成的報價（收盤價），即時行情查不到時使用。"""
        security = await self.security(symbol)
        bars = await self.closes(symbol, days=2)
        if not security or not bars:
            return None
        last = bars[-1]
        row = await self._one("SELECT open, high, low FROM daily_prices WHERE symbol = ? AND trade_date = ?",
                              (symbol, last.trade_date.isoformat()))
        return Quote(symbol=symbol, name=security.name, price=last.close,
                     prev_close=bars[-2].close if len(bars) > 1 else None, trade_date=last.trade_date,
                     open=_dec(row["open"]) if row else None, high=_dec(row["high"]) if row else None,
                     low=_dec(row["low"]) if row else None)

    async def first_bar_on_or_after(self, symbol: str, day: date) -> Optional[Bar]:
        """day 當天或之後第一個交易日的行情；那天沒成交的話 open 是 None。

        「那天有開盤」用全市場有沒有資料判斷，個股停牌或沒成交時也能知道要取消委託。
        """
        row = await self._one("SELECT MIN(trade_date) AS d FROM daily_prices WHERE trade_date >= ?",
                              (day.isoformat(),))
        if not row or not row["d"]:
            return None
        bar = await self._one("SELECT open, close FROM daily_prices WHERE symbol = ? AND trade_date = ?",
                              (symbol, row["d"]))
        return Bar(date.fromisoformat(row["d"]), _dec(bar["open"]) if bar else None,
                   _dec(bar["close"]) if bar else None)


# ---------------------------------------------------------------------- 行情總入口
class Market:
    """報價一律先查證交所即時行情，查不到再退回 twfolio 的最新收盤價；其他資料都從 twfolio 拿。"""

    def __init__(self, twfolio_path: Optional[str], live: Optional[LiveQuotes] = None):
        self.db = Twfolio(twfolio_path)
        self.live = live or LiveQuotes()
        self._last_live_error = 0.0

    async def close(self) -> None:
        await self.db.close()

    async def _live(self, symbols: Iterable[str]) -> Dict[str, Quote]:
        try:
            return await self.live.get(symbols)
        except (MarketError, aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            if _time.monotonic() - self._last_live_error > 600:   # 一直連不上時不要洗紀錄
                self._last_live_error = _time.monotonic()
                log.warning("證交所即時行情查詢失敗，改用每日收盤資料：%s", e)
            return {}

    async def quotes(self, symbols: Iterable[str]) -> Dict[str, Quote]:
        symbols = sorted(set(symbols))
        result = {s: q for s, q in (await self._live(symbols)).items() if q.reference is not None}
        for symbol in symbols:
            if symbol not in result:
                fallback = await self.db.latest_quote(symbol)
                if fallback:
                    result[symbol] = fallback
        return result

    async def quote(self, symbol: str) -> Optional[Quote]:
        return (await self.quotes([symbol])).get(symbol)


# ---------------------------------------------------------------------- 成交規則
def in_session(now: datetime) -> bool:
    """交易時段（週一到週五 9:00–13:30）。假日由報價日期另外判斷。"""
    now = now.astimezone(TAIPEI)
    return now.weekday() < 5 and SESSION_OPEN <= now.time() < SESSION_CLOSE


def can_trade_now(quote: Optional[Quote], now: datetime) -> bool:
    """盤中而且即時行情是今天的資料，才能立刻成交；國定假日的報價日期會停在前一個交易日。"""
    return bool(quote and quote.live and in_session(now) and quote.trade_date == now.astimezone(TAIPEI).date())


def instant_price(quote: Quote, side: str) -> Tuple[Optional[Decimal], Optional[str]]:
    """盤中立刻成交的價格：買進用最佳賣價、賣出用最佳買價，沒有掛單時用最新成交價。

    回傳 (價格, None)，或 (None, 不能成交的原因)。
    """
    if side == "buy":
        if quote.ask is None and quote.limit_up is not None and quote.price == quote.limit_up:
            return None, "目前漲停鎖住，沒有人賣，買不到。"
        price = quote.ask or quote.price
    else:
        if quote.bid is None and quote.limit_down is not None and quote.price == quote.limit_down:
            return None, "目前跌停鎖住，沒有人買，賣不掉。"
        price = quote.bid or quote.price
    if price is None:
        return None, "這檔目前沒有成交價也沒有掛單，請稍後再試。"
    return price, None


def queue_start(now: datetime) -> date:
    """委託單最早可以成交的日期：開盤前下單是當天，開盤後下單是隔天（之後第一個交易日的開盤價）。"""
    now = now.astimezone(TAIPEI)
    return now.date() if now.time() < SESSION_OPEN else now.date() + timedelta(days=1)


# ---------------------------------------------------------------------- 顯示
def fmt_price(value: Optional[Decimal]) -> str:
    if value is None:
        return "—"
    text = f"{value:,.2f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def sparkline(values: List[Decimal]) -> str:
    if len(values) < 2:
        return ""
    low, high = min(values), max(values)
    if high == low:
        return SPARK[3] * len(values)
    return "".join(SPARK[int((v - low) / (high - low) * (len(SPARK) - 1))] for v in values)
