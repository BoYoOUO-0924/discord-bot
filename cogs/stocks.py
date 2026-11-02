"""股票模擬交易：用錢包裡的新台幣買賣台股（上市），賣出的錢也回到同一個錢包。

- 報價先查證交所即時行情，查不到再用 twfolio 的最新收盤價；歷史走勢、本益比、股票清單都來自 twfolio。
- 盤中（交易日 9:00–13:30）立刻用即時價成交；其他時間先委託，用下一個交易日的開盤價成交。
- 委託單每分鐘檢查一次：盤中用即時行情的開盤價，bot 錯過的話等 twfolio 18:30 更新後用每日資料補成交。
- 手續費與交易稅讀 twfolio 的 config.yaml，跟真實帳戶一致。
- 交易相關的畫面只有本人看得到；排行榜公開。
"""
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Dict, List, Optional

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands, tasks

from core import stocks as st
from core.db import StockFill
from core.timeutil import now_tw, ts
from core.ui import BaseView, Color, defer, fmt_signed, make_embed, respond, send_error

log = logging.getLogger(__name__)

UP = 0xE74C3C       # 台灣習慣：漲是紅色
DOWN = 0x2ECC71     # 跌是綠色
MEDALS = ["🥇", "🥈", "🥉"]
SIDE_TEXT = {"buy": "買進", "sell": "賣出"}
MAX_SHARES = 10_000_000


# ---------------------------------------------------------------------- 顯示小工具
def trend_color(value: Optional[Decimal]) -> int:
    if value is None or value == 0:
        return Color.MUTED
    return UP if value > 0 else DOWN


def trend_dot(value) -> str:
    if not value:
        return "⚪"
    return "🔴" if value > 0 else "🟢"


def fmt_change(quote: st.Quote) -> str:
    change, pct = quote.change, quote.change_pct
    if change is None:
        return ""
    if change == 0:
        return "平盤（0.00%）"
    arrow = "▲" if change > 0 else "▼"
    return f"{arrow} {st.fmt_price(abs(change))}（{pct:+.2f}%）"


def fmt_pct(value: float) -> str:
    return f"{value:+.2f}%"


def source_text(quote: st.Quote, now: datetime) -> str:
    if quote.live:
        status = "🔔 盤中" if st.can_trade_now(quote, now) else "🌙 已收盤"
        when = quote.updated.strftime("%H:%M:%S") if quote.updated else ""
        if quote.trade_date != now.date():
            when = f"{quote.trade_date.month}/{quote.trade_date.day} {when}".strip()
        return f"{status}・即時 {when}・證交所"
    return f"🌙 收盤 {quote.trade_date.month}/{quote.trade_date.day}・twfolio 每日資料"


def fill_embed(fill: StockFill, queued: bool) -> discord.Embed:
    side = SIDE_TEXT[fill.side]
    embed = make_embed(f"✅ {side}成交", f"**{fill.symbol} {fill.name}**　{fill.shares:,} 股 @ **{st.fmt_price(fill.price)}**",
                       Color.SUCCESS)
    embed.add_field(name="成交金額", value=f"{fill.gross:,} 元")
    embed.add_field(name="手續費", value=f"{fill.fee:,} 元")
    if fill.side == "sell":
        embed.add_field(name="交易稅", value=f"{fill.tax:,} 元")
        embed.add_field(name="實拿", value=f"**{fill.amount:,} 元**")
        embed.add_field(name="已實現損益", value=f"{trend_dot(fill.realized)} {fmt_signed(fill.realized or 0)} 元")
    else:
        embed.add_field(name="實付", value=f"**{fill.amount:,} 元**")
        if fill.refund:
            embed.add_field(name="退回預扣", value=f"{fill.refund:,} 元")
    embed.add_field(name="錢包餘額", value=f"{fill.cash:,} 元")
    embed.set_footer(text=f"委託單 #{fill.order_id}・開盤價成交" if queued else "盤中即時成交")
    return embed


