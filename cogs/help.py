
import discord
from discord.ext import commands
from discord import ui
import copy

# --- Modals for Commands with Arguments ---

class CommandModal(ui.Modal):
    def __init__(self, cog, command_name: str, input_label: str, title: str):
        super().__init__(title=title)
        self.cog = cog
        self.command_name = command_name
        self.input_label = input_label
        prefix = self.cog.bot.command_prefix if isinstance(self.cog.bot.command_prefix, str) else '!'
        self.value_input = ui.TextInput(label=input_label, placeholder=f"此處輸入的內容將作為 `{prefix}{command_name}` 的參數。")
        self.add_item(self.value_input)

    async def on_submit(self, interaction: discord.Interaction):
        value = self.value_input.value
        prefix = self.cog.bot.command_prefix if isinstance(self.cog.bot.command_prefix, str) else '!'
        fake_message = copy.copy(interaction.message)
        fake_message.author = interaction.user
        fake_message.content = f"{prefix}{self.command_name} {value}"
        
        ctx = await self.cog.bot.get_context(fake_message)
        if ctx.command:
            await interaction.response.send_message(f"▶️ 為您執行指令：`{fake_message.content}`", ephemeral=True, delete_after=10)
            await self.cog.bot.invoke(ctx)
        else:
            await interaction.response.send_message(f"錯誤：找不到指令 `{self.command_name}`", ephemeral=True)

# --- Main Help Navigation View ---

class HelpView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=180.0)
        self.cog = cog
        self.message = None

    async def on_timeout(self):
        if self.message:
            for item in self.children:
                item.disabled = True
            await self.message.edit(view=self)

    async def show_main_menu(self, interaction: discord.Interaction):
        prefix = self.cog.bot.command_prefix if isinstance(self.cog.bot.command_prefix, str) else '!'
        embed = self.cog._get_main_help_embed(prefix, self.cog.bot.user)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="首頁", style=discord.ButtonStyle.primary, emoji="🏠", row=0)
    async def home_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        prefix = self.cog.bot.command_prefix if isinstance(self.cog.bot.command_prefix, str) else '!'
        embed = self.cog._get_main_help_embed(prefix, self.cog.bot.user)
        await interaction.response.edit_message(embed=embed, view=HelpView(self.cog))

    @discord.ui.button(label="通用", style=discord.ButtonStyle.secondary, emoji="🔧", row=0)
    async def general_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        prefix = self.cog.bot.command_prefix if isinstance(self.cog.bot.command_prefix, str) else '!'
        embed = self.cog._get_general_help_embed(prefix)
        await interaction.response.edit_message(embed=embed, view=GeneralHelpView(self.cog, self))

    @discord.ui.button(label="遊戲", style=discord.ButtonStyle.secondary, emoji="🎮", row=0)
    async def game_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        prefix = self.cog.bot.command_prefix if isinstance(self.cog.bot.command_prefix, str) else '!'
        embed = self.cog._get_game_help_embed(prefix)
        await interaction.response.edit_message(embed=embed, view=GameHelpView(self.cog, self))

# --- Base View for Categories ---

class CategoryBaseView(ui.View):
    def __init__(self, cog, main_view_instance):
        super().__init__(timeout=180.0)
        self.cog = cog
        self.main_view_instance = main_view_instance
        self.add_item(self.create_back_button())

    def create_back_button(self):
        async def back_callback(interaction: discord.Interaction):
            prefix = self.cog.bot.command_prefix if isinstance(self.cog.bot.command_prefix, str) else '!'
            embed = self.cog._get_main_help_embed(prefix, self.cog.bot.user)
            await interaction.response.edit_message(embed=embed, view=HelpView(self.cog))
        
        button = ui.Button(label="返回主選單", style=discord.ButtonStyle.grey, emoji="↩️", row=4)
        button.callback = back_callback
        return button

    async def _execute_command(self, interaction: discord.Interaction, command_name: str):
        prefix = self.cog.bot.command_prefix if isinstance(self.cog.bot.command_prefix, str) else '!'
        fake_message = copy.copy(interaction.message)
        fake_message.author = interaction.user
        fake_message.content = f"{prefix}{command_name}"

        ctx = await self.cog.bot.get_context(fake_message)
        if ctx and ctx.command:
            await interaction.response.send_message(f"▶️ 為您執行指令：`{fake_message.content}`", ephemeral=True, delete_after=5)
            await self.cog.bot.invoke(ctx)
        else:
            await interaction.response.send_message(f"❌ 錯誤：找不到指令 `{command_name}` 或權限不足。", ephemeral=True)

# --- Category Specific Views with Chinese Labeled Buttons ---

class GeneralHelpView(CategoryBaseView):
    @ui.button(label="每日簽到", style=discord.ButtonStyle.success, emoji="✨", row=0)
    async def execute_checkin(self, interaction: discord.Interaction, button: ui.Button):
        await self._execute_command(interaction, "checkin")

    @ui.button(label="查詢積分", style=discord.ButtonStyle.primary, emoji="💰", row=1)
    async def execute_point(self, interaction: discord.Interaction, button: ui.Button):
        await self._execute_command(interaction, "point")

    @ui.button(label="清除訊息", style=discord.ButtonStyle.danger, emoji="🧹", row=2)
    async def execute_clear(self, interaction: discord.Interaction, button: ui.Button):
        prefix = self.cog.bot.command_prefix if isinstance(self.cog.bot.command_prefix, str) else '!'
        modal = CommandModal(self.cog, "clear", "要清除的訊息數量 (預設10)", f"執行 {prefix}clear")
        await interaction.response.send_modal(modal)

