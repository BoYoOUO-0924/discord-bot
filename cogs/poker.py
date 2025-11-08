import discord
from discord.ext import commands
from typing import Dict, Optional

from .poker_utils.game_room import GameRoom
from .poker_utils.views import LobbyView

class Poker(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.lobbies: Dict[int, Dict] = {}
        self.game_rooms: Dict[int, GameRoom] = {}
        self.player_hands: Dict = {}

    @property
    def points_cog(self) -> Optional[commands.Cog]:
        """透過屬性即時、安全地獲取 Points cog。"""
        return self.bot.get_cog('Points')

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
        
        self.lobbies[ctx.channel.id] = {
            "host": ctx.author,
            "players": [ctx.author],
            "big_blind": big_blind
        }

        embed = discord.Embed(
            title="🎲 德州撲克大廳已創建！",
            color=discord.Color.blue()
        )
        embed.add_field(name="房主", value=ctx.author.mention, inline=False)
        embed.add_field(name="大盲注", value=str(big_blind), inline=False)
        embed.description = "目前的玩家:\n- {}".format(ctx.author.mention)

        await ctx.send(embed=embed, view=LobbyView(self))

    async def _start_game_from_lobby(self, lobby: dict, channel: discord.TextChannel):
        if not self.points_cog:
            await channel.send("錯誤：無法啟動遊戲，積分系統未載入。")
            return
        
        initial_players = lobby["players"]
        big_blind = lobby["big_blind"]
        small_blind = big_blind // 2
        
        initial_chips = {p.id: self.points_cog.get_points(p.id) for p in initial_players}

        if channel.id in self.lobbies:
            del self.lobbies[channel.id]
        
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
        if ctx.channel.id in self.lobbies:
            del self.lobbies[ctx.channel.id]
            await ctx.send("遊戲大廳已由管理員強制關閉。")
            return
            
        room = self.game_rooms.get(ctx.channel.id)
        if room and room.is_active:
            await room._end_game(reason=f"遊戲已由 {ctx.author.mention} 強制結束。")
        else:
            await ctx.send("這個頻道沒有正在進行的遊戲或等待中的大廳。")


async def setup(bot):
    await bot.add_cog(Poker(bot))
