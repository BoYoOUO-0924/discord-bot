import asyncio
import secrets
from typing import Dict, List, Optional, TYPE_CHECKING

import discord

from .cards import Card, generate_shuffled_deck
from .evaluate import evaluate_hand
from .views import ActionView

if TYPE_CHECKING:
    from ..poker import Poker
    from discord.ext.commands import Bot


class GameRoom:
    def __init__(self, bot: "Bot", cog: "Poker", channel_id: int, players: List[discord.Member], chips: Dict[int, int], small_blind: int, big_blind: int):
        self.bot: "Bot" = bot
        self.cog: "Poker" = cog
        self.channel_id: int = channel_id
        self.players: List[discord.Member] = players
        self.player_ids: List[int] = [p.id for p in players]
        self.chips: Dict[int, int] = chips
        self.is_active: bool = True
        self.small_blind: int = small_blind
        self.big_blind: int = big_blind
        self.deck: List[Card] = []
        self.pot: int = 0
        self.community_cards: List[Card] = []
        self.bets: Dict[int, int] = {}
        self.current_bet: int = 0
        self.last_raiser: Optional[int] = None
        self.current_player_idx: int = 0
        self.active_players: List[int] = []
        self.hand_over: bool = False
        self.dealer_button_pos: int = secrets.randbelow(len(self.players))
        self.game_state_message: Optional[discord.Message] = None

    async def get_channel(self) -> Optional[discord.TextChannel]:
        channel = self.bot.get_channel(self.channel_id)
        if isinstance(channel, discord.TextChannel):
            return channel
        return None

    async def start_game(self):
        channel = await self.get_channel()
        if not channel:
            print(f"Error: Channel {self.channel_id} not found.")
            return
        await channel.send(f"遊戲開始！玩家：{', '.join([p.display_name for p in self.players])}\n大小盲注: {self.small_blind}/{self.big_blind}")
        await self._start_hand()

    async def _start_hand(self):
        self.deck = generate_shuffled_deck()
        self.pot = 0
        self.community_cards = []
        self.bets = {p_id: 0 for p_id in self.player_ids}
        self.current_bet = 0
        self.last_raiser = None
        self.hand_over = False
        self.active_players = [p.id for p in self.players if self.chips.get(p.id, 0) > 0]

        if len(self.active_players) < 2:
            await self._end_game()
            return

        self.dealer_button_pos = (self.dealer_button_pos + 1) % len(self.players)
        
        sb_player_idx = (self.dealer_button_pos + 1) % len(self.players)
        bb_player_idx = (self.dealer_button_pos + 2) % len(self.players)
        sb_player_id = self.players[sb_player_idx].id
        bb_player_id = self.players[bb_player_idx].id

        sb_amount = min(self.small_blind, self.chips[sb_player_id])
        self.chips[sb_player_id] -= sb_amount
        self.bets[sb_player_id] = sb_amount
        self.pot += sb_amount

        bb_amount = min(self.big_blind, self.chips[bb_player_id])
        self.chips[bb_player_id] -= bb_amount
        self.bets[bb_player_id] = bb_amount
        self.pot += bb_amount

        self.current_bet = self.big_blind
        self.last_raiser = bb_player_id

        for p_id in self.active_players:
            hole_cards = [self.deck.pop(), self.deck.pop()]
            member = self.bot.get_user(p_id)
            if member:
                try:
                    await member.send(f"你在牌局 #{self.channel_id} 的手牌: `{' '.join(map(str, hole_cards))}`")
                except discord.Forbidden:
                    channel = await self.get_channel()
                    if channel:
                        await channel.send(f"{member.mention}, 我無法私訊你手牌！請檢查你的隱私設定。", delete_after=30)
            self.cog.player_hands[p_id] = hole_cards

        self.current_player_idx = (bb_player_idx + 1) % len(self.players)
        while self.players[self.current_player_idx].id not in self.active_players:
             self.current_player_idx = (self.current_player_idx + 1) % len(self.players)

        await self._update_game_state_message()
        await self._prompt_for_action()

    async def _handle_action(self, player_id: int, action: str, amount: int = 0, bot: Optional["Bot"] = None, cog: Optional["Poker"] = None):
        player = bot.get_user(player_id)
        channel = await self.get_channel()
        
        if not player or not channel:
            return

        if action == "fold":
            self.active_players.remove(player_id)
            await channel.send(f"{player.display_name} 棄牌了。")
            if len(self.active_players) == 1:
                await self._end_hand()
                return

        elif action == "check":
            await channel.send(f"{player.display_name} 過牌。")

        elif action == "call":
            to_call = self.current_bet - self.bets.get(player_id, 0)
            self.chips[player_id] -= to_call
            self.bets[player_id] += to_call
            self.pot += to_call
            await channel.send(f"{player.display_name} 跟注 {to_call}。")

        elif action == "raise":
            self.chips[player_id] -= amount
            self.pot += amount
            self.bets[player_id] += amount
            self.current_bet = self.bets[player_id]
            self.last_raiser = player_id
            await channel.send(f"{player.display_name} 加注到 {self.current_bet}！")

        elif action == "all_in":
            all_in_amount = self.chips[player_id]
            self.chips[player_id] = 0
            self.bets[player_id] += all_in_amount
            self.pot += all_in_amount
            if self.bets[player_id] > self.current_bet:
                self.current_bet = self.bets[player_id]
                self.last_raiser = player_id
            await channel.send(f"{player.display_name} All-in ({all_in_amount})！")

        if self._is_betting_round_over():
            await self._progress_to_next_stage()
        else:
            self._move_to_next_player()
            await self._update_game_state_message()
            await self._prompt_for_action()

    def _move_to_next_player(self):
        self.current_player_idx = (self.current_player_idx + 1) % len(self.players)
        while self.players[self.current_player_idx].id not in self.active_players:
            self.current_player_idx = (self.current_player_idx + 1) % len(self.players)

    def _is_betting_round_over(self) -> bool:
        if len(self.active_players) <= 1: 
            return True

        first_active_player_id = self.active_players[0]
        if self.last_raiser == self.players[self.current_player_idx].id:
             return True

        active_non_all_in_players = [p_id for p_id in self.active_players if self.chips[p_id] > 0]
        if not active_non_all_in_players:
             return True

        bets_of_contending_players = {p_id: self.bets[p_id] for p_id in active_non_all_in_players}
        if len(set(bets_of_contending_players.values())) == 1:
            return True
            
        return False

    async def _progress_to_next_stage(self):
        channel = await self.get_channel()
        if not channel: return

        self.current_bet = 0
        self.last_raiser = None

        for p_id in self.player_ids:
            self.bets[p_id] = 0

        start_idx = (self.dealer_button_pos + 1) % len(self.players)
        while self.players[start_idx].id not in self.active_players or self.chips[self.players[start_idx].id] == 0:
            start_idx = (start_idx + 1) % len(self.players)
        self.current_player_idx = start_idx

        if len(self.community_cards) == 0: # Flop
            self.community_cards.extend([self.deck.pop() for _ in range(3)])
            await channel.send("--- 翻牌圈 ---")
        elif len(self.community_cards) == 3: # Turn
            self.community_cards.append(self.deck.pop())
            await channel.send("--- 轉牌圈 ---")
        elif len(self.community_cards) == 4: # River
            self.community_cards.append(self.deck.pop())
            await channel.send("--- 河牌圈 ---")
        else: # Showdown
            await self._handle_showdown()
            return

        await self._update_game_state_message()
        await self._prompt_for_action()

    async def _handle_showdown(self):
        channel = await self.get_channel()
        if not channel: return

        await channel.send("--- 攤牌 --- 公共牌: `" + ' '.join(map(str, self.community_cards)) + "`")

        final_hands = {}
        for p_id in self.active_players:
            hole_cards = self.cog.player_hands.get(p_id, [])
            player = self.bot.get_user(p_id)
            if not player or not hole_cards:
                continue
            
            best_rank, kickers, hand_name, best_hand_cards = evaluate_hand(hole_cards, self.community_cards)
            final_hands[p_id] = (best_rank, kickers, hand_name, best_hand_cards, player.display_name)
            
            hand_str = ' '.join(map(str, hole_cards))
            await channel.send(f"{player.display_name} 的手牌: `{hand_str}` ({hand_name}) - 最佳五張: `{' '.join(map(str, best_hand_cards))}`")

        sorted_players = sorted(final_hands.items(), key=lambda item: (item[1][0], item[1][1]), reverse=True)

        winner_id, (rank, kickers, name, cards, display_name) = sorted_players[0]
        self.chips[winner_id] += self.pot
        await channel.send(f"**{display_name} 贏得底池 ({self.pot})！**")

        await self._end_hand(start_next=True)

    async def _prompt_for_action(self):
        if self.hand_over: return

        player_id = self.players[self.current_player_idx].id
        player = self.bot.get_user(player_id)
        channel = await self.get_channel()
        if not player or not channel:
            return

        view = ActionView(self, player_id, self.cog)
        await channel.send(f"輪到 {player.mention} 了。", view=view)

    async def _update_game_state_message(self):
        channel = await self.get_channel()
        if not channel: return

        embed = discord.Embed(title=f"德州撲克 - 牌局進行中", color=discord.Color.blue())
        community_str = ' '.join(map(str, self.community_cards)) if self.community_cards else "尚未發牌"
        embed.add_field(name="公共牌", value=f"`{community_str}`", inline=False)
        embed.add_field(name="總底池", value=str(self.pot), inline=False)
        
        player_statuses = []
        for i, p in enumerate(self.players):
            chip_count = self.chips.get(p.id, 0)
            bet_amount = self.bets.get(p.id, 0)
            status_icon = "" 
            if i == self.dealer_button_pos: status_icon += "\U0001f4c0"
            if p.id in self.active_players: status_icon += "\U0001f0cf"
            else: status_icon += "\U0001f6ab"
            
            player_line = f"{p.display_name}: {chip_count} 籌碼"
            if bet_amount > 0:
                player_line += f" (下注: {bet_amount})"
            player_statuses.append(status_icon + " " + player_line)

        embed.add_field(name="玩家狀態", value="\n".join(player_statuses), inline=False)
        
        if self.game_state_message:
            try:
                await self.game_state_message.edit(embed=embed)
            except discord.NotFound:
                self.game_state_message = await channel.send(embed=embed)
        else:
            self.game_state_message = await channel.send(embed=embed)

    async def _end_hand(self, start_next: bool = False):
        self.hand_over = True
        channel = await self.get_channel()
        if not channel: return

        for p_id in self.player_ids:
            if p_id in self.cog.player_hands:
                del self.cog.player_hands[p_id]

        if start_next:
            await asyncio.sleep(5)
            await self._start_hand()
        else:
            winner_id = self.active_players[0]
            winner = self.bot.get_user(winner_id)
            if winner:
                self.chips[winner_id] += self.pot
                await channel.send(f"其他人都棄牌了！**{winner.display_name}** 贏得底池 ({self.pot})。")
            await asyncio.sleep(5)
            await self._start_hand()

    async def _end_game(self):
        self.is_active = False
        channel = await self.get_channel()

        for player_id, final_chips in self.chips.items():
            self.cog.user_points[str(player_id)] = final_chips
        self.cog._save_points()

        if channel:
            active_players_with_chips = [p_id for p_id, chip_count in self.chips.items() if chip_count > 0]
            if len(active_players_with_chips) == 1:
                 winner = self.bot.get_user(active_players_with_chips[0])
                 if winner:
                     await channel.send(f"**遊戲結束！總冠軍是 {winner.mention}！** 玩家積分已儲存。")
            else:
                 await channel.send("**遊戲結束！** 玩家積分已儲存。")

        if self.channel_id in self.cog.game_rooms:
            del self.cog.game_rooms[self.channel_id]
