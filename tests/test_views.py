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


if __name__ == "__main__":
    unittest.main()
