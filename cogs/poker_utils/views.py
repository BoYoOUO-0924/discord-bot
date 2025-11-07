import discord
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .game_room import GameRoom
    from ..poker import Poker

class RaiseModal(discord.ui.Modal, title="加注金額"):
    def __init__(self, room: "GameRoom", player_id: int):
        super().__init__(timeout=60)
        self.room = room
        self.player_id = player_id

        player_chip = self.room.chips.get(player_id, 0)
        player_bet = self.room.bets.get(player_id, 0)
        min_raise_total_bet = self.room.current_bet + self.room.big_blind

        self.amount_input = discord.ui.TextInput(
            label="輸入你的總下注金額",
            placeholder=f"至少為 {min_raise_total_bet}。你有 {player_chip + player_bet} 可用。",
            min_length=1,
            max_length=10,
        )
        self.add_item(self.amount_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            amount = int(self.amount_input.value)
            player_total_chips = self.room.chips.get(self.player_id, 0)
            player_current_bet = self.room.bets.get(self.player_id, 0)
            
            total_new_bet = amount
            chips_to_add = total_new_bet - player_current_bet

            min_raise_bet = self.room.current_bet + self.room.big_blind
            
            if chips_to_add > player_total_chips:
                await interaction.followup.send(f"你的籌碼不足！你只有 {player_total_chips} 可用來加注。", ephemeral=True)
                return

            if total_new_bet < min_raise_bet and (player_total_chips > chips_to_add):
                 await interaction.followup.send(f"加注金額太低。總下注額至少需要為 {min_raise_bet}，除非你選擇 All-in。", ephemeral=True)
                 return

            await self.room._handle_action(self.player_id, "raise", amount=total_new_bet)
            await interaction.followup.send("加注已處理。", ephemeral=True)

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
        player_bet = self.room.bets.get(self.player_id, 0)
        player_chips = self.room.chips.get(self.player_id, 0)
        to_call = self.room.current_bet - player_bet

        can_check = (to_call == 0)
        can_call = (player_chips >= to_call and to_call > 0)
        
        self.call_check_button.label = "過牌" if can_check else f"跟注 {to_call}"
        self.call_check_button.disabled = not (can_check or can_call)
        
        can_raise = player_chips > to_call
        self.raise_bet_button.disabled = not can_raise

        self.all_in_button.label = f"All-in ({player_chips})"
        self.all_in_button.disabled = not (player_chips > 0)

    @discord.ui.button(label="棄牌", style=discord.ButtonStyle.red, row=0)
    async def fold_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.player_id:
            await interaction.response.send_message("現在不是你的回合。", ephemeral=True)
            return
        
        await interaction.response.edit_message(view=None)
        await self.room._handle_action(self.player_id, "fold")

    @discord.ui.button(label="過牌/跟注", style=discord.ButtonStyle.grey, row=0)
    async def call_check_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.player_id:
            await interaction.response.send_message("現在不是你的回合。", ephemeral=True)
            return
        
        to_call = self.room.current_bet - self.room.bets.get(self.player_id, 0)
        action = "check" if to_call == 0 else "call"

        await interaction.response.edit_message(view=None)
        await self.room._handle_action(self.player_id, action)

    @discord.ui.button(label="加注", style=discord.ButtonStyle.green, row=0)
    async def raise_bet_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.player_id:
            await interaction.response.send_message("現在不是你的回合。", ephemeral=True)
            return

        modal = RaiseModal(self.room, self.player_id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="查看手牌", style=discord.ButtonStyle.secondary, row=1)
    async def view_hand_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.player_id:
            await interaction.response.send_message("現在不是你的回合。", ephemeral=True)
            return
        
        hand = self.cog.player_hands.get(self.player_id)
        if hand:
            hand_str = ' '.join(map(str, hand))
            await interaction.response.send_message(f"你的手牌: `{hand_str}`", ephemeral=True)
        else:
            await interaction.response.send_message("找不到你的手牌。", ephemeral=True)

    @discord.ui.button(label="All-in", style=discord.ButtonStyle.blurple, row=1)
    async def all_in_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.player_id:
            await interaction.response.send_message("現在不是你的回合。", ephemeral=True)
            return
        
        await interaction.response.edit_message(view=None)
        await self.room._handle_action(self.player_id, "all_in")

class LobbyView(discord.ui.View):
    def __init__(self, cog: "Poker"):
        super().__init__(timeout=3600)
        self.cog = cog

    async def _update_lobby_message(self, interaction: discord.Interaction):
        lobby = self.cog.lobbies.get(interaction.channel_id)
        if not lobby:
            embed = discord.Embed(title="撲克大廳已關閉", color=discord.Color.dark_grey())
            try:
                await interaction.message.edit(embed=embed, view=None)
            except discord.NotFound:
                pass
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

        if not self.cog.points_cog or self.cog.points_cog.get_points(interaction.user.id) <= 0:
             await interaction.response.send_message("你的積分不足，無法加入遊戲。", ephemeral=True)
             return

        lobby["players"].append(interaction.user)
        await interaction.response.defer()
        await self._update_lobby_message(interaction)
        await interaction.channel.send(f"{interaction.user.mention} 已加入遊戲。", allowed_mentions=discord.AllowedMentions.none(), delete_after=5)

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
        
        self.stop()
        
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
        
        self.stop()
        del self.cog.lobbies[interaction.channel_id]
        
        embed = discord.Embed(title="撲克大廳已取消", description=f"由 {interaction.user.mention} 操作。", color=discord.Color.red())
        await interaction.response.edit_message(embed=embed, view=None)
