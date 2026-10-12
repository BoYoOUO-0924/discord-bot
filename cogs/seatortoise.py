"""AI 海龜湯：Gemini 出題與裁判，玩家用是非題推理真相。"""
import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core.ai import AIError
from core.ui import BaseView, Color, make_embed, respond, send_error

log = logging.getLogger(__name__)

IDLE_TIMEOUT = 30 * 60
QUESTION_COOLDOWN = 3
MAX_HINTS = 3
DIFFICULTIES = {
    "easy": ("簡單", "線索直接、推理步驟少，適合新手", 100),
    "normal": ("普通", "需要幾個關鍵轉折才能想通", 150),
    "hard": ("困難", "真相出乎意料、需要跳脫常理思考", 250),
}
DIFFICULTY_CHOICES = [app_commands.Choice(name=name, value=key) for key, (name, _, _) in DIFFICULTIES.items()]

STORY_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "premise": {"type": "string"},
        "answer": {"type": "string"},
        "key_points": {"type": "array", "items": {"type": "string"}},
        "hints": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "premise", "answer", "key_points", "hints"],
}

# 回答文字 -> 顯示樣式（判斷時由長到短比對，避免「是也不是」被當成「是」）
ANSWERS: List[Tuple[str, str]] = [
    ("是也不是", "🔶 是也不是"),
    ("不是", "❌ 不是"),
    ("無關", "➖ 無關"),
    ("是", "✅ 是"),
]


