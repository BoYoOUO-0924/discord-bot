import discord
from discord.ext import commands

# --- UI View for Buttons ---
class HelpView(discord.ui.View):
    def __init__(self, cog, prefix: str):
        super().__init__(timeout=180.0)  # 3 minutes timeout
        self.cog = cog
        self.prefix = prefix
        self.message = None

    async def on_timeout(self):
        if self.message:
            for item in self.children:
                item.disabled = True
            await self.message.edit(view=self)

    @discord.ui.button(label="首頁", style=discord.ButtonStyle.green, emoji="🏠")
    async def home_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = self.cog._get_main_help_embed(self.prefix, interaction.client.user)
        await interaction.response.edit_message(embed=embed)

    @discord.ui.button(label="通用", style=discord.ButtonStyle.secondary, emoji="🔧")
    async def general_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = self.cog._get_general_help_embed(self.prefix)
        await interaction.response.edit_message(embed=embed)

    @discord.ui.button(label="遊戲", style=discord.ButtonStyle.secondary, emoji="🎮")
    async def game_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = self.cog._get_game_help_embed(self.prefix)
        await interaction.response.edit_message(embed=embed)

# --- Cog with Embed Generation and Command ---
class HelpCog(commands.Cog):
    """一個可分類的互動式幫助指令。"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _get_main_help_embed(self, prefix: str, bot_user: discord.ClientUser) -> discord.Embed:
        embed = discord.Embed(
            title=f'{bot_user.name} 指令選單',
            description=f"歡迎使用！請點擊下方按鈕來瀏覽不同類別的指令。\n目前指令前綴為 `{prefix}`",
            color=discord.Color.blurple()
        )
        if bot_user.avatar:
            embed.set_thumbnail(url=bot_user.avatar.url)
        embed.add_field(
            name="導覽",
            value=(
                "- `🏠 首頁`: 回到這個主畫面。\n"
                "- `🔧 通用`: 查看日常實用指令。\n"
                "- `🎮 遊戲`: 尋找所有可玩的遊戲。"
            ),
            inline=False
        )
        embed.set_footer(text="選單將在 3 分鐘後自動失效。")
        return embed

    def _get_general_help_embed(self, prefix: str) -> discord.Embed:
        embed = discord.Embed(title='🔧 通用指令 (General)', description="日常使用的實用工具。", color=0x2ECC71)
        embed.add_field(name=f'{prefix}checkin', value='✨ **每日簽到**: 獲取每日積分獎勵，連續簽到有加成！', inline=False)
        embed.add_field(name=f'{prefix}point', value='💰 **查詢積分**: 查詢你目前擁有的積分總額。', inline=False)
        embed.add_field(name=f'{prefix}clear [數量]', value='🧹 **清除訊息**: 清除頻道訊息(預設10則)，僅限管理員。', inline=False)
        return embed

    def _get_game_help_embed(self, prefix: str) -> discord.Embed:
        embed = discord.Embed(title='🎮 遊戲指令 (Game)', description="與朋友們一起同樂！", color=0xE67E22)
        embed.add_field(
            name='猜數字 (Guess Number)',
            value=f'`{prefix}start_guess`: 開始一場新的猜數字遊戲。\n`{prefix}stop_guess`: 放棄當前的遊戲。\n*提示：遊戲開始後，直接在頻道輸入數字即可猜測！*',
            inline=False
        )
        embed.add_field(
            name='德州撲克 (Texas Hold\'em)',
            value=f'`{prefix}poker [大盲注]`: 創建一個撲克遊戲大廳，玩家可透過按鈕加入或開始。\n`{prefix}stopgame`: 強制結束遊戲或關閉大廳。',
            inline=False
        )
        embed.add_field(
            name='21點 (Blackjack)',
            value=f'`{prefix}blackjack [賭注]`: 開始一局21點。\n`{prefix}hit`: 要牌。\n`{prefix}stand`: 停牌。\n*遊戲中可透過按鈕進行互動。*',
            inline=False
        )
        embed.add_field(
            name='井字遊戲 (Tic-Tac-Toe)',
            value=f'`{prefix}tictactoe @對手`: 開始一場井字遊戲，透過按鈕互動。',
            inline=False
        )
        return embed

    @commands.command(name='help', help='顯示互動式幫助選單。')
    async def help_command(self, ctx: commands.Context):
        prefix = '!'
        embed = self._get_main_help_embed(prefix, self.bot.user)
        view = HelpView(self, prefix)
        view.message = await ctx.send(embed=embed, view=view)

async def setup(bot):
    bot.remove_command('help')
    await bot.add_cog(HelpCog(bot))
