import asyncio
import secrets
from typing import Dict, List, Optional, TYPE_CHECKING, Tuple
from collections import defaultdict

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
        self.initial_players: List[discord.Member] = players
        self.player_ids: List[int] = [p.id for p in players]
        self.initial_chips: Dict[int, int] = chips.copy()
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
        self.dealer_button_pos: int = secrets.randbelow(len(self.initial_players))
        self.game_state_message: Optional[discord.Message] = None
        self.players_acted_this_round: set[int] = set()

    def get_player_from_id(self, player_id: int) -> Optional[discord.Member]:
        return next((p for p in self.initial_players if p.id == player_id), None)

    async def get_channel(self) -> Optional[discord.TextChannel]:
        channel = self.bot.get_channel(self.channel_id)
        if isinstance(channel, discord.TextChannel):
            return channel
        return None

    def _get_next_active_player_idx(self, start_idx: int) -> int:
        next_idx = start_idx
        for _ in range(len(self.initial_players) * 2):
            next_idx = (next_idx + 1) % len(self.initial_players)
            if self.initial_players[next_idx].id in self.active_players:
                return next_idx
        return start_idx

    async def start_game(self):
        channel = await self.get_channel()
        if not channel:
            print(f"Error: Channel {self.channel_id} not found.")
            return
        player_names = [p.display_name for p in self.initial_players]
        await channel.send(f"遊戲開始！玩家：{', '.join(player_names)}\n大小盲注: {self.small_blind}/{self.big_blind}")
        await self._start_hand()

    async def _start_hand(self):
        self.deck = generate_shuffled_deck()
        self.pot = 0
        self.community_cards = []
        self.bets = {p_id: 0 for p_id in self.player_ids}
        self.current_bet = 0
        self.last_raiser = None
        self.hand_over = False
        self.players_acted_this_round = set()
        self.active_players = [p_id for p_id in self.player_ids if self.chips.get(p_id, 0) > 0]

        if len(self.active_players) < 2:
            await self._end_game()
            return

        self.dealer_button_pos = self._get_next_active_player_idx(self.dealer_button_pos)

        sb_player_id, bb_player_id = None, None
        if len(self.active_players) == 2:  # Heads-up
            dealer_idx = self.dealer_button_pos
            sb_player_id = self.initial_players[dealer_idx].id
            bb_idx = self._get_next_active_player_idx(dealer_idx)
            bb_player_id = self.initial_players[bb_idx].id
            self.current_player_idx = dealer_idx
        else:  # 3+ players
            sb_idx = self._get_next_active_player_idx(self.dealer_button_pos)
            sb_player_id = self.initial_players[sb_idx].id
            bb_idx = self._get_next_active_player_idx(sb_idx)
            bb_player_id = self.initial_players[bb_idx].id
            self.current_player_idx = self._get_next_active_player_idx(bb_idx)

        channel = await self.get_channel()
        if sb_player_id:
            sb_amount = min(self.small_blind, self.chips.get(sb_player_id, 0))
            self.chips[sb_player_id] -= sb_amount
            self.bets[sb_player_id] = sb_amount
            self.pot += sb_amount

        if bb_player_id:
            bb_amount = min(self.big_blind, self.chips.get(bb_player_id, 0))
            self.chips[bb_player_id] -= bb_amount
            self.bets[bb_player_id] = bb_amount
            self.pot += bb_amount

        self.current_bet = self.big_blind
        self.last_raiser = bb_player_id

        for p_id in self.active_players:
            hole_cards = [self.deck.pop(), self.deck.pop()]
            self.cog.player_hands[p_id] = hole_cards

        await self._update_game_state_message()
        await self._prompt_for_action()

    async def _handle_action(self, player_id: int, action: str, amount: int = 0):
        player = self.get_player_from_id(player_id)
        channel = await self.get_channel()
        if not player or not channel: return

        self.players_acted_this_round.add(player_id)

        original_bet = self.bets.get(player_id, 0)
        player_chips = self.chips.get(player_id, 0)

        if action == "fold":
            self.active_players.remove(player_id)
            await channel.send(f"{player.display_name} 棄牌了。")
            
        elif action == "check":
            await channel.send(f"{player.display_name} 過牌。")

        elif action == "call":
            to_call = self.current_bet - original_bet
            actual_call = min(to_call, player_chips)
            self.chips[player_id] -= actual_call
            self.bets[player_id] += actual_call
            self.pot += actual_call
            await channel.send(f"{player.display_name} 跟注 {actual_call}。")

        elif action == "raise":
            raise_amount = amount - original_bet
            self.chips[player_id] -= raise_amount
            self.bets[player_id] = amount
            self.pot += raise_amount
            self.current_bet = amount
            self.last_raiser = player_id
            self.players_acted_this_round = {player_id} # Reset acting history
            await channel.send(f"{player.display_name} 加注到 {amount}！")

        elif action == "all_in":
            all_in_amount = player_chips
            self.chips[player_id] = 0
            self.bets[player_id] += all_in_amount
            self.pot += all_in_amount
            if self.bets[player_id] > self.current_bet:
                self.current_bet = self.bets[player_id]
                self.last_raiser = player_id
                self.players_acted_this_round = {player_id} # Reset acting history
            await channel.send(f"{player.display_name} All-in ({all_in_amount})！")
        
        # This check is crucial and must be after the action logic
        if len(self.active_players) <= 1:
            self.hand_over = True
            await self._end_hand()
            return

        await self._update_game_state_message()

        if self._is_betting_round_over():
            await self._progress_to_next_stage()
        else:
            self._move_to_next_player()
            await self._prompt_for_action()

    def _move_to_next_player(self):
        self.current_player_idx = self._get_next_active_player_idx(self.current_player_idx)

    def _is_betting_round_over(self) -> bool:
        if len(self.active_players) <= 1:
            return True

        contenders = [p_id for p_id in self.active_players if self.chips.get(p_id, 0) > 0]
        if not contenders:
            return True # All remaining players are all-in

        # Have all contenders acted?
        if not all(p_id in self.players_acted_this_round for p_id in contenders):
            return False

        # Are all contenders' bets equal?
        first_bet = self.bets.get(contenders[0], 0)
        if not all(self.bets.get(p_id, 0) == first_bet for p_id in contenders):
            return False
        
        # Is the current bet matched?
        if first_bet != self.current_bet:
            return False
            
        return True

    async def _progress_to_next_stage(self):
        channel = await self.get_channel()
        if not channel or self.hand_over: return
        
        # Reset for next betting round
        self.last_raiser = None
        self.players_acted_this_round.clear()
        
        # Set starting player for post-flop
        if len(self.community_cards) > 0: # If not pre-flop
             self.current_player_idx = self._get_next_active_player_idx(self.dealer_button_pos)
        
        # Deal community cards
        if len(self.community_cards) == 0:
            self.community_cards.extend([self.deck.pop() for _ in range(3)])
            await channel.send(f"--- 翻牌圈 ---\n`{' '.join(map(str, self.community_cards))}`")
        elif len(self.community_cards) == 3:
            self.community_cards.append(self.deck.pop())
            await channel.send(f"--- 轉牌圈 ---\n`{' '.join(map(str, self.community_cards))}`")
        elif len(self.community_cards) == 4:
            self.community_cards.append(self.deck.pop())
            await channel.send(f"--- 河牌圈 ---\n`{' '.join(map(str, self.community_cards))}`")
        else: # River betting is over
            await self._handle_showdown()
            return
        
        await self._update_game_state_message()

        # If only one player is not all-in, they don't need to bet against themselves.
        non_all_in_players = [p for p in self.active_players if self.chips.get(p, 0) > 0]
        if len(non_all_in_players) <= 1:
            await asyncio.sleep(1.5) # Pause to show the card
            await self._progress_to_next_stage()
        else:
            await self._prompt_for_action()

    async def _handle_showdown(self):
        self.hand_over = True
        channel = await self.get_channel()
        if not channel: return

        await self._update_game_state_message(show_all=True)
        await channel.send("--- 攤牌 ---")

        final_hands = {}
        for p_id in self.active_players:
            player = self.get_player_from_id(p_id)
            hole_cards = self.cog.player_hands.get(p_id, [])
            if not player or not hole_cards: continue
            
            best_rank, kickers, hand_name, best_hand_cards = evaluate_hand(hole_cards, self.community_cards)
            final_hands[p_id] = (best_rank, kickers, hand_name, best_hand_cards, player.display_name)
            
            hand_str = ' '.join(map(str, hole_cards))
            sorted_best_hand = ' '.join(map(str, best_hand_cards))
            await channel.send(f"{player.display_name} 的手牌: `{hand_str}` ({hand_name}) - 最佳五張: `{sorted_best_hand}`")
            await asyncio.sleep(1)

        # Group players by hand strength (rank and kickers)
        hand_groups = defaultdict(list)
        for p_id, data in final_hands.items():
            rank_tuple = (data[0], tuple(data[1]))
            hand_groups[rank_tuple].append(p_id)

        # Sort groups from best to worst
        sorted_ranks = sorted(hand_groups.keys(), reverse=True)
        
        # Simple pot splitting (no side pots yet)
        if sorted_ranks:
            best_rank_tuple = sorted_ranks[0]
            winner_ids = hand_groups[best_rank_tuple]
            
            if len(winner_ids) > 1:
                # Split pot
                split_amount = self.pot // len(winner_ids)
                remainder = self.pot % len(winner_ids)
                winner_names = []
                for i, winner_id in enumerate(winner_ids):
                    win_amount = split_amount + (1 if i < remainder else 0)
                    self.chips[winner_id] = self.chips.get(winner_id, 0) + win_amount
                    winner_names.append(self.get_player_from_id(winner_id).display_name)
                await channel.send(f"**平手！ {'、'.join(winner_names)} 平分底池 ({self.pot})！** 每人獲得 {split_amount}。")
            else:
                # Single winner
                winner_id = winner_ids[0]
                display_name = final_hands[winner_id][4]
                self.chips[winner_id] = self.chips.get(winner_id, 0) + self.pot
                await channel.send(f"**{display_name} 贏得底池 ({self.pot})！**")

        await self._end_hand(start_next=True)

    async def _prompt_for_action(self):
        if self.hand_over: return

        player_id_to_act = self.initial_players[self.current_player_idx].id
        
        # If the player to act is not active or has no chips, skip them
        if player_id_to_act not in self.active_players or self.chips.get(player_id_to_act, 0) == 0:
            self._move_to_next_player()
            # This can happen if players are all-in. Check if the round is over.
            if self._is_betting_round_over():
                 await self._progress_to_next_stage()
            else: # Recursively find the next actual player
                 await self._prompt_for_action()
            return

        player = self.initial_players[self.current_player_idx]
        channel = await self.get_channel()
        if not player or not channel: return

        view = ActionView(self, player.id, self.cog)
        await channel.send(f"輪到 {player.mention} 了。", view=view)

    async def _update_game_state_message(self, show_all=False):
        channel = await self.get_channel()
        if not channel: return

        embed = discord.Embed(title="德州撲克 - 牌局進行中", color=discord.Color.dark_green())
        community_str = ' '.join(map(str, self.community_cards)) if self.community_cards else "尚未發牌"
        embed.add_field(name=f"公共牌 [{len(self.community_cards)}/5]", value=f"`{community_str}`", inline=False)
        embed.add_field(name="總底池", value=str(self.pot), inline=False)
        
        player_statuses = []
        for i, p in enumerate(self.initial_players):
            if p.id not in self.player_ids: continue

            chip_count = self.chips.get(p.id, 0)
            bet_amount = self.bets.get(p.id, 0)
            status_icons = []
            if i == self.dealer_button_pos: status_icons.append("🔘") # Dealer Button
            
            player_line = ""
            if p.id in self.active_players:
                if p.id == self.initial_players[self.current_player_idx].id and not self.hand_over:
                    status_icons.append("▶️")
                
                hole_cards_str = ""
                if show_all and p.id in self.cog.player_hands:
                     hole_cards_str = f" `{' '.join(map(str,self.cog.player_hands[p.id]))}`"
                
                player_line = f"{p.display_name}: {chip_count} 籌碼{hole_cards_str}"
                if bet_amount > 0:
                    player_line += f" (下注: {bet_amount})"
                if chip_count == 0 and p.id in self.active_players:
                    player_line += " (All-in)"

            else:
                status_icons.append("❌")
                player_line = f"~~{p.display_name}~~ (棄牌)"

            player_statuses.append(" ".join(status_icons) + " " + player_line)

        embed.add_field(name="玩家狀態", value="\n".join(player_statuses), inline=False)
        
        if not self.hand_over:
            current_player = self.initial_players[self.current_player_idx]
            embed.set_footer(text=f"輪到: {current_player.display_name}")
        else:
            embed.set_footer(text="牌局結束")
        
        try:
            if self.game_state_message:
                await self.game_state_message.edit(embed=embed)
            else:
                self.game_state_message = await channel.send(embed=embed)
        except discord.NotFound:
            self.game_state_message = await channel.send(embed=embed)

    async def _end_hand(self, start_next: bool = False):
        if not self.hand_over:
            self.hand_over = True # Ensure hand is marked as over
            
        channel = await self.get_channel()
        if not channel: return

        # If the hand ended because all but one folded
        if not start_next and len(self.active_players) == 1:
            winner_id = self.active_players[0]
            winner = self.get_player_from_id(winner_id)
            if winner:
                self.chips[winner_id] = self.chips.get(winner_id, 0) + self.pot
                await channel.send(f"其他人都棄牌了！**{winner.display_name}** 贏得底池 ({self.pot})。")

        await self._update_game_state_message(show_all=True)

        # Cleanup for next hand
        for p_id in list(self.cog.player_hands.keys()):
            if p_id in self.player_ids:
                del self.cog.player_hands[p_id]
        
        alive_players = [p_id for p_id in self.player_ids if self.chips.get(p_id, 0) > 0]
        if start_next and len(alive_players) >= 2:
            await channel.send("5 秒後將開始下一手牌...", delete_after=4)
            await asyncio.sleep(5)
            await self._start_hand()
        else:
            # End the game if not starting next hand or not enough players
            await self._end_game()

    async def _end_game(self):
        if not self.is_active: return
        self.is_active = False
        
        channel = await self.get_channel()
        if channel:
            await channel.send("**遊戲結束！** 正在結算積分...")

        if not self.cog.points_cog:
            if channel: await channel.send("錯誤：積分系統未連接，無法儲存遊戲結果。")
            return
        
        final_report_lines = ["**積分結算完畢：**"]
        for p in self.initial_players:
            initial_chip_count = self.initial_chips.get(p.id, 0)
            final_chip_count = self.chips.get(p.id, 0)
            delta = final_chip_count - initial_chip_count
            if delta != 0:
                self.cog.points_cog.update_points(p.id, delta)
            
            display_name = p.display_name
            final_report_lines.append(f"{display_name}: {initial_chip_count} -> {final_chip_count} ({delta:+})")

        if channel:
            await channel.send("\n".join(final_report_lines))

        # Remove the game room
        if self.channel_id in self.cog.game_rooms:
            del self.cog.game_rooms[self.channel_id]
