# cogs/games.py
import discord
from discord.ext import commands
import random
import os
import json

# 儲存猜數字遊戲的狀態
# 我們把狀態存在 Cog 實例(self)裡面，這樣更乾淨
class GameCog(commands.Cog):
    
    def __init__(self, bot):
        self.bot = bot
        self.guessing_games = {} # 格式：{ '頻道ID': { 'answer': int, 'attempts': int } }
        # 積分存檔（與 blackjack、checkin 共用 points.json）
        root_dir = os.path.dirname(os.path.dirname(__file__))
        self.points_path = os.path.join(root_dir, 'points.json')

    # 告訴 discord.py 這是一個 Cog
    # 這個 setup 函數是必須的，用來讓主程式 bot.py 載入
    async def setup(self):
        await self.bot.add_cog(self)

    # --- 猜數字指令 ---
    # @commands.command() 會自動辨識 "!" 前綴
    @commands.command(name='guess', help='猜數字遊戲 (1-100)。用法: !guess [數字]')
    async def guess(self, ctx, guess: int):
        """
        猜數字遊戲。
        ctx (Context) 包含了訊息的所有資訊 (頻道, 作者...)
        guess: int 會自動嘗試將使用者的第二個參數轉型為整數
        """
        channel_id = ctx.channel.id
        
        # 如果這個頻道目前沒有遊戲
        if channel_id not in self.guessing_games:
            answer = random.randint(1, 100)
            self.guessing_games[channel_id] = { 'answer': answer, 'attempts': 0 }
            await ctx.send(f'猜數字遊戲開始！我心裡想了一個 1 到 100 之間的數字。')

        # 取得狀態並記錄此次嘗試
        state = self.guessing_games[channel_id]
        state['attempts'] += 1
        answer = state['answer']
        
        if guess < answer:
            await ctx.send(f'{guess} 太低了，再高一點！嘎蛙')
        elif guess > answer:
            await ctx.send(f'{guess} 太高了，再低一點！嘎蛙')
        else:
            # 計算獎勵
            attempts = state["attempts"]
            reward = 100 if attempts <= 5 else (50 if attempts <= 10 else 0)
            # 更新積分
            user_id = str(ctx.author.id)
            points = self._load_json(self.points_path, default={})
            points[user_id] = int(points.get(user_id, 0)) + reward
            self._save_json(self.points_path, points)
            # 訊息
            reward_text = f' 獎勵 +{reward} 分，' if reward > 0 else ' '
            await ctx.send(f'🎉 恭喜 {ctx.author.mention}！你猜對了！答案就是 {answer}！你總共猜了 {attempts} 次！{reward_text}目前積分：{points[user_id]}')
            # 猜對了，清除遊戲狀態
            del self.guessing_games[channel_id]

    @commands.command(name='giveup', help='放棄猜數字遊戲')
    async def giveup(self, ctx):
        """放棄遊戲"""
        channel_id = ctx.channel.id
        if channel_id in self.guessing_games:
            state = self.guessing_games[channel_id]
            await ctx.send(f'太可惜了... 答案是 {state["answer"]}。你總共猜了 {state["attempts"]} 次。')
            del self.guessing_games[channel_id]
        else:
            await ctx.send('这个頻道目前沒有在玩猜數字遊戲喔。')

    # 處理 !guess 沒輸入數字的錯誤
    @guess.error
    async def guess_error(self, ctx, error):
        if isinstance(error, commands.BadArgument) or isinstance(error, commands.MissingRequiredArgument):
            await ctx.send('請輸入像這樣的格式： `!guess 50`')

    # ------- I/O -------
    def _load_json(self, path, default):
        try:
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception:
            pass
        return default.copy() if isinstance(default, dict) else default

    def _save_json(self, path, data):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

# 這是讓 bot.py 能載入這個 Cog 的必要函式
async def setup(bot):
    await bot.add_cog(GameCog(bot))

    