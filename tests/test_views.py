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


class DeferTest(unittest.IsolatedAsyncioTestCase):
    async def test_defer_then_respond_uses_followup(self):
        from core.ui import defer, respond

        state = {"done": False}

        async def fake_defer(**kwargs):
            state["done"] = True

        message = SimpleNamespace(id=1)
        interaction = SimpleNamespace(
            response=SimpleNamespace(is_done=lambda: state["done"], defer=AsyncMock(side_effect=fake_defer),
                                     send_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock(return_value=message)),
            channel=None,
        )
        await defer(interaction, ephemeral=True)
        await defer(interaction, ephemeral=True)   # 重複呼叫不會出錯
        interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        self.assertIs(await respond(interaction, content="hi", ephemeral=True), message)
        interaction.followup.send.assert_awaited_once()
        interaction.response.send_message.assert_not_awaited()


class PokerTableViewTest(unittest.IsolatedAsyncioTestCase):
    async def test_button_emojis_are_discord_emojis(self):
        from cogs.poker.cog import Lobby, PokerGame, TableView

        lobby = Lobby(SimpleNamespace(id=1), 10, 20, 1000)
        lobby.players[1] = SimpleNamespace(display_name="P1")
        lobby.add_ai()
        game = PokerGame(lobby)
        game.table.start_hand()
        game.table.dealer_id = game.table.seats[0].user_id
        view = TableView(MagicMock(), game)
        emojis = [(getattr(item, "label", None), getattr(item, "emoji", None)) for item in view.children]
        for item in view.children:
            emojis += [(o.label, o.emoji) for o in getattr(item, "options", [])]
        for label, emoji in emojis:
            if emoji:
                # 「撲克牌」區塊（U+1F0A0–1F0FF）只有 🃏 是 Discord 表情，其他會讓整則訊息更新失敗
                for ch in emoji.name:
                    self.assertFalse(0x1F0A0 <= ord(ch) <= 0x1F0FF and ch != "🃏",
                                     f"「{label}」的圖示 {emoji.name} 不是 Discord 支援的表情")

    def _game(self, humans=1, bots=1):
        from cogs.poker.cog import Lobby, PokerGame

        lobby = Lobby(SimpleNamespace(id=1), 10, 20, 1000)
        for uid in range(1, humans + 1):
            lobby.players[uid] = SimpleNamespace(display_name=f"玩家名字很長很長很長很長很長很長很長{uid}")
        for _ in range(bots):
            lobby.add_ai()
        game = PokerGame(lobby)
        game.table.dealer_id = game.table.seats[-1].user_id   # 讓第一手莊家固定是第一位
        return game

    def test_controls_only_enabled_for_player_to_act(self):
        from cogs.poker.cog import HandView, TableView

        game = self._game(humans=2, bots=0)
        game.table.start_hand()
        actor = game.table.current_seat.user_id
        other = 2 if actor == 1 else 1

        def enabled(view):
            return [getattr(i, "label", None) or i.placeholder for i in view.children if not i.disabled]

        self.assertIn("棄牌", enabled(TableView(MagicMock(), game)))
        self.assertIn("棄牌", enabled(HandView(MagicMock(), game, actor)))
        self.assertNotIn("棄牌", enabled(HandView(MagicMock(), game, other)))
        # 加注選單有預設金額可選
        select = next(i for i in TableView(MagicMock(), game).children if hasattr(i, "options"))
        self.assertFalse(select.disabled)
        self.assertGreaterEqual(len(select.options), 3)

    def test_embeds_fit_discord_limits_at_full_showdown(self):
        from cogs.poker.cog import MAX_SEATS, Poker

        game = self._game(humans=4, bots=MAX_SEATS - 4)
        t = game.table
        t.start_hand()
        while not t.hand_over:   # 大家一路跟到攤牌
            t.act(t.current_seat.user_id, "call")
        cog = Poker(MagicMock())
        embed = cog.table_embed(game)
        for field in embed.fields:
            self.assertLessEqual(len(field.value), 1024)
        self.assertLessEqual(len(embed), 6000)
        self.assertTrue(any(f.name == "🏆 結果" for f in embed.fields))
        panel = cog.panel_embed(game, t.seats[0])
        self.assertIn("牌型", "".join(f.name for f in panel.fields))


if __name__ == "__main__":
    unittest.main()
