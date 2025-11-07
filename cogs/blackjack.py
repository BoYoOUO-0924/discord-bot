# cogs/blackjack.py
import discord
from discord.ext import commands
import random
from typing import Union

# --- Helper Functions ---
def build_shuffled_deck():
    ranks = ['A', '2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K']
    suits = ['♠', '♥', '♦', '♣']
    return random.sample([(r, s) for s in suits for r in ranks], 52)

def hand_value(cards):
    value_map = {'A': 11, 'K': 10, 'Q': 10, 'J': 10, '10': 10, '9': 9, '8': 8, '7': 7, '6': 6, '5': 5, '4': 4, '3': 3, '2': 2}
    total, aces = 0, 0
    for r, _ in cards:
        if r == 'A': aces += 1
        total += value_map[r]
    while total > 21 and aces > 0:
        total -= 10
        aces -= 1
    return total

def render_cards(cards):
    return ' '.join([f"{r}{s}" for r, s in cards]) if cards else '(無)'

# --- Views ---
class BlackjackView(discord.ui.View):
    def __init__(self, cog: commands.Cog, owner_id: int):
        super().__init__(timeout=300)
        self.cog = cog
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message('只有開局者可以操作本局按鈕。', ephemeral=True)
            return False
        return True

    @discord.ui.button(label='Hit 要牌', style=discord.ButtonStyle.primary)
    async def hit_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_hit(interaction)

    @discord.ui.button(label='Stand 停牌', style=discord.ButtonStyle.secondary)
    async def stand_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_stand(interaction)

class PlayAgainView(discord.ui.View):
    """A view with a 'Play Again' button that carries over the previous bet."""
    def __init__(self, cog: commands.Cog, owner_id: int, previous_bet: int):
        super().__init__(timeout=300)
        self.cog = cog
        self.owner_id = owner_id
        self.previous_bet = previous_bet

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message('只有開局者可以再玩一局。', ephemeral=True)
            return False
        return True

    @discord.ui.button(label="再來一局 (相同賭注)", style=discord.ButtonStyle.success)
    async def play_again_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Disable the button to prevent multiple clicks
        self.stop()
        button.disabled = True
        button.label = "正在開始新的一局..."
        await interaction.response.edit_message(view=self)
        # Call the cog to start a new game
        await self.cog.start_new_game(interaction, bet=self.previous_bet)

