import discord
from discord.ext import commands
from typing import Dict, Optional, List
import random  # <--- 引入 random 模組

# 遊戲邏輯核心，儲存每個遊戲的狀態
class TicTacToeGame:
    def __init__(self, player1: discord.User, player2: discord.User):
        self.player1 = player1  # X
        self.player2 = player2  # O
        self.board = [0] * 9  # 0: empty, 1: X, 2: O
        # --- 核心改動：隨機決定先手 --- #
        self.current_turn = random.choice([player1, player2])
        self.winner = None  # None: in progress, 1: P1 wins, 2: P2 wins, 3: Draw

    def make_move(self, player: discord.User, position: int) -> bool:
        if player != self.current_turn or self.winner is not None:
            return False
        if not (0 <= position < 9) or self.board[position] != 0:
            return False

        # 更新棋盤
        self.board[position] = 1 if self.current_turn == self.player1 else 2

        # 檢查勝負
        if self.check_win():
            self.winner = 1 if self.current_turn == self.player1 else 2
        elif 0 not in self.board:
            self.winner = 3  # Draw

        # 交換回合
        if self.winner is None:
            self.current_turn = self.player2 if self.current_turn == self.player1 else self.player1
        
        return True

    def check_win(self) -> bool:
        b = self.board
        win_conditions = [
            (0, 1, 2), (3, 4, 5), (6, 7, 8),  # Horizontal
            (0, 3, 6), (1, 4, 7), (2, 5, 8),  # Vertical
            (0, 4, 8), (2, 4, 6)             # Diagonal
        ]
        player_symbol = 1 if self.current_turn == self.player1 else 2
        for c in win_conditions:
            if b[c[0]] == b[c[1]] == b[c[2]] == player_symbol:
                return True
        return False

# UI 視圖，包含棋盤按鈕
class GameBoardView(discord.ui.View):
    def __init__(self, game: TicTacToeGame, cog: "TicTacToe"):
        super().__init__(timeout=180) # 3 分鐘無動作自動結束
        self.game = game
        self.cog = cog
        self.create_board_buttons()

    def create_board_buttons(self):
        for i in range(9):
            button = discord.ui.Button(
                style=discord.ButtonStyle.secondary,
                label="\u200b", # 空白字元
                row=i // 3,
                custom_id=f"tictactoe_{i}"
            )
            button.callback = self.button_callback
            self.add_item(button)

    async def button_callback(self, interaction: discord.Interaction):
        # 檢查是否為遊戲玩家
        if interaction.user not in (self.game.player1, self.game.player2):
            await interaction.response.send_message("這不是你的遊戲！", ephemeral=True)
            return

        # 檢查是否輪到該玩家
        if interaction.user != self.game.current_turn:
            await interaction.response.send_message("現在不是你的回合！", ephemeral=True)
            return

        # 進行移動
        position = int(interaction.data["custom_id"].split("_")[-1])
        if not self.game.make_move(interaction.user, position):
            await interaction.response.send_message("無效的移動！", ephemeral=True)
            return

        # 更新被點擊的按鈕
        button = discord.utils.get(self.children, custom_id=f"tictactoe_{position}")
        if self.game.board[position] == 1:
            button.style = discord.ButtonStyle.primary
            button.label = "X"
        else:
            button.style = discord.ButtonStyle.success
            button.label = "O"
        button.disabled = True
        
        # 判斷遊戲是否結束
        if self.game.winner is not None:
            # 停用所有按鈕
            for child in self.children:
                child.disabled = True

            # 產生結束訊息
            if self.game.winner == 3:
                embed = discord.Embed(title="井字遊戲結束！", description="平手！", color=discord.Color.gold())
            else:
                winner_user = self.game.player1 if self.game.winner == 1 else self.game.player2
                embed = discord.Embed(title="井字遊戲結束！", description=f"恭喜 {winner_user.mention} 獲勝！", color=discord.Color.green())
            
            # 從追蹤中移除遊戲
            if interaction.channel.id in self.cog.games:
                del self.cog.games[interaction.channel.id]
            
            await interaction.response.edit_message(embed=embed, view=self)
            self.stop()
        else:
            # 更新輪到誰的提示
            embed = interaction.message.embeds[0]
            embed.description = f"{self.game.player1.mention} (X) vs {self.game.player2.mention} (O)\n\n輪到 {self.game.current_turn.mention}！"
            await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self):
        # 超時處理
        if self.game and hasattr(self, 'message') and self.message:
             if self.message.channel.id in self.cog.games:
                del self.cog.games[self.message.channel.id]

             # 停用所有按鈕
             for item in self.children:
                item.disabled = True

             embed = discord.Embed(title="井字遊戲超時", description="遊戲因長時間無動作已自動結束。", color=discord.Color.orange())
             await self.message.edit(embed=embed, view=self)

