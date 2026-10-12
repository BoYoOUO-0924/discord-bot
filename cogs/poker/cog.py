"""德州撲克：大廳 → 牌桌。整局只用一則牌桌訊息即時更新，不洗版。"""
import asyncio
import logging
import random
import time
from typing import Dict, Optional

import discord
from discord import app_commands
from discord.ext import commands

from core.timeutil import ts
from core.ui import BaseView, Color, fmt_points, fmt_signed, make_embed, respond, send_error

from .engine import STREET_NAMES, IllegalAction, PokerTable, Seat
from .evaluate import evaluate_hand

log = logging.getLogger(__name__)

TURN_SECONDS = 60
NEXT_HAND_DELAY = 10
MAX_SEATS = 8
LOBBY_TIMEOUT = 15 * 60
MIN_BUY_IN_BB = 10
DEFAULT_BUY_IN_BB = 50


def cards_text(cards) -> str:
    return " ".join(f"`{c}`" for c in cards)


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
        self.message = None
        self.closed = False


class PokerGame:
    def __init__(self, lobby: Lobby):
        self.channel_id = lobby.channel_id
        self.host_id = lobby.host.id
        self.buy_in = lobby.buy_in
        self.holds = dict(lobby.holds)
        self.bought_in = {uid: lobby.buy_in for uid in lobby.players}
        seats = [Seat(uid, member.display_name, lobby.buy_in) for uid, member in lobby.players.items()]
        self.table = PokerTable(seats, lobby.small_blind, lobby.big_blind)
        self.table.dealer_id = random.choice(seats).user_id  # 第一手的莊家隨機
        self.message = None
        self.view: Optional[discord.ui.View] = None
        self.lock = asyncio.Lock()
        self.deadline: Optional[float] = None
        self.turn_task: Optional[asyncio.Task] = None
        self.next_hand_task: Optional[asyncio.Task] = None
        self.turn_id = 0   # 每次狀態改變 +1，讓過期的計時器失效
        self.ended = False


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
        embed.add_field(name="人數", value=f"{len(lobby.players)}/{MAX_SEATS}")
        players = "\n".join(f"{'👑' if uid == lobby.host.id else '・'} {m.mention}" for uid, m in lobby.players.items())
        embed.add_field(name="玩家", value=players, inline=False)
        embed.set_footer(text="加入時會先扣下帶入籌碼，離開或牌局結束時依剩餘籌碼退回")
        return embed

    async def create_lobby(self, interaction: discord.Interaction, big_blind: int, buy_in: Optional[int]) -> None:
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
    def table_embed(self, game: PokerGame) -> discord.Embed:
        t = game.table
        board = cards_text(t.board) + " `▢`" * (5 - len(t.board))
        stage = STREET_NAMES.get(t.street, "")
        embed = make_embed(f"♠️ 德州撲克・第 {t.hand_number} 手", color=Color.TABLE)
        embed.description = f"{board}\n**底池 {t.pot:,}**　・　{stage}　・　盲注 {t.small_blind}/{t.big_blind}"

        current = t.current_seat
        showdown = t.result.showdown if t.result else False
        lines = []
        for seat in t.seats:
            marker = "▶️" if current is seat else "　"
            badges = ""
            if seat.user_id == t.dealer_id and seat.in_hand:
                badges += "🔘"
            name = seat.name
            if seat.in_hand and seat.folded:
                name = f"~~{name}~~"
            status = []
            if not seat.in_hand:
                status.append("下一手加入" if seat.stack > 0 and not seat.leaving else "")
            elif seat.folded:
                status.append("棄牌")
            else:
                if seat.bet:
                    status.append(f"下注 {seat.bet:,}")
                if seat.all_in:
                    status.append("**All-in**")
                if showdown and t.result and seat.user_id in t.result.hands:
                    status.append(f"{cards_text(seat.hole)} {t.result.hands[seat.user_id][0]}")
            if seat.leaving:
                status.append("離桌中")
            win = t.result.payouts.get(seat.user_id) if t.result else None
            if win:
                status.append(f"🏆 +{win:,}")
            status_text = "・".join(s for s in status if s)
            lines.append(f"{marker}{badges}**{name}**　`{seat.stack:,}`　{status_text}".rstrip())
        embed.add_field(name="玩家", value="\n".join(lines), inline=False)

        if t.log:
            embed.add_field(name="動態", value="\n".join(f"・{line}" for line in t.log[-5:]), inline=False)

        if current and game.deadline:
            legal = t.legal(current)
            auto = "過牌" if legal.can_check else "棄牌"
            embed.add_field(name="⏳ 等待行動",
                            value=f"輪到 <@{current.user_id}>，{ts(game.deadline, 'R')}自動{auto}", inline=False)
        elif t.hand_over and not game.ended:
            embed.set_footer(text=f"下一手將在 {NEXT_HAND_DELAY} 秒後開始・按「看手牌」隨時查看自己的牌")
        return embed

    def build_view(self, game: PokerGame) -> discord.ui.View:
        if game.view:
            game.view.stop()
        view = TableView(self, game)
        game.view = view
        return view

    async def refresh(self, game: PokerGame) -> None:
        embed = self.table_embed(game)
        view = self.build_view(game) if not game.ended else None
        try:
            await game.message.edit(embed=embed, view=view)
        except discord.NotFound:
            # 牌桌訊息被刪掉了，重新發一則
            channel = self.bot.get_channel(game.channel_id)
            if channel:
                message = await channel.send(embed=embed, view=view)
                game.message = channel.get_partial_message(message.id)
        except discord.HTTPException:
            log.warning("更新牌桌失敗", exc_info=True)

    # ================================================================== 牌局流程
    async def start_game(self, lobby: Lobby, interaction: discord.Interaction) -> None:
        lobby.closed = True
        self.lobbies.pop(lobby.channel_id, None)
        game = PokerGame(lobby)
        self.games[game.channel_id] = game
        players = "、".join(m.mention for m in lobby.players.values())
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
        if game.host_id not in [s.user_id for s in t.seats] and t.seats:
            game.host_id = t.seats[0].user_id
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
            await self.refresh(game)
            await self._announce_result(game)
            game.next_hand_task = asyncio.create_task(self._next_hand_later(game, t.hand_number))
        else:
            game.deadline = time.time() + TURN_SECONDS
            await self.refresh(game)
            game.turn_task = asyncio.create_task(self._turn_timer(game, game.turn_id))

    async def act(self, interaction: discord.Interaction, game: PokerGame, action: str, amount: Optional[int] = None) -> None:
        async with game.lock:
            if game.ended:
                return await send_error(interaction, "牌局已經結束了。")
            try:
                game.table.act(interaction.user.id, action, amount)
            except IllegalAction as e:
                return await send_error(interaction, str(e))
            await interaction.response.defer()
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

    async def _announce_result(self, game: PokerGame) -> None:
        t = game.table
        result = t.result
        if not result or not result.pots:
            return
        names = {s.user_id: s.name for s in t.order}
        lines = []
        for i, pot in enumerate(result.pots):
            label = "底池" if len(result.pots) == 1 else ("主池" if i == 0 else f"邊池 {i}")
            winners = "、".join(f"<@{uid}>" for uid in pot.winners)
            hand = f"（{pot.hand_name}）" if pot.hand_name else ""
            lines.append(f"**{label} {pot.amount:,}** → {winners}{hand}")
        if result.showdown:
            lines.append("")
            for uid, (hand_name, _) in result.hands.items():
                seat = t.seat_of(uid)
                lines.append(f"{names.get(uid, uid)}：{cards_text(seat.hole) if seat else ''} {hand_name}")
        embed = make_embed(f"🏆 第 {t.hand_number} 手結果", "\n".join(lines), Color.GOLD)
        channel = self.bot.get_channel(game.channel_id)
        if channel:
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

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
            net = await self._cash_out(game, seat)
            lines.append(f"<@{seat.user_id}>　`{seat.stack:,}`　{fmt_signed(net)}")
        if game.view:
            game.view.stop()
        embed = make_embed("♠️ 牌局結束", reason, Color.MUTED)
        if lines:
            embed.add_field(name="結算（剩餘籌碼已退回積分）", value="\n".join(lines), inline=False)
        embed.set_footer(text=f"共進行 {t.hand_number} 手")
        if game.message:
            try:
                await game.message.edit(embed=embed, view=None)
            except discord.HTTPException:
                pass

    @staticmethod
    def _cancel(task: Optional[asyncio.Task]) -> None:
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()

    # ================================================================== 牌桌操作
    async def show_hand(self, interaction: discord.Interaction, game: PokerGame) -> None:
        t = game.table
        seat = t.seat_of(interaction.user.id)
        if not seat:
            return await send_error(interaction, "你不在這張牌桌上，可以按「加入牌桌」。")
        if not seat.in_hand or not seat.hole:
            return await send_error(interaction, "你這一手沒有參與，下一手開始就會發牌給你。")
        embed = make_embed("🂠 你的手牌", f"# {cards_text(seat.hole)}", Color.TABLE)
        if len(t.board) >= 3:
            _, _, hand_name, best = evaluate_hand(seat.hole, t.board)
            embed.add_field(name="目前最佳牌型", value=f"**{hand_name}**　{cards_text(best)}", inline=False)
        embed.add_field(name="你的籌碼", value=f"{seat.stack:,}")
        if t.current_seat is seat:
            legal = t.legal(seat)
            embed.add_field(name="輪到你了", value="可以過牌" if legal.can_check else f"跟注需要 {legal.call_amount:,}")
        if seat.folded:
            embed.set_footer(text="你已經棄牌了")
        await interaction.response.send_message(embed=embed, ephemeral=True)

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

    @app_commands.command(name="poker", description="德州撲克：開一個大廳，2–8 人一起玩")
    @app_commands.describe(big_blind="大盲注（小盲注是一半）", buy_in="每人帶入的籌碼，預設是大盲注的 50 倍")
    @app_commands.guild_only()
    async def poker(self, interaction: discord.Interaction,
                    big_blind: app_commands.Range[int, 2, 100_000] = 20,
                    buy_in: Optional[app_commands.Range[int, 20, 10_000_000]] = None):
        await self.create_lobby(interaction, big_blind, buy_in)