# --- Main Cog ---
class BlackjackCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.tables = {}
        self.points_cog = None

    @commands.Cog.listener()
    async def on_ready(self):
        self.points_cog = self.bot.get_cog('Points')
        if not self.points_cog:
            print("Error: PointsCog not found in BlackjackCog.")

    @commands.command(name='blackjack', help='開始一局 21 點。可加上賭注：!blackjack 100')
    async def blackjack(self, ctx: commands.Context, bet: int = 0):
        await self.start_new_game(ctx, bet)

    async def start_new_game(self, ctx_or_interaction: Union[commands.Context, discord.Interaction], bet: int):
        is_interaction = isinstance(ctx_or_interaction, discord.Interaction)
        author = ctx_or_interaction.user if is_interaction else ctx_or_interaction.author
        channel = ctx_or_interaction.channel

        if not self.points_cog:
            return await channel.send("積分系統目前無法使用，請聯絡管理員。")
        
        if bet < 0:
            return await channel.send('賭注必須是非負整數。')

        player_points = self.points_cog.get_points(author.id)
        if bet > player_points:
            return await channel.send(f'你的積分不足。目前餘額：{player_points}')

        deck = build_shuffled_deck()
        player_hand = [deck.pop(), deck.pop()]
        dealer_hand = [deck.pop(), deck.pop()]

        self.tables[channel.id] = {
            'deck': deck, 'player_hand': player_hand, 'dealer_hand': dealer_hand,
            'bet': bet, 'owner_id': author.id, 'finished': False
        }

        p_val = hand_value(player_hand)
        embed: discord.Embed
        view: discord.ui.View

        if p_val == 21:  # Natural Blackjack
            embed = self._build_final_embed(channel.id)
            view = PlayAgainView(self, author.id, bet)
        else:
            embed = self._build_status_embed(self.tables[channel.id])
            view = BlackjackView(self, author.id)

        if is_interaction:
            # The original message with the play again button has been edited, 
            # so we need to send a new message for the new game.
            await channel.send(embed=embed, view=view)
        else:
            await channel.send(embed=embed, view=view)

    async def handle_hit(self, interaction: discord.Interaction):
        table = self.tables.get(interaction.channel.id)
        if not table or table.get('finished'):
            return await interaction.response.send_message('本局已結束或不存在。', ephemeral=True)

        table['player_hand'].append(table['deck'].pop())

        if hand_value(table['player_hand']) > 21:  # Player busts
            final_embed = self._build_final_embed(interaction.channel.id)
            view = PlayAgainView(self, table['owner_id'], table['bet'])
            await interaction.response.edit_message(embed=final_embed, view=view)
        else:
            embed = self._build_status_embed(table)
            await interaction.response.edit_message(embed=embed)

    async def handle_stand(self, interaction: discord.Interaction):
        table = self.tables.get(interaction.channel.id)
        if not table or table.get('finished'):
            return await interaction.response.send_message('本局已結束或不存在。', ephemeral=True)
        
        while hand_value(table['dealer_hand']) < 17:
            table['dealer_hand'].append(table['deck'].pop())
        
        final_embed = self._build_final_embed(interaction.channel.id)
        view = PlayAgainView(self, table['owner_id'], table['bet'])
        await interaction.response.edit_message(embed=final_embed, view=view)

    def _decide_result(self, p_val, d_val):
        if p_val > 21: return 'lose'
        if d_val > 21 or p_val > d_val: return 'win'
        if p_val < d_val: return 'lose'
        return 'push'

    def _build_status_embed(self, table, reveal_dealer=False) -> discord.Embed:
        embed = discord.Embed(title='Blackjack', color=0x00bcd4)
        embed.add_field(name="你的手牌", value=f"{render_cards(table['player_hand'])} ({hand_value(table['player_hand'])})", inline=False)
        d_text = f"{render_cards([table['dealer_hand'][0]])} 🂠" if not reveal_dealer else f"{render_cards(table['dealer_hand'])} ({hand_value(table['dealer_hand'])})"
        embed.add_field(name="莊家手牌", value=d_text, inline=False)
        if table['bet'] > 0: embed.set_footer(text=f"賭注: {table['bet']}")
        return embed

    def _build_final_embed(self, channel_id: int) -> discord.Embed:
        table = self.tables.get(channel_id)
        if not table or table.get('finished'): 
            return self._build_status_embed(table, reveal_dealer=True)

        p_val = hand_value(table['player_hand'])
        d_val = hand_value(table['dealer_hand'])
        result = self._decide_result(p_val, d_val)
        owner_id, bet = table['owner_id'], table['bet']
        delta = bet if result == 'win' else -bet if result == 'lose' else 0

        if bet > 0 and delta != 0:
            self.points_cog.update_points(owner_id, delta)
        
        table['finished'] = True
        new_total = self.points_cog.get_points(owner_id)

        title, color = {'win': ('你贏了！🎉', 0x43a047), 'lose': ('你輸了。', 0xe53935), 'push': ('平手。', 0x9e9e9e)}[result]
        embed = self._build_status_embed(table, reveal_dealer=True)
        embed.title = title
        embed.color = color
        
        footer_text = f"賭注: {bet} / 淨得: {delta:+} | 目前積分: {new_total}" if bet > 0 else f"目前積分: {new_total}"
        embed.set_footer(text=footer_text)
        return embed

async def setup(bot):
    await bot.add_cog(BlackjackCog(bot))
