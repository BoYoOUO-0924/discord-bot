import discord
from discord.ext import commands
import random
from typing import Optional

class GuessNumberCog(commands.Cog, name="GuessNumber"):
    """猜數字遊戲，支援無指令猜測和動態範圍提示。"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # 遊戲狀態: { channel_id: { 'answer': int, 'attempts': int, 'lower_bound': int, 'upper_bound': int } }
        self.guessing_games = {}

    @property
    def points_cog(self) -> Optional[commands.Cog]:
        """透過屬性即時、安全地獲取 Points cog。"""
        return self.bot.get_cog('Points')

    @commands.command(name='start_guess', help='開始一場猜數字遊戲 (1-100)。')
    @commands.guild_only()
    async def start_guess(self, ctx: commands.Context):
        channel_id = ctx.channel.id
        if channel_id in self.guessing_games:
            await ctx.send('這個頻道已經有猜數字遊戲正在進行中了！')
            return

        answer = random.randint(1, 100)
        self.guessing_games[channel_id] = {
            'answer': answer,
            'attempts': 0,
            'lower_bound': 1,
            'upper_bound': 100
        }
        await ctx.send('🎮 **猜數字遊戲開始！** 🎮\n我心裡想好了一個 **1** 到 **100** 之間的數字，請直接輸入你猜的數字！')

    @commands.command(name='stop_guess', help='放棄目前的猜數字遊戲。')
    @commands.guild_only()
    async def stop_guess(self, ctx: commands.Context):
        channel_id = ctx.channel.id
        if channel_id in self.guessing_games:
            state = self.guessing_games[channel_id]
            await ctx.send(f'太可惜了... 答案是 **{state["answer"]}**。')
            del self.guessing_games[channel_id]
        else:
            await ctx.send('這個頻道目前沒有在玩猜數字遊戲喔。')

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author == self.bot.user:
            return

        channel_id = message.channel.id
        
        if channel_id not in self.guessing_games:
            return

        try:
            guess = int(message.content)
        except ValueError:
            return

        if not self.points_cog:
            await message.channel.send("錯誤：積分系統目前無法使用，請聯絡管理員。", delete_after=10)
            return
            
        state = self.guessing_games[channel_id]
        state['attempts'] += 1
        answer = state['answer']
        
        if not (state['lower_bound'] <= guess <= state['upper_bound']):
            await message.channel.send(f'喂！你猜的數字 **{guess}** 已經超出範圍了喔！目前的範圍是 **{state["lower_bound"]}** 到 **{state["upper_bound"]}**。')
            return

        if guess < answer:
            state['lower_bound'] = guess + 1
            await message.channel.send(f'**{guess}** 太低了！🤏\n目前的範圍是 **{state["lower_bound"]}** 到 **{state["upper_bound"]}** 之間。')
        elif guess > answer:
            state['upper_bound'] = guess - 1
            await message.channel.send(f'**{guess}** 太高了！👆\n目前的範圍是 **{state["lower_bound"]}** 到 **{state["upper_bound"]}** 之間。')
        else:
            attempts = state["attempts"]
            reward = 100 if attempts <= 5 else (50 if attempts <= 10 else 20)
            
            new_total = self.points_cog.update_points(message.author.id, reward)
            
            reward_text = f' 獎勵 **+{reward}** 分，' if reward > 0 else ' '
            
            embed = discord.Embed(
                title="🎉 恭喜！你猜對了！ 🎉",
                description=f'答案就是 **{answer}**！',
                color=discord.Color.gold()
            )
            embed.set_author(name=message.author.display_name, icon_url=message.author.avatar.url)
            embed.add_field(name="總共猜測次數", value=f"**{attempts}** 次", inline=False)
            embed.add_field(name="積分獎勵", value=f"{reward_text}你現在共有 **{new_total}** 分。", inline=False)
            embed.set_footer(text="遊戲結束")

            await message.channel.send(embed=embed)
            
            del self.guessing_games[channel_id]

async def setup(bot: commands.Bot):
    await bot.add_cog(GuessNumberCog(bot))