@dataclass
class SoupGame:
    channel_id: int
    host_id: int
    difficulty: str
    title: str
    premise: str
    answer: str
    key_points: List[str]
    hints: List[str]
    qa: List[Tuple[str, str, str]] = field(default_factory=list)   # (提問者, 問題, 回答)
    hints_used: int = 0
    participants: Set[int] = field(default_factory=set)
    last_activity: float = field(default_factory=time.time)
    message: Optional[discord.PartialMessage] = None
    over: bool = False

    @property
    def reward(self) -> int:
        base = DIFFICULTIES[self.difficulty][2]
        return max(base // 4, int(base * (1 - 0.25 * self.hints_used)))


def parse_answer(text: str) -> str:
    for key, display in ANSWERS:
        if key in text:
            return display
    return "➖ 無關"


class SeaTortoise(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.games: Dict[int, SoupGame] = {}
        self.preparing: Set[int] = set()
        self.cooldowns: Dict[int, float] = {}

    async def cog_load(self):
        self.expire_idle.start()

    async def cog_unload(self):
        self.expire_idle.cancel()

    @property
    def ai(self):
        return self.bot.ai

    # ------------------------------------------------------------------ AI
    async def generate_story(self, difficulty: str, theme: Optional[str]) -> Dict:
        name, desc, _ = DIFFICULTIES[difficulty]
        theme_line = f"故事主題或場景：{theme}。" if theme else "主題自由發揮，避免老套的經典題。"
        prompt = f"""你是「海龜湯」情境推理遊戲的出題者。請原創一則難度「{name}」（{desc}）的海龜湯。{theme_line}
要求：
- title：4–10 字的標題，不能洩漏答案。
- premise（湯面）：2–4 句，懸疑、不完整，但真相必須能從湯面合理推理出來。
- answer（湯底）：3–6 句，完整解釋真相與因果。
- key_points：猜中所需的 2–4 個關鍵要素，每個一句話。
- hints：3 個由淺入深的提示，最後一個也不能直接說出答案。
- 使用繁體中文（台灣用語）。可以有懸疑、死亡元素，但避免血腥細節。"""
        story = await self.ai.json(prompt, STORY_SCHEMA, temperature=1.0)
        if not story.get("premise") or not story.get("answer"):
            raise AIError("AI 出的題目不完整，請再試一次。")
        return story

    def _context(self, game: SoupGame) -> str:
        points = "\n".join(f"- {p}" for p in game.key_points)
        return f"湯面：{game.premise}\n湯底（真相，絕對不能透露）：{game.answer}\n關鍵要素：\n{points}"

    async def judge_question(self, game: SoupGame, question: str) -> str:
        prompt = f"""你是海龜湯的主持人。
{self._context(game)}

玩家提問：「{question}」

依照湯底判斷，只能回答以下四個之一：是、不是、無關、是也不是。
「是也不是」用在問題部分正確、或前提有誤需要釐清的時候。只輸出答案本身，不要任何其他文字。"""
        return parse_answer(await self.ai.text(prompt, max_tokens=16))

    async def judge_guess(self, game: SoupGame, guess: str) -> Tuple[str, str]:
        prompt = f"""你是海龜湯的主持人，要判斷玩家的推理是否猜中真相。
{self._context(game)}

玩家的推理：「{guess}」

第一行只能輸出「正確」、「接近」或「錯誤」其中之一：
- 正確：抓到所有關鍵要素的核心（細節不同沒關係）
- 接近：抓到部分關鍵要素，但還缺重要的部分
- 錯誤：方向不對
如果是「接近」，第二行用一句話鼓勵玩家、點出他猜對的方向，但不能透露他還沒猜到的內容。"""
        text = await self.ai.text(prompt, max_tokens=120)
        first, _, rest = text.partition("\n")
        verdict = "correct" if "正確" in first else "close" if "接近" in first else "wrong"
        return verdict, rest.strip()

    # ------------------------------------------------------------------ 畫面
    def board_embed(self, game: SoupGame) -> discord.Embed:
        embed = make_embed(f"🐢 {game.title}", f"**湯面**\n> {game.premise}", Color.TABLE)
        embed.add_field(name="難度", value=DIFFICULTIES[game.difficulty][0])
        embed.add_field(name="已提問", value=f"{len(game.qa)} 題")
        embed.add_field(name="猜中可得", value=f"{game.reward} 分")
        if game.hints_used:
            hints = "\n".join(f"{i + 1}. {h}" for i, h in enumerate(game.hints[:game.hints_used]))
            embed.add_field(name="💡 提示", value=hints, inline=False)
        embed.set_footer(text="在頻道輸入以「？」結尾的問題，湯主會回答 是／不是／無關／是也不是")
        return embed

    async def refresh(self, game: SoupGame) -> None:
        if game.message and not game.over:
            try:
                await game.message.edit(embed=self.board_embed(game))
            except discord.HTTPException:
                pass

    # ------------------------------------------------------------------ 流程
    async def start(self, interaction: discord.Interaction, difficulty: str = "normal", theme: Optional[str] = None) -> None:
        if not self.ai.available:
            return await send_error(interaction, "海龜湯需要 AI 功能，請管理員在 config.py 設定 `GEMINI_API_KEY`。")
        if not interaction.guild:
            return await send_error(interaction, "海龜湯要在伺服器頻道裡玩。")
        channel_id = interaction.channel_id
        if channel_id in self.games or channel_id in self.preparing:
            return await send_error(interaction, "這個頻道已經有一鍋海龜湯了！")

        self.preparing.add(channel_id)
        try:
            await interaction.response.defer(thinking=True)
            story = await self.generate_story(difficulty, theme)
        except AIError as e:
            return await send_error(interaction, str(e))
        finally:
            self.preparing.discard(channel_id)

        hints = (story.get("hints") or [])[:MAX_HINTS]
        game = SoupGame(channel_id, interaction.user.id, difficulty, story["title"], story["premise"],
                        story["answer"], story.get("key_points") or [], hints)
        self.games[channel_id] = game
        view = SoupView(self, game)
        game.message = await respond(interaction, embed=self.board_embed(game), view=view)

    def _on_cooldown(self, user_id: int) -> bool:
        now = time.time()
        if now - self.cooldowns.get(user_id, 0) < QUESTION_COOLDOWN:
            return True
        self.cooldowns[user_id] = now
        return False

    async def ask(self, game: SoupGame, user: discord.abc.User, question: str) -> str:
        game.last_activity = time.time()
        game.participants.add(user.id)
        answer = await self.judge_question(game, question)
        game.qa.append((user.display_name, question, answer))
        self.bot.dispatch("activity", user.id, "soup_question", game.channel_id, {})
        await self.refresh(game)
        return answer

    async def guess(self, interaction: discord.Interaction, game: SoupGame, text: str) -> None:
        game.last_activity = time.time()
        game.participants.add(interaction.user.id)
        await interaction.response.defer(thinking=True)
        try:
            verdict, feedback = await self.judge_guess(game, text)
        except AIError as e:
            return await send_error(interaction, str(e))
        if game.over:
            return await interaction.followup.send("這鍋湯剛剛已經被喝完了！")

        quoted = "\n".join(f"> {line}" for line in text.splitlines())
        if verdict == "correct":
            reward = game.reward
            await self.finish(game)
            balance = await self.bot.db.add_points(interaction.user.id, reward, "海龜湯猜中真相")
            await self.bot.db.record_game(interaction.user.id, "seatortoise", True, reward)
            self.bot.dispatch("activity", interaction.user.id, "game", game.channel_id,
                              {"game": "seatortoise", "won": True})
            embed = make_embed(f"🎉 {interaction.user.display_name} 猜中了！", f"{quoted}\n\n**湯底**\n{game.answer}", Color.GOLD)
            embed.add_field(name="獎勵", value=f"+{reward} 分（餘額 {balance:,}）")
            embed.add_field(name="共提問", value=f"{len(game.qa)} 題")
            embed.add_field(name="參與人數", value=f"{len(game.participants)} 人")
            await interaction.followup.send(embed=embed, view=AgainView(self, game.difficulty))
        elif verdict == "close":
            message = f"**{interaction.user.display_name}** 猜：\n{quoted}\n\n🔶 **很接近了！** {feedback}"
            await interaction.followup.send(message)
        else:
            await interaction.followup.send(f"**{interaction.user.display_name}** 猜：\n{quoted}\n\n❌ **不對喔，再想想！**")

    async def finish(self, game: SoupGame, note: Optional[str] = None) -> None:
        game.over = True
        self.games.pop(game.channel_id, None)
        if game.message:
            embed = make_embed(f"🐢 {game.title}【已結束】", f"**湯面**\n> {game.premise}\n\n**湯底**\n||{game.answer}||",
                               Color.MUTED)
            if note:
                embed.set_footer(text=note)
            try:
                await game.message.edit(embed=embed, view=None)
            except discord.HTTPException:
                pass

    async def reveal(self, game: SoupGame, reason: str, channel: Optional[discord.abc.Messageable] = None) -> None:
        await self.finish(game, reason)
        channel = channel or self.bot.get_channel(game.channel_id)
        if channel:
            embed = make_embed(f"🐢 公布答案：{game.title}", game.answer, Color.WARNING)
            embed.set_footer(text=reason)
            await channel.send(embed=embed, view=AgainView(self, game.difficulty))

    async def launch_from_hub(self, interaction: discord.Interaction) -> None:
        await self.start(interaction)

    @app_commands.command(name="seatortoise", description="AI 海龜湯：AI 出謎題，用是非題推理真相")
    @app_commands.describe(difficulty="題目難度", theme="想要的主題或場景，例如：校園、太空、便利商店")
    @app_commands.choices(difficulty=DIFFICULTY_CHOICES)
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, 20.0, key=lambda i: i.channel_id)
    async def seatortoise(self, interaction: discord.Interaction, difficulty: str = "normal",
                          theme: Optional[app_commands.Range[str, 1, 50]] = None):
        await self.start(interaction, difficulty, theme)

    # ------------------------------------------------------------------ 頻道提問
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        game = self.games.get(message.channel.id)
        content = message.content.strip()
        if not game or game.over or not content.endswith(("?", "？")) or len(content) > 200:
            return
        if self._on_cooldown(message.author.id):
            return await message.add_reaction("⏳")
        try:
            async with message.channel.typing():
                answer = await self.ask(game, message.author, content)
        except AIError as e:
            return await message.reply(f"⚠️ {e}", mention_author=False)
        await message.reply(f"**{answer}**", mention_author=False)

    @tasks.loop(minutes=1)
    async def expire_idle(self):
        now = time.time()
        for game in [g for g in self.games.values() if now - g.last_activity > IDLE_TIMEOUT]:
            await self.reveal(game, "太久沒人提問，自動公布答案")


class QuestionModal(discord.ui.Modal, title="向湯主提問"):
    question = discord.ui.TextInput(label="是非題", placeholder="例如：他是被別人殺死的嗎？", max_length=200)

    def __init__(self, cog: SeaTortoise, game: SoupGame):
        super().__init__()
        self.cog, self.game = cog, game

    async def on_submit(self, interaction: discord.Interaction):
        if self.game.over:
            return await send_error(interaction, "這鍋湯已經結束了。")
        await interaction.response.defer(thinking=True)
        try:
            answer = await self.cog.ask(self.game, interaction.user, self.question.value.strip())
        except AIError as e:
            return await send_error(interaction, str(e))
        await interaction.followup.send(f"**{interaction.user.display_name}** 問：{self.question.value.strip()}\n湯主：**{answer}**")


class GuessModal(discord.ui.Modal, title="猜真相"):
    guess = discord.ui.TextInput(label="你認為的真相", style=discord.TextStyle.paragraph,
                                 placeholder="把你推理出的完整經過寫下來…", max_length=600)

    def __init__(self, cog: SeaTortoise, game: SoupGame):
        super().__init__()
        self.cog, self.game = cog, game

    async def on_submit(self, interaction: discord.Interaction):
        if self.game.over:
            return await send_error(interaction, "這鍋湯已經結束了。")
        await self.cog.guess(interaction, self.game, self.guess.value.strip())


class SoupView(BaseView):
    def __init__(self, cog: SeaTortoise, game: SoupGame):
        super().__init__(timeout=None)
        self.cog, self.game = cog, game

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.game.over:
            await send_error(interaction, "這鍋湯已經結束了。")
            return False
        return True

    @discord.ui.button(label="提問", emoji="🗣️", style=discord.ButtonStyle.primary)
    async def ask_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(QuestionModal(self.cog, self.game))

    @discord.ui.button(label="猜真相", emoji="💡", style=discord.ButtonStyle.success)
    async def guess_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(GuessModal(self.cog, self.game))

    @discord.ui.button(label="提示", emoji="🔍", style=discord.ButtonStyle.secondary)
    async def hint_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        game = self.game
        if game.hints_used >= len(game.hints):
            return await send_error(interaction, "提示已經全部用完了！")
        game.hints_used += 1
        game.last_activity = time.time()
        await interaction.response.send_message(
            f"🔍 **提示 {game.hints_used}**（{interaction.user.display_name} 要求）：{game.hints[game.hints_used - 1]}\n"
            f"猜中獎勵降為 {game.reward} 分")
        await self.cog.refresh(game)

    @discord.ui.button(label="問答紀錄", emoji="📜", style=discord.ButtonStyle.secondary)
    async def history_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.game.qa:
            return await send_error(interaction, "還沒有人提問喔。")
        lines = [f"**{q}** → {a}（{who}）" for who, q, a in self.game.qa[-20:]]
        embed = make_embed(f"📜 問答紀錄（共 {len(self.game.qa)} 題）", "\n".join(lines))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="公布答案", emoji="🏳️", style=discord.ButtonStyle.danger)
    async def reveal_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        is_mod = isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.manage_messages
        if interaction.user.id != self.game.host_id and not is_mod:
            return await send_error(interaction, "只有開湯的人或管理員可以公布答案。")
        await interaction.response.defer()
        await self.cog.reveal(self.game, f"{interaction.user.display_name} 公布了答案", interaction.channel)


class AgainView(BaseView):
    def __init__(self, cog: SeaTortoise, difficulty: str):
        super().__init__(timeout=600)
        self.cog, self.difficulty = cog, difficulty

    @discord.ui.button(label="再來一鍋", emoji="🐢", style=discord.ButtonStyle.success)
    async def again(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.start(interaction, self.difficulty)


async def setup(bot):
    await bot.add_cog(SeaTortoise(bot))
