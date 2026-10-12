import random
import unittest
from dataclasses import replace
from datetime import date

from cogs.poker.ai import PERSONALITIES, decide, estimate_equity
from cogs.poker.cards import Card
from cogs.poker.engine import PokerTable, Seat
from core.quests import QUEST_POOL, daily_quests


def C(text: str) -> Card:
    return Card(text[:-1], text[-1])


class EquityTest(unittest.TestCase):
    def test_pocket_aces_are_favourite(self):
        equity = estimate_equity([C("A♠"), C("A♥")], [], 1, 600, random.Random(1))
        self.assertGreater(equity, 0.78)

    def test_seven_two_is_underdog(self):
        equity = estimate_equity([C("7♠"), C("2♥")], [], 1, 600, random.Random(1))
        self.assertLess(equity, 0.42)

    def test_made_nut_flush_on_river(self):
        board = [C("K♥"), C("9♥"), C("4♥"), C("2♣"), C("J♦")]
        equity = estimate_equity([C("A♥"), C("Q♥")], board, 2, 300, random.Random(1))
        self.assertGreater(equity, 0.95)


class AIPlayTest(unittest.TestCase):
    """電腦和隨機玩家混打，確認電腦每個決定都合法、籌碼守恆。"""

    def test_ai_decisions_are_legal(self):
        rng = random.Random(99)
        for round_ in range(12):
            n = rng.randint(2, 5)
            seats = [Seat(-(i + 1), f"AI{i}", rng.choice([200, 1000])) for i in range(n - 1)] + [Seat(1, "Human", 1000)]
            table = PokerTable(seats, 10, 20)
            total = sum(s.stack for s in seats)
            # 測試只檢查動作合不合法，模擬次數調低以加快速度
            personalities = {s.user_id: replace(PERSONALITIES[i % len(PERSONALITIES)], sims=40)
                             for i, s in enumerate(seats)}
            for _hand in range(3):
                if len([s for s in table.seats if s.stack > 0]) < 2:
                    break
                table.start_hand()
                while not table.hand_over:
                    seat = table.current_seat
                    if seat.user_id < 0:
                        action, amount = decide(table, seat, personalities[seat.user_id], rng)
                    else:
                        legal = table.legal(seat)
                        action = "check" if legal.can_check else rng.choice(["call", "fold"])
                        amount = None
                    table.act(seat.user_id, action, amount)   # 不合法會直接丟例外讓測試失敗
                self.assertEqual(sum(s.stack for s in table.seats), total)


class QuestTest(unittest.TestCase):
    def test_daily_quests_are_stable_per_day(self):
        day = date(2026, 10, 12)
        first = daily_quests(42, day)
        self.assertEqual(first, daily_quests(42, day))
        self.assertEqual(len({q.key for q in first}), 3)

    def test_ai_quests_hidden_without_ai(self):
        for uid in range(200):
            self.assertTrue(all(not q.needs_ai for q in daily_quests(uid, date(2026, 10, 12), ai_available=False)))

    def test_quest_matching(self):
        quests = {q.key: q for q in QUEST_POOL}
        self.assertTrue(quests["win2"].matches("game", {"game": "slots", "won": True}))
        self.assertFalse(quests["win2"].matches("game", {"game": "slots", "won": False}))
        self.assertTrue(quests["poker5"].matches("game", {"game": "poker", "won": False}))
        self.assertFalse(quests["poker5"].matches("game", {"game": "slots", "won": True}))
        self.assertTrue(quests["chat20"].matches("chat", {}))


if __name__ == "__main__":
    unittest.main()
