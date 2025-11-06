import discord
from discord.ext import commands
import google.generativeai as genai
import asyncio
import json
from typing import Dict, Any

# 載入本地設定
try:
    import config
    GEMINI_API_KEY = getattr(config, "GEMINI_API_KEY", None)
except ImportError:
    GEMINI_API_KEY = None

if not GEMINI_API_KEY or GEMINI_API_KEY == "PUT_YOUR_GEMINI_API_KEY_HERE":
    raise ValueError("Gemini API Key 未在 config.py 中設定！")

# 設定 Gemini API
genai.configure(api_key=GEMINI_API_KEY)

# --- Gemini AI 模型設定 ---
# 用於生成故事和判斷答案
generation_config = {
    "temperature": 0.9,
    "top_p": 1,
    "top_k": 1,
    "max_output_tokens": 2048,
}
safety_settings = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
]
model = genai.GenerativeModel(model_name="gemini-1.0-pro",
                              generation_config=generation_config,
                              safety_settings=safety_settings)

class SeaTortoiseGame:
    """代表一個頻道的遊戲狀態"""
    def __init__(self, premise: str, answer: str):
        self.premise = premise
        self.answer = answer
        self.active = True

class SeaTortoise(commands.Cog):
    """由 Gemini AI 驅動的海龜湯遊戲功能"""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.games: Dict[int, SeaTortoiseGame] = {}  # channel_id -> Game

    async def generate_story(self) -> Dict[str, str]:
        """呼叫 Gemini API 生成一則海龜湯故事"""
        prompt = ("""
        請你扮演一位「海龜湯」遊戲的出題者。海龜湯是一個情境猜謎遊戲，
        你會先說出一個不完整、帶有懸疑感的短篇故事開頭（謎題），然後由我來猜測故事的完整真相（謎底）。
        
        你的任務是，只生成一則新的海龜湯故事，不需要任何額外對話。
        
        請嚴格按照以下 JSON 格式輸出，不要有任何多餘的文字或 markdown 標記：
        ```json
        {
          "premise": "這裡放謎題的開頭",
          "answer": "這裡放故事的完整真相"
        }
        ```
        """
        )
        
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None, lambda: model.generate_content(prompt)
        )
        
        # 清理並解析 JSON
        text = response.text.strip()
        if text.startswith('```json'):
            text = text[7:]
        if text.endswith('```'):
            text = text[:-3]
        
        return json.loads(text.strip())

    @commands.command(name="seatortoise", aliases=["海龜湯"], help="開始一場由 AI 生成的海龜湯遊戲。")
    async def start_game(self, ctx: commands.Context):
        if ctx.channel.id in self.games:
            await ctx.send("這個頻道已經在進行一場海龜湯遊戲了！")
            return

        try:
            await ctx.send("🐢 AI 湯主正在熬製一鍋新鮮的海龜湯，請稍候...", tts=False)
            story_data = await self.generate_story()
            self.games[ctx.channel.id] = SeaTortoiseGame(story_data['premise'], story_data['answer'])

            embed = discord.Embed(
                title="🐢 AI 海龜湯：謎題來了！",
                description=f"**{story_data['premise']}**",
                color=discord.Color.dark_green()
            )
            embed.set_footer(text="請直接在頻道中提出「是/否」問題來推理，或用 `!answer <答案>` 來猜測真相！")
            await ctx.send(embed=embed)
        except Exception as e:
            await ctx.send(f"糟糕，AI 湯主煮湯失敗了... 錯誤訊息：`{e}`")
            if ctx.channel.id in self.games:
                del self.games[ctx.channel.id]

    @commands.command(name="answer", aliases=["答案"], help="猜測海龜湯的最終答案。")
    async def guess_answer(self, ctx: commands.Context, *, guess: str):
        game = self.games.get(ctx.channel.id)
        if not game:
            await ctx.send("這裡沒有正在進行的海龜湯遊戲。")
            return

        prompt = (f"""
        你是一位海龜湯遊戲的主持人（湯主）。現在有位玩家正在嘗試猜出最終的謎底。
        
        **這是標準的完整謎底：**
        {game.answer}
        
        **這是玩家的猜測：**
        「{guess}」
        
        請根據標準謎底，判斷玩家的猜測是否正確。如果玩家的猜測涵蓋了謎底的核心要素和關鍵情節，即使細節略有出入，也應視為正確。
        你的回答**只能是「正確」或「錯誤」**，兩個詞其中之一，不要有任何其他解釋。
        """
        )
        
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None, lambda: model.generate_content(prompt)
            )
            result = response.text.strip()

            if "正確" in result:
                embed = discord.Embed(
                    title="🎉 恭喜你，猜對了！",
                    description=f"**真相是：**\n{game.answer}",
                    color=discord.Color.gold()
                )
                await ctx.send(embed=embed)
                del self.games[ctx.channel.id]
            else:
                await ctx.send(f"不對喔，{ctx.author.mention}。再猜猜看！")
        except Exception as e:
            await ctx.send(f"抱歉，AI 裁判長腦袋當機了... 錯誤訊息：`{e}`")

    @commands.command(name="giveup", aliases=["放棄"], help="結束目前的海龜湯遊戲並公布答案。")
    async def give_up(self, ctx: commands.Context):
        game = self.games.get(ctx.channel.id)
        if not game:
            await ctx.send("這裡沒有正在進行的海龜湯遊戲。")
            return

        embed = discord.Embed(
            title="🤔 遊戲結束",
            description=f"**公布答案：**\n{game.answer}",
            color=discord.Color.orange()
        )
        await ctx.send(embed=embed)
        del self.games[ctx.channel.id]

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.content.startswith(self.bot.command_prefix):
            return

        game = self.games.get(message.channel.id)
        if not game or not message.content.endswith(("?", "？")):
            return

        prompt = (f"""
        你是一位海龜湯遊戲的主持人（湯主）。玩家正在根據你出的謎題進行推理。
        
        **這是完整的真相（謎底），請記在心裡，但不要透露給玩家：**
        {game.answer}
        
        **現在，一位玩家問了以下問題：**
        「{message.content}」
        
        你的任務是，根據你所知道的完整真相，判斷這個問題的答案。
        **你「只能」也「必須」從以下三個詞中選擇一個來回答：**
        - 「是」
        - 「否」
        - 「與此無關」
        
        不要提供任何解釋或額外的文字。請直接給出你的判斷。
        """
        )
        
        try:
            # 創建一個異步任務來處理 AI 請求，以免阻塞
            async def get_response():
                loop = asyncio.get_running_loop()
                response = await loop.run_in_executor(
                    None, lambda: model.generate_content(prompt)
                )
                await message.channel.send(f"{message.author.mention} {response.text.strip()}")
            
            asyncio.create_task(get_response())

        except Exception as e:
            await message.channel.send(f"AI 湯主突然斷線了... 錯誤訊息：`{e}`")

async def setup(bot: commands.Bot):
    await bot.add_cog(SeaTortoise(bot))
