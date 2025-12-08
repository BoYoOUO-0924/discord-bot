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
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
]
model = genai.GenerativeModel(model_name="gemini-2.5-flash",
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

    async def generate_content_safe(self, prompt: str) -> str:
        """安全地調用 Gemini API，處理可能的錯誤或空白回應"""
        loop = asyncio.get_running_loop()
        try:
            response = await loop.run_in_executor(
                None, lambda: model.generate_content(prompt)
            )
            # 檢查是否有內容
            if not response.parts:
                # 若因安全原因被阻擋，finish_reason 會顯示相關資訊 (通常在 console 可見)
                # 這裡簡單回傳一個錯誤標示
                print(f"Gemini 回應空的 (Finish Reason: {response.candidates[0].finish_reason})")
                if response.candidates[0].finish_reason == 2: # MAX_TOKENS or unknown mapping
                     return "錯誤：AI 生成中斷 (Max Tokens)"
                return "錯誤：AI 拒絕產生內容 (可能觸發安全機制)"
            
            return response.text.strip()
            
        except Exception as e:
            print(f"Gemini API 錯誤: {e}")
            return f"錯誤：{str(e)}"

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
        
        text = await self.generate_content_safe(prompt)
        
        # 簡單的錯誤檢查
        if text.startswith("錯誤："):
            raise ValueError(text)

        # 清理並解析 JSON
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
            msg = await ctx.send("🐢 AI 湯主正在熬製一鍋新鮮的海龜湯，請稍候...", tts=False)
            story_data = await self.generate_story()
            
            # 建立遊戲實例
            game = SeaTortoiseGame(story_data['premise'], story_data['answer'])
            self.games[ctx.channel.id] = game

            # 建立 Embed
            embed = discord.Embed(
                title="🐢 AI 海龜湯：謎題來了！",
                description=f"**{story_data['premise']}**",
                color=discord.Color.dark_green()
            )
            embed.set_footer(text="點擊下方按鈕來進行遊戲！")

            # 建立 View (按鈕介面)
            view = SeaTortoiseView(self, game, ctx.channel.id)
            await msg.edit(content=None, embed=embed, view=view)

        except Exception as e:
            await ctx.send(f"糟糕，AI 湯主煮湯失敗了... 錯誤訊息：`{e}`")
            if ctx.channel.id in self.games:
                del self.games[ctx.channel.id]

    # --- 核心邏輯函式 (供指令與 Modal 共用) ---

    async def core_process_guess(self, channel_id: int, user: discord.User, guess: str, interaction: discord.Interaction = None):
        """處理猜測答案的核心邏輯"""
        game = self.games.get(channel_id)
        if not game:
            msg = "這裡沒有正在進行的海龜湯遊戲。"
            if interaction: await interaction.response.send_message(msg, ephemeral=True)
            else: await user.send(msg) # Fallback
            return

        prompt = (f"""
        你是一位海龜湯遊戲的主持人（湯主）。現在有位玩家正在嘗試猜出最終的謎底。
        
        **這是標準的完整謎底：**
        {game.answer}
        
        **這是玩家的猜測：**
        「{guess}」
        
        請根據標準謎底，判斷玩家的猜測是否正確。如果玩家的猜測涵蓋了謎底的核心要素和關鍵情節，即使細節略有出入，也應視為正確。
        你的回答**只能是「正確」或「錯誤」**，兩個詞其中之一，不要有任何其他解釋。
        """)

        # 若是 Interaction，先 defer 以免超時
        if interaction:
            await interaction.response.defer()

        try:
            result = await self.generate_content_safe(prompt)
            
            # 定義發送訊息的 helper
            async def send_result(content=None, embed=None):
                if interaction:
                    await interaction.followup.send(content=content, embed=embed)
                else:
                    channel = self.bot.get_channel(channel_id)
                    await channel.send(content=content, embed=embed)

            if "正確" in result:
                embed = discord.Embed(
                    title="🎉 恭喜你，猜對了！",
                    description=f"**玩家：** {user.mention}\n**真相是：**\n{game.answer}",
                    color=discord.Color.gold()
                )
                await send_result(embed=embed)
                del self.games[channel_id]
            else:
                await send_result(content=f"不對喔，{user.mention}。再猜猜看！")

        except Exception as e:
            err_msg = f"抱歉，AI 裁判長腦袋當機了... 錯誤訊息：`{e}`"
            if interaction: await interaction.followup.send(err_msg, ephemeral=True)
            else: 
                channel = self.bot.get_channel(channel_id)
                await channel.send(err_msg)

    async def core_process_question(self, channel_id: int, user: discord.User, question: str, interaction: discord.Interaction = None):
        """處理提問的核心邏輯"""
        game = self.games.get(channel_id)
        if not game:
            return

        prompt = (f"""
        你是一位海龜湯遊戲的主持人（湯主）。玩家正在根據你出的謎題進行推理。
        
        **這是完整的真相（謎底），請記在心裡，但不要透露給玩家：**
        {game.answer}
        
        **現在，一位玩家問了以下問題：**
        「{question}」
        
        你的任務是，根據你所知道的完整真相，判斷這個問題的答案。
        **你「只能」也「必須」從以下三個詞中選擇一個來回答：**
        - 「是」
        - 「否」
        - 「與此無關」
        
        不要提供任何解釋或額外的文字。請直接給出你的判斷。
        """)

        if interaction:
            await interaction.response.defer()

        try:
            response_text = await self.generate_content_safe(prompt)
            
            # 組合問答文字
            reply_text = f"**{user.display_name} 問：** {question}\n**湯主答：** {response_text}"
            
            if interaction:
                await interaction.followup.send(reply_text)
            else:
                channel = self.bot.get_channel(channel_id)
                await channel.send(reply_text)

        except Exception as e:
            err_msg = f"AI 湯主突然斷線了... `{e}`"
            if interaction: await interaction.followup.send(err_msg)
            else: 
                channel = self.bot.get_channel(channel_id)
                await channel.send(err_msg)

    # --- 指令介面 (維持相容性) ---

    @commands.command(name="answer", aliases=["答案"], help="猜測海龜湯的最終答案。")
    async def guess_answer(self, ctx: commands.Context, *, guess: str):
        # 轉發給核心邏輯
        await self.core_process_guess(ctx.channel.id, ctx.author, guess)

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

        # 這裡會自動從文字頻道讀取問題，轉發給核心邏輯
        # 為避免阻塞，不使用 await 等待它完成
        asyncio.create_task(self.core_process_question(message.channel.id, message.author, message.content))


# --- UI 組件 ---

class QuestionModal(discord.ui.Modal, title='向湯主提問'):
    question = discord.ui.TextInput(
        label='你的問題 (請以 是/否 回答為主)',
        style=discord.TextStyle.short,
        placeholder='例如：他是被謀殺的嗎？',
        required=True,
        max_length=100
    )

    def __init__(self, cog: 'SeaTortoise', channel_id: int):
        super().__init__()
        self.cog = cog
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction):
        # 呼叫 Cog 中的核心邏輯
        await self.cog.core_process_question(self.channel_id, interaction.user, self.question.value, interaction)

