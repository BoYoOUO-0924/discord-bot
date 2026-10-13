import random
import unittest
from dataclasses import replace
from datetime import date

from cogs.poker.ai import PERSONALITIES, decide, estimate_equity, preflop_rank
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


class PreflopRankTest(unittest.TestCase):
    def test_rank_order(self):
        self.assertLess(preflop_rank([C("A♠"), C("A♥")]), 0.01)            # AA 第一名
        self.assertLess(preflop_rank([C("K♠"), C("A♠")]), 0.03)            # AKs 前 3%
        self.assertEqual(preflop_rank([C("7♠"), C("2♥")]), 1.0)            # 72 不同花最後一名
        self.assertLess(preflop_rank([C("Q♥"), C("J♥")]), preflop_rank([C("Q♥"), C("J♣")]))


class LooseTableTest(unittest.TestCase):
    """8 人全電腦桌：確認每手有足夠多電腦跟進，入局率接近個性設定。"""

    def test_most_bots_see_the_flop(self):
        rng = random.Random(2024)
        hands, flop_seers = 300, 0
        entered = {p.name: [0, 0] for p in PERSONALITIES}   # 個性 -> [入局次數, 手數]
        for _ in range(hands):
            seats = [Seat(-(i + 1), PERSONALITIES[i % 4].name, 2000) for i in range(8)]
            people = {s.user_id: replace(PERSONALITIES[i % 4], sims=30) for i, s in enumerate(seats)}
            table = PokerTable(seats, 10, 20)
            table.dealer_id = rng.choice(seats).user_id
            table.start_hand()
            voluntary = set()
            while not table.hand_over and not table.board:
                seat = table.current_seat
                action, amount = decide(table, seat, people[seat.user_id], rng)
                if action in ("call", "raise", "all_in"):
                    voluntary.add(seat.user_id)
                table.act(seat.user_id, action, amount)
            if table.board:
                flop_seers += sum(1 for s in table.order if not s.folded)
            for s in seats:
                if s.user_id == table.bb_id:
                    continue   # 大盲常常免費看牌，不列入入局率
                stats = entered[people[s.user_id].name]
                stats[1] += 1
                stats[0] += s.user_id in voluntary
        # 8 人桌平均 5.5 位看翻牌 ≈ 真人加 7 位電腦時，電腦有 5 位以上跟進
        self.assertGreaterEqual(flop_seers / hands, 5.5)
        for p in PERSONALITIES:
            played, total = entered[p.name]
            self.assertAlmostEqual(played / total, p.vpip, delta=0.12, msg=p.name)


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