# ====================================================================== 介面
class PokerSetupModal(discord.ui.Modal, title="♠️ 開一桌德州撲克"):
    big_blind = discord.ui.TextInput(label="大盲注（小盲注是一半）", default="20", max_length=6)
    buy_in = discord.ui.TextInput(label="每人帶入籌碼（留空 = 大盲注 × 50）", required=False, max_length=9)

    def __init__(self, cog: Poker):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        bb_text, buy_text = self.big_blind.value.strip(), self.buy_in.value.strip()
        if not bb_text.isdigit() or not 2 <= int(bb_text) <= 100_000:
            return await send_error(interaction, "大盲注需要是 2 到 100,000 的整數。")
        if buy_text and not buy_text.isdigit():
            return await send_error(interaction, "帶入籌碼需要是整數。")
        await self.cog.create_lobby(interaction, int(bb_text), int(buy_text) if buy_text else None)


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
        if len(lobby.players) >= MAX_SEATS:
            return await send_error(interaction, f"大廳已滿（{MAX_SEATS} 人）。")
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
        if len(self.lobby.players) < 2:
            return await send_error(interaction, "至少要兩位玩家才能開始。")
        self.stop()
        await self.cog.start_game(self.lobby, interaction)

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
    def __init__(self, cog: Poker, game: PokerGame, min_to: int, max_to: int):
        super().__init__()
        self.cog, self.game = cog, game
        self.amount = discord.ui.TextInput(label=f"加注到多少（總下注 {min_to:,} ~ {max_to:,}）",
                                           default=str(min_to), max_length=10)
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction):
        text = self.amount.value.replace(",", "").strip()
        if not text.isdigit():
            return await send_error(interaction, "請輸入整數。")
        await self.cog.act(interaction, self.game, "raise", int(text))


