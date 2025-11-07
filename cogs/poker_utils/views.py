
import discord
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .game_room import GameRoom
    from ..poker import Poker

# ... (existing RaiseModal and ActionView code remains unchanged) ...

class RaiseModal(discord.ui.Modal, title="加注金額"):
    def __init__(self, room: "GameRoom", player_id: int, cog: "Poker", to_call: int):
        super().__init__(timeout=60)
        self.room = room
        self.player_id = player_id
        self.cog = cog
        self.to_call = to_call
        
        player_chip = self.room.chips.get(player_id, 0)
        min_raise = self.room.current_bet - self.room.bets.get(player_id, 0) + self.room.big_blind
        
        self.amount_input = discord.ui.TextInput(
            label="輸入你的總下注金額",
            placeholder=f"至少為 {self.room.current_bet + self.room.big_blind}。你有 {player_chip} 籌碼。",
            min_length=1,
            max_length=10
        )
        self.add_item(self.amount_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            amount = int(self.amount_input.value)
            player_chip = self.room.chips.get(self.player_id, 0)
            player_current_bet = self.room.bets.get(self.player_id, 0)
            
            # The amount entered is the TOTAL bet the player wants to make
            total_new_bet = amount
            
            # Amount to add to the pot
            additional_bet = total_new_bet - player_current_bet

            # Minimum valid total bet is current_bet + one big blind, unless all-in
            min_raise_bet = self.room.current_bet + self.room.big_blind
            
            if total_new_bet < min_raise_bet and total_new_bet < player_chip + player_current_bet:
                await interaction.followup.send(f"加注金額太低。總下注額至少需要為 {min_raise_bet}。", ephemeral=True)
                return

            if additional_bet > player_chip:
                await interaction.followup.send(f"你的籌碼不足！你只有 {player_chip}。你的當前下注是 {player_current_bet}。", ephemeral=True)
                return

            await self.room._handle_action(self.player_id, "raise", amount=additional_bet, bot=self.cog.bot, cog=self.cog)
        
        except ValueError:
            await interaction.followup.send("請輸入有效的數字。", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"處理加注時發生錯誤: {e}", ephemeral=True)


class ActionView(discord.ui.View):
    def __init__(self, room: "GameRoom", player_id: int, cog: "Poker"):
        super().__init__(timeout=180)
        self.room = room
        self.player_id = player_id
        self.cog = cog
        self._update_buttons()

    def _update_buttons(self):
        """Disables buttons based on the game state for the current player."""
        player_bet = self.room.bets.get(self.player_id, 0)
        to_call = self.room.current_bet - player_bet
        player_can_check = (to_call == 0)
        player_can_call = (to_call > 0) and (self.room.chips.get(self.player_id, 0) > to_call)
        player_can_all_in = self.room.chips.get(self.player_id, 0) > 0

        # Update check/call button
        self.call_check_button.label = "過牌" if player_can_check else f"跟注 {to_call}"
        self.call_check_button.disabled = not (player_can_check or player_can_call)

        # Update bet/raise button
        is_first_action = (self.room.current_bet == self.room.big_blind and player_bet == self.room.big_blind) or self.room.current_bet == 0
        self.raise_bet_button.label = "下注" if is_first_action else "加注"

        # A player can always raise or bet if they have chips left
        self.raise_bet_button.disabled = not player_can_all_in
        
        # All-in button is always an option if the player has chips
        self.all_in_button.disabled = not player_can_all_in
        self.all_in_button.label = f"All-in ({self.room.chips.get(self.player_id, 0)})"


    @discord.ui.button(label="棄牌", style=discord.ButtonStyle.red, row=0)
    async def fold_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.player_id:
            await interaction.response.send_message("現在不是你的回合。", ephemeral=True)
            return
        await interaction.response.defer()
        await self.room._handle_action(self.player_id, "fold", bot=self.cog.bot, cog=self.cog)


    @discord.ui.button(label="過牌/跟注", style=discord.ButtonStyle.grey, row=0)
    async def call_check_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.player_id:
            await interaction.response.send_message("現在不是你的回合。", ephemeral=True)
            return
        
        await interaction.response.defer()
        
        action = "check" if "過牌" in button.label else "call"
        await self.room._handle_action(self.player_id, action, bot=self.cog.bot, cog=self.cog)

    @discord.ui.button(label="下注/加注", style=discord.ButtonStyle.green, row=0)
    async def raise_bet_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.player_id:
            await interaction.response.send_message("現在不是你的回合。", ephemeral=True)
            return
            
        player_bet = self.room.bets.get(self.player_id, 0)
        to_call = self.room.current_bet - player_bet
        modal = RaiseModal(self.room, self.player_id, self.cog, to_call)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="All-in", style=discord.ButtonStyle.blurple, row=1)
    async def all_in_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.player_id:
            await interaction.response.send_message("現在不是你的回合。", ephemeral=True)
            return
        await interaction.response.defer()
        await self.room._handle_action(self.player_id, "all_in", bot=self.cog.bot, cog=self.cog)


