"""德州撲克：大廳 → 牌桌。整局只用一則牌桌訊息即時更新，不洗版。

每位玩家可以另外開一個只有自己看得到的「手牌面板」，裡面有手牌、目前牌型與下注按鈕，
牌局每次變化都會自動更新，不用在牌桌與私人訊息之間來回切換。
"""
import asyncio
import logging
import random
import time
from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands

from core.timeutil import ts
from core.ui import BaseView, Color, fmt_points, fmt_signed, make_embed, respond, send_error

from .ai import PERSONALITIES, Personality, decide
from .engine import STREET_NAMES, IllegalAction, PokerTable, Seat
from .evaluate import evaluate_hand

log = logging.getLogger(__name__)

TURN_SECONDS = 60
NEXT_HAND_DELAY = 10
MAX_SEATS = 8
LOBBY_TIMEOUT = 15 * 60
MIN_BUY_IN_BB = 10
DEFAULT_BUY_IN_BB = 50
# 私人面板靠互動權杖更新，權杖 15 分鐘後失效；提早一點收掉，提示玩家重新開啟
PANEL_LIFETIME = 13 * 60

SUIT_EMOJI = {"♠": "♠️", "♥": "♥️", "♦": "♦️", "♣": "♣️"}   # 表情版的紅心、方塊是紅色，比較好認
HIDDEN_CARD = "▫️"


def card_text(card) -> str:
    return f"**{card.rank}**{SUIT_EMOJI.get(card.suit, card.suit)}"


def cards_text(cards) -> str:
    return "  ".join(card_text(c) for c in cards)


def board_text(board) -> str:
    return "  ".join([card_text(c) for c in board] + [HIDDEN_CARD] * (5 - len(board)))


