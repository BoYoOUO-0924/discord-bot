
import discord
from discord.ext import commands
from discord import ui
import copy
from typing import Optional

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

class PollModal(ui.Modal, title='發起投票'):
    question = ui.TextInput(label='投票題目', placeholder='例如：嘎蛙今天吃什麼?？', required=True)
    option1 = ui.TextInput(label='選項 1', placeholder='例如：越南河粉', required=True)
    option2 = ui.TextInput(label='選項 2', placeholder='例如：韓式炸雞', required=True)
    option3 = ui.TextInput(label='選項 3 (選填)', placeholder='選填', required=False)
    option4 = ui.TextInput(label='選項 4 (選填)', placeholder='選填', required=False)

    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        # 收集非空選項
        opts = [self.option1.value, self.option2.value, self.option3.value, self.option4.value]
        valid_opts = [f'"{opt}"' for opt in opts if opt] # 包裹引號
        
        args = f'"{self.question.value}" ' + " ".join(valid_opts)
        
        prefix = self.cog.bot.command_prefix if isinstance(self.cog.bot.command_prefix, str) else '!'
        fake_message = copy.copy(interaction.message)
        fake_message.author = interaction.user
        fake_message.content = f"{prefix}poll {args}"
        
        ctx = await self.cog.bot.get_context(fake_message)
        if ctx.command:
            await interaction.response.send_message(f"▶️ 正在建立投票...", ephemeral=True, delete_after=5)
            await self.cog.bot.invoke(ctx)
        else:
            await interaction.response.send_message("錯誤：找不到 `poll` 指令。", ephemeral=True)

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

# --- Category Specific Views ---