class LobbyView(discord.ui.View):
    def __init__(self, cog: "Poker"):
        super().__init__(timeout=3600)  # Lobby can stay open for a while
        self.cog = cog

    async def _update_lobby_message(self, interaction: discord.Interaction):
        """Helper to update the original lobby message with the current player list."""
        lobby = self.cog.lobbies.get(interaction.channel_id)
        if not lobby:
            # The lobby has been closed, edit the message to reflect that.
            embed = discord.Embed(title="撲克大廳已關閉", color=discord.Color.dark_grey())
            await interaction.message.edit(embed=embed, view=None)
            return

        embed = interaction.message.embeds[0]
        embed.description = "目前的玩家:\n" + "\n".join(f"- {player.mention}" for player in lobby["players"])
        await interaction.message.edit(embed=embed)

    @discord.ui.button(label="加入遊戲", style=discord.ButtonStyle.success)
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        lobby = self.cog.lobbies.get(interaction.channel_id)
        if not lobby:
            await interaction.response.send_message("這個大廳已經關閉了。", ephemeral=True)
            return

        if interaction.user in lobby["players"]:
            await interaction.response.send_message("你已經在大廳裡了。", ephemeral=True)
            return

        player_points = self.cog.points_cog.get_points(interaction.user.id)
        if player_points <= 0:
            await interaction.response.send_message(f"你的積分不足（目前為 {player_points}），無法加入遊戲。", ephemeral=True)
            return

        lobby["players"].append(interaction.user)
        await interaction.response.defer() # Acknowledge the interaction
        await self._update_lobby_message(interaction)
        await interaction.channel.send(f"{interaction.user.mention} 已加入遊戲。", allowed_mentions=discord.AllowedMentions.none())


    @discord.ui.button(label="開始遊戲", style=discord.ButtonStyle.primary)
    async def start_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        lobby = self.cog.lobbies.get(interaction.channel_id)
        if not lobby:
            await interaction.response.send_message("這個大廳已經關閉了。", ephemeral=True)
            return
            
        if interaction.user != lobby["host"]:
            await interaction.response.send_message("只有大廳創建者可以開始遊戲。", ephemeral=True)
            return

        if len(lobby["players"]) < 2:
            await interaction.response.send_message("玩家人數不足 2 人，無法開始遊戲。", ephemeral=True)
            return
        
        # Stop this view and confirm
        self.stop()
        
        # Transition to the game
        await interaction.response.defer()
        await self.cog._start_game_from_lobby(lobby, interaction.channel)


    @discord.ui.button(label="取消大廳", style=discord.ButtonStyle.danger)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        lobby = self.cog.lobbies.get(interaction.channel_id)
        if not lobby:
            await interaction.response.send_message("這個大廳已經關閉了。", ephemeral=True)
            return
            
        if interaction.user != lobby["host"]:
            await interaction.response.send_message("只有大廳創建者可以取消大廳。", ephemeral=True)
            return
        
        # Stop this view
        self.stop()
        
        # Remove the lobby
        del self.cog.lobbies[interaction.channel_id]
        
        embed = discord.Embed(title="撲克大廳已取消", description=f"由 {interaction.user.mention} 操作。", color=discord.Color.red())
        await interaction.response.edit_message(embed=embed, view=None)

