"""骰寶（押大小）：一個頻道一張桌，大家在同一則訊息下注、一起開獎。

一局的流程：開放下注 BET_SECONDS 秒 → 停止下注、搖骰 → 開獎結算 → 休息 BREAK_SECONDS 秒後
在頻道發新訊息開下一局。停止下注之後才來的人要等下一局；沒人下注的那局直接收桌。
"""
import asyncio
import logging
import math
import time
from collections import deque
from typing import Dict, List, Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands

from core.features import enabled
from core.quests import MIN_QUEST_BET
from core.sicbo import (MAX_PER_ROUND, MIN_CHIP, RULES, SPOTS, SicBoRound, describe, faces, road_mark, roll)
from core.ui import (AmountModal, Color, defer, fmt_points, fmt_signed, log_interaction_error, make_embed, progress_bar,
                     respond, send_error)

log = logging.getLogger(__name__)

BET_SECONDS = 30
ROLL_SECONDS = 1.5
BREAK_SECONDS = 8
REFRESH_SECONDS = 2.0     # 押注畫面最多每 2 秒更新一次，避免被 Discord 限速
DEFAULT_CHIP = 5_000
CHIPS = (1_000, 5_000, 10_000, 25_000, 50_000)
HISTORY = 12
RULES_TIMEOUT = 60         # 之後由 cog_load 註冊的永久介面接手


def fmt_spot_amount(key: str, amount: int) -> str:
    return f"{SPOTS[key].label} {amount:,}"


def rules_embed() -> discord.Embed:
    embed = make_embed("📋 骰寶押法說明", color=Color.TABLE)
    for name, value in RULES:
        embed.add_field(name=name, value=value, inline=False)
    return embed


def _clip(lines: List[str], limit: int = 1000) -> str:
    out, size = [], 0
    for i, line in enumerate(lines):
        if size + len(line) + 1 > limit:
            out.append(f"…還有 {len(lines) - i} 筆")
            break
        out.append(line)
        size += len(line) + 1
    return "\n".join(out) or "—"


def next_tick(remaining: float) -> int:
    """倒數下一次要更新成幾秒：剩 10 秒以上每 5 秒更新一次，最後 10 秒每 2 秒一次。

    不每秒更新：同一個頻道約 5 秒只能改 5 次訊息，還要留給押注更新和其他遊戲。
    """
    step = 5 if remaining > 10 else 2
    return (math.ceil(remaining - 0.05) - 1) // step * step


class Table:
    def __init__(self, channel, guild_id: Optional[int]):
        self.channel = channel
        self.channel_id: int = channel.id
        self.guild_id = guild_id
        self.phase = "betting"        # betting / rolling / break / closed
        self.number = 0
        self.round: Optional[SicBoRound] = None
        self.closes_at = 0.0
        self.next_at = 0.0
        self.history: deque = deque(maxlen=HISTORY)
        self.message = None
        self.view: Optional["TableView"] = None
        self.lock = asyncio.Lock()
        self.task: Optional[asyncio.Task] = None
        self.refresh_task: Optional[asyncio.Task] = None
        self.ticker: Optional[asyncio.Task] = None
        self.edit_lock = asyncio.Lock()      # 更新畫面排隊，舊的倒數才不會蓋掉開獎
        self.result: Optional[discord.Embed] = None

    def road(self) -> str:
        return "　".join(road_mark(d) for d in self.history) or "還沒有紀錄"