class GeneralHelpView(CategoryBaseView):
    @ui.button(label="每日簽到", style=discord.ButtonStyle.success, emoji="✨", row=0)
    async def execute_checkin(self, interaction: discord.Interaction, button: ui.Button):
        await self._execute_command(interaction, "checkin")

    @ui.button(label="查詢積分", style=discord.ButtonStyle.primary, emoji="💰", row=1)
    async def execute_point(self, interaction: discord.Interaction, button: ui.Button):
        await self._execute_command(interaction, "point")

    @ui.button(label="投票", style=discord.ButtonStyle.primary, emoji="📊", row=1)
    async def execute_poll(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(PollModal(self.cog))

    @ui.button(label="清除訊息", style=discord.ButtonStyle.danger, emoji="🧹", row=2)
    async def execute_clear(self, interaction: discord.Interaction, button: ui.Button):
        prefix = self.cog.bot.command_prefix if isinstance(self.cog.bot.command_prefix, str) else '!'
        modal = CommandModal(self.cog, "clear", "要清除的訊息數量 (預設10)", f"執行 {prefix}clear")
        await interaction.response.send_modal(modal)

class GameHelpView(CategoryBaseView):
    @ui.button(label="開始猜數字", style=discord.ButtonStyle.success, emoji="🔢", row=0)
    async def execute_start_guess(self, interaction: discord.Interaction, button: ui.Button):
        await self._execute_command(interaction, "`start_guess`")
        
    @ui.button(label="結束猜數字", style=discord.ButtonStyle.danger, emoji="⏹️", row=0)
    async def execute_stop_guess(self, interaction: discord.Interaction, button: ui.Button):
        await self._execute_command(interaction, "`stop_guess`")

    @ui.button(label="德州撲克", style=discord.ButtonStyle.success, emoji="♠️", row=1)
    async def execute_poker(self, interaction: discord.Interaction, button: ui.Button):
        prefix = self.cog.bot.command_prefix if isinstance(self.cog.bot.command_prefix, str) else '!'
        modal = CommandModal(self.cog, "poker", "大盲注金額 (預設20)", f"執行 {prefix}poker")
        await interaction.response.send_modal(modal)

    @ui.button(label="結束撲克", style=discord.ButtonStyle.danger, emoji="⏹️", row=1)
    async def execute_stopgame(self, interaction: discord.Interaction, button: ui.Button):
        await self._execute_command(interaction, "stopgame")

    @ui.button(label="21點", style=discord.ButtonStyle.success, emoji="🃏", row=2)
    async def execute_blackjack(self, interaction: discord.Interaction, button: ui.Button):
        prefix = self.cog.bot.command_prefix if isinstance(self.cog.bot.command_prefix, str) else '!'
        modal = CommandModal(self.cog, "blackjack", "您的賭注", f"執行 {prefix}blackjack")
        await interaction.response.send_modal(modal)

    @ui.button(label="拉霸機", style=discord.ButtonStyle.success, emoji="🎰", row=3)
    async def execute_slots(self, interaction: discord.Interaction, button: ui.Button):
        prefix = self.cog.bot.command_prefix if isinstance(self.cog.bot.command_prefix, str) else '!'
        modal = CommandModal(self.cog, "slots", "您的賭注", f"執行 {prefix}slots")
        await interaction.response.send_modal(modal)

    @ui.button(label="海龜湯", style=discord.ButtonStyle.success, emoji="🐢", row=3)
    async def execute_seatortoise(self, interaction: discord.Interaction, button: ui.Button):
        await self._execute_command(interaction, "seatortoise")

# --- The Main Cog ---

class HelpCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _get_main_help_embed(self, prefix: str, bot_user: discord.ClientUser) -> discord.Embed:
        embed = discord.Embed(title=f'{bot_user.name} 指令選單', description=f"歡迎！點擊下方按鈕瀏覽指令分類，或使用 `{prefix}help [主題]` 尋求特定幫助 (例如: `{prefix}help poker`)。", color=discord.Color.blurple())
        if bot_user.avatar: embed.set_thumbnail(url=bot_user.avatar.url)
        embed.add_field(name="導覽", value="- `🏠 首頁`: 回到主畫面\n- `🔧 通用`: 日常實用指令\n- `🎮 遊戲`: 所有可玩的遊戲", inline=False)
        embed.set_footer(text=f"指令前綴: {prefix} | 選單將在3分鐘後失效")
        return embed

    def _get_general_help_embed(self, prefix: str) -> discord.Embed:
        embed = discord.Embed(title='🔧 通用指令', description="點擊下方對應的中文指令按鈕來快速執行。", color=0x2ECC71)
        embed.add_field(name=f'{prefix}checkin', value='✨ **每日簽到**: 獲取每日積分獎勵，連續簽到有加成！', inline=False)
        embed.add_field(name=f'{prefix}point', value='💰 **查詢積分**: 查詢你目前擁有的積分總額。', inline=False)
        embed.add_field(name=f'{prefix}poll', value='📊 **投票系統**: 發起一個即時互動投票。', inline=False)
        embed.add_field(name=f'{prefix}clear [數量]', value='🧹 **清除訊息**: 清除頻道訊息(預設10則)，僅限管理員。', inline=False)
        return embed

    def _get_game_help_embed(self, prefix: str) -> discord.Embed:
        embed = discord.Embed(title='🎮 遊戲指令', description="點擊下方按鈕開始遊戲，或使用 `!help [遊戲名稱]` 查看詳細玩法。", color=0x3498DB)
        embed.add_field(name=f'{prefix}start_guess | {prefix}stop_guess', value='🔢 **猜數字**: 啟動或結束一個猜數字遊戲。', inline=False)
        embed.add_field(name=f'{prefix}poker [大盲注]', value='♠️ **德州撲克**: 開設一局德州撲克。可使用 `!help poker` 查看完整規則。', inline=False)
        embed.add_field(name=f'{prefix}blackjack [賭注]', value='🃏 **21點**: 開始一局21點遊戲，並指定你的賭注。', inline=False)
        embed.add_field(name=f'{prefix}slots [賭注]', value='🎰 **拉霸機**: 玩一次拉霸機，並指定你的賭注。', inline=False)
        embed.add_field(name=f'{prefix}seatortoise', value='🐢 **海龜湯**: 啟動一局 AI 生成的海龜湯推理遊戲。', inline=False)
        return embed

    # --- 關鍵修改：help 指令現在會向 Poker cog 請求教學內容 ---
    @commands.command(name='help', help='顯示互動式幫助選單')
    async def help_command(self, ctx: commands.Context, *, topic: Optional[str] = None):
        prefix = self.bot.command_prefix if isinstance(self.bot.command_prefix, str) else '!'

        if topic and topic.lower() == 'poker':
            poker_cog = self.bot.get_cog('Poker')
            if poker_cog and hasattr(poker_cog, 'get_poker_help_embed'):
                embed = poker_cog.get_poker_help_embed(prefix)
                await ctx.send(embed=embed)
            else:
                await ctx.send(f"錯誤：找不到 `poker` 的教學內容或 `Poker` 插件未載入。")
        else:
            embed = self._get_main_help_embed(prefix, self.bot.user)
            view = HelpView(self)
            view.message = await ctx.send(embed=embed, view=view)

async def setup(bot):
    original_help = bot.get_command('help')
    if original_help:
        bot.remove_command('help')
    await bot.add_cog(HelpCog(bot))
