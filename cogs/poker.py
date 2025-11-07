import discord
from discord.ext import commands
from typing import Dict

from .poker_utils.game_room import GameRoom
from .poker_utils.views import LobbyView # Import the new LobbyView

class Poker(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.lobbies: Dict[int, Dict] = {}
        self.game_rooms: Dict[int, GameRoom] = {}
        self.points_cog = None

    @commands.Cog.listener()
    async def on_ready(self):
        self.points_cog = self.bot.get_cog('Points')
        if not self.points_cog:
            print("Error: PointsCog not found in Poker. Make sure it is loaded.")

    @commands.command(name="poker", help="創建一個帶有互動按鈕的德州撲克大廳。")
    @commands.guild_only()
    async def poker(self, ctx: commands.Context, big_blind: int = 20):
        if not self.points_cog:
            await ctx.send("積分系統目前無法使用，請聯絡管理員。")
            return

        if ctx.channel.id in self.game_rooms or ctx.channel.id in self.lobbies:
            await ctx.send("此頻道已經有正在進行的遊戲或已創建大廳。")
            return

        player_points = self.points_cog.get_points(ctx.author.id)
        if player_points <= 0:
            await ctx.send(f"{ctx.author.mention}, 你的積分不足（目前為 {player_points}），無法創建遊戲。")
            return
        
        # Create the lobby data structure
        self.lobbies[ctx.channel.id] = {
            "host": ctx.author,
            "players": [ctx.author],
            "big_blind": big_blind
        }

        # Create the Embed and View
        embed = discord.Embed(
            title="🎲 德州撲克大廳已創建！",
            color=discord.Color.blue()
        )
        embed.add_field(name="房主", value=ctx.author.mention, inline=False)
        embed.add_field(name="大盲注", value=str(big_blind), inline=False)
        embed.description = "目前的玩家:\n- {}".format(ctx.author.mention)

        # Send the message with the Embed and the View
        await ctx.send(embed=embed, view=LobbyView(self))

    async def _start_game_from_lobby(self, lobby: dict, channel: discord.TextChannel):
        """Internal function to transition from a lobby to a game room."""
        if not self.points_cog:
            await channel.send("錯誤：無法啟動遊戲，積分系統未載入。")
            return
        
        initial_players = lobby["players"]
        big_blind = lobby["big_blind"]
        small_blind = big_blind // 2
        
        initial_chips = {p.id: self.points_cog.get_points(p.id) for p in initial_players}

        # Clean up the lobby
        if channel.id in self.lobbies:
            del self.lobbies[channel.id]
        
        # Create and start the game room
        room = GameRoom(
            bot=self.bot, 
            cog=self, 
            channel_id=channel.id,
            players=initial_players, 
            chips=initial_chips,
            small_blind=small_blind, 
            big_blind=big_blind
        )
        self.game_rooms[channel.id] = room
        await room.start_game()

    @commands.command(name="stopgame", help="停止當前頻道的撲克遊戲或關閉大廳。")
    @commands.guild_only()
    async def stopgame(self, ctx: commands.Context):
        # This command can now also be used to forcefully close a button-based lobby
        if ctx.channel.id in self.lobbies:
            del self.lobbies[ctx.channel.id]
            # Optionally, find the original message and disable the view
            # This is more complex, for now just deleting the lobby data is enough.
            await ctx.send("遊戲大廳已由管理員強制關閉。")
            return
            
        room = self.game_rooms.get(ctx.channel.id)
        if room and room.is_active:
            await room.stop_game("遊戲已由管理員強制結束。")
        else:
            await ctx.send("這個頻道沒有正在進行的遊戲或等待中的大廳。")


async def setup(bot):
    await bot.add_cog(Poker(bot))