class TicTacToe(commands.Cog):
    """井字遊戲功能"""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.games: Dict[int, TicTacToeGame] = {}

    @commands.command(name="tictactoe", aliases=['ttt', '井字遊戲'], help="開始一場井字遊戲。用法：`!tictactoe @對手`")
    async def start_game(self, ctx: commands.Context, opponent: discord.Member):
        if ctx.channel.id in self.games:
            await ctx.send("這個頻道已經在進行一場遊戲了！")
            return

        player1 = ctx.author
        player2 = opponent

        if player1 == player2:
            await ctx.send("你不能挑戰自己！")
            return
        if player2.bot:
            await ctx.send("你不能挑戰一個機器人！")
            return

        game = TicTacToeGame(player1, player2)
        self.games[ctx.channel.id] = game
        view = GameBoardView(game, self)

        embed = discord.Embed(
            title="井字遊戲開始！",
            description=f"{player1.mention} (X) vs {player2.mention} (O)\n\n**由 {game.current_turn.mention} 先手！**",
            color=discord.Color.blue()
        )
        embed.set_footer(text="直接點擊下方按鈕即可下棋。")
        
        message = await ctx.send(embed=embed, view=view)
        view.message = message # 儲存 message 以便於超時中使用

    @commands.command(name="stoptictactoe", aliases=['sttt', '結束井字遊戲'], help="強制結束目前的井字遊戲。")
    @commands.has_permissions(manage_messages=True)
    async def stop_game(self, ctx: commands.Context):
        if ctx.channel.id in self.games:
            # 找到對應的 message 並停用 view
            game_to_stop = self.games[ctx.channel.id]
            # This is a bit tricky without a message reference. We assume the game view has a message.
            # This part is still weak, but let's try to find the message.
            async for message in ctx.channel.history(limit=50):
                if message.author == self.bot.user and message.embeds and "井字遊戲" in message.embeds[0].title:
                    if f"{game_to_stop.player1.mention}" in message.embeds[0].description:
                        try:
                            view = GameBoardView.from_message(message)
                            for item in view.children:
                                item.disabled = True
                            embed = message.embeds[0]
                            embed.title = "遊戲已強制結束"
                            embed.color = discord.Color.red()
                            await message.edit(embed=embed, view=view)
                            await ctx.send("遊戲已由管理員強制結束。")
                            break
                        except Exception:
                            continue # Not a view message or already timed out
            
            del self.games[ctx.channel.id]
        else:
            await ctx.send("這個頻道沒有正在進行的遊戲。")

    @start_game.error
    async def tictactoe_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("請指定一位你要挑戰的對手！用法：`!tictactoe @對手`")
        else:
            print(f"TicTacToe Error: {error}") # 打印錯誤以供調試
            await ctx.send(f"發生未知錯誤，請檢查控制台紀錄。")

async def setup(bot: commands.Bot):
    await bot.add_cog(TicTacToe(bot))
