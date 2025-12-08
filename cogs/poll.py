import discord
from discord.ext import commands
from discord import ui
from typing import Dict, List, Optional

class PollView(ui.View):
    def __init__(self, author_id: int, options: List[str], title: str):
        super().__init__(timeout=None) # Persistent view (or long timeout)
        self.author_id = author_id
        self.options = options
        self.title = title
        self.votes: Dict[int, int] = {} # user_id -> option_index
        
        # Define colors for progress bars (loopable)
        self.colors = ["🟦", "🟩", "🟨", "🟥", "🟪"]

        # Dynamically add buttons
        for i, option in enumerate(options):
            button = ui.Button(
                label=option, 
                style=discord.ButtonStyle.primary, 
                custom_id=f"poll_{i}"
            )
            # Bind the callback with the specific index using a closure default arg
            button.callback = self.create_callback(i)
            self.add_item(button)
            
        self.add_item(self.create_end_button())

    def create_callback(self, index: int):
        async def callback(interaction: discord.Interaction):
            user_id = interaction.user.id
            
            # Update vote
            if user_id in self.votes and self.votes[user_id] == index:
                # User clicked same option -> Remove vote? Or just ignore.
                # Let's toggle off if clicked again.
                del self.votes[user_id]
                msg = "🗑️ 已移除您的投票。"
            else:
                self.votes[user_id] = index
                msg = f"✅ 您投給了：**{self.options[index]}**"

            # Update Embed
            embed = self.generate_embed()
            await interaction.response.edit_message(embed=embed, view=self)
            await interaction.followup.send(msg, ephemeral=True)
        
        return callback

    def create_end_button(self):
        async def end_callback(interaction: discord.Interaction):
            if interaction.user.id != self.author_id:
                await interaction.response.send_message("只有發起人可以結束投票喔！", ephemeral=True)
                return
            
            # Disable all buttons
            for child in self.children:
                child.disabled = True
            
            embed = self.generate_embed()
            embed.title = f"📊 [已結束] {self.title}"
            embed.color = discord.Color.greyple()
            
            await interaction.response.edit_message(embed=embed, view=self)
            self.stop()
            
        button = ui.Button(label="結束投票", style=discord.ButtonStyle.danger, row=1)
        button.callback = end_callback
        return button

    def generate_embed(self) -> discord.Embed:
        embed = discord.Embed(title=f"📊 投票：{self.title}", color=discord.Color.gold())
        embed.set_footer(text=f"總票數：{len(self.votes)}")
        
        # Calculate counts
        counts = [0] * len(self.options)
        for opt_index in self.votes.values():
            counts[opt_index] += 1
            
        total_votes = len(self.votes)
        
        for i, option in enumerate(self.options):
            count = counts[i]
            percentage = (count / total_votes * 100) if total_votes > 0 else 0
            
            # Bar generation (10 chars length)
            # E.g. [████░░░░░░]
            num_filled = int(percentage / 10)
            bar = "█" * num_filled + "░" * (10 - num_filled)
            
            # Add field
            embed.add_field(
                name=f"{option} ({count}票)",
                value=f"`{bar}` {percentage:.1f}%",
                inline=False
            )
            
        return embed

class Poll(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="poll", aliases=["投票"], help='發起投票。格式：!poll "題目" "選項1" "選項2"...')
    async def poll(self, ctx: commands.Context, key: str = None, *options):
        if not key or len(options) < 2:
            await ctx.send("格式錯誤！請至少提供一個題目和兩個選項。\n範例：`!poll \"今晚吃什麼？\" \"拉麵\" \"咖哩\"`")
            return
            
        if len(options) > 5:
            await ctx.send("選項太多了！最多支援 5 個選項。")
            return

        view = PollView(ctx.author.id, list(options), key)
        embed = view.generate_embed()
        
        await ctx.send(embed=embed, view=view)

async def setup(bot):
    await bot.add_cog(Poll(bot))
