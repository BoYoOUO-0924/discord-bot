import os
import sqlite3
import tempfile
import unittest
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from core import stocks as st
from core.db import Database
from core.timeutil import TAIPEI


def make_twfolio(path: str) -> None:
    """建一個跟 twfolio 同樣欄位的小資料庫。"""
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE securities (symbol TEXT PRIMARY KEY, name TEXT, security_type TEXT, market TEXT,
                                 updated_at TEXT, industry TEXT);
        CREATE TABLE daily_prices (symbol TEXT, trade_date TEXT, open TEXT, high TEXT, low TEXT, close TEXT,
                                   change TEXT, volume INTEGER, turnover INTEGER, transactions INTEGER,
                                   source TEXT, ingested_at TEXT, PRIMARY KEY (symbol, trade_date));
        CREATE TABLE daily_valuations (symbol TEXT, trade_date TEXT, pe_ratio TEXT, dividend_yield TEXT,
                                       pb_ratio TEXT, source TEXT, ingested_at TEXT);
        CREATE TABLE trading_calendar (trade_date TEXT PRIMARY KEY, is_open INTEGER, source TEXT, checked_at TEXT);
    """)
    conn.executemany("INSERT INTO securities VALUES (?, ?, ?, 'TWSE', '', ?)", [
        ("2330", "台積電", "STOCK", "半導體業"), ("0050", "元大台灣50", "ETF", "ETF"),
        ("2303", "聯電", "STOCK", "半導體業")])
    rows = [
        ("2330", "2026-10-29", "2300", "2380", "2290", "2370"),
        ("2330", "2026-10-30", "2360", "2380", "2340", "2370"),
        ("0050", "2026-10-30", "104", "105.2", "103.8", "104.95"),
        ("2330", "2026-11-02", "2310", "2345", "2310", "2330"),
        ("2303", "2026-11-02", None, None, None, None),      # 停牌沒成交
    ]
    conn.executemany("INSERT INTO daily_prices VALUES (?, ?, ?, ?, ?, ?, NULL, 0, 0, 0, 'test', '')", rows)
    conn.execute("INSERT INTO daily_valuations VALUES ('2330', '2026-10-30', '24.3', '1.60', '6.1', 'test', '')")
    conn.commit()
    conn.close()


class FeesTest(unittest.TestCase):
    def test_fee_floor_and_minimums(self):
        fees = st.Fees()
        self.assertEqual(fees.fee(2_330_000, 1000), 3320)        # 3320.25 捨去
        self.assertEqual(fees.fee(10_000, 1000), 20)             # 整股最低 20
        self.assertEqual(fees.fee(500, 5), 1)                    # 零股最低 1
        self.assertEqual(fees.sell_tax(1_000_000, is_etf=False), 3000)
        self.assertEqual(fees.sell_tax(1_000_000, is_etf=True), 1000)

    def test_load_fees_reads_twfolio_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.yaml")
            with open(path, "w", encoding="utf-8") as f:
                f.write("fees:\n  fee_rate: 0.001425\n  discount: 0.28   # 2.8 折\n  min_fee_board_lot: 20\n"
                        "screener:\n  discount: 9\n")
            fees = st.load_fees(path)
        self.assertEqual(fees.discount, Decimal("0.28"))
        self.assertEqual(fees.fee(2_330_000, 1000), 929)
        self.assertEqual(st.load_fees(os.path.join("nope", "config.yaml")), st.Fees())

    def test_queue_reserve_covers_limit_up(self):
        fees = st.Fees()
        reserve = st.queue_reserve(fees, Decimal("2370"), 1000)
        worst = sum(st.buy_total(fees, Decimal("2607"), 1000))   # 漲停 +10%
        self.assertGreaterEqual(reserve, worst)


class RulesTest(unittest.TestCase):
    MIS = {"c": "2330", "n": "台積電", "z": "2330.0000", "o": "2310.0000", "h": "2345.0000", "l": "2310.0000",
           "y": "2370.0000", "u": "2605.0000", "w": "2135.0000", "a": "2335.0000_2340.0000_", "b": "2330.0000_",
           "v": "35123", "d": "20261102", "t": "10:15:03"}

    def test_parse_mis(self):
        q = st.parse_mis(self.MIS)
        self.assertEqual((q.symbol, q.price, q.ask, q.bid, q.volume_lots), ("2330", Decimal("2330"), Decimal("2335"),
                                                                          Decimal("2330"), 35123))
        self.assertEqual(q.trade_date, date(2026, 11, 2))
        self.assertEqual(q.change, Decimal("-40"))
        self.assertIsNone(st.parse_mis({**self.MIS, "z": "-"}).price)

    def test_can_trade_now(self):
        q = st.parse_mis(self.MIS)
        self.assertTrue(st.can_trade_now(q, datetime(2026, 11, 2, 10, 15, tzinfo=TAIPEI)))
        self.assertFalse(st.can_trade_now(q, datetime(2026, 11, 2, 13, 30, tzinfo=TAIPEI)))   # 收盤
        self.assertFalse(st.can_trade_now(q, datetime(2026, 11, 3, 10, 0, tzinfo=TAIPEI)))    # 報價停在前一天＝休市
        self.assertFalse(st.can_trade_now(q, datetime(2026, 11, 7, 10, 0, tzinfo=TAIPEI)))    # 週六

    def test_instant_price(self):
        q = st.parse_mis(self.MIS)
        self.assertEqual(st.instant_price(q, "buy"), (Decimal("2335"), None))
        self.assertEqual(st.instant_price(q, "sell"), (Decimal("2330"), None))
        locked = st.parse_mis({**self.MIS, "z": "2605.0000", "a": "-", "b": "2605.0000_"})
        price, reason = st.instant_price(locked, "buy")
        self.assertIsNone(price)
        self.assertIn("漲停", reason)

    def test_queue_start(self):
        self.assertEqual(st.queue_start(datetime(2026, 11, 2, 8, 30, tzinfo=TAIPEI)), date(2026, 11, 2))
        self.assertEqual(st.queue_start(datetime(2026, 11, 2, 14, 0, tzinfo=TAIPEI)), date(2026, 11, 3))

    def test_format(self):
        self.assertEqual(st.fmt_price(Decimal("2330.00")), "2,330")
        self.assertEqual(st.fmt_price(Decimal("104.10")), "104.1")
        self.assertEqual(st.sparkline([Decimal(1), Decimal(2), Decimal(3)]), "▁▄█")


class TwfolioTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "twfolio.db")
        make_twfolio(self.path)
        self.tw = st.Twfolio(self.path)

    async def asyncTearDown(self):
        await self.tw.close()
        self.tmp.cleanup()

    async def test_resolve_and_search(self):
        self.assertEqual((await self.tw.resolve("2330")).name, "台積電")
        self.assertEqual((await self.tw.resolve("2330 台積電")).symbol, "2330")
        self.assertEqual((await self.tw.resolve("台積電")).symbol, "2330")
        self.assertTrue((await self.tw.resolve("0050")).is_etf)
        self.assertIsNone(await self.tw.resolve("9999"))
        self.assertEqual([s.symbol for s in await self.tw.search("23")], ["2303", "2330"])

    async def test_quote_and_bars(self):
        quote = await self.tw.latest_quote("2330")
        self.assertEqual((quote.trade_date, quote.price, quote.prev_close), (date(2026, 11, 2), Decimal("2330"),
                                                                            Decimal("2370")))
        self.assertFalse(quote.live)
        bar = await self.tw.first_bar_on_or_after("2330", date(2026, 10, 31))
        self.assertEqual((bar.trade_date, bar.open), (date(2026, 11, 2), Decimal("2310")))
        halted = await self.tw.first_bar_on_or_after("2303", date(2026, 10, 31))
        self.assertIsNone(halted.open)
        self.assertIsNone(await self.tw.first_bar_on_or_after("2330", date(2026, 11, 3)))
        self.assertEqual(await self.tw.valuation("2330"), (Decimal("24.3"), Decimal("1.60")))

    async def test_opened_read_only(self):
        await self.tw.search("2330")
        with self.assertRaises(Exception):
            await self.tw._conn.execute("DELETE FROM securities")


class StockWalletTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.tmp.name, "bot.db"))
        await self.db.connect()
        await self.db.add_points(1, 3_000_000, "test")
        self.fees = st.Fees()

    async def asyncTearDown(self):
        await self.db.close()
        self.tmp.cleanup()

    async def buy(self, price: str, shares: int):
        gross, fee = st.buy_total(self.fees, Decimal(price), shares)
        return await self.db.stock_trade(1, "buy", "2330", "台積電", shares, Decimal(price), gross, fee, 0)

    async def test_buy_and_sell_use_wallet(self):
        fill, error = await self.buy("2000", 1000)
        self.assertIsNone(error)
        self.assertEqual(fill.amount, 2_000_000 + 2850)
        self.assertEqual(await self.db.get_points(1), 3_000_000 - 2_002_850)
        _, error = await self.buy("2000", 1000)
        self.assertIn("存款不足", error)

        gross, fee, tax = st.sell_net(self.fees, Decimal("2100"), 400, is_etf=False)
        fill, error = await self.db.stock_trade(1, "sell", "2330", "台積電", 400, Decimal("2100"), gross, fee, tax)
        self.assertIsNone(error)
        # 成本 2,002,850 × 400/1000 = 801,140；實拿 840,000 - 1,197 - 2,520
        self.assertEqual(fill.realized, 840_000 - 1197 - 2520 - 801_140)
        self.assertEqual(await self.db.stock_holding(1, "2330"), (600, 600, 2_002_850 - 801_140))
        self.assertEqual(await self.db.get_points(1), 997_150 + 836_283)
        _, error = await self.db.stock_trade(1, "sell", "2330", "台積電", 601, Decimal("2100"), 1, 1, 1)
        self.assertIn("可賣股數不足", error)
        reasons = [r["reason"] for r in await self.db.recent_transactions(1)]
        self.assertTrue(any(r.startswith("股票賣出 2330") for r in reasons))

    async def test_queued_buy_refunds_difference(self):
        reserve = st.queue_reserve(self.fees, Decimal("2370"), 1000)
        order_id, error = await self.db.stock_queue(1, "buy", "2330", "台積電", 1000, date(2026, 11, 2), reserve)
        self.assertIsNone(error)
        self.assertEqual(await self.db.get_points(1), 3_000_000 - reserve)
        _, _, _, _ = await self.db.stock_account(1)
        gross, fee = st.buy_total(self.fees, Decimal("2310"), 1000)
        fill, note = await self.db.stock_fill(order_id, Decimal("2310"), gross, fee, 0)
        self.assertIsNone(note)
        self.assertEqual(fill.refund, reserve - gross - fee)
        self.assertEqual(await self.db.get_points(1), 3_000_000 - gross - fee)
        self.assertEqual((await self.db.stock_fill(order_id, Decimal("1"), 1, 1, 0)), (None, None))   # 不會重複成交

    async def test_cancel_and_queued_sell(self):
        await self.buy("2000", 1000)
        balance = await self.db.get_points(1)
        order_id, _ = await self.db.stock_queue(1, "sell", "2330", "台積電", 600, date(2026, 11, 3), 0)
        self.assertEqual(await self.db.stock_holding(1, "2330"), (1000, 400, 2_002_850))
        _, error = await self.db.stock_queue(1, "sell", "2330", "台積電", 500, date(2026, 11, 3), 0)
        self.assertIn("可賣股數不足", error)
        self.assertIsNone(await self.db.stock_cancel(order_id, "x", user_id=2))       # 別人不能取消
        self.assertIsNotNone(await self.db.stock_cancel(order_id, "自己取消", user_id=1))
        self.assertEqual(await self.db.stock_holding(1, "2330"), (1000, 1000, 2_002_850))

        buy_id, _ = await self.db.stock_queue(1, "buy", "0050", "元大台灣50", 100, date(2026, 11, 3), 12_000)
        self.assertEqual(await self.db.get_points(1), balance - 12_000)
        await self.db.stock_cancel(buy_id, "自己取消")
        self.assertEqual(await self.db.get_points(1), balance)
        history = await self.db.stock_history(1)
        self.assertEqual([r["status"] for r in history[:2]], ["cancelled", "cancelled"])

    async def test_restart_does_not_refund_stock_reserves(self):
        await self.db.stock_queue(1, "buy", "2330", "台積電", 1000, date(2026, 11, 3), 2_700_000)
        self.assertEqual(await self.db.refund_all_holds(), 0)
        self.assertEqual(await self.db.get_points(1), 300_000)


class CurrencyMigrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_points_become_twd_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bot.db")
            db = Database(path)
            await db.connect()
            await db.add_points(1, 1234, "舊積分")
            await db.conn.execute("DELETE FROM meta WHERE key = 'currency_twd'")   # 模擬改版前的資料庫
            await db.conn.commit()
            await db.close()
            for _ in range(2):   # 第二次啟動不會再乘一次
                db = Database(path)
                await db.connect()
                self.assertEqual(await db.get_points(1), 123_400)
                self.assertEqual((await db.recent_transactions(1))[0]["amount"], 123_400)
                await db.close()


class MatchOrdersTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from cogs.stocks import Stocks

        self.tmp = tempfile.TemporaryDirectory()
        tw_path = os.path.join(self.tmp.name, "twfolio.db")
        make_twfolio(tw_path)
        self.db = Database(os.path.join(self.tmp.name, "bot.db"))
        await self.db.connect()
        await self.db.add_points(1, 5_000_000, "test")
        settings = SimpleNamespace(twfolio_db=tw_path, twfolio_config=None)
        self.cog = Stocks(MagicMock(settings=settings, db=self.db))
        self.cog.market.live.get = AsyncMock(return_value={})
        self.cog.notify = AsyncMock()

    async def asyncTearDown(self):
        await self.cog.market.close()
        await self.db.close()
        self.tmp.cleanup()

    async def test_queued_orders_fill_at_next_open(self):
        order_id, _ = await self.db.stock_queue(1, "buy", "2330", "台積電", 1000, date(2026, 10, 31), 2_700_000)
        halted_id, _ = await self.db.stock_queue(1, "buy", "2303", "聯電", 1000, date(2026, 10, 31), 60_000)
        later_id, _ = await self.db.stock_queue(1, "buy", "2330", "台積電", 10, date(2026, 11, 3), 30_000)
        await self.cog.match_orders(datetime(2026, 11, 2, 19, 0, tzinfo=TAIPEI))

        statuses = {r["id"]: r for r in await self.db.stock_history(1)}
        self.assertEqual(statuses[order_id]["price"], "2310")           # 11/2 開盤價
        self.assertEqual(statuses[halted_id]["status"], "cancelled")     # 沒成交就取消退款
        self.assertEqual([r["id"] for r in await self.db.stock_pending(1)], [later_id])   # 還沒到日子
        self.assertEqual(self.cog.notify.await_count, 2)
        gross, fee = st.buy_total(st.Fees(), Decimal("2310"), 1000)
        self.assertEqual(await self.db.get_points(1), 5_000_000 - gross - fee - 30_000)

    async def test_live_open_used_during_session(self):
        order_id, _ = await self.db.stock_queue(1, "buy", "2330", "台積電", 1000, date(2026, 11, 3), 2_700_000)
        live = st.parse_mis({**RulesTest.MIS, "d": "20261103", "o": "2400.0000"})
        self.cog.market.live.get = AsyncMock(return_value={"2330": live})
        await self.cog.match_orders(datetime(2026, 11, 3, 9, 1, tzinfo=TAIPEI))
        self.assertEqual((await self.db.stock_history(1))[0]["price"], "2400")


if __name__ == "__main__":
    unittest.main()