class GameHelpView(CategoryBaseView):
    @ui.button(label="開始猜數字", style=discord.ButtonStyle.success, emoji="▶️", row=0)
    async def execute_start_guess(self, interaction: discord.Interaction, button: ui.Button):
        await self._execute_command(interaction, "start_guess")
        
    @ui.button(label="結束猜數字", style=discord.ButtonStyle.danger, emoji="⏹️", row=0)
    async def execute_stop_guess(self, interaction: discord.Interaction, button: ui.Button):
        await self._execute_command(interaction, "stop_guess")

    @ui.button(label="德州撲克", style=discord.ButtonStyle.success, emoji="▶️", row=1)
    async def execute_poker(self, interaction: discord.Interaction, button: ui.Button):
        prefix = self.cog.bot.command_prefix if isinstance(self.cog.bot.command_prefix, str) else '!'
        modal = CommandModal(self.cog, "poker", "大盲注金額 (預設20)", f"執行 {prefix}poker")
        await interaction.response.send_modal(modal)

    @ui.button(label="結束撲克", style=discord.ButtonStyle.danger, emoji="⏹️", row=1)
    async def execute_stopgame(self, interaction: discord.Interaction, button: ui.Button):
        await self._execute_command(interaction, "stopgame")

    @ui.button(label="21點", style=discord.ButtonStyle.success, emoji="▶️", row=2)
    async def execute_blackjack(self, interaction: discord.Interaction, button: ui.Button):
        prefix = self.cog.bot.command_prefix if isinstance(self.cog.bot.command_prefix, str) else '!'
        modal = CommandModal(self.cog, "blackjack", "您的賭注", f"執行 {prefix}blackjack")
        await interaction.response.send_modal(modal)

    # --- 關鍵新增：拉霸機按鈕 ---
    @ui.button(label="拉霸機", style=discord.ButtonStyle.success, emoji="🎰", row=3)
    async def execute_slots(self, interaction: discord.Interaction, button: ui.Button):
        prefix = self.cog.bot.command_prefix if isinstance(self.cog.bot.command_prefix, str) else '!'
        modal = CommandModal(self.cog, "slots", "您的賭注", f"執行 {prefix}slots")
        await interaction.response.send_modal(modal)

# --- The Main Cog ---

class HelpCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _get_main_help_embed(self, prefix: str, bot_user: discord.ClientUser) -> discord.Embed:
        embed = discord.Embed(title=f'{bot_user.name} 指令選單', description=f"歡迎！點擊下方按鈕瀏覽指令分類，或點擊指令按鈕直接執行。", color=discord.Color.blurple())
        if bot_user.avatar: embed.set_thumbnail(url=bot_user.avatar.url)
        embed.add_field(name="導覽", value="- `🏠 首頁`: 回到主畫面\n- `🔧 通用`: 日常實用指令\n- `🎮 遊戲`: 所有可玩的遊戲", inline=False)
        embed.set_footer(text=f"指令前綴: {prefix} | 選單將在3分鐘後失效")
        return embed

    def _get_general_help_embed(self, prefix: str) -> discord.Embed:
        embed = discord.Embed(title='🔧 通用指令', description="點擊下方對應的中文指令按鈕來快速執行。", color=0x2ECC71)
        embed.add_field(name=f'{prefix}checkin', value='✨ **每日簽到**: 獲取每日積分獎勵，連續簽到有加成！', inline=False)
        embed.add_field(name=f'{prefix}point', value='💰 **查詢積分**: 查詢你目前擁有的積分總額。', inline=False)
        embed.add_field(name=f'{prefix}clear [數量]', value='🧹 **清除訊息**: 清除頻道訊息(預設10則)，僅限管理員。', inline=False)
        return embed

  # --- 關鍵修改：德州撲克教學 Embed，加入範例 ---
    def _get_poker_help_embed(self, prefix: str) -> discord.Embed:
        embed = discord.Embed(title="♠️♥️ 德州撲克 (Texas Hold'em) 遊戲教學 ♦️♣️",
                              description="目標：用你的 **2張底牌** 和 **5張公共牌**，組合出最強的5張牌組，贏得底池！",
                              color=0xC41E3A) # Poker Red

        embed.add_field(
            name="➡️ 遊戲流程",
            value="1. **發起遊戲**: 玩家用 `!poker [大盲注]` 指令開局。\n"
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

    # --- help 指令保持不變 ---
    @commands.command(name='help', help='顯示互動式幫助選單，或特定遊戲的玩法。用法: !help [主題]')
    async def help_command(self, ctx: commands.Context, *, topic: str = None):
        prefix = self.bot.command_prefix if isinstance(self.bot.command_prefix, str) else '!'

        if topic and topic.lower() == 'poker':
            embed = self._get_poker_help_embed(prefix)
            await ctx.send(embed=embed)
        else:
            embed = self._get_main_help_embed(prefix, self.bot.user)
            view = HelpView(self)
            view.message = await ctx.send(embed=embed, view=view)

async def setup(bot):
    original_help = bot.get_command('help')
    if original_help:
        bot.remove_command('help')
    await bot.add_cog(HelpCog(bot))