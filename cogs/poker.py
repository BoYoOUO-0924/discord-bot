
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

    @staticmethod
    def get_poker_help_embed(prefix: str) -> discord.Embed:
        embed = discord.Embed(title="♠️♥️ 德州撲克 (Texas Hold'em) 遊戲教學 ♦️♣️",
                              description="目標：用你的 **2張底牌** 和 **5張公共牌**，組合出最強的5張牌組，贏得底池！",
                              color=0xC41E3A) # Poker Red

        embed.add_field(
            name="➡️ 遊戲流程",
            value=f"1. **發起遊戲**: 玩家用 `{prefix}poker [大盲注]` 指令開局。\n"
                  "2. **盲注 (Blinds)**: 遊戲開始時，兩位玩家需強制下注（小盲注和大盲注）。\n"
                  "3. **翻牌前 (Pre-flop)**: 每位玩家拿到2張底牌，第一輪下注開始。\n"
                  "4. **翻牌圈 (Flop)**: 桌上發出3張公共牌，第二輪下注開始。\n"
                  "5. **轉牌圈 (Turn)**: 桌上發出第4張公共牌，第三輪下注開始。\n"
                  "6. **河牌圈 (River)**: 桌上發出第5張公共牌，最終輪下注。\n"
                  "7. **攤牌 (Showdown)**: 所有剩餘玩家開牌，持有最強牌組的玩家贏得所有籌碼！",
            inline=False
        )

        embed.add_field(
            name="💪 玩家操作",
            value="- **跟注 (Call)**: 跟隨前一位玩家的下注額。\n"
                  "- **加注 (Raise)**: 提高當前的下注額。\n"
                  "- **蓋牌 (Fold)**: 放棄這一手牌，輸掉已下注的籌碼。\n"
                  "- **過牌 (Check)**: 在無人下注的情況下，將行動權交給下一位。\n"
                  "- **全下 (All-in)**: 將你剩下的所有籌碼全部下注。",
            inline=False
        )

        embed.add_field(
            name="👑 牌型大小 (由大到小)",
            value=(
                "**皇家同花順 > 同花順 > 四條 > 葫蘆 > 同花 > 順子 > 三條 > 兩對 > 一對 > 高牌**\n\n"
                "- **皇家同花順 (Royal Flush)**: A, K, Q, J, 10 同花色。\n"
                "  `例: ♥A ♥K ♥Q ♥J ♥10`\n"
                "- **同花順 (Straight Flush)**: 連續的五張牌，且花色相同。\n"
                "  `例: ♦9 ♦8 ♦7 ♦6 ♦5`\n"
                "- **四條 (Four of a Kind)**: 四張點數相同的牌。\n"
                "  `例: ♠A ♥A ♦A ♣A ♠K`\n"
                "- **葫蘆 (Full House)**: 一組三條加上一組對子。\n"
                "  `例: ♥K ♠K ♦K ♥7 ♠7`\n"
                "- **同花 (Flush)**: 五張花色相同但不連續的牌。\n"
                "  `例: ♣A ♣Q ♣9 ♣5 ♣2`\n"
                "- **順子 (Straight)**: 五張點數連續但花色不同的牌。\n"
                "  `例: ♥A ♠K ♦Q ♣J ♥10`\n"
                "- **三條 (Three of a Kind)**: 三張點數相同的牌。\n"
                "  `例: ♥Q ♠Q ♦Q ♥9 ♠3`\n"
                "- **兩對 (Two Pair)**: 兩組不同的對子。\n"
                "  `例: ♥J ♠J ♥8 ♠8 ♦K`\n"
                "- **一對 (One Pair)**: 兩張點數相同的牌。\n"
                "  `例: ♦A ♥A ♠Q ♦J ♣5`\n"
                "- **高牌 (High Card)**: 不符合以上任何牌型的牌，由最大的一張牌決定大小。\n"
                "  `例: ♠A ♦Q ♥9 ♣5 ♥2`"
            ),
            inline=False
        )

        embed.add_field(
            name="🚪 結束遊戲",
            value=f"- `{prefix}stopgame`: 由遊戲發起人使用，可強制結束該頻道正在進行的撲克遊戲。",
            inline=False
        )

        embed.set_footer(text="祝您在牌桌上無往不利！")
        return embed

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
