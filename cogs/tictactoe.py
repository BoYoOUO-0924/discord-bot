"""井字遊戲：可以邀請朋友對戰，或挑戰三種難度的機器人。"""
import random
from functools import lru_cache
from typing import List, Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands

from core.ui import BaseView, Color, make_embed, respond, send_error

EMPTY, X, O = 0, 1, 2
SYMBOLS = {X: "❌", O: "⭕"}
LINES = [(0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6)]
DIFFICULTIES = [
    app_commands.Choice(name="簡單", value="easy"),
    app_commands.Choice(name="普通", value="normal"),
    app_commands.Choice(name="困難（不會輸）", value="hard"),
]
DIFFICULTY_NAMES = {c.value: c.name for c in DIFFICULTIES}


def find_winner(board: Tuple[int, ...]) -> Tuple[int, Optional[Tuple[int, int, int]]]:
    """回傳 (勝方, 連線)；勝方 0 = 未分勝負，3 = 平手。"""
    for line in LINES:
        a, b, c = line
        if board[a] != EMPTY and board[a] == board[b] == board[c]:
            return board[a], line
    return (3, None) if EMPTY not in board else (0, None)


@lru_cache(maxsize=None)
def _minimax(board: Tuple[int, ...], turn: int, me: int) -> int:
    winner, _ = find_winner(board)
    if winner == me:
        return 10
    if winner == 3:
        return 0
    if winner:
        return -10
    scores = []
    for i in range(9):
        if board[i] == EMPTY:
            nxt = board[:i] + (turn,) + board[i + 1:]
            scores.append(_minimax(nxt, X if turn == O else O, me))
    return max(scores) if turn == me else min(scores)


def bot_move(board: Tuple[int, ...], me: int, difficulty: str) -> int:
    empty = [i for i in range(9) if board[i] == EMPTY]
    opponent = X if me == O else O
    if difficulty == "easy":
        return random.choice(empty)
    if difficulty == "normal":
        # 會贏就贏、會輸就擋，其他時候有一半機率亂下
        for target in (me, opponent):
            for i in empty:
                if find_winner(board[:i] + (target,) + board[i + 1:])[0] == target:
                    return i
        if random.random() < 0.5:
            return random.choice(empty)
    scored = [(_minimax(board[:i] + (me,) + board[i + 1:], opponent, me), i) for i in empty]
    best = max(s for s, _ in scored)
    return random.choice([i for s, i in scored if s == best])


class TicTacToeGame:
    def __init__(self, player_x: discord.abc.User, player_o: Optional[discord.abc.User], difficulty: str = "normal"):
        # player_o 為 None 代表對手是機器人
        self.players = {X: player_x, O: player_o}
        self.difficulty = difficulty
        self.board: List[int] = [EMPTY] * 9
        self.turn = random.choice([X, O])
        self.winner, self.line = 0, None

    @property
    def vs_bot(self) -> bool:
        return self.players[O] is None

    def name(self, symbol: int) -> str:
        player = self.players[symbol]
        return player.mention if player else f"🤖 機器人（{DIFFICULTY_NAMES[self.difficulty]}）"

    def place(self, index: int) -> None:
        self.board[index] = self.turn
        self.winner, self.line = find_winner(tuple(self.board))
        if not self.winner:
            self.turn = X if self.turn == O else O

    def bot_turn(self) -> None:
        if self.vs_bot and self.turn == O and not self.winner:
            self.place(bot_move(tuple(self.board), O, self.difficulty))