# ---------------------------------------------------------------------- 介面
class SharesModal(discord.ui.Modal):
    def __init__(self, cog: "Stocks", security: st.Security, side: str, default: Optional[int] = None):
        super().__init__(title=f"{SIDE_TEXT[side]} {security.symbol} {security.name}"[:45])
        self.cog, self.security, self.side = cog, security, side
        self.shares = discord.ui.TextInput(label="股數", default=str(default) if default else None,
                                           placeholder="1 張 = 1,000 股；零股輸入 1–999", max_length=10)
        self.add_item(self.shares)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        text = self.shares.value.replace(",", "").strip()
        if not text.isdigit() or not 1 <= int(text) <= MAX_SHARES:
            return await send_error(interaction, f"股數要是 1 到 {MAX_SHARES:,} 的整數。")
        await self.cog.prepare_order(interaction, self.security, self.side, int(text))

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.exception("股數表單發生錯誤", exc_info=error)
        await send_error(interaction, "處理表單時發生錯誤，請稍後再試。")


class QuoteView(BaseView):
    def __init__(self, cog: "Stocks", owner_id: int, security: st.Security, held: int):
        super().__init__(owner_id=owner_id, timeout=600)
        self.cog, self.security = cog, security
        if not held:
            self.remove_item(self.sell)

    @discord.ui.button(label="買進", emoji="🛒", style=discord.ButtonStyle.danger)
    async def buy(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SharesModal(self.cog, self.security, "buy"))

    @discord.ui.button(label="賣出", emoji="💸", style=discord.ButtonStyle.success)
    async def sell(self, interaction: discord.Interaction, button: discord.ui.Button):
        _, available, _ = await self.cog.db.stock_holding(interaction.user.id, self.security.symbol)
        await interaction.response.send_modal(SharesModal(self.cog, self.security, "sell", default=available or None))

    @discord.ui.button(label="更新", emoji="🔄", style=discord.ButtonStyle.secondary)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        embed, held = await self.cog.quote_embed(self.security, interaction.user.id)
        view = QuoteView(self.cog, interaction.user.id, self.security, held)
        await interaction.edit_original_response(embed=embed, view=view)