class GuessModal(discord.ui.Modal, title='猜測真相'):
    guess = discord.ui.TextInput(
        label='你認為的真相是...',
        style=discord.TextStyle.paragraph,
        placeholder='請詳細描述你的推理...',
        required=True,
        max_length=500
    )

    def __init__(self, cog: 'SeaTortoise', channel_id: int):
        super().__init__()
        self.cog = cog
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction):
        # 呼叫 Cog 中的核心邏輯
        await self.cog.core_process_guess(self.channel_id, interaction.user, self.guess.value, interaction)

class SeaTortoiseView(discord.ui.View):
    def __init__(self, cog: 'SeaTortoise', game: SeaTortoiseGame, channel_id: int):
        super().__init__(timeout=None) # 遊戲介面不逾時，直到遊戲結束
        self.cog = cog
        self.game = game
        self.channel_id = channel_id

    @discord.ui.button(label="🗣️ 提問", style=discord.ButtonStyle.primary)
    async def ask_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(QuestionModal(self.cog, self.channel_id))

    @discord.ui.button(label="💡 猜答", style=discord.ButtonStyle.success)
    async def guess_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(GuessModal(self.cog, self.channel_id))

    @discord.ui.button(label="🏳️ 放棄", style=discord.ButtonStyle.danger)
    async def giveup_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # 直接執行放棄邏輯
        if self.channel_id in self.cog.games:
            del self.cog.games[self.channel_id]
            
            embed = discord.Embed(
                title="🤔 遊戲結束",
                description=f"**公布答案：**\n{self.game.answer}",
                color=discord.Color.orange()
            )
            await interaction.response.send_message(embed=embed)
            # 停止並移除 View
            self.stop()
        else:
            await interaction.response.send_message("遊戲已經結束了。", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(SeaTortoise(bot))