class TicTacToe(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def build_embed(self, game: TicTacToeGame, note: Optional[str] = None) -> discord.Embed:
        header = f"{SYMBOLS[X]} {game.name(X)}　vs　{SYMBOLS[O]} {game.name(O)}"
        if game.winner == 3:
            status, color = "🤝 平手！", Color.MUTED
        elif game.winner:
            status, color = f"🏆 {game.name(game.winner)} 獲勝！", Color.SUCCESS
        else:
            status, color = f"輪到 {SYMBOLS[game.turn]} {game.name(game.turn)}", Color.GAME
        embed = make_embed("⭕ 井字遊戲", f"{header}\n\n{status}", color)
        if note:
            embed.set_footer(text=note)
        return embed

    async def begin(self, interaction: discord.Interaction, game: TicTacToeGame, *, edit: bool = False) -> None:
        game.bot_turn()  # 機器人先手的話先下
        view = BoardView(self, game)
        if edit:
            await interaction.response.edit_message(embed=self.build_embed(game), view=view)
            view.message = interaction.message
        else:
            view.message = await respond(interaction, embed=self.build_embed(game), view=view)

    async def challenge(self, interaction: discord.Interaction, opponent: Optional[discord.abc.User],
                        difficulty: str = "normal") -> None:
        if opponent is None or opponent.id == self.bot.user.id:
            return await self.begin(interaction, TicTacToeGame(interaction.user, None, difficulty))
        if opponent.bot:
            return await send_error(interaction, "其他機器人不會玩井字遊戲，不填對手就能挑戰我！")
        if opponent.id == interaction.user.id:
            return await send_error(interaction, "不能跟自己下，不填對手就能挑戰機器人！")
        embed = make_embed("⭕ 井字遊戲邀請", f"{interaction.user.mention} 邀請 {opponent.mention} 來一局井字遊戲！", Color.PRIMARY)
        embed.set_footer(text="60 秒內沒有回應會自動取消")
        view = InviteView(self, interaction.user, opponent)
        view.message = await respond(interaction, content=opponent.mention, embed=embed, view=view,
                                     allowed_mentions=discord.AllowedMentions(users=[opponent]))

    async def record(self, game: TicTacToeGame, channel_id: Optional[int]) -> None:
        for symbol in (X, O):
            player = game.players[symbol]
            if player:
                won = game.winner == symbol
                await self.bot.db.record_game(player.id, "tictactoe", won)
                # 贏簡單電腦太容易，不算任務進度
                if not (game.vs_bot and game.difficulty == "easy"):
                    self.bot.dispatch("activity", player.id, "game", channel_id, {"game": "tictactoe", "won": won})

    async def launch_from_hub(self, interaction: discord.Interaction) -> None:
        view = OpponentPickerView(self, interaction.user.id)
        await interaction.response.send_message(
            embed=make_embed("⭕ 井字遊戲", "選一位朋友邀請對戰，或直接挑戰機器人。"), view=view, ephemeral=True)

    @app_commands.command(name="tictactoe", description="井字遊戲：找朋友對戰，或挑戰機器人")
    @app_commands.describe(opponent="要挑戰的人，不填就是跟機器人玩", difficulty="機器人的難度")
    @app_commands.choices(difficulty=DIFFICULTIES)
    async def tictactoe(self, interaction: discord.Interaction, opponent: Optional[discord.User] = None,
                        difficulty: str = "normal"):
        await self.challenge(interaction, opponent, difficulty)


class BoardView(BaseView):
    def __init__(self, cog: TicTacToe, game: TicTacToeGame):
        super().__init__(timeout=180)
        self.cog, self.game = cog, game
        self.render()

    def render(self) -> None:
        self.clear_items()
        game = self.game
        for i, cell in enumerate(game.board):
            button = discord.ui.Button(row=i // 3, style=discord.ButtonStyle.secondary,
                                       label="​" if cell == EMPTY else None,
                                       emoji=SYMBOLS.get(cell), disabled=cell != EMPTY or bool(game.winner))
            if game.line and i in game.line:
                button.style = discord.ButtonStyle.success
            button.callback = self._make_move(i)
            self.add_item(button)
        if game.winner:
            again = discord.ui.Button(label="再來一局", emoji="🔁", style=discord.ButtonStyle.primary, row=3)
            again.callback = self.rematch
            self.add_item(again)
        else:
            resign = discord.ui.Button(label="認輸", emoji="🏳️", style=discord.ButtonStyle.secondary, row=3)
            resign.callback = self.resign
            self.add_item(resign)

    def _participants(self) -> List[int]:
        return [p.id for p in self.game.players.values() if p]

    def _make_move(self, index: int):
        async def callback(interaction: discord.Interaction):
            game = self.game
            if interaction.user.id not in self._participants():
                return await send_error(interaction, "這不是你的對局，用 /tictactoe 開一局吧！")
            current = game.players[game.turn]
            if not current or interaction.user.id != current.id:
                return await send_error(interaction, "還沒輪到你喔！")
            if game.board[index] != EMPTY or game.winner:
                return await send_error(interaction, "這格不能下。")
            game.place(index)
            game.bot_turn()
            await self._update(interaction)
        return callback

    async def _update(self, interaction: discord.Interaction, note: Optional[str] = None) -> None:
        if self.game.winner:
            # 不能在這裡 stop()：「再來一局」按鈕也在這個介面上，停掉就按不了了
            await self.cog.record(self.game, interaction.channel_id)
        self.render()
        await interaction.response.edit_message(embed=self.cog.build_embed(self.game, note), view=self)

    async def resign(self, interaction: discord.Interaction):
        game = self.game
        if interaction.user.id not in self._participants():
            return await send_error(interaction, "這不是你的對局。")
        loser = X if game.players[X] and game.players[X].id == interaction.user.id else O
        game.winner = O if loser == X else X
        await self._update(interaction, note=f"{interaction.user.display_name} 認輸了")

    async def rematch(self, interaction: discord.Interaction):
        game = self.game
        if interaction.user.id not in self._participants():
            return await send_error(interaction, "只有剛剛對戰的玩家可以再來一局。")
        self.stop()
        # 交換 ❌ ⭕，機器人永遠是 ⭕
        if game.vs_bot:
            new_game = TicTacToeGame(game.players[X], None, game.difficulty)
        else:
            new_game = TicTacToeGame(game.players[O], game.players[X])
        await self.cog.begin(interaction, new_game, edit=True)

    async def on_timeout(self) -> None:
        if not self.message:
            return
        try:
            if self.game.winner:
                # 已經分出勝負：只把「再來一局」停用，保留棋盤結果
                self.disable_all()
                await self.message.edit(view=self)
            else:
                await self.message.edit(embed=self.cog.build_embed(self.game, "太久沒有動作，對局已結束"), view=None)
        except discord.HTTPException:
            pass


class InviteView(BaseView):
    def __init__(self, cog: TicTacToe, host: discord.abc.User, opponent: discord.abc.User):
        super().__init__(owner_id=opponent.id, timeout=60)
        self.owner_denied_message = "這不是給你的邀請喔！"
        self.cog, self.host, self.opponent = cog, host, opponent

    @discord.ui.button(label="接受", emoji="⚔️", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await self.cog.begin(interaction, TicTacToeGame(self.host, self.opponent), edit=True)

    @discord.ui.button(label="拒絕", style=discord.ButtonStyle.secondary)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        embed = make_embed("⭕ 井字遊戲邀請", f"{self.opponent.mention} 婉拒了 {self.host.mention} 的邀請。", Color.MUTED)
        await interaction.response.edit_message(content=None, embed=embed, view=None)

    async def on_timeout(self) -> None:
        if self.message:
            embed = make_embed("⭕ 井字遊戲邀請", f"{self.opponent.mention} 沒有回應，邀請已取消。", Color.MUTED)
            try:
                await self.message.edit(content=None, embed=embed, view=None)
            except discord.HTTPException:
                pass


class OpponentPickerView(BaseView):
    """從大廳開啟時，讓玩家選對手或挑戰機器人。"""

    def __init__(self, cog: TicTacToe, owner_id: int):
        super().__init__(owner_id=owner_id, timeout=120)
        self.cog = cog

    @discord.ui.select(cls=discord.ui.UserSelect, placeholder="選一位朋友邀請對戰…")
    async def pick(self, interaction: discord.Interaction, select: discord.ui.UserSelect):
        await self.cog.challenge(interaction, select.values[0])

    @discord.ui.button(label="挑戰機器人", emoji="🤖", style=discord.ButtonStyle.primary)
    async def vs_bot(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.challenge(interaction, None)


async def setup(bot):
    await bot.add_cog(TicTacToe(bot))