class TableView(discord.ui.View):
    """牌桌按鈕：大家共用同一組，按下時才檢查是不是輪到你。"""

    def __init__(self, cog: Poker, game: PokerGame):
        super().__init__(timeout=None)
        self.cog, self.game = cog, game
        t = game.table
        seat = t.current_seat
        in_progress = seat is not None

        call_label, raise_disabled, allin_label, allin_disabled = "過牌 / 跟注", True, "All-in", not in_progress
        if seat:
            legal = t.legal(seat)
            call_label = "過牌" if legal.can_check else f"跟注 {legal.call_amount:,}"
            raise_disabled = not legal.can_raise
            allin_label = f"All-in {seat.stack:,}"
            # 其他人都 All-in 時再推也只等於跟注，就不顯示 All-in 了
            allin_disabled = not legal.can_raise and seat.stack > legal.to_call

        self._button("棄牌", discord.ButtonStyle.danger, 0, self.fold, disabled=not in_progress)
        self._button(call_label, discord.ButtonStyle.primary, 0, self.call, disabled=not in_progress)
        self._button("加注", discord.ButtonStyle.success, 0, self.raise_, disabled=not in_progress or raise_disabled)
        self._button(allin_label, discord.ButtonStyle.secondary, 0, self.all_in, disabled=allin_disabled)
        self._button("看手牌", discord.ButtonStyle.secondary, 1, self.hand, emoji="🂠")
        self._button("加入牌桌", discord.ButtonStyle.secondary, 1, self.join, emoji="🪑",
                     disabled=len(t.seats) >= MAX_SEATS)
        self._button("離桌", discord.ButtonStyle.secondary, 1, self.leave, emoji="👋")
        self._button("結束牌局", discord.ButtonStyle.danger, 1, self.stop_game, emoji="⏹️")

    def _button(self, label, style, row, callback, *, emoji=None, disabled=False):
        button = discord.ui.Button(label=label, style=style, row=row, emoji=emoji, disabled=disabled)
        button.callback = callback
        self.add_item(button)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        log.exception("牌桌操作發生錯誤", exc_info=error)
        await send_error(interaction, "操作時發生錯誤，請稍後再試。")

    async def fold(self, interaction: discord.Interaction):
        await self.cog.act(interaction, self.game, "fold")

    async def call(self, interaction: discord.Interaction):
        await self.cog.act(interaction, self.game, "call")

    async def all_in(self, interaction: discord.Interaction):
        await self.cog.act(interaction, self.game, "all_in")

    async def raise_(self, interaction: discord.Interaction):
        t = self.game.table
        seat = t.current_seat
        if not seat or seat.user_id != interaction.user.id:
            return await send_error(interaction, "還沒輪到你喔！")
        legal = t.legal(seat)
        if not legal.can_raise:
            return await send_error(interaction, "現在不能加注。")
        await interaction.response.send_modal(RaiseModal(self.cog, self.game, legal.min_raise_to, legal.max_raise_to))

    async def hand(self, interaction: discord.Interaction):
        await self.cog.show_hand(interaction, self.game)

    async def join(self, interaction: discord.Interaction):
        await self.cog.join_table(interaction, self.game)

    async def leave(self, interaction: discord.Interaction):
        await self.cog.leave_table(interaction, self.game)

    async def stop_game(self, interaction: discord.Interaction):
        await self.cog.stop_table(interaction, self.game)
