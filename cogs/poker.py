import asyncio
import json
import os
import secrets
from collections import Counter
from enum import Enum
from typing import Dict, List, Optional, Tuple

import discord
from discord.ext import commands


class Card:
    SUITS = ["♠", "♥", "♦", "♣"]
    RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "T", "J", "Q", "K", "A"]
    RANK_VALUES = {rank: idx for idx, rank in enumerate(RANKS)}

    def __init__(self, rank: str, suit: str) -> None:
        self.rank = rank
        self.suit = suit

    def __str__(self) -> str:
        return f"{self.rank}{self.suit}"

    def value(self) -> int:
        return Card.RANK_VALUES[self.rank]


class RoundStage(Enum):
    PREFLOP = "Preflop"
    FLOP = "Flop"
    TURN = "Turn"
    RIVER = "River"
    SHOWDOWN = "Showdown"


def generate_shuffled_deck() -> List[Card]:
    deck = [Card(rank, suit) for suit in Card.SUITS for rank in Card.RANKS]
    for i in range(len(deck) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        deck[i], deck[j] = deck[j], deck[i]
    return deck


def evaluate_hand(hole: List[Card], community: List[Card]) -> Tuple[int, List[int]]:
    """評估牌型，回傳 (等級, 排序後的牌值列表)"""
    all_cards = hole + community
    if len(community) < 3:
        # Preflop/Flop 階段，只評估手牌
        return (0, sorted([c.value() for c in hole], reverse=True))

    # 找出最佳五張牌組合
    best_rank = -1
    best_kicker = []

    for combo in _combinations(all_cards, 5):
        rank, kicker = _evaluate_five(combo)
        if rank > best_rank or (rank == best_rank and kicker > best_kicker):
            best_rank = rank
            best_kicker = kicker

    return (best_rank, best_kicker)


def _combinations(cards: List[Card], k: int):
    """生成組合"""
    if k == 0:
        yield []
        return
    if not cards:
        return
    for i in range(len(cards)):
        for combo in _combinations(cards[i + 1:], k - 1):
            yield [cards[i]] + combo


def _evaluate_five(cards: List[Card]) -> Tuple[int, List[int]]:
    """評估五張牌"""
    values = sorted([c.value() for c in cards], reverse=True)
    suits = [c.suit for c in cards]
    value_counts = Counter(values)

    # 同花順
    is_flush = len(set(suits)) == 1
    is_straight = _is_straight(values)
    if is_flush and is_straight:
        if values == [12, 11, 10, 9, 8]:  # A-K-Q-J-10
            return (9, values)  # 皇家同花順
        return (8, values)  # 同花順

    # 四條
    if 4 in value_counts.values():
        quad = [v for v, c in value_counts.items() if c == 4][0]
        kicker = [v for v, c in value_counts.items() if c == 1][0]
        return (7, [quad, kicker])

    # 葫蘆
    if 3 in value_counts.values() and 2 in value_counts.values():
        trips = [v for v, c in value_counts.items() if c == 3][0]
        pair = [v for v, c in value_counts.items() if c == 2][0]
        return (6, [trips, pair])

    # 同花
    if is_flush:
        return (5, values)

    # 順子
    if is_straight:
        return (4, values)

    # 三條
    if 3 in value_counts.values():
        trips = [v for v, c in value_counts.items() if c == 3][0]
        kickers = sorted([v for v, c in value_counts.items() if c == 1], reverse=True)
        return (3, [trips] + kickers)

    # 兩對
    pairs = sorted([v for v, c in value_counts.items() if c == 2], reverse=True)
    if len(pairs) == 2:
        kicker = [v for v, c in value_counts.items() if c == 1][0]
        return (2, pairs + [kicker])

    # 一對
    if len(pairs) == 1:
        kickers = sorted([v for v, c in value_counts.items() if c == 1], reverse=True)
        return (1, pairs + kickers)

    # 高牌
    return (0, values)


def _is_straight(values: List[int]) -> bool:
    """檢查是否為順子（含 A-2-3-4-5）"""
    if len(set(values)) != 5:
        return False
    sorted_vals = sorted(values)
    # 一般順子
    if sorted_vals == list(range(sorted_vals[0], sorted_vals[0] + 5)):
        return True
    # A-2-3-4-5（輪子）
    if sorted_vals == [0, 1, 2, 3, 12]:
        return True
    return False


class GameRoom:
    def __init__(self, owner_id: int, channel: discord.TextChannel, small_blind: int = 10, big_blind: int = 20) -> None:
        self.owner_id = owner_id
        self.channel = channel
        self.players: List[int] = []  # user ids
        self.started = False
        self.deck: List[Card] = []
        self.hole: Dict[int, List[Card]] = {}
        self.community: List[Card] = []
        self.lock = asyncio.Lock()

        # 遊戲狀態
        self.small_blind = small_blind
        self.big_blind = big_blind
        self.chips: Dict[int, int] = {}  # 玩家籌碼
        self.bets: Dict[int, int] = {}  # 當前下注金額
        self.folded: List[int] = []  # 已棄牌玩家
        self.stage = RoundStage.PREFLOP
        self.current_player_idx = 0
        self.dealer_idx = 0
        self.pot = 0
        self.current_bet = 0  # 當前最高下注
        self.action_message: Optional[discord.Message] = None
        self.action_view: Optional[discord.ui.View] = None
        # 下注規則追蹤
        self.last_raiser_idx: Optional[int] = None
        self.last_raise_amount: int = 0
        # 行動鎖避免競態
        self.action_lock = asyncio.Lock()
        # 邊池/全下狀態
        self.committed: Dict[int, int] = {}
        self.all_in: List[int] = []
        self.pots: List[Dict[str, object]] = []  # [{"amount": int, "eligible": set[int]}]

    def _load_chips(self, cog: "Poker") -> None:
        """從 points.json 載入玩家籌碼"""
        data_dir = os.path.join(os.getcwd(), "data")
        os.makedirs(data_dir, exist_ok=True)
        points_path = os.path.join(data_dir, "points.json")
        try:
            with open(points_path, "r", encoding="utf-8") as f:
                points = json.load(f)
            for uid in self.players:
                uid_str = str(uid)
                self.chips[uid] = points.get(uid_str, 1000)  # 預設 1000
        except Exception:
            for uid in self.players:
                self.chips[uid] = 1000

    def _save_chips(self, cog: "Poker") -> None:
        """儲存玩家籌碼到 points.json"""
        data_dir = os.path.join(os.getcwd(), "data")
        os.makedirs(data_dir, exist_ok=True)
        points_path = os.path.join(data_dir, "points.json")
        try:
            with open(points_path, "r", encoding="utf-8") as f:
                points = json.load(f)
        except Exception:
            points = {}
        for uid, amount in self.chips.items():
            points[str(uid)] = amount
        with open(points_path, "w", encoding="utf-8") as f:
            json.dump(points, f, indent=2)

    def reset_deck(self) -> None:
        self.deck = generate_shuffled_deck()

    def add_player(self, user_id: int) -> bool:
        if user_id in self.players:
            return False
        if self.started:
            return False
        self.players.append(user_id)
        return True

    def remove_player(self, user_id: int) -> bool:
        if user_id in self.players and not self.started:
            self.players.remove(user_id)
            return True
        return False

    def get_active_players(self) -> List[int]:
        """取得未棄牌的玩家"""
        return [uid for uid in self.players if uid not in self.folded]

    async def start(self, bot: commands.Bot, cog: "Poker") -> None:
        if self.started:
            return
        if len(self.players) < 2:
            raise ValueError("玩家不足，至少需要 2 人。")
        self.started = True
        self._load_chips(cog)

        # 初始化
        self.reset_deck()
        self.hole.clear()
        self.community.clear()
        self.bets.clear()
        self.folded.clear()
        self.committed = {uid: 0 for uid in self.players}
        self.all_in = []
        self.pots = []
        self.pot = 0
        self.current_bet = 0
        self.stage = RoundStage.PREFLOP
        self.dealer_idx = (self.dealer_idx + 1) % len(self.players)

        # 發手牌（不使用私訊；之後透過按鈕以 ephemeral 顯示）
        for uid in self.players:
            self.hole[uid] = [self.deck.pop(), self.deck.pop()]

        # 下盲注
        sb_idx = (self.dealer_idx + 1) % len(self.players)
        bb_idx = (self.dealer_idx + 2) % len(self.players)
        sb_player = self.players[sb_idx]
        bb_player = self.players[bb_idx]

        self.bets[sb_player] = min(self.small_blind, self.chips[sb_player])
        self.chips[sb_player] -= self.bets[sb_player]
        self.committed[sb_player] += self.bets[sb_player]
        self.pot += self.bets[sb_player]
        if self.chips[sb_player] == 0 and sb_player not in self.all_in:
            self.all_in.append(sb_player)

        self.bets[bb_player] = min(self.big_blind, self.chips[bb_player])
        self.chips[bb_player] -= self.bets[bb_player]
        self.committed[bb_player] += self.bets[bb_player]
        self.pot += self.bets[bb_player]
        if self.chips[bb_player] == 0 and bb_player not in self.all_in:
            self.all_in.append(bb_player)
        self.current_bet = self.big_blind

        # 初始下注狀態
        self.current_bet = self.bets[bb_player]
        self.last_raiser_idx = bb_idx
        self.last_raise_amount = self.big_blind

        # 公告盲注（使用 mention）
        await self.channel.send(
            f"小盲注：<@{sb_player}> 下注 {self.bets[sb_player]}\n"
            f"大盲注：<@{bb_player}> 下注 {self.bets[bb_player]}\n"
            f"底池：{self.pot}"
        )

        # 提供查看手牌的按鈕（ephemeral 顯示，只有點擊者可見）
        await self.channel.send(
            "點擊下方按鈕查看你的手牌（只有你可見）",
            view=HandView(self)
        )

        # 開始 Preflop 下注輪
        self.current_player_idx = (bb_idx + 1) % len(self.players)
        await self._start_betting_round(bot, cog)

    async def _start_betting_round(self, bot: commands.Bot, cog: "Poker") -> None:
        """開始下注輪"""
        active = self.get_active_players()
        if len(active) <= 1:
            await self._end_hand(bot, cog)
            return

        # 檢查是否下注輪應結束（回到最後加注者左手，且平注）
        if self._betting_round_complete():
            await self._next_stage(bot, cog)
            return

        # 找到下一個需要行動的玩家
        while True:
            player = self.players[self.current_player_idx]
            if player in self.folded or player in self.all_in:
                self.current_player_idx = (self.current_player_idx + 1) % len(self.players)
                continue
            if player not in active:
                break

            # 檢查是否需要下注
            player_bet = self.bets.get(player, 0)
            if player_bet < self.current_bet:
                break

            self.current_player_idx = (self.current_player_idx + 1) % len(self.players)
            # 若回到最後加注者左手，檢查是否完成下注輪
            if self._betting_round_complete():
                await self._next_stage(bot, cog)
                return
            break

        player = self.players[self.current_player_idx]
        await self._show_action_buttons(bot, player, cog)

    def _all_bets_equal(self) -> bool:
        """檢查需要行動的玩家是否都已平注。
        僅考慮未棄牌且未全下的玩家；全下者不再要求補齊最高注。
        若沒有任何需要行動的玩家，視為平注。
        """
        candidates = [uid for uid in self.players if uid not in self.folded and uid not in self.all_in]
        if not candidates:
            return True
        return all(self.bets.get(uid, 0) == self.current_bet for uid in candidates)

    def _betting_round_complete(self) -> bool:
        """判斷是否完成本輪下注（回到最後加注者左手，且平注）。
        若本街尚無加注者，預設以莊家為基準。
        """
        base_raiser_idx = self.last_raiser_idx if self.last_raiser_idx is not None else self.dealer_idx
        start_idx = (base_raiser_idx + 1) % len(self.players)
        if self.current_player_idx != start_idx:
            return False
        return self._all_bets_equal()

    async def _show_action_buttons(self, bot: commands.Bot, player_id: int, cog: "Poker") -> None:
        """顯示行動按鈕給指定玩家"""
        player = await bot.fetch_user(player_id)
        player_bet = self.bets.get(player_id, 0)
        to_call = self.current_bet - player_bet
        can_check = to_call == 0
        can_raise = (self.chips[player_id] > to_call) and (player_id not in self.all_in)

        view = ActionView(self, player_id, cog, can_check, to_call, can_raise)
        self.action_view = view

        stage_name = self.stage.value
        msg_text = (
            f"**{stage_name} 階段**\n"
            f"輪到 {player.mention} 行動\n"
            f"當前最高下注：{self.current_bet}\n"
            f"你的下注：{player_bet}\n"
            f"需要跟注：{to_call}\n"
            f"你的籌碼：{self.chips[player_id]}\n"
            f"底池：{self.pot}"
        )
        if self.community:
            msg_text += f"\n公共牌：{' '.join(map(str, self.community))}"

        if self.action_message:
            try:
                await self.action_message.delete()
            except Exception:
                pass

        self.action_message = await self.channel.send(msg_text, view=view)

        # 設定超時（30 秒）
        await asyncio.sleep(30)
        if view and not view.action_taken:
            # 自動 Fold
            await self._handle_action(player_id, "fold", 0, bot, cog)

    async def _handle_action(self, player_id: int, action: str, amount: int, bot: commands.Bot, cog: "Poker") -> None:
        """處理玩家行動"""
        async with self.action_lock:
            if self.action_view:
                self.action_view.action_taken = True
                self.action_view.stop()
                self.action_view = None

        if action == "fold":
            if player_id not in self.folded:
                self.folded.append(player_id)
            await self.channel.send(f"<@{player_id}> 棄牌")
        elif action == "check":
            # 不下注
            await self.channel.send(f"<@{player_id}> 過牌")
        elif action == "call":
            player_bet = self.bets.get(player_id, 0)
            to_call = max(0, self.current_bet - player_bet)
            bet_amount = min(to_call, self.chips[player_id])
            self.bets[player_id] = player_bet + bet_amount
            self.chips[player_id] -= bet_amount
            self.pot += bet_amount
            self.committed[player_id] += bet_amount
            if self.chips[player_id] == 0 and player_id not in self.all_in:
                self.all_in.append(player_id)
            await self.channel.send(f"<@{player_id}> 跟注 {bet_amount}")
        elif action == "raise":
            player_bet = self.bets.get(player_id, 0)
            to_call = max(0, self.current_bet - player_bet)
            # amount 是純加注額（不含跟注）
            # 檢查最小加注（若非全下且小於 last_raise_amount，拒絕）
            max_raise_cap = max(0, self.chips[player_id] - to_call)
            raise_amt = min(amount, max_raise_cap)
            total_bet = to_call + raise_amt

            if raise_amt < self.last_raise_amount and total_bet < (to_call + self.last_raise_amount) and total_bet < self.chips[player_id] + player_bet:
                await self.channel.send(f"<@{player_id}> 加注失敗：最小加注為 {self.last_raise_amount}")
            else:
                self.bets[player_id] = player_bet + total_bet
                self.chips[player_id] -= total_bet
                self.pot += total_bet
                self.committed[player_id] += total_bet
                # 若為完整加注（非因全下導致不足），更新追蹤
                if raise_amt >= self.last_raise_amount or self.chips[player_id] == 0:
                    self.current_bet = self.bets[player_id]
                    self.last_raise_amount = max(self.last_raise_amount, raise_amt)
                    self.last_raiser_idx = self.current_player_idx
                if self.chips[player_id] == 0 and player_id not in self.all_in:
                    self.all_in.append(player_id)
                await self.channel.send(f"<@{player_id}> 加注 {total_bet}")

        # 刪除行動訊息
        if self.action_message:
            try:
                await self.action_message.delete()
            except Exception:
                pass
            self.action_message = None

        # 移動到下一個玩家
        self.current_player_idx = (self.current_player_idx + 1) % len(self.players)
        await self._start_betting_round(bot, cog)

    async def _next_stage(self, bot: commands.Bot, cog: "Poker") -> None:
        """進入下一個階段"""
        # 重置下注
        for uid in self.players:
            self.bets[uid] = 0
        self.current_bet = 0

        if self.stage == RoundStage.PREFLOP:
            # Flop
            self.stage = RoundStage.FLOP
            self.community.extend([self.deck.pop(), self.deck.pop(), self.deck.pop()])
            await self.channel.send(f"**Flop：** {' '.join(map(str, self.community))}")
            self.current_player_idx = (self.dealer_idx + 1) % len(self.players)
            # 新一街預設以莊家作為加注基準，確保全員過牌可正確結束
            self.last_raiser_idx = self.dealer_idx
            self.last_raise_amount = self.big_blind
        elif self.stage == RoundStage.FLOP:
            # Turn
            self.stage = RoundStage.TURN
            self.community.append(self.deck.pop())
            await self.channel.send(f"**Turn：** {' '.join(map(str, self.community))}")
            self.current_player_idx = (self.dealer_idx + 1) % len(self.players)
            self.last_raiser_idx = self.dealer_idx
            self.last_raise_amount = self.big_blind
        elif self.stage == RoundStage.TURN:
            # River
            self.stage = RoundStage.RIVER
            self.community.append(self.deck.pop())
            await self.channel.send(f"**River：** {' '.join(map(str, self.community))}")
            self.current_player_idx = (self.dealer_idx + 1) % len(self.players)
            self.last_raiser_idx = self.dealer_idx
            self.last_raise_amount = self.big_blind
        else:
            # Showdown
            await self._showdown(bot, cog)
            return

        await self._start_betting_round(bot, cog)

    async def _showdown(self, bot: commands.Bot, cog: "Poker") -> None:
        """攤牌比牌"""
        active = self.get_active_players()
        if len(active) == 1:
            # 只有一人，直接獲勝
            winner = active[0]
            self.chips[winner] += self.pot
            await self.channel.send(f"{await bot.fetch_user(winner)} 獲勝！獲得 {self.pot} 籌碼")
        else:
            # 計算各玩家牌力
            hand_rank: Dict[int, Tuple[int, List[int]]] = {}
            for uid in self.players:
                hand_rank[uid] = evaluate_hand(self.hole.get(uid, []), self.community)

            # 建立邊池
            side_pots = self._compute_side_pots()
            total_distributed = 0
            rank_names = ["高牌", "一對", "兩對", "三條", "順子", "同花", "葫蘆", "四條", "同花順", "皇家同花順"]
            lines = ["**攤牌結果（含邊池）**"]
            for i, pot in enumerate(side_pots, start=1):
                amount = pot["amount"]
                eligible = pot["eligible"]
                if amount <= 0 or not eligible:
                    continue
                # 找出本邊池的最佳牌力
                best = None
                winners: List[int] = []
                for uid in eligible:
                    r = hand_rank.get(uid, (0, []))
                    if best is None or r > best:
                        best = r
                        winners = [uid]
                    elif r == best:
                        winners.append(uid)
                # 平手分池
                share = amount // len(winners)
                remainder = amount % len(winners)
                for idx, uid in enumerate(sorted(winners)):
                    gain = share + (1 if idx < remainder else 0)
                    self.chips[uid] += gain
                    total_distributed += gain
                rank_name = rank_names[best[0]] if best else "高牌"
                winner_mentions = ", ".join(f"<@{uid}>" for uid in winners)
                lines.append(f"邊池 {i}：{winner_mentions}（{rank_name}）平分 {amount} → 每人 {share}{' +1' if remainder else ''}")
            # 若有剩餘未分配（理論上不會），一併加到最佳總贏家
            leftover = self.pot - total_distributed
            if leftover > 0:
                # 給第一個主池贏家
                first_pot = next((p for p in side_pots if p["amount"] > 0 and p["eligible"]), None)
                if first_pot:
                    uid0 = sorted(first_pot["eligible"])[0]
                    self.chips[uid0] += leftover
                    lines.append(f"餘額 {leftover} 發放給 <@{uid0}>")
            await self.channel.send("\n".join(lines))

        self._save_chips(cog)
        self.started = False
        self.stage = RoundStage.PREFLOP

    def _compute_side_pots(self) -> List[Dict[str, object]]:
        """根據 self.committed 計算邊池。
        金額計算包含所有玩家的投入，但 eligible 只包含未棄牌的玩家。
        回傳列表由小到大主序依序列出。
        """
        amounts = [v for v in self.committed.values() if v > 0]
        if not amounts:
            return []
        thresholds = sorted(set(amounts))
        pots: List[Dict[str, object]] = []
        prev = 0
        for t in thresholds:
            contributors = [uid for uid, c in self.committed.items() if c >= t]
            delta = (t - prev) * len(contributors)
            if delta > 0:
                eligible = set(uid for uid in contributors if uid not in self.folded)
                pots.append({"amount": delta, "eligible": eligible})
            prev = t
        # 若仍有超過最大門檻的投入（金額相等不會再有），此處不需再處理
        return pots

    async def _end_hand(self, bot: commands.Bot, cog: "Poker") -> None:
        """結束牌局（所有人棄牌）"""
        active = self.get_active_players()
        if active:
            winner = active[0]
            self.chips[winner] += self.pot
            await self.channel.send(f"{await bot.fetch_user(winner)} 獲勝！獲得 {self.pot} 籌碼（所有人棄牌）")
        self._save_chips(cog)
        self.started = False
        self.stage = RoundStage.PREFLOP


class ActionView(discord.ui.View):
    def __init__(self, room: GameRoom, player_id: int, cog: "Poker", can_check: bool, to_call: int, can_raise: bool) -> None:
        super().__init__(timeout=30)
        self.room = room
        self.player_id = player_id
        self.cog = cog
        self.action_taken = False
        self.to_call = to_call
        self.can_raise = can_raise

        if can_check:
            self.check_button.label = "過牌"
        else:
            self.check_button.label = f"跟注 {to_call}"
            self.check_button.style = discord.ButtonStyle.primary

        if not can_raise:
            self.raise_button.disabled = True

    @discord.ui.button(label="棄牌", style=discord.ButtonStyle.danger)
    async def fold_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.player_id:
            await interaction.response.send_message("輪到其他玩家行動。", ephemeral=True)
            return
        self.action_taken = True
        await interaction.response.defer()
        await self.room._handle_action(self.player_id, "fold", 0, self.cog.bot, self.cog)

    @discord.ui.button(label="過牌", style=discord.ButtonStyle.secondary)
    async def check_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.player_id:
            await interaction.response.send_message("輪到其他玩家行動。", ephemeral=True)
            return
        self.action_taken = True
        action = "check" if button.label == "過牌" else "call"
        await interaction.response.defer()
        await self.room._handle_action(self.player_id, action, 0, self.cog.bot, self.cog)

    @discord.ui.button(label="加注", style=discord.ButtonStyle.success)
    async def raise_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.player_id:
            await interaction.response.send_message("輪到其他玩家行動。", ephemeral=True)
            return
        if not self.can_raise:
            await interaction.response.send_message("無法加注。", ephemeral=True)
            return
        # 顯示輸入金額的 Modal
        modal = RaiseModal(self.room, self.player_id, self.cog, self.to_call)
        await interaction.response.send_modal(modal)


class RaiseModal(discord.ui.Modal, title="輸入加注金額"):
    amount = discord.ui.TextInput(label="金額", placeholder="輸入整數，例如 50", required=True)

    def __init__(self, room: GameRoom, player_id: int, cog: "Poker", to_call: int):
        super().__init__()
        self.room = room
        self.player_id = player_id
        self.cog = cog
        self.to_call = to_call

    async def on_submit(self, interaction: discord.Interaction) -> None:
        # 僅允許該玩家
        if interaction.user.id != self.player_id:
            await interaction.response.send_message("這不是你的回合。", ephemeral=True)
            return

        # 解析金額
        try:
            amt = int(str(self.amount.value).strip())
        except Exception:
            await interaction.response.send_message("金額必須是整數。", ephemeral=True)
            return

        if amt < 1:
            await interaction.response.send_message("加注金額至少為 1。", ephemeral=True)
            return

        # 檢查籌碼
        chips = self.room.chips.get(self.player_id, 0)
        max_raise = max(0, chips - self.to_call)  # 跟注後可再加的最大額度
        if max_raise <= 0:
            await interaction.response.send_message("籌碼不足以加注，請改用跟注或棄牌。", ephemeral=True)
            return

        if amt > max_raise:
            await interaction.response.send_message(f"加注上限為 {max_raise}。", ephemeral=True)
            return

        # 送出行動：加注（含跟注額度）
        await interaction.response.defer()
        await self.room._handle_action(self.player_id, "raise", amt, self.cog.bot, self.cog)


class HandView(discord.ui.View):
    def __init__(self, room: GameRoom) -> None:
        super().__init__(timeout=None)
        self.room = room

    @discord.ui.button(label="查看手牌", style=discord.ButtonStyle.secondary)
    async def show_hand(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        if uid not in self.room.players:
            await interaction.response.send_message("你不在這個房間內。", ephemeral=True)
            return
        cards = self.room.hole.get(uid)
        if not cards:
            await interaction.response.send_message("目前沒有可顯示的手牌。", ephemeral=True)
            return
        await interaction.response.send_message(
            f"你的手牌：{' '.join(map(str, cards))}",
            ephemeral=True
        )

class LobbyView(discord.ui.View):
    def __init__(self, cog: "Poker", room_id: int, *, timeout: Optional[float] = 300) -> None:
        super().__init__(timeout=timeout)
        self.cog = cog
        self.room_id = room_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        room = self.cog.rooms.get(self.room_id)
        return room is not None and room.channel.id == interaction.channel_id

    @discord.ui.button(label="加入", style=discord.ButtonStyle.success)
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        room = self.cog.rooms.get(self.room_id)
        if room is None:
            await interaction.response.send_message("房間不存在。", ephemeral=True)
            return
        async with room.lock:
            if room.started:
                await interaction.response.send_message("遊戲已開始，無法加入。", ephemeral=True)
                return
            added = room.add_player(interaction.user.id)
            if not added:
                await interaction.response.send_message("你已在房內或遊戲已開始。", ephemeral=True)
                return
        await interaction.response.send_message(f"{interaction.user.mention} 已加入。", ephemeral=False)

    @discord.ui.button(label="離開", style=discord.ButtonStyle.secondary)
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        room = self.cog.rooms.get(self.room_id)
        if room is None:
            await interaction.response.send_message("房間不存在。", ephemeral=True)
            return
        async with room.lock:
            removed = room.remove_player(interaction.user.id)
            if not removed:
                await interaction.response.send_message("你不在房內或遊戲已開始。", ephemeral=True)
                return
        await interaction.response.send_message(f"{interaction.user.mention} 已離開。", ephemeral=False)

    @discord.ui.button(label="開始", style=discord.ButtonStyle.primary)
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
        room = self.cog.rooms.get(self.room_id)
        if room is None:
            await interaction.response.send_message("房間不存在。", ephemeral=True)
            return
        if interaction.user.id != room.owner_id:
            await interaction.response.send_message("只有房主可以開始。", ephemeral=True)
            return
        async with room.lock:
            try:
                await room.start(self.cog.bot, self.cog)
            except ValueError as e:
                await interaction.response.send_message(str(e), ephemeral=True)
                return
        await interaction.response.send_message("遊戲開始！", ephemeral=False)
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        await interaction.message.edit(view=self)


class Poker(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.rooms: Dict[int, GameRoom] = {}

    @commands.command(name="poker")
    async def poker(self, ctx: commands.Context):
        """建立撲克房間（按鈕加入/離開/開始）。"""
        room = GameRoom(owner_id=ctx.author.id, channel=ctx.channel)
        room.add_player(ctx.author.id)
        msg = await ctx.send(
            f"撲克房間建立，房主：{ctx.author.mention}。按下方按鈕加入/離開/開始。"
        )
        view = LobbyView(self, room_id=msg.id)
        self.rooms[msg.id] = room
        await msg.edit(view=view)

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if message.id in self.rooms:
            self.rooms.pop(message.id, None)


async def setup(bot: commands.Bot):
    await bot.add_cog(Poker(bot))
