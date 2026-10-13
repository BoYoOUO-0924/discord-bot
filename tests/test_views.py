import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from cogs.tictactoe import O, X, BoardView, TicTacToeGame


class TicTacToeViewTest(unittest.IsolatedAsyncioTestCase):
    async def test_rematch_button_still_works_after_game_ends(self):
        player = SimpleNamespace(id=1, mention="<@1>", display_name="P1")
        game = TicTacToeGame(player, None, "hard")
        cog = MagicMock()
        cog.record = AsyncMock()
        view = BoardView(cog, game)

        game.board = [X, X, X, O, O, 0, 0, 0, 0]
        game.winner, game.line = X, (0, 1, 2)
        interaction = SimpleNamespace(channel_id=10, response=SimpleNamespace(edit_message=AsyncMock()))
        await view._update(interaction)

        # 分出勝負後介面不能停掉，否則「再來一局」按了會沒反應
        self.assertFalse(view.is_finished())
        labels = [getattr(item, "label", None) for item in view.children]
        self.assertIn("再來一局", labels)
        cog.record.assert_awaited_once()


class PokerTableViewTest(unittest.IsolatedAsyncioTestCase):
    async def test_button_emojis_are_discord_emojis(self):
        from cogs.poker.cog import Lobby, PokerGame, TableView

        lobby = Lobby(SimpleNamespace(id=1), 10, 20, 1000)
        lobby.players[1] = SimpleNamespace(display_name="P1")
        lobby.add_ai()
        game = PokerGame(lobby)
        game.table.start_hand()
        view = TableView(MagicMock(), game)
        for item in view.children:
            if item.emoji:
                # 「撲克牌」區塊（U+1F0A0–1F0FF）只有 🃏 是 Discord 表情，其他會讓整則訊息更新失敗
                for ch in item.emoji.name:
                    self.assertFalse(0x1F0A0 <= ord(ch) <= 0x1F0FF and ch != "🃏",
                                     f"按鈕「{item.label}」的圖示 {item.emoji.name} 不是 Discord 支援的表情")


if __name__ == "__main__":
    unittest.main()