class ConfirmView(BaseView):
    def __init__(self, cog: "Stocks", owner_id: int, security: st.Security, side: str, shares: int):
        super().__init__(owner_id=owner_id, timeout=120)
        self.cog, self.security, self.side, self.shares = cog, security, side, shares

    @discord.ui.button(label="確認下單", emoji="✅", style=discord.ButtonStyle.primary)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.defer()
        embed = await self.cog.execute_order(interaction.user.id, self.security, self.side, self.shares)
        await interaction.edit_original_response(embed=embed, view=None)

    @discord.ui.button(label="取消", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(embed=make_embed("已取消，沒有下單。", color=Color.MUTED), view=None)


class AccountView(BaseView):
    def __init__(self, cog: "Stocks", owner_id: int, sellable: List[st.Security], pending: int):
        super().__init__(owner_id=owner_id, timeout=600)
        self.cog = cog
        self.sellable = {s.symbol: s for s in sellable}
        if sellable:
            select = discord.ui.Select(placeholder="選擇持股快速賣出", row=0, options=[
                discord.SelectOption(label=f"{s.symbol} {s.name}"[:100], value=s.symbol) for s in sellable[:25]])
            select.callback = self._sell
            self.add_item(select)
        self.orders.label = f"委託中（{pending}）"

    async def _sell(self, interaction: discord.Interaction) -> None:
        security = self.sellable[interaction.data["values"][0]]
        _, available, _ = await self.cog.db.stock_holding(interaction.user.id, security.symbol)
        await interaction.response.send_modal(SharesModal(self.cog, security, "sell", default=available or None))

    @discord.ui.button(label="委託中", emoji="📋", style=discord.ButtonStyle.secondary, row=1)
    async def orders(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.send_orders(interaction)

    @discord.ui.button(label="交易紀錄", emoji="🧾", style=discord.ButtonStyle.secondary, row=1)
    async def history(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.send_history(interaction)

    @discord.ui.button(label="分享到頻道", emoji="📢", style=discord.ButtonStyle.primary, row=1)
    async def share(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            return await send_error(interaction, "分享只能在伺服器裡使用。")
        await defer(interaction)
        embed, _, _ = await self.cog.account_embed(interaction.user, public=True)
        await respond(interaction, embed=embed)


class OrdersView(BaseView):
    def __init__(self, cog: "Stocks", owner_id: int, orders: List[aiosqlite.Row]):
        super().__init__(owner_id=owner_id, timeout=300)
        self.cog = cog
        select = discord.ui.Select(placeholder="選擇要取消的委託", options=[
            discord.SelectOption(label=f"#{o['id']} {SIDE_TEXT[o['side']]} {o['symbol']} {o['name']} {o['shares']:,} 股"[:100],
                                 value=str(o["id"])) for o in orders[:25]])
        select.callback = self._cancel
        self.add_item(select)

    async def _cancel(self, interaction: discord.Interaction) -> None:
        order_id = int(interaction.data["values"][0])
        order = await self.cog.db.stock_cancel(order_id, "自己取消", user_id=interaction.user.id)
        if not order:
            return await send_error(interaction, "這筆委託已經成交或取消了。")
        refund = f"，退回預扣 {order['reserved']:,} 元" if order["side"] == "buy" else ""
        await interaction.response.edit_message(
            embed=make_embed(f"🗑️ 已取消委託 #{order_id}",
                             f"{SIDE_TEXT[order['side']]} {order['symbol']} {order['name']} {order['shares']:,} 股{refund}。",
                             Color.MUTED), view=None)


# ---------------------------------------------------------------------- 功能
class Stocks(commands.Cog):
    stock = app_commands.Group(name="stock", description="股票模擬交易：用錢包裡的新台幣買賣台股")

    def __init__(self, bot):
        self.bot = bot
        settings = bot.settings
        self.market = st.Market(getattr(settings, "twfolio_db", None))
        self.fees = st.load_fees(getattr(settings, "twfolio_config", None))

    @property
    def db(self):
        return self.bot.db

    async def cog_load(self):
        if not self.market.db.available:
            log.warning("股票模擬：找不到 twfolio 資料庫 %s，/股票 會停用（可在 .env 設定 TWFOLIO_DB）",
                        self.market.db.path)
            return
        self.match_loop.start()

    async def cog_unload(self):
        self.match_loop.cancel()
        await self.market.close()

    # ------------------------------------------------------------------ 共用
    async def find(self, interaction: discord.Interaction, text: str) -> Optional[st.Security]:
        if not self.market.db.available:
            await send_error(interaction, "股票資料還沒準備好（找不到 twfolio 資料庫），請管理員確認。")
            return None
        security = await self.market.db.resolve(text)
        if not security:
            await send_error(interaction, f"找不到「{text}」，請輸入股票代號（例如 2330）或從選單挑選。")
        return security

    async def _symbol_choices(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        if not self.market.db.available:
            return []
        try:
            found = await self.market.db.search(current, limit=25)
        except (aiosqlite.Error, st.MarketError):
            return []
        return [app_commands.Choice(name=f"{s.symbol} {s.name}"[:100], value=s.symbol) for s in found]

    async def _names(self, symbols) -> Dict[str, st.Security]:
        result = {}
        for symbol in symbols:
            security = await self.market.db.security(symbol)
            result[symbol] = security or st.Security(symbol, symbol, False)
        return result

    # ------------------------------------------------------------------ 報價
    async def quote_embed(self, security: st.Security, user_id: int):
        """回傳 (報價卡, 持有股數)。"""
        now = now_tw()
        quote = await self.market.quote(security.symbol)
        if not quote:
            return make_embed(f"{security.symbol} {security.name}", "目前查不到這檔的行情。", Color.MUTED), 0
        embed = make_embed(f"📈 {security.symbol} {security.name}",
                           f"## {st.fmt_price(quote.price)}　{fmt_change(quote)}", trend_color(quote.change))
        embed.add_field(name="開／高／低", value=" / ".join(st.fmt_price(v) for v in (quote.open, quote.high, quote.low)))
        embed.add_field(name="昨收", value=st.fmt_price(quote.prev_close))
        if quote.limit_up:
            embed.add_field(name="漲停／跌停", value=f"{st.fmt_price(quote.limit_up)} / {st.fmt_price(quote.limit_down)}")
        if quote.volume_lots is not None:
            embed.add_field(name="成交量", value=f"{quote.volume_lots:,} 張")

        bars = await self.market.db.closes(security.symbol, days=30)
        closes = [b.close for b in bars if b.close is not None]
        if quote.live and quote.price is not None and bars and quote.trade_date > bars[-1].trade_date:
            closes.append(quote.price)    # 盤中把今天的價格接在走勢後面
        low, high = await self.market.db.range_52w(security.symbol, now.date())
        if quote.live:   # 每日資料還沒有今天，把盤中高低點算進去
            low = min(v for v in (low, quote.low) if v is not None) if (low or quote.low) else None
            high = max(v for v in (high, quote.high) if v is not None) if (high or quote.high) else None
        lines = []
        if len(closes) >= 2:
            lines.append(f"近 {len(closes)} 日 {st.sparkline(closes)}")
        if low and high:
            lines.append(f"52 週 {st.fmt_price(low)} ~ {st.fmt_price(high)}")
        pe, dividend_yield = await self.market.db.valuation(security.symbol)
        valuation = []
        if pe is not None:
            valuation.append(f"本益比 {pe:.1f}")
        if dividend_yield is not None:
            valuation.append(f"殖利率 {dividend_yield:.2f}%")
        if valuation:
            lines.append("　".join(valuation))
        if lines:
            embed.add_field(name="走勢與評價", value="\n".join(lines), inline=False)

        shares, _, cost = await self.db.stock_holding(user_id, security.symbol)
        if shares and quote.reference:
            value = st.gross_amount(quote.reference, shares)
            pnl = value - cost
            embed.add_field(name="你持有", value=f"{shares:,} 股・成本 {st.fmt_price(Decimal(cost) / shares)}・"
                            f"損益 {trend_dot(pnl)} {fmt_signed(pnl)}（{fmt_pct(pnl / cost * 100 if cost else 0)}）",
                            inline=False)
        embed.set_footer(text=source_text(quote, now))
        return embed, shares

    async def send_quote(self, interaction: discord.Interaction, security: st.Security) -> None:
        await defer(interaction, ephemeral=True)
        embed, held = await self.quote_embed(security, interaction.user.id)
        view = QuoteView(self, interaction.user.id, security, held)
        view.message = await respond(interaction, embed=embed, view=view, ephemeral=True)

    # ------------------------------------------------------------------ 下單
    async def prepare_order(self, interaction: discord.Interaction, security: st.Security, side: str,
                            shares: int) -> None:
        """顯示確認卡：盤中是即時價估算，盤後是委託說明。真正成交在按下確認時重新計算。"""
        await defer(interaction, ephemeral=True)
        now = now_tw()
        quote = await self.market.quote(security.symbol)
        if not quote or quote.reference is None:
            return await send_error(interaction, "目前查不到這檔的行情，無法下單。")
        if side == "sell":
            held, available, _ = await self.db.stock_holding(interaction.user.id, security.symbol)
            if available < shares:
                extra = f"（另有 {held - available:,} 股在委託賣出中）" if held > available else ""
                return await send_error(interaction, f"可賣股數不足：你目前可以賣 {available:,} 股{extra}。")
        cash = await self.db.get_points(interaction.user.id)

        title = f"{SIDE_TEXT[side]} {security.symbol} {security.name}　{shares:,} 股"
        if st.can_trade_now(quote, now):
            price, reason = st.instant_price(quote, side)
            if reason:
                return await send_error(interaction, reason)
            embed = make_embed(f"🧾 確認{title}", f"盤中即時成交，目前價格約 **{st.fmt_price(price)}**。\n"
                               "按下確認時會用當下的價格成交。", Color.PRIMARY)
            if side == "buy":
                gross, fee = st.buy_total(self.fees, price, shares)
                embed.add_field(name="成交金額", value=f"{gross:,} 元")
                embed.add_field(name="手續費", value=f"{fee:,} 元")
                embed.add_field(name="預估總額", value=f"**{gross + fee:,} 元**")
            else:
                gross, fee, tax = st.sell_net(self.fees, price, shares, security.is_etf)
                embed.add_field(name="成交金額", value=f"{gross:,} 元")
                embed.add_field(name="手續費＋交易稅", value=f"{fee + tax:,} 元")
                embed.add_field(name="預估實拿", value=f"**{gross - fee - tax:,} 元**")
        else:
            start = st.queue_start(now)
            embed = make_embed(f"🕘 確認委託{title}",
                               f"現在不是交易時段，會用 **{start.month}/{start.day} 起第一個交易日的開盤價**成交，"
                               "開盤後會私訊通知你。成交前可以在「委託」取消。", Color.WARNING)
            embed.add_field(name="參考價", value=f"{st.fmt_price(quote.reference)}（{source_text(quote, now)}）",
                            inline=False)
            if side == "buy":
                reserve = st.queue_reserve(self.fees, quote.reference, shares)
                embed.add_field(name="先預扣", value=f"**{reserve:,} 元**\n（參考價 × 1.1，成交後多的退回）")
            else:
                gross, fee, tax = st.sell_net(self.fees, quote.reference, shares, security.is_etf)
                embed.add_field(name="以參考價估算實拿", value=f"{gross - fee - tax:,} 元")
        if side == "buy" and shares < st.BOARD_LOT:
            embed.add_field(name="零股", value="零股也用同一個價格成交，手續費最低 1 元。", inline=False)
        embed.set_footer(text=f"錢包餘額 {cash:,} 元")
        view = ConfirmView(self, interaction.user.id, security, side, shares)
        view.message = await respond(interaction, embed=embed, view=view, ephemeral=True)

    async def execute_order(self, user_id: int, security: st.Security, side: str, shares: int) -> discord.Embed:
        """按下確認後重新判斷：盤中立刻成交，否則送出委託。回傳結果畫面。"""
        now = now_tw()
        quote = await self.market.quote(security.symbol)
        if not quote or quote.reference is None:
            return make_embed("❌ 下單失敗", "目前查不到這檔的行情。", Color.DANGER)
        if st.can_trade_now(quote, now):
            price, reason = st.instant_price(quote, side)
            if reason:
                return make_embed("❌ 下單失敗", reason, Color.DANGER)
            if side == "buy":
                gross, fee = st.buy_total(self.fees, price, shares)
                tax = 0
            else:
                gross, fee, tax = st.sell_net(self.fees, price, shares, security.is_etf)
            fill, error = await self.db.stock_trade(user_id, side, security.symbol, security.name, shares, price,
                                                    gross, fee, tax)
            if error:
                return make_embed("❌ 下單失敗", error, Color.DANGER)
            log.info("股票成交：%s %s %s %d 股 @ %s", user_id, side, security.symbol, shares, price)
            return fill_embed(fill, queued=False)

        start = st.queue_start(now)
        reserve = st.queue_reserve(self.fees, quote.reference, shares) if side == "buy" else 0
        order_id, error = await self.db.stock_queue(user_id, side, security.symbol, security.name, shares, start, reserve)
        if error:
            return make_embed("❌ 委託失敗", error, Color.DANGER)
        embed = make_embed(f"🕘 已委託 #{order_id}",
                           f"{SIDE_TEXT[side]} **{security.symbol} {security.name}** {shares:,} 股，"
                           f"用 {start.month}/{start.day} 起第一個交易日的開盤價成交，成交後私訊通知你。", Color.WARNING)
        if reserve:
            embed.add_field(name="已預扣", value=f"{reserve:,} 元（成交後多的退回）")
        return embed

    # ------------------------------------------------------------------ 帳戶
    async def account_embed(self, user: discord.abc.User, public: bool = False):
        """回傳 (帳戶畫面, 可賣的持股, 委託中筆數)。public=True 是分享到頻道的版本，不列錢包餘額。"""
        cash, reserved, realized, holdings = await self.db.stock_account(user.id)
        quotes = await self.market.quotes([h["symbol"] for h in holdings])
        names = await self._names([h["symbol"] for h in holdings])
        value_total, lines, sellable = 0, [], []
        for h in holdings:
            quote = quotes.get(h["symbol"])
            price = quote.reference if quote else None
            value = st.gross_amount(price, h["shares"]) if price else h["cost"]
            value_total += value
            pnl = value - h["cost"]
            pct = pnl / h["cost"] * 100 if h["cost"] else 0
            security = names[h["symbol"]]
            lines.append(f"{trend_dot(pnl)} **{h['symbol']} {security.name}**　{h['shares']:,} 股　"
                         f"現價 {st.fmt_price(price)}\n　市值 {value:,}　損益 {fmt_signed(pnl)}（{fmt_pct(pct)}）")
            if h["shares"] > h["reserved"]:
                sellable.append(security)
        cost_total = sum(h["cost"] for h in holdings)
        unrealized = value_total - cost_total
        pct = unrealized / cost_total * 100 if cost_total else 0
        embed = make_embed(f"💼 {user.display_name} 的股票帳戶",
                           f"## 股票市值 {value_total:,} 元\n{trend_dot(unrealized)} 未實現損益 "
                           f"**{fmt_signed(unrealized)} 元**（{fmt_pct(pct)}）",
                           trend_color(Decimal(unrealized)))
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(name="已實現損益", value=f"{trend_dot(realized)} {fmt_signed(realized)} 元")
        if not public:
            embed.add_field(name="錢包餘額", value=f"{cash:,} 元")
            embed.add_field(name="委託預扣", value=f"{reserved:,} 元")
        text = "\n".join(lines) or "還沒有持股，用「/股票 查詢」找一檔試試看！"
        embed.add_field(name=f"持股（{len(holdings)} 檔）", value=text[:1024], inline=False)
        pending = len(await self.db.stock_pending(user.id))
        embed.set_footer(text="買股票用的是錢包裡的新台幣・市值以最新價格計算，未扣賣出時的手續費與交易稅")
        return embed, sellable, pending

    async def send_account(self, interaction: discord.Interaction) -> None:
        if not self.market.db.available:
            return await send_error(interaction, "股票資料還沒準備好（找不到 twfolio 資料庫），請管理員確認。")
        await defer(interaction, ephemeral=True)
        embed, sellable, pending = await self.account_embed(interaction.user)
        view = AccountView(self, interaction.user.id, sellable, pending)
        view.message = await respond(interaction, embed=embed, view=view, ephemeral=True)

    async def send_orders(self, interaction: discord.Interaction) -> None:
        orders = await self.db.stock_pending(interaction.user.id)
        if not orders:
            embed = make_embed("📋 委託中", "目前沒有委託單。盤後下單才會變成委託，盤中是立刻成交。", Color.MUTED)
            return await respond(interaction, embed=embed, ephemeral=True)
        lines = []
        for o in orders:
            start = date.fromisoformat(o["not_before"])
            reserve = f"・預扣 {o['reserved']:,} 元" if o["reserved"] else ""
            lines.append(f"`#{o['id']}` {SIDE_TEXT[o['side']]} **{o['symbol']} {o['name']}** {o['shares']:,} 股　"
                         f"{start.month}/{start.day} 起開盤價{reserve}・{ts(o['created_at'], 'R')}下單")
        embed = make_embed("📋 委託中", "\n".join(lines)[:4000], Color.WARNING)
        await respond(interaction, embed=embed, view=OrdersView(self, interaction.user.id, orders), ephemeral=True)

    async def send_history(self, interaction: discord.Interaction) -> None:
        rows = await self.db.stock_history(interaction.user.id)
        if not rows:
            embed = make_embed("🧾 交易紀錄", "還沒有交易紀錄。", Color.MUTED)
            return await respond(interaction, embed=embed, ephemeral=True)
        lines = []
        for r in rows:
            head = f"{SIDE_TEXT[r['side']]} **{r['symbol']} {r['name']}** {r['shares']:,} 股"
            if r["status"] == "cancelled":
                lines.append(f"~~{head}~~　已取消：{r['note'] or ''}・{ts(r['filled_at'] or r['created_at'], 'R')}")
                continue
            if r["side"] == "buy":
                money = f"付 {r['gross'] + r['fee']:,}"
            else:
                money = f"拿 {r['gross'] - r['fee'] - r['tax']:,}・損益 {trend_dot(r['realized'])} {fmt_signed(r['realized'])}"
            lines.append(f"{head} @ {st.fmt_price(Decimal(r['price']))}　{money}・{ts(r['filled_at'], 'R')}")
        embed = make_embed("🧾 交易紀錄（最近 15 筆）", "\n".join(lines)[:4000], Color.PRIMARY)
        await respond(interaction, embed=embed, ephemeral=True)

    # ------------------------------------------------------------------ 排行榜
    async def rank_embed(self, guild: discord.Guild, viewer_id: int) -> discord.Embed:
        realized, holdings = await self.db.stock_players(guild.id)
        embed = make_embed("🏆 股票損益排行榜", color=Color.GOLD)
        if not realized:
            embed.description = "還沒有人買過股票，用「/股票 查詢」挑一檔試試看！"
            return embed
        quotes = await self.market.quotes({h["symbol"] for h in holdings})
        pnl = dict(realized)          # 已實現 + 未實現
        top: Dict[int, List[tuple]] = {}
        for h in holdings:
            quote = quotes.get(h["symbol"])
            value = st.gross_amount(quote.reference, h["shares"]) if quote and quote.reference else h["cost"]
            pnl[h["user_id"]] += value - h["cost"]
            top.setdefault(h["user_id"], []).append((value, h["symbol"]))
        ranking = sorted(pnl.items(), key=lambda item: (-item[1], item[0]))
        lines = []
        for i, (user_id, total) in enumerate(ranking[:10]):
            badge = MEDALS[i] if i < 3 else f"`{i + 1:>2}.`"
            picks = "、".join(symbol for _, symbol in sorted(top.get(user_id, []), reverse=True)[:3]) or "空手"
            me = " ← 你" if user_id == viewer_id else ""
            lines.append(f"{badge} <@{user_id}>　{trend_dot(total)} **{fmt_signed(total)} 元**{me}\n　　持股：{picks}")
        embed.description = "\n".join(lines)
        mine = [i for i, (uid, _) in enumerate(ranking) if uid == viewer_id]
        if mine and mine[0] >= 10:
            embed.set_footer(text=f"你目前第 {mine[0] + 1} 名・損益 = 已實現 + 未實現")
        elif not mine:
            embed.set_footer(text="你還沒有買過股票・損益 = 已實現 + 未實現")
        else:
            embed.set_footer(text="損益 = 已實現 + 未實現")
        return embed

    # ------------------------------------------------------------------ 委託單撮合
    @tasks.loop(minutes=1)
    async def match_loop(self):
        try:
            await self.match_orders(now_tw())
        except (aiosqlite.Error, st.MarketError) as e:
            log.warning("委託單撮合失敗，下一分鐘重試：%s", e)

    @match_loop.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()

    async def match_orders(self, now: datetime) -> None:
        """委託單用「最早可成交日」當天或之後第一個交易日的開盤價成交。

        盤中先看即時行情（今天的開盤價），bot 那時沒開的話，等 twfolio 更新後用每日資料補上。
        """
        pending = await self.db.stock_pending()
        if not pending:
            return
        live = await self.market.live.get({o["symbol"] for o in pending}) if st.in_session(now) else {}
        for order in pending:
            start = date.fromisoformat(order["not_before"])
            quote = live.get(order["symbol"])
            price, day = None, None
            if quote and quote.trade_date == now.date() and quote.trade_date >= start and quote.open is not None:
                price, day = quote.open, quote.trade_date
            else:
                bar = await self.market.db.first_bar_on_or_after(order["symbol"], start)
                if bar is None:
                    continue
                if bar.open is None:
                    note = f"{bar.trade_date.month}/{bar.trade_date.day} 這檔沒有成交，委託已取消"
                    if await self.db.stock_cancel(order["id"], note):
                        await self.notify(order["user_id"], make_embed(
                            f"🗑️ 委託 #{order['id']} 已取消",
                            f"{SIDE_TEXT[order['side']]} {order['symbol']} {order['name']} {order['shares']:,} 股：{note}，"
                            "預扣的已經退回。", Color.MUTED))
                    continue
                price, day = bar.open, bar.trade_date
            if order["side"] == "buy":
                gross, fee = st.buy_total(self.fees, price, order["shares"])
                tax = 0
            else:
                security = await self.market.db.security(order["symbol"])
                gross, fee, tax = st.sell_net(self.fees, price, order["shares"], bool(security and security.is_etf))
            fill, note = await self.db.stock_fill(order["id"], price, gross, fee, tax)
            if fill:
                log.info("委託單 #%d 於 %s 開盤價 %s 成交", order["id"], day, price)
                await self.notify(order["user_id"], fill_embed(fill, queued=True))
            elif note:
                await self.notify(order["user_id"], make_embed(f"🗑️ 委託 #{order['id']} 已取消", note, Color.MUTED))

    async def notify(self, user_id: int, embed: discord.Embed) -> None:
        try:
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
            await user.send(embed=embed)
        except discord.HTTPException as e:
            log.info("無法私訊 %s 委託單結果（可能關閉了私訊）：%s", user_id, e)

    # ------------------------------------------------------------------ 指令
    @stock.command(name="quote", description="查詢股價、漲跌、走勢與本益比，可以直接買賣")
    @app_commands.describe(symbol="股票代號或名稱，例如 2330 或 台積電")
    @app_commands.autocomplete(symbol=_symbol_choices)
    async def quote_command(self, interaction: discord.Interaction, symbol: str):
        security = await self.find(interaction, symbol)
        if security:
            await self.send_quote(interaction, security)

    @stock.command(name="buy", description="買進股票：盤中立刻成交，盤後用下一個交易日的開盤價成交")
    @app_commands.describe(symbol="股票代號或名稱，例如 2330 或 台積電", shares="股數：1 張 = 1,000 股，零股可輸入 1–999")
    @app_commands.autocomplete(symbol=_symbol_choices)
    async def buy_command(self, interaction: discord.Interaction, symbol: str,
                          shares: app_commands.Range[int, 1, MAX_SHARES]):
        security = await self.find(interaction, symbol)
        if security:
            await self.prepare_order(interaction, security, "buy", shares)

    @stock.command(name="sell", description="賣出持股：盤中立刻成交，盤後用下一個交易日的開盤價成交")
    @app_commands.describe(symbol="股票代號或名稱，例如 2330 或 台積電", shares="股數：1 張 = 1,000 股")
    @app_commands.autocomplete(symbol=_symbol_choices)
    async def sell_command(self, interaction: discord.Interaction, symbol: str,
                           shares: app_commands.Range[int, 1, MAX_SHARES]):
        security = await self.find(interaction, symbol)
        if security:
            await self.prepare_order(interaction, security, "sell", shares)

    @stock.command(name="account", description="查看持股、未實現與已實現損益")
    async def account_command(self, interaction: discord.Interaction):
        await self.send_account(interaction)

    @stock.command(name="orders", description="查看或取消還沒成交的委託單")
    async def orders_command(self, interaction: discord.Interaction):
        await self.send_orders(interaction)

    @stock.command(name="history", description="最近 15 筆成交與取消紀錄")
    async def history_command(self, interaction: discord.Interaction):
        await self.send_history(interaction)

    @stock.command(name="rank", description="伺服器股票損益排行榜（已實現 + 未實現）")
    @app_commands.guild_only()
    async def rank_command(self, interaction: discord.Interaction):
        if not self.market.db.available:
            return await send_error(interaction, "股票資料還沒準備好（找不到 twfolio 資料庫），請管理員確認。")
        await defer(interaction)
        await respond(interaction, embed=await self.rank_embed(interaction.guild, interaction.user.id))


async def setup(bot):
    await bot.add_cog(Stocks(bot))
