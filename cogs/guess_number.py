# cogs/guess_number.py
import discord
from discord.ext import commands
import random

class GameCog(commands.Cog):
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.guessing_games = {} # Format: { channel_id: { 'answer': int, 'attempts': int } }
        self.points_cog = None # Will be set in on_ready

    @commands.Cog.listener()
    async def on_ready(self):
        # Get the centralized PointsCog instance
        self.points_cog = self.bot.get_cog('Points')
        if not self.points_cog:
            print("Error: PointsCog not found in GameCog. Make sure it is loaded.")

    @commands.command(name='guess', help='猜數字遊戲 (1-100)。用法: !guess [數字]')
    async def guess(self, ctx: commands.Context, guess: int):
        if not self.points_cog:
            await ctx.send("積分系統目前無法使用，請聯絡管理員。")
            return

        channel_id = ctx.channel.id
        
        if channel_id not in self.guessing_games:
            answer = random.randint(1, 100)
            self.guessing_games[channel_id] = { 'answer': answer, 'attempts': 0 }
            await ctx.send(f'猜數字遊戲開始！我心裡想了一個 1 到 100 之間的數字。')

        state = self.guessing_games[channel_id]
        state['attempts'] += 1
        answer = state['answer']
        
        if guess < answer:
            await ctx.send(f'{guess} 太低了，再高一點！嘎蛙')
        elif guess > answer:
            await ctx.send(f'{guess} 太高了，再低一點！嘎蛙')
        else:
            attempts = state["attempts"]
            reward = 100 if attempts <= 5 else (50 if attempts <= 10 else 0)
            
            # CRITICAL: Update points using the centralized PointsCog
            new_total = self.points_cog.update_points(ctx.author.id, reward)
            
            reward_text = f' 獎勵 +{reward} 分，' if reward > 0 else ' '
            await ctx.send(
                f'🎉 恭喜 {ctx.author.mention}！你猜對了！答案就是 {answer}！'
                f'你總共猜了 {attempts} 次！{reward_text}目前積分：{new_total}'
            )
            
            del self.guessing_games[channel_id]

    @commands.command(name='guess_giveup', help='放棄猜數字遊戲')
    async def guess_giveup(self, ctx: commands.Context):
        channel_id = ctx.channel.id
        if channel_id in self.guessing_games:
            state = self.guessing_games[channel_id]
            await ctx.send(f'太可惜了... 答案是 {state["answer"]}。你總共猜了 {state["attempts"]} 次。')
            del self.guessing_games[channel_id]
        else:
            await ctx.send('这个頻道目前沒有在玩猜數字遊戲喔。')

    @guess.error
    async def guess_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.BadArgument) or isinstance(error, commands.MissingRequiredArgument):
            await ctx.send('請輸入像這樣的格式： `!guess 50`')

async def setup(bot: commands.Bot):
    await bot.add_cog(GameCog(bot))