class SicBo(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.tables: Dict[int, Table] = {}
        self.chips: Dict[int, int] = {}

    async def cog_load(self):
        # 開獎中、開獎後的「押法說明」：註冊成永久介面，過多久、重啟後都能按
        self.bot.add_view(RulesView())

    async def cog_unload(self):
        for table in list(self.tables.values()):
            if table.task:
                table.task.cancel()

    def chip(self, user_id: int) -> int:
        return self.chips.get(user_id, DEFAULT_CHIP)

    # ------------------------------------------------------------------ 畫面
    def betting_embed(self, table: Table) -> discord.Embed:
        rnd = table.round
        embed = make_embed(
            f"🎲 骰寶・第 {table.number} 局",
            f"**開放下注中**　⏳ {self.countdown(table.closes_at, BET_SECONDS)}\n"
            "按下面的按鈕用你的籌碼押一注（可以連按疊加），點數、總和、對子等在「🎯 更多押法」。\n"
            "⚠️ 開出豹子時，大小單雙全部算輸。",
            Color.TABLE)
        totals = [f"{SPOTS[k].label}　{n} 人・{a:,} 元" for k, n, a in rnd.spot_totals()]
        embed.add_field(name="目前押注", value=_clip(totals) if totals else "還沒有人下注", inline=True)
        players = [f"{rnd.names.get(u, u)}　{rnd.stake(u):,} 元" for u in rnd.bets]
        embed.add_field(name=f"玩家（{len(players)} 人）", value=_clip(players) if players else "—", inline=True)
        embed.add_field(name="路單（舊 → 新）", value=table.road(), inline=False)
        embed.set_footer(text=f"預設籌碼 {DEFAULT_CHIP:,} 元・每人每局最多 {MAX_PER_ROUND:,} 元")
        return embed

    @staticmethod
    def countdown(until: float, total: float) -> str:
        """倒數條。用固定秒數、由 bot 定時更新：Discord 的相對時間（:R）不保證每個人的畫面都會跳。"""
        left = max(0, round(until - time.time()))
        return f"`{progress_bar(left / total if total else 0)}` 剩 **{left}** 秒"

    def rolling_embed(self, table: Table) -> discord.Embed:
        rnd = table.round
        return make_embed(f"🎲 骰寶・第 {table.number} 局",
                          f"**停止下注！** 莊家搖骰中…\n# 🎲 🎲 🎲\n"
                          f"本局 {len(rnd.bets)} 人下注，共 {rnd.total:,} 元", Color.GAME)

    def result_embed(self, table: Table, dice, results: Dict[int, Tuple[int, int, list]],
                     balances: Dict[int, int]) -> discord.Embed:
        rnd = table.round
        triple = dice[0] == dice[1] == dice[2]
        embed = make_embed(f"🎲 骰寶・第 {table.number} 局開獎", f"# {faces(dice)}\n**{describe(dice)}**",
                           Color.GOLD if triple else Color.TABLE)
        lines = []
        for user_id, (stake, paid, spots) in sorted(results.items(), key=lambda r: r[1][0] - r[1][1]):
            won = [f"{SPOTS[k].label}（+{r - a:,}）" for k, a, r in spots if r]
            detail = "中 " + "、".join(won) if won else "沒中"
            lines.append(f"**{rnd.names.get(user_id, user_id)}** 押 {stake:,}，{detail}，"
                         f"損益 **{fmt_signed(paid - stake)}**・餘額 {balances.get(user_id, 0):,}")
        embed.add_field(name="結算", value=_clip(lines), inline=False)
        embed.add_field(name="路單（舊 → 新）", value=table.road(), inline=False)
        embed.add_field(name="下一局", value=self.next_text(table), inline=False)
        return embed

    def next_text(self, table: Table) -> str:
        return f"⏳ {self.countdown(table.next_at, BREAK_SECONDS)}後開放下注"

    def wait_message(self, table: Table) -> str:
        if table.phase in ("rolling", "break") and table.next_at > time.time():
            left = math.ceil(table.next_at - time.time())
            return f"這局已經停止下注，下一局大約 {left} 秒後開放，請等一下再押。"
        if table.phase in ("rolling", "break"):
            return "這局已經停止下注，正在開獎，下一局馬上開放。"
        return "這張賭桌已經收起來了，用 /sicbo 再開一桌。"

    # ------------------------------------------------------------------ 開桌與每一局
    async def open_table(self, interaction: discord.Interaction) -> None:
        channel = interaction.channel
        if channel is None:
            return await send_error(interaction, "這裡不能開賭桌。")
        if await self._already_open(interaction, channel):
            return
        # 先回應 Discord 再登記賭桌：回應失敗（逾時）就不會留下沒有畫面的桌子
        await defer(interaction, private_errors=True)
        if await self._already_open(interaction, channel):     # 等回應時別人先開了
            return
        table = Table(channel, interaction.guild_id)
        self.tables[channel.id] = table
        try:
            await self.start_round(table, interaction)
        except Exception:
            # 畫面沒出來：移除賭桌，這個頻道才能再開
            log.exception("骰寶賭桌送不出去，取消開桌")
            await self.shutdown(table, "⚠️ 賭桌開桌失敗，請再開一次。")
            return await send_error(interaction, "賭桌畫面送不出去，請再開一次。")
        table.task = asyncio.create_task(self._run(table))

    async def _already_open(self, interaction: discord.Interaction, channel) -> bool:
        """這個頻道已經有桌子就告訴本人該怎麼做，並回傳 True。"""
        table = self.tables.get(channel.id)
        if table is None:
            return False
        if table.phase == "betting":
            link = f"（[前往賭桌]({table.message.jump_url})）" if getattr(table.message, "jump_url", None) else ""
            text = f"這個頻道的骰寶正在開放下注，直接按賭桌上的按鈕押注{link}。"
        else:
            text = self.wait_message(table)
        await send_error(interaction, text)
        return True

    async def start_round(self, table: Table, interaction: Optional[discord.Interaction] = None) -> None:
        async with table.lock:
            table.number += 1
            table.round = SicBoRound(table.number)
            table.phase = "betting"
            table.closes_at = time.time() + BET_SECONDS
            table.view = TableView(self, table)
            embed = self.betting_embed(table)
        if interaction is not None:
            table.message = await respond(interaction, embed=embed, view=table.view)
        else:
            table.message = await table.channel.send(embed=embed, view=table.view)

    async def _run(self, table: Table) -> None:
        try:
            while True:
                # 倒數在另一個工作更新：就算改訊息被 Discord 限速，也不會拖到停止下注的時間
                table.ticker = asyncio.create_task(self.tick(table, "betting", table.closes_at))
                await asyncio.sleep(max(0.0, table.closes_at - time.time()))
                if not await self.close_round(table):
                    return
                await asyncio.sleep(ROLL_SECONDS)
                await self.reveal(table)
                table.ticker = asyncio.create_task(self.tick(table, "break", table.next_at))
                await asyncio.sleep(max(0.0, table.next_at - time.time()))
                if not enabled(self.bot, "sicbo", table.guild_id):
                    return await self.shutdown(table, "🛑 骰寶功能已經關閉，賭桌收起來了。")
                await self.start_round(table)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("骰寶賭桌發生錯誤")
            await self.shutdown(table, "⚠️ 賭桌發生錯誤，這局的押注已全數退還。")

    async def close_round(self, table: Table) -> bool:
        """停止下注。沒人下注就收桌並回傳 False。"""
        async with table.lock:
            table.phase = "rolling"
            table.next_at = time.time() + ROLL_SECONDS + BREAK_SECONDS
            if table.view:
                table.view.stop()
            empty = not table.round.bets
        if empty:
            await self.shutdown(table, "沒人下注，賭桌收起來了。想玩再用 /sicbo 開一桌！")
            return False
        await self._edit(table, embed=self.rolling_embed(table), view=RulesView(timeout=RULES_TIMEOUT))
        return True

    async def reveal(self, table: Table, dice=None) -> None:
        dice = dice or roll()
        async with table.lock:
            if table.phase == "closed":      # 管理員剛好從後台收桌，押注已經退還
                return
            rnd = table.round
            results = rnd.settle(dice)
            balances = {}
            for user_id, (stake, paid, _) in results.items():
                balances[user_id] = await self.bot.db.settle(rnd.holds.pop(user_id), paid, "骰寶",
                                                             game="sicbo", won=paid > stake)
                if stake >= MIN_QUEST_BET:
                    self.bot.dispatch("activity", user_id, "game", table.channel_id,
                                      {"game": "sicbo", "won": paid > stake})
            table.history.append(tuple(dice))
            table.phase = "break"
            table.next_at = time.time() + BREAK_SECONDS
        table.result = self.result_embed(table, dice, results, balances)
        await self._edit(table, embed=table.result, view=RulesView(timeout=RULES_TIMEOUT))

    async def shutdown(self, table: Table, text: str) -> None:
        """收桌：退還這局還沒結算的押注。"""
        async with table.lock:
            if table.phase == "closed":
                return
            table.phase = "closed"
            self.tables.pop(table.channel_id, None)
            if table.view:
                table.view.stop()
            rnd = table.round
            if rnd:
                for user_id, hold_id in list(rnd.holds.items()):
                    await self.bot.db.settle(hold_id, rnd.stake(user_id), "骰寶：收桌退還押注")
                rnd.holds.clear()
        if table.task and table.task is not asyncio.current_task():
            table.task.cancel()
        embed = make_embed(f"🎲 骰寶・第 {table.number} 局", text, Color.MUTED)
        embed.add_field(name="路單（舊 → 新）", value=table.road(), inline=False)
        await self._edit(table, embed=embed, view=None)

    async def _edit(self, table: Table, **kwargs) -> None:
        async with table.edit_lock:
            await self._edit_now(table, **kwargs)

    async def _edit_now(self, table: Table, **kwargs) -> None:
        if table.message is None:
            return
        try:
            await table.message.edit(**kwargs)
        except discord.HTTPException:
            log.warning("骰寶賭桌訊息更新失敗", exc_info=True)

    async def refresh(self, table: Table, phase: str, number: int) -> bool:
        """重畫倒數與押注。已經換階段或換局就不畫並回傳 False。"""
        async with table.edit_lock:
            if table.phase != phase or table.number != number:
                return False
            if phase == "betting":
                embed = self.betting_embed(table)
            else:
                embed = table.result
                embed.set_field_at(-1, name="下一局", value=self.next_text(table), inline=False)
            await self._edit_now(table, embed=embed)
            return True

    async def tick(self, table: Table, phase: str, until: float) -> None:
        """倒數到 until 為止，定時更新畫面上的秒數。"""
        number = table.number
        try:
            while True:
                remaining = until - time.time()
                target = next_tick(remaining)
                if target <= 0:
                    return
                await asyncio.sleep(remaining - target)
                if not await self.refresh(table, phase, number):
                    return
        except Exception:
            log.exception("骰寶倒數更新失敗")       # 只是畫面，不影響牌局進行

    def schedule_refresh(self, table: Table) -> None:
        if table.refresh_task and not table.refresh_task.done():
            return

        number = table.number

        async def later():
            await asyncio.sleep(REFRESH_SECONDS)
            await self.refresh(table, "betting", number)
        table.refresh_task = asyncio.create_task(later())

    # ------------------------------------------------------------------ 押注
    async def place_bet(self, user, table: Table, key: str) -> Tuple[bool, str]:
        """押一注（金額＝這個人的籌碼面額）。回傳 (成功, 給本人看的訊息)。"""
        amount = self.chip(user.id)
        async with table.lock:
            if table.phase != "betting" or table.round is None:
                return False, self.wait_message(table)
            rnd = table.round
            error = rnd.check(user.id, key, amount)
            if error:
                return False, error
            hold_id = rnd.holds.get(user.id)
            if hold_id is None:
                hold_id = await self.bot.db.hold(user.id, amount, "骰寶")
                ok = hold_id is not None
            else:
                ok = await self.bot.db.extend_hold(hold_id, amount)
            if not ok:
                balance = await self.bot.db.get_points(user.id)
                return False, f"存款不足，你目前有 {fmt_points(balance)}，籌碼是 {amount:,} 元（可以按「💰 換籌碼」改小）。"
            rnd.holds[user.id] = hold_id
            rnd.add(user.id, key, amount, discord.utils.escape_markdown(getattr(user, "display_name", str(user.id))))
            mine = "、".join(fmt_spot_amount(k, a) for k, a in rnd.bets[user.id].items())
            stake = rnd.stake(user.id)
        balance = await self.bot.db.get_points(user.id)
        self.schedule_refresh(table)
        return True, (f"✅ 已押 **{SPOTS[key].label} {amount:,}** 元（{SPOTS[key].odds}）\n"
                      f"你這局共押 {stake:,}／{MAX_PER_ROUND:,} 元：{mine}\n"
                      f"目前籌碼 {amount:,} 元・餘額 {balance:,} 元")

    async def withdraw(self, user_id: int, table: Table) -> Tuple[bool, str]:
        async with table.lock:
            if table.phase != "betting" or table.round is None:
                return False, "已經停止下注，押注不能撤回了。"
            rnd = table.round
            hold_id = rnd.holds.pop(user_id, None)
            if hold_id is None:
                return False, "你這局還沒有下注。"
            amount = rnd.clear(user_id)
            balance = await self.bot.db.settle(hold_id, amount, "骰寶：撤回押注")
        self.schedule_refresh(table)
        return True, f"↩️ 已撤回這局的押注，退還 {amount:,} 元，餘額 {balance:,} 元。"

    async def bet_from(self, interaction: discord.Interaction, table: Table, key: str) -> None:
        await defer(interaction, ephemeral=True)      # 先回應再扣錢
        ok, text = await self.place_bet(interaction.user, table, key)
        if ok:
            await respond(interaction, content=text, ephemeral=True)
        else:
            await send_error(interaction, text)

    # ------------------------------------------------------------------ 入口
    async def launch_from_hub(self, interaction: discord.Interaction) -> None:
        await self.open_table(interaction)

    @app_commands.command(name="sicbo", description="骰寶（押大小）：押大小、點數、豹子，大家同桌一起開獎")
    @app_commands.checks.cooldown(1, 3.0)
    async def sicbo(self, interaction: discord.Interaction):
        await self.open_table(interaction)


# ====================================================================== 介面
class TableView(discord.ui.View):
    """賭桌訊息上的按鈕，大家共用。停止下注時就收掉。"""

    def __init__(self, cog: SicBo, table: Table):
        super().__init__(timeout=None)
        self.cog, self.table = cog, table

    async def _bet(self, interaction: discord.Interaction, key: str) -> None:
        await self.cog.bet_from(interaction, self.table, key)

    @discord.ui.button(label="大 11~17", style=discord.ButtonStyle.danger, row=0)
    async def big(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._bet(interaction, "big")

    @discord.ui.button(label="小 4~10", style=discord.ButtonStyle.primary, row=0)
    async def small(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._bet(interaction, "small")

    @discord.ui.button(label="單", style=discord.ButtonStyle.secondary, row=0)
    async def odd(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._bet(interaction, "odd")

    @discord.ui.button(label="雙", style=discord.ButtonStyle.secondary, row=0)
    async def even(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._bet(interaction, "even")

    @discord.ui.button(label="全圍 30倍", emoji="🐆", style=discord.ButtonStyle.success, row=0)
    async def any_triple(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._bet(interaction, "any_triple")

    @discord.ui.button(label=f"換籌碼（預設 {DEFAULT_CHIP:,}）", emoji="💰", style=discord.ButtonStyle.secondary, row=1)
    async def chip(self, interaction: discord.Interaction, button: discord.ui.Button):
        current = self.cog.chip(interaction.user.id)
        await interaction.response.send_message(f"💰 你目前的籌碼是 **{current:,} 元**，選一個新的面額：",
                                                view=ChipView(self.cog, interaction.user.id), ephemeral=True)

    @discord.ui.button(label="更多押法", emoji="🎯", style=discord.ButtonStyle.secondary, row=1)
    async def more(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=MoreBetsView.embed(self.cog, interaction.user.id),
                                                view=MoreBetsView(self.cog, self.table), ephemeral=True)

    @discord.ui.button(label="撤回我的押注", emoji="↩️", style=discord.ButtonStyle.secondary, row=1)
    async def withdraw(self, interaction: discord.Interaction, button: discord.ui.Button):
        await defer(interaction, ephemeral=True)
        ok, text = await self.cog.withdraw(interaction.user.id, self.table)
        if ok:
            await respond(interaction, content=text, ephemeral=True)
        else:
            await send_error(interaction, text)

    @discord.ui.button(label="押法說明", emoji="📋", style=discord.ButtonStyle.secondary, row=1)
    async def rules(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=rules_embed(), ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        if log_interaction_error(interaction, error, "骰寶按鈕"):
            await send_error(interaction, "操作時發生錯誤，請稍後再試。")


class RulesView(discord.ui.View):
    """停止下注後（搖骰中、開獎後）只留「押法說明」。"""

    def __init__(self, timeout: Optional[float] = None):
        super().__init__(timeout=timeout)

    @discord.ui.button(label="押法說明", emoji="📋", style=discord.ButtonStyle.secondary, custom_id="sicbo:rules")
    async def rules(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=rules_embed(), ephemeral=True)


class ChipView(discord.ui.View):
    def __init__(self, cog: SicBo, user_id: int):
        super().__init__(timeout=120)
        self.cog, self.user_id = cog, user_id
        for value in CHIPS:
            button = discord.ui.Button(label=f"{value:,}", style=discord.ButtonStyle.primary
                                       if value == cog.chip(user_id) else discord.ButtonStyle.secondary)
            button.callback = self._picker(value)
            self.add_item(button)
        custom = discord.ui.Button(label="自訂金額", emoji="✏️", style=discord.ButtonStyle.secondary, row=1)
        custom.callback = self._custom
        self.add_item(custom)

    def _picker(self, value: int):
        async def callback(interaction: discord.Interaction):
            await self.set_chip(interaction, value)
        return callback

    async def set_chip(self, interaction: discord.Interaction, value: int) -> None:
        self.cog.chips[self.user_id] = value
        await interaction.response.edit_message(content=f"✅ 籌碼改成 **{value:,} 元**，之後每按一次押注就押這個金額。",
                                                view=None)

    async def _custom(self, interaction: discord.Interaction):
        await interaction.response.send_modal(AmountModal("💰 自訂籌碼", "每注金額", self.set_chip,
                                                          default=self.cog.chip(self.user_id),
                                                          min_value=MIN_CHIP, max_value=MAX_PER_ROUND))


def _options(keys: List[str]) -> List[discord.SelectOption]:
    return [discord.SelectOption(label=f"{SPOTS[k].label}（{SPOTS[k].odds}）", value=k) for k in keys]


class MoreBetsView(discord.ui.View):
    """點數、總和、對子／圍骰、兩骰組合。每押一注就換一個新的面板，選單才能重複選同一個。"""

    MENUS = [
        ("單骰：押 1～6，開出幾顆賠幾倍", [f"single:{n}" for n in range(1, 7)]),
        ("總和：押 3 顆加起來的點數", [f"total:{t}" for t in range(4, 18)]),
        ("對子／圍骰：押兩顆或三顆一樣", [f"double:{n}" for n in range(1, 7)] + [f"triple:{n}" for n in range(1, 7)]),
        ("兩骰組合：兩個點數都出現", [k for k in SPOTS if k.startswith("combo:")]),
    ]

    def __init__(self, cog: SicBo, table: Table):
        super().__init__(timeout=BET_SECONDS + 60)
        self.cog, self.table = cog, table
        for i, (placeholder, keys) in enumerate(self.MENUS):
            select = discord.ui.Select(placeholder=placeholder, options=_options(keys), row=i)
            select.callback = self._picked(select)
            self.add_item(select)

    @staticmethod
    def embed(cog: SicBo, user_id: int, note: str = "") -> discord.Embed:
        text = (f"選一個押法，就用你的籌碼 **{cog.chip(user_id):,} 元** 押一注。\n"
                "詳細規則按賭桌上的「📋 押法說明」。")
        return make_embed("🎯 更多押法", f"{note}\n\n{text}" if note else text, Color.TABLE)

    def _picked(self, select: discord.ui.Select):
        async def callback(interaction: discord.Interaction):
            key = select.values[0]
            await interaction.response.defer()            # 先回應再扣錢
            ok, text = await self.cog.place_bet(interaction.user, self.table, key)
            note = text if ok else f"❌ {text}"
            fresh = MoreBetsView(self.cog, self.table) if ok or self.table.phase == "betting" else None
            await interaction.edit_original_response(embed=self.embed(self.cog, interaction.user.id, note), view=fresh)
        return callback


async def setup(bot):
    await bot.add_cog(SicBo(bot))