def raise_options(table: PokerTable, seat: Seat) -> List[Tuple[str, int]]:
    """常用的加注金額（加注到的總下注），不含 All-in（另有按鈕）。"""
    legal = table.legal(seat)
    if not legal.can_raise or legal.min_raise_to >= legal.max_raise_to:
        return []
    pot_after_call = table.pot + legal.to_call
    candidates = [("最小加注", legal.min_raise_to)]
    if table.street == "preflop" and table.current_bet <= table.big_blind:
        candidates += [(f"{n} 倍大盲", table.big_blind * n) for n in (3, 4)]
    candidates += [(label, table.current_bet + pot_after_call * num // den)
                   for label, num, den in (("½ 底池", 1, 2), ("¾ 底池", 3, 4), ("1 倍底池", 1, 1))]
    options, seen = [], set()
    for label, amount in candidates:
        if legal.min_raise_to <= amount < legal.max_raise_to and amount not in seen:
            seen.add(amount)
            options.append((label, amount))
    return sorted(options, key=lambda o: o[1])


def is_moderator(user: discord.abc.User) -> bool:
    return isinstance(user, discord.Member) and user.guild_permissions.manage_messages


class Lobby:
    def __init__(self, host: discord.Member, channel_id: int, big_blind: int, buy_in: int):
        self.host = host
        self.channel_id = channel_id
        self.big_blind, self.small_blind = big_blind, max(1, big_blind // 2)
        self.buy_in = buy_in
        self.players: Dict[int, discord.Member] = {}
        self.holds: Dict[int, int] = {}
        self.ai: Dict[int, Personality] = {}   # 電腦玩家用負數當 ID
        self.message = None
        self.closed = False

    @property
    def seat_count(self) -> int:
        return len(self.players) + len(self.ai)

    def add_ai(self) -> Personality:
        used = {p.name for p in self.ai.values()}
        fresh = [p for p in PERSONALITIES if p.name not in used]
        if fresh:
            personality = fresh[0]
        else:   # 四種個性都用過了，加上編號區分
            base = PERSONALITIES[len(self.ai) % len(PERSONALITIES)]
            personality = replace(base, name=f"{base.name} {len(self.ai) // len(PERSONALITIES) + 1}號")
        self.ai[-(len(self.ai) + 1)] = personality
        return personality

    def remove_ai(self) -> bool:
        if not self.ai:
            return False
        del self.ai[min(self.ai)]   # 移除最後加入的（ID 最小）
        return True


@dataclass
class Panel:
    """玩家的私人手牌面板（私密訊息），用開啟它的互動權杖編輯。"""
    interaction: discord.Interaction
    opened: float
    view: Optional[discord.ui.View] = None


class PokerGame:
    def __init__(self, lobby: Lobby):
        self.channel_id = lobby.channel_id
        self.host_id = lobby.host.id
        self.buy_in = lobby.buy_in
        self.holds = dict(lobby.holds)
        self.bought_in = {uid: lobby.buy_in for uid in lobby.players}
        self.ai = dict(lobby.ai)
        seats = [Seat(uid, member.display_name, lobby.buy_in) for uid, member in lobby.players.items()]
        # 電腦的籌碼由莊家提供，牌局結束時不會結算成任何人的積分
        seats += [Seat(uid, p.name, lobby.buy_in) for uid, p in lobby.ai.items()]
        self.table = PokerTable(seats, lobby.small_blind, lobby.big_blind)
        self.table.dealer_id = random.choice(seats).user_id  # 第一手的莊家隨機
        self.message = None
        self.view: Optional[discord.ui.View] = None
        self.lock = asyncio.Lock()
        self.deadline: Optional[float] = None
        self.turn_task: Optional[asyncio.Task] = None
        self.next_hand_task: Optional[asyncio.Task] = None
        self.turn_id = 0   # 每次狀態改變 +1，讓過期的計時器失效
        self.next_hand_at: Optional[float] = None
        self.last_summary: Optional[str] = None   # 上一手的結果，下一手時顯示一行
        self.panels: Dict[int, Panel] = {}
        self.ended = False
        self.rng = random.Random()

    def who(self, user_id: int) -> str:
        """真人顯示成提及，電腦顯示名稱（電腦沒有 Discord 帳號可以提及）。"""
        if user_id > 0:
            return f"<@{user_id}>"
        seat = self.table.seat_of(user_id)
        return f"**{seat.name if seat else '電腦'}**"


class Poker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.lobbies: Dict[int, Lobby] = {}
        self.games: Dict[int, PokerGame] = {}

    async def cog_unload(self):
        for game in list(self.games.values()):
            await self.end_game(game, "bot 重新載入，牌局已結算。")
        for lobby in list(self.lobbies.values()):
            await self.close_lobby(lobby)

    # ================================================================== 大廳
    def lobby_embed(self, lobby: Lobby) -> discord.Embed:
        embed = make_embed("♠️ 德州撲克大廳", color=Color.TABLE)
        embed.add_field(name="盲注", value=f"{lobby.small_blind}/{lobby.big_blind}")
        embed.add_field(name="帶入籌碼", value=fmt_points(lobby.buy_in))
        embed.add_field(name="人數", value=f"{lobby.seat_count}/{MAX_SEATS}")
        lines = [f"{'👑' if uid == lobby.host.id else '・'} {m.mention}" for uid, m in lobby.players.items()]
        lines += [f"・{p.name}（{p.style}）" for p in lobby.ai.values()]
        embed.add_field(name="玩家", value="\n".join(lines), inline=False)
        footer = "加入時會先扣下帶入籌碼，離開或牌局結束時依剩餘籌碼退回"
        if lobby.ai:
            footer += "・電腦的籌碼由莊家提供，從電腦贏來的就是你的"
        embed.set_footer(text=footer)
        return embed

    async def create_lobby(self, interaction: discord.Interaction, big_blind: int, buy_in: Optional[int],
                           bots: int = 0) -> None:
        if not interaction.guild:
            return await send_error(interaction, "德州撲克要在伺服器頻道裡玩。")
        if interaction.channel_id in self.lobbies or interaction.channel_id in self.games:
            return await send_error(interaction, "這個頻道已經有牌局或大廳了。")
        buy_in = buy_in or big_blind * DEFAULT_BUY_IN_BB
        if buy_in < big_blind * MIN_BUY_IN_BB:
            return await send_error(interaction, f"帶入籌碼至少要是大盲注的 {MIN_BUY_IN_BB} 倍（{big_blind * MIN_BUY_IN_BB:,}）。")

        hold_id = await self.bot.db.hold(interaction.user.id, buy_in, "德州撲克")
        if hold_id is None:
            balance = await self.bot.db.get_points(interaction.user.id)
            return await send_error(interaction, f"帶入籌碼需要 {fmt_points(buy_in)}，你目前有 {fmt_points(balance)}。")

        lobby = Lobby(interaction.user, interaction.channel_id, big_blind, buy_in)
        lobby.players[interaction.user.id] = interaction.user
        lobby.holds[interaction.user.id] = hold_id
        for _ in range(min(bots, MAX_SEATS - 1)):
            lobby.add_ai()
        self.lobbies[lobby.channel_id] = lobby
        view = LobbyView(self, lobby)
        lobby.message = await respond(interaction, embed=self.lobby_embed(lobby), view=view)
        view.message = lobby.message

    async def close_lobby(self, lobby: Lobby, note: Optional[str] = None) -> None:
        if lobby.closed:
            return
        lobby.closed = True
        self.lobbies.pop(lobby.channel_id, None)
        for uid, hold_id in lobby.holds.items():
            await self.bot.db.settle(hold_id, lobby.buy_in, "德州撲克大廳取消，退回帶入籌碼")
        if note and lobby.message:
            try:
                await lobby.message.edit(embed=make_embed("♠️ 德州撲克大廳", note, Color.MUTED), view=None)
            except discord.HTTPException:
                pass

    # ================================================================== 牌桌畫面
    @staticmethod
    def position_tags(t: PokerTable, seat: Seat) -> str:
        if not seat.in_hand:
            return ""
        tags = []
        if seat.user_id == t.dealer_id:
            tags.append("莊")
        if seat.user_id == t.sb_id:
            tags.append("小盲")
        elif seat.user_id == t.bb_id:
            tags.append("大盲")
        return "/".join(tags)

    def result_lines(self, game: PokerGame, mention: bool = True) -> List[str]:
        t = game.table
        if not t.result or not t.result.pots:
            return []
        pots = t.result.pots
        lines = []
        for i, pot in enumerate(pots):
            label = "底池" if len(pots) == 1 else ("主池" if i == 0 else f"邊池 {i}")
            names = [game.who(uid) if mention else (t.seat_of(uid).name if t.seat_of(uid) else "?")
                     for uid in pot.winners]
            hand = f"（{pot.hand_name}）" if pot.hand_name else ""
            lines.append(f"{label} **{pot.amount:,}** → {'、'.join(names)}{hand}")
        return lines

    def table_embed(self, game: PokerGame) -> discord.Embed:
        t = game.table
        stage = STREET_NAMES.get(t.street, "")
        current = t.current_seat
        embed = make_embed(f"♠️ 德州撲克・第 {t.hand_number} 手・{stage}", color=Color.TABLE)
        info = f"💰 **底池 {t.pot:,}**　・　盲注 {t.small_blind}/{t.big_blind}"
        if current and t.current_bet:
            info += f"　・　目前下注 {t.current_bet:,}"
        embed.description = f"## {board_text(t.board)}\n{info}"

        showdown = t.result.showdown if t.result else False
        lines = []
        for seat in t.seats:
            win = t.result.payouts.get(seat.user_id) if t.result else None
            if current is seat:
                marker = "▶️"
            elif win:
                marker = "🏆"
            elif not seat.in_hand or seat.folded:
                marker = "💤"
            else:
                marker = "🟢"
            name = f"~~{seat.name}~~" if seat.in_hand and seat.folded else seat.name
            tags = self.position_tags(t, seat)
            head = f"{marker} **{name}**" + (f" `{tags}`" if tags else "") + f"　💵 {seat.stack:,}"
            status = []
            if not seat.in_hand:
                if seat.stack > 0 and not seat.leaving:
                    status.append("下一手加入")
            elif seat.folded:
                status.append("棄牌")
            else:
                if seat.last_action:
                    status.append(seat.last_action)
                elif seat.bet:
                    status.append(f"下注 {seat.bet:,}")
                if seat.all_in and "All-in" not in seat.last_action:
                    status.append("**All-in**")
                if showdown and seat.user_id in t.result.hands:
                    status.append(f"{cards_text(seat.hole)}（{t.result.hands[seat.user_id][0]}）")
            if seat.leaving:
                status.append("離桌中")
            if win:
                status.append(f"**+{win:,}**")
            lines.append(head + ("\n　　" + "・".join(status) if status else ""))
        # 欄位上限 1024 字，人多又攤牌時拆成兩欄
        chunks, chunk = [], []
        for line in lines:
            if chunk and len("\n".join(chunk + [line])) > 1024:
                chunks.append(chunk)
                chunk = []
            chunk.append(line)
        chunks.append(chunk)
        for i, chunk in enumerate(chunks):
            embed.add_field(name=f"玩家（{len(t.seats)}/{MAX_SEATS}）" if i == 0 else "\u200b",
                            value="\n".join(chunk), inline=False)

        if t.hand_over and not game.ended:
            result = self.result_lines(game)
            if result:
                embed.add_field(name="🏆 結果", value="\n".join(result), inline=False)
            embed.color = Color.GOLD
            if game.next_hand_at:
                embed.add_field(name="⏭️ 下一手", value=f"{ts(game.next_hand_at, 'R')}開始", inline=False)
        else:
            if t.log:
                embed.add_field(name="動態", value="\n".join(f"・{line}" for line in t.log[-3:]), inline=False)
            if current and current.user_id in game.ai:
                embed.add_field(name="⏳ 等待行動", value=f"🤖 {current.name} 思考中…", inline=False)
            elif current and game.deadline:
                legal = t.legal(current)
                need = "可以過牌" if legal.can_check else f"跟注需要 **{legal.call_amount:,}**"
                auto = "過牌" if legal.can_check else "棄牌"
                embed.add_field(name="⏳ 等待行動",
                                value=f"輪到 {game.who(current.user_id)}・{need}\n{ts(game.deadline, 'R')}自動{auto}",
                                inline=False)
            if game.last_summary:
                embed.add_field(name="上一手", value=game.last_summary, inline=False)
        if not game.ended:
            embed.set_footer(text="按「🃏 我的手牌」開啟私人面板：看手牌與牌型、直接下注，會自動更新")
        return embed

    def panel_embed(self, game: PokerGame, seat: Seat) -> discord.Embed:
        """只有該玩家看得到的手牌面板。"""
        t = game.table
        current = t.current_seat
        embed = make_embed(f"🃏 你的手牌・第 {t.hand_number} 手", color=Color.TABLE)
        lines = []
        if seat.in_hand and seat.hole:
            lines.append(f"# {cards_text(seat.hole)}")
        else:
            lines.append("你這一手沒有參與，下一手開始就會發牌給你。")
        lines.append(f"牌面：{board_text(t.board)}")
        lines.append(f"💰 底池 **{t.pot:,}**　・　你的籌碼 **{seat.stack:,}**"
                     + (f"　・　本輪已下 {seat.bet:,}" if seat.bet else ""))
        embed.description = "\n".join(lines)
        if seat.in_hand and seat.hole and len(t.board) >= 3:
            _, _, hand_name, best = evaluate_hand(seat.hole, t.board)
            embed.add_field(name="目前最佳牌型", value=f"**{hand_name}**　{cards_text(best)}", inline=False)

        if game.ended:
            status, embed.color = "牌局已結束。", Color.MUTED
        elif t.hand_over:
            win = t.result.payouts.get(seat.user_id, 0) if t.result else 0
            if win:
                status, embed.color = f"🏆 你贏得 **{win:,}**！", Color.GOLD
            else:
                status = "這手結束了。"
            if game.next_hand_at:
                status += f"\n下一手 {ts(game.next_hand_at, 'R')}開始"
        elif not seat.in_hand:
            status = "等待下一手…"
        elif seat.folded:
            status, embed.color = "你已經棄牌，等待下一手。", Color.MUTED
        elif current is seat:
            legal = t.legal(seat)
            need = "可以過牌" if legal.can_check else f"跟注需要 **{legal.call_amount:,}**"
            status = f"👉 **輪到你了！** {need}"
            if game.deadline:
                status += f"\n{ts(game.deadline, 'R')}自動{'過牌' if legal.can_check else '棄牌'}"
            embed.color = Color.WARNING
        elif seat.all_in:
            status = "你已 All-in，等待發牌…"
        elif current:
            status = f"等待 {current.name} 行動…"
        else:
            status = "發牌中…"
        embed.add_field(name="狀態", value=status, inline=False)
        return embed

    def build_view(self, game: PokerGame) -> discord.ui.View:
        if game.view:
            game.view.stop()
        view = TableView(self, game)
        game.view = view
        return view

    def turn_content(self, game: PokerGame) -> str:
        """訊息內文提及目前要行動的真人，讓他在頻道裡看到高亮。"""
        seat = game.table.current_seat
        if game.ended or not seat or seat.user_id in game.ai:
            return ""
        return f"🔔 輪到 <@{seat.user_id}> 行動"

    async def refresh(self, game: PokerGame) -> None:
        embed = self.table_embed(game)
        view = self.build_view(game) if not game.ended else None
        content = self.turn_content(game)
        try:
            await game.message.edit(content=content, embed=embed, view=view)
        except discord.NotFound:
            # 牌桌訊息被刪掉了，重新發一則
            channel = self.bot.get_channel(game.channel_id)
            if channel:
                message = await channel.send(content=content, embed=embed, view=view,
                                             allowed_mentions=discord.AllowedMentions.none())
                game.message = channel.get_partial_message(message.id)
        except discord.HTTPException:
            log.warning("更新牌桌失敗", exc_info=True)
        await self.refresh_panels(game)

    async def refresh_panels(self, game: PokerGame) -> None:
        if game.panels:
            await asyncio.gather(*(self._refresh_panel(game, uid, panel) for uid, panel in list(game.panels.items())),
                                 return_exceptions=True)

    async def _refresh_panel(self, game: PokerGame, user_id: int, panel: Panel) -> None:
        seat = game.table.seat_of(user_id)
        expired = time.time() - panel.opened > PANEL_LIFETIME
        if panel.view:
            panel.view.stop()
        if game.ended or not seat or expired:
            game.panels.pop(user_id, None)
            if game.ended and seat:
                embed = self.panel_embed(game, seat)
            elif expired:
                embed = make_embed("🃏 手牌面板已過期", "Discord 限制私人訊息只能更新 15 分鐘，"
                                   "請在牌桌上再按一次「🃏 我的手牌」。", Color.MUTED)
            else:
                embed = make_embed("🃏 你已離開牌桌", color=Color.MUTED)
            view = None
        else:
            embed = self.panel_embed(game, seat)
            view = panel.view = HandView(self, game, user_id)
        try:
            await panel.interaction.edit_original_response(embed=embed, view=view)
        except discord.HTTPException:
            if game.panels.get(user_id) is panel:
                game.panels.pop(user_id, None)

    def track_panel(self, game: PokerGame, interaction: discord.Interaction) -> None:
        """從面板上操作時換成新的互動權杖，面板就能再更新 15 分鐘。"""
        old = game.panels.get(interaction.user.id)
        if old and old.view:
            old.view.stop()
        game.panels[interaction.user.id] = Panel(interaction, time.time())

    # ================================================================== 牌局流程
    async def start_game(self, lobby: Lobby, interaction: discord.Interaction) -> None:
        lobby.closed = True
        self.lobbies.pop(lobby.channel_id, None)
        game = PokerGame(lobby)
        self.games[game.channel_id] = game
        players = "、".join([m.mention for m in lobby.players.values()] + [p.name for p in lobby.ai.values()])
        await interaction.response.edit_message(
            embed=make_embed("♠️ 牌局開始！", f"{players}\n牌桌在下方 👇", Color.TABLE), view=None)
        game.message = await respond(interaction, embed=make_embed("♠️ 德州撲克", "洗牌中…", Color.TABLE))
        async with game.lock:
            await self.start_next_hand(game)

    async def start_next_hand(self, game: PokerGame) -> None:
        """呼叫前需持有 game.lock。"""
        if game.ended:
            return
        t = game.table
        # 籌碼歸零或要離桌的人先結算
        for seat in list(t.seats):
            if seat.stack == 0 or seat.leaving:
                await self._cash_out(game, seat)
                t.seats.remove(seat)
        humans = [s for s in t.seats if s.user_id > 0]
        if not humans:
            return await self.end_game(game, "真人玩家都離開了，牌局結束。")
        if game.host_id not in [s.user_id for s in humans]:
            game.host_id = humans[0].user_id
        if len([s for s in t.seats if s.stack > 0]) < 2:
            return await self.end_game(game, "剩不到兩位玩家，牌局結束。")
        t.start_hand()
        await self._after_change(game)

    async def _after_change(self, game: PokerGame) -> None:
        """每次牌局狀態改變後：處理離桌玩家、計時、刷新畫面。呼叫前需持有 game.lock。"""
        t = game.table
        while t.current_seat and t.current_seat.leaving:
            t.act(t.current_seat.user_id, "fold")
        self._cancel(game.turn_task)
        game.turn_id += 1
        if t.hand_over:
            game.deadline = None
            game.next_hand_at = time.time() + NEXT_HAND_DELAY
            # 結果直接顯示在牌桌上，不另外發訊息洗版；下一手再保留一行摘要
            game.last_summary = "；".join(self.result_lines(game, mention=False)) or None
            await self.refresh(game)
            self._report_activity(game)
            game.next_hand_task = asyncio.create_task(self._next_hand_later(game, t.hand_number))
        elif t.current_seat.user_id in game.ai:
            game.deadline = None
            await self.refresh(game)
            game.turn_task = asyncio.create_task(self._ai_turn(game, game.turn_id))
        else:
            game.deadline = time.time() + TURN_SECONDS
            await self.refresh(game)
            game.turn_task = asyncio.create_task(self._turn_timer(game, game.turn_id))

    def _report_activity(self, game: PokerGame) -> None:
        """每手結束時通知任務系統（只算真人、有拿到牌的玩家）。"""
        t = game.table
        if not t.result or not t.result.pots:
            return
        for seat in t.order:
            if seat.user_id > 0:
                won = t.result.payouts.get(seat.user_id, 0) > seat.contributed
                self.bot.dispatch("activity", seat.user_id, "game", game.channel_id, {"game": "poker", "won": won})

    async def _ai_turn(self, game: PokerGame, turn_id: int) -> None:
        await asyncio.sleep(game.rng.uniform(1.2, 2.5))   # 假裝思考，不然電腦動作快到看不清楚
        async with game.lock:
            t = game.table
            seat = t.current_seat
            if game.ended or game.turn_id != turn_id or not seat or seat.user_id not in game.ai:
                return
            personality = game.ai[seat.user_id]
            # 模擬計算比較花時間，丟到背景執行緒以免卡住 bot
            action, amount = await asyncio.to_thread(decide, t, seat, personality, game.rng)
            try:
                t.act(seat.user_id, action, amount)
            except IllegalAction:
                log.warning("電腦做出不合法的動作 %s %s，改為過牌或棄牌", action, amount)
                t.act(seat.user_id, "check" if t.legal(seat).can_check else "fold")
            await self._after_change(game)

    async def act(self, interaction: discord.Interaction, game: PokerGame, action: str,
                  amount: Optional[int] = None, *, from_panel: bool = False) -> None:
        async with game.lock:
            if game.ended:
                return await send_error(interaction, "牌局已經結束了。")
            try:
                game.table.act(interaction.user.id, action, amount)
            except IllegalAction as e:
                return await send_error(interaction, str(e))
            await interaction.response.defer()
            if from_panel:
                self.track_panel(game, interaction)
            await self._after_change(game)

    async def _turn_timer(self, game: PokerGame, turn_id: int) -> None:
        await asyncio.sleep(TURN_SECONDS)
        async with game.lock:
            t = game.table
            seat = t.current_seat
            # 期間有任何動作就代表這個計時器已經過期
            if game.ended or game.turn_id != turn_id or not seat:
                return
            action = "check" if t.legal(seat).can_check else "fold"
            t.act(seat.user_id, action)
            t.log[-1] += "（逾時）"
            await self._after_change(game)

    async def _next_hand_later(self, game: PokerGame, hand_number: int) -> None:
        await asyncio.sleep(NEXT_HAND_DELAY)
        async with game.lock:
            if not game.ended and game.table.hand_number == hand_number:
                await self.start_next_hand(game)

    async def _cash_out(self, game: PokerGame, seat: Seat) -> int:
        hold_id = game.holds.pop(seat.user_id, None)
        if hold_id is None:
            return 0
        net = seat.stack - game.bought_in.get(seat.user_id, game.buy_in)
        await self.bot.db.settle(hold_id, seat.stack, "德州撲克", game="poker", won=net > 0)
        return net

    async def end_game(self, game: PokerGame, reason: str) -> None:
        """結算所有人並關閉牌桌。從牌局流程呼叫時需持有 game.lock。"""
        if game.ended:
            return
        game.ended = True
        self.games.pop(game.channel_id, None)
        self._cancel(game.turn_task)
        if game.next_hand_task and game.next_hand_task is not asyncio.current_task():
            game.next_hand_task.cancel()
        t = game.table
        if t.hand_number and not t.hand_over:
            t.cancel_hand()   # 中途結束：這手的投入全部退回
        lines = []
        for seat in t.seats:
            if seat.user_id in game.ai:
                lines.append(f"{seat.name}　`{seat.stack:,}`")
                continue
            net = await self._cash_out(game, seat)
            lines.append(f"<@{seat.user_id}>　`{seat.stack:,}`　{fmt_signed(net)}")
        if game.view:
            game.view.stop()
        await self.refresh_panels(game)
        embed = make_embed("♠️ 牌局結束", reason, Color.MUTED)
        if lines:
            embed.add_field(name="結算（剩餘籌碼已退回積分）", value="\n".join(lines), inline=False)
        embed.set_footer(text=f"共進行 {t.hand_number} 手")
        if game.message:
            try:
                await game.message.edit(content="", embed=embed, view=None)
            except discord.HTTPException:
                pass

    @staticmethod
    def _cancel(task: Optional[asyncio.Task]) -> None:
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()

    # ================================================================== 牌桌操作
    async def show_hand(self, interaction: discord.Interaction, game: PokerGame) -> None:
        """開啟私人手牌面板；之後牌局每次變化都會自動更新。"""
        seat = game.table.seat_of(interaction.user.id)
        if not seat:
            return await send_error(interaction, "你不在這張牌桌上，可以按「加入牌桌」。")
        old = game.panels.get(seat.user_id)
        panel = Panel(interaction, time.time())
        panel.view = HandView(self, game, seat.user_id)
        game.panels[seat.user_id] = panel
        await interaction.response.send_message(embed=self.panel_embed(game, seat), view=panel.view, ephemeral=True)
        if old:   # 舊面板收掉，避免兩個面板同時存在
            if old.view:
                old.view.stop()
            try:
                await old.interaction.delete_original_response()
            except discord.HTTPException:
                pass

    async def join_table(self, interaction: discord.Interaction, game: PokerGame) -> None:
        async with game.lock:
            t = game.table
            if t.seat_of(interaction.user.id):
                return await send_error(interaction, "你已經在牌桌上了。")
            if len(t.seats) >= MAX_SEATS:
                return await send_error(interaction, f"牌桌已滿（{MAX_SEATS} 人）。")
            hold_id = await self.bot.db.hold(interaction.user.id, game.buy_in, "德州撲克")
            if hold_id is None:
                return await send_error(interaction, f"帶入籌碼需要 {fmt_points(game.buy_in)}，你的積分不夠。")
            game.holds[interaction.user.id] = hold_id
            game.bought_in[interaction.user.id] = game.buy_in
            t.seats.append(Seat(interaction.user.id, interaction.user.display_name, game.buy_in))
            t.log.append(f"{interaction.user.display_name} 入座，下一手加入")
            await interaction.response.defer()
            await self.refresh(game)

    async def leave_table(self, interaction: discord.Interaction, game: PokerGame) -> None:
        async with game.lock:
            seat = game.table.seat_of(interaction.user.id)
            if not seat:
                return await send_error(interaction, "你不在這張牌桌上。")
            if seat.leaving:
                return await send_error(interaction, "你已經在離桌了，這手結束後會結算。")
            seat.leaving = True
            game.table.log.append(f"{seat.name} 準備離桌")
            await interaction.response.send_message(
                f"👋 這手結束後會幫你結算，剩餘籌碼會退回積分。", ephemeral=True)
            if game.table.current_seat is seat:
                await self._after_change(game)   # 正輪到他就直接棄牌
            else:
                await self.refresh(game)

    async def stop_table(self, interaction: discord.Interaction, game: PokerGame) -> None:
        if interaction.user.id != game.host_id and not is_moderator(interaction.user):
            return await send_error(interaction, "只有房主或管理員可以結束牌局。")
        await interaction.response.defer()
        async with game.lock:
            await self.end_game(game, f"{interaction.user.mention} 結束了牌局。")

    # ================================================================== 指令
    async def launch_from_hub(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(PokerSetupModal(self))

    @app_commands.command(name="poker", description="德州撲克：開一個大廳，可以找朋友或加入電腦對手")
    @app_commands.describe(big_blind="大盲注（小盲注是一半）", buy_in="每人帶入的籌碼，預設是大盲注的 50 倍",
                           bots="一開始就加入幾位電腦對手（之後也能在大廳增減）")
    @app_commands.guild_only()
    async def poker(self, interaction: discord.Interaction,
                    big_blind: app_commands.Range[int, 2, 100_000] = 20,
                    buy_in: Optional[app_commands.Range[int, 20, 10_000_000]] = None,
                    bots: app_commands.Range[int, 0, MAX_SEATS - 1] = 0):
        await self.create_lobby(interaction, big_blind, buy_in, bots)


# ====================================================================== 介面
class PokerSetupModal(discord.ui.Modal, title="♠️ 開一桌德州撲克"):
    big_blind = discord.ui.TextInput(label="大盲注（小盲注是一半）", default="20", max_length=6)
    buy_in = discord.ui.TextInput(label="每人帶入籌碼（留空 = 大盲注 × 50）", required=False, max_length=9)
    bots = discord.ui.TextInput(label=f"電腦對手數量（0–{MAX_SEATS - 1}，自己玩就填 1 以上）", default="0", max_length=1)

    def __init__(self, cog: Poker):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        bb_text, buy_text, bots_text = self.big_blind.value.strip(), self.buy_in.value.strip(), self.bots.value.strip()
        if not bb_text.isdigit() or not 2 <= int(bb_text) <= 100_000:
            return await send_error(interaction, "大盲注需要是 2 到 100,000 的整數。")
        if buy_text and not buy_text.isdigit():
            return await send_error(interaction, "帶入籌碼需要是整數。")
        if not bots_text.isdigit() or int(bots_text) > MAX_SEATS - 1:
            return await send_error(interaction, f"電腦對手數量需要是 0 到 {MAX_SEATS - 1}。")
        await self.cog.create_lobby(interaction, int(bb_text), int(buy_text) if buy_text else None, int(bots_text))


class LobbyView(BaseView):
    def __init__(self, cog: Poker, lobby: Lobby):
        super().__init__(timeout=LOBBY_TIMEOUT)
        self.cog, self.lobby = cog, lobby

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.lobby.closed:
            await send_error(interaction, "這個大廳已經關閉了。")
            return False
        return True

    async def _update(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(embed=self.cog.lobby_embed(self.lobby), view=self)

    @discord.ui.button(label="加入", emoji="🪑", style=discord.ButtonStyle.success)
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        lobby = self.lobby
        if interaction.user.id in lobby.players:
            return await send_error(interaction, "你已經在大廳裡了。")
        if lobby.seat_count >= MAX_SEATS:
            if not lobby.ai:
                return await send_error(interaction, f"大廳已滿（{MAX_SEATS} 人）。")
            lobby.remove_ai()   # 真人優先，讓出一位電腦的座位
        hold_id = await self.cog.bot.db.hold(interaction.user.id, lobby.buy_in, "德州撲克")
        if hold_id is None:
            balance = await self.cog.bot.db.get_points(interaction.user.id)
            return await send_error(interaction, f"帶入籌碼需要 {fmt_points(lobby.buy_in)}，你目前有 {fmt_points(balance)}。")
        lobby.players[interaction.user.id] = interaction.user
        lobby.holds[interaction.user.id] = hold_id
        await self._update(interaction)

    @discord.ui.button(label="離開", style=discord.ButtonStyle.secondary)
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        lobby = self.lobby
        if interaction.user.id not in lobby.players:
            return await send_error(interaction, "你不在大廳裡。")
        if interaction.user.id == lobby.host.id:
            await interaction.response.defer()
            self.stop()
            return await self.cog.close_lobby(lobby, "房主離開了，大廳已關閉，帶入籌碼已全數退回。")
        del lobby.players[interaction.user.id]
        await self.cog.bot.db.settle(lobby.holds.pop(interaction.user.id), lobby.buy_in, "離開德州撲克大廳，退回帶入籌碼")
        await self._update(interaction)

    @discord.ui.button(label="開始", emoji="▶️", style=discord.ButtonStyle.primary)
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.lobby.host.id:
            return await send_error(interaction, "只有房主可以開始牌局。")
        if self.lobby.seat_count < 2:
            return await send_error(interaction, "至少要兩位玩家才能開始，可以按「加入電腦」。")
        self.stop()
        await self.cog.start_game(self.lobby, interaction)

    @discord.ui.button(label="加入電腦", emoji="🤖", style=discord.ButtonStyle.secondary, row=1)
    async def add_bot(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.lobby.host.id:
            return await send_error(interaction, "只有房主可以加入電腦。")
        if self.lobby.seat_count >= MAX_SEATS:
            return await send_error(interaction, f"大廳已滿（{MAX_SEATS} 人）。")
        self.lobby.add_ai()
        await self._update(interaction)

    @discord.ui.button(label="移除電腦", style=discord.ButtonStyle.secondary, row=1)
    async def remove_bot(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.lobby.host.id:
            return await send_error(interaction, "只有房主可以移除電腦。")
        if not self.lobby.remove_ai():
            return await send_error(interaction, "大廳裡沒有電腦。")
        await self._update(interaction)

    @discord.ui.button(label="取消", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.lobby.host.id and not is_moderator(interaction.user):
            return await send_error(interaction, "只有房主或管理員可以取消大廳。")
        await interaction.response.defer()
        self.stop()
        await self.cog.close_lobby(self.lobby, f"{interaction.user.mention} 取消了大廳，帶入籌碼已全數退回。")

    async def on_timeout(self) -> None:
        await self.cog.close_lobby(self.lobby, "大廳閒置太久，已自動關閉，帶入籌碼已全數退回。")


class RaiseModal(discord.ui.Modal, title="加注"):
    def __init__(self, cog: Poker, game: PokerGame, min_to: int, max_to: int, from_panel: bool = False):
        super().__init__()
        self.cog, self.game, self.from_panel = cog, game, from_panel
        self.amount = discord.ui.TextInput(label=f"加注到多少（總下注 {min_to:,} ~ {max_to:,}）",
                                           default=str(min_to), max_length=10)
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction):
        text = self.amount.value.replace(",", "").strip()
        if not text.isdigit():
            return await send_error(interaction, "請輸入整數。")
        await self.cog.act(interaction, self.game, "raise", int(text), from_panel=self.from_panel)


class ActionView(discord.ui.View):
    """下注按鈕＋加注金額選單。牌桌與私人面板共用，按下時才檢查是不是輪到你。"""

    from_panel = False

    def __init__(self, cog: Poker, game: PokerGame, seat: Optional[Seat]):
        """seat：可以操作的玩家；None 時動作元件全部停用。"""
        super().__init__(timeout=None)
        self.cog, self.game = cog, game
        self.turn_id = game.turn_id
        t = game.table

        call_label, allin_label, allin_disabled = "過牌 / 跟注", "All-in", True
        options = []
        if seat:
            legal = t.legal(seat)
            call_label = "過牌" if legal.can_check else f"跟注 {legal.call_amount:,}"
            allin_label = f"All-in {seat.stack:,}"
            # 其他人都 All-in 時再推也只等於跟注，就不顯示 All-in 了
            allin_disabled = not legal.can_raise and seat.stack > legal.to_call
            options = [discord.SelectOption(label=f"{label}：加注到 {amount:,}", value=f"{self.turn_id}:{amount}")
                       for label, amount in raise_options(t, seat)]
            if legal.can_raise and legal.min_raise_to < legal.max_raise_to:
                options.append(discord.SelectOption(label="自訂金額…", value=f"{self.turn_id}:custom", emoji="✏️"))

        self._button("棄牌", discord.ButtonStyle.danger, 0, self.fold, disabled=seat is None)
        self._button(call_label, discord.ButtonStyle.primary, 0, self.call, disabled=seat is None)
        self._button(allin_label, discord.ButtonStyle.secondary, 0, self.all_in, emoji="🔥", disabled=allin_disabled)

        if options:
            placeholder = "💰 加注…"
        elif seat:
            placeholder = "現在不能加注" if allin_disabled else "籌碼只夠 All-in"
        else:
            placeholder = "💰 加注（輪到你時可選）"
        select = discord.ui.Select(placeholder=placeholder, row=1, disabled=not options,
                                   options=options or [discord.SelectOption(label="—", value="none")])
        select.callback = lambda interaction: self.raise_(interaction, select)
        self.add_item(select)

    def _button(self, label, style, row, callback, *, emoji=None, disabled=False):
        button = discord.ui.Button(label=label, style=style, row=row, emoji=emoji, disabled=disabled)
        button.callback = callback
        self.add_item(button)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        log.exception("牌桌操作發生錯誤", exc_info=error)
        await send_error(interaction, "操作時發生錯誤，請稍後再試。")

    async def fold(self, interaction: discord.Interaction):
        await self.cog.act(interaction, self.game, "fold", from_panel=self.from_panel)

    async def call(self, interaction: discord.Interaction):
        await self.cog.act(interaction, self.game, "call", from_panel=self.from_panel)

    async def all_in(self, interaction: discord.Interaction):
        await self.cog.act(interaction, self.game, "all_in", from_panel=self.from_panel)

    async def raise_(self, interaction: discord.Interaction, select: discord.ui.Select):
        turn_id, _, choice = select.values[0].partition(":")
        if choice == "" or int(turn_id) != self.game.turn_id:
            return await send_error(interaction, "牌桌已經更新了，請重新選擇。")
        if choice != "custom":
            return await self.cog.act(interaction, self.game, "raise", int(choice), from_panel=self.from_panel)
        t = self.game.table
        seat = t.current_seat
        if not seat or seat.user_id != interaction.user.id:
            return await send_error(interaction, "還沒輪到你喔！")
        legal = t.legal(seat)
        await interaction.response.send_modal(
            RaiseModal(self.cog, self.game, legal.min_raise_to, legal.max_raise_to, self.from_panel))


class TableView(ActionView):
    """公開牌桌上的按鈕：大家共用同一組。"""

    def __init__(self, cog: Poker, game: PokerGame):
        seat = game.table.current_seat
        # 輪到電腦時，動作按鈕先停用
        super().__init__(cog, game, seat if seat and seat.user_id not in game.ai else None)
        t = game.table
        self._button("我的手牌", discord.ButtonStyle.success, 2, self.hand, emoji="🃏")
        self._button("加入牌桌", discord.ButtonStyle.secondary, 2, self.join, emoji="🪑",
                     disabled=len(t.seats) >= MAX_SEATS)
        self._button("離桌", discord.ButtonStyle.secondary, 2, self.leave, emoji="👋")
        self._button("結束牌局", discord.ButtonStyle.danger, 2, self.stop_game, emoji="⏹️")

    async def hand(self, interaction: discord.Interaction):
        await self.cog.show_hand(interaction, self.game)

    async def join(self, interaction: discord.Interaction):
        await self.cog.join_table(interaction, self.game)

    async def leave(self, interaction: discord.Interaction):
        await self.cog.leave_table(interaction, self.game)

    async def stop_game(self, interaction: discord.Interaction):
        await self.cog.stop_table(interaction, self.game)


class HandView(ActionView):
    """私人手牌面板上的按鈕：只有輪到面板主人時才能按。"""

    from_panel = True

    def __init__(self, cog: Poker, game: PokerGame, user_id: int):
        seat = game.table.current_seat
        super().__init__(cog, game, seat if seat and seat.user_id == user_id and not game.ended else None)
