import unittest
import sys
import os

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cogs.battle_logic import calculate_damage, TYPE_CHART, ELEMENT_MAP

class TestBattleLogic(unittest.TestCase):
    def setUp(self):
        self.attacker = {
            "name": "Attacker",
            "pet": {
                "stats": {"atk": 50},
                "element": "fire"
            },
            "status_effects": []
        }
        self.defender = {
            "name": "Defender",
            "pet": {
                "stats": {"def": 20},
                "element": "forest"
            },
            "status_effects": []
        }

    def test_element_map(self):
        """Test Chinese to Key mapping"""
        self.assertEqual(ELEMENT_MAP["火"], "fire")
        self.assertEqual(ELEMENT_MAP["幽靈"], "ghost")

    def test_damage_basic(self):
        """Test basic damage formula without type advantage"""
        # Power 50
        # Dmg = (ATK * 0.5 * 2) - (DEF * 0.2)
        #     = (50 * 0.5 * 2) - (20 * 0.2)
        #     = 50 - 4 = 46
        dmg, eff = calculate_damage(self.attacker, self.defender, 50, atk_element='normal', def_element='normal')
        self.assertEqual(dmg, 46)
        self.assertEqual(eff, 1.0)

    def test_damage_advantage(self):
        """Test Fire > Forest advantage (1.5x)"""
        # Base 46 * 1.5 = 69
        dmg, eff = calculate_damage(self.attacker, self.defender, 50, atk_element='fire', def_element='forest')
        self.assertEqual(eff, 1.5)
        self.assertEqual(dmg, 69)

    def test_damage_disadvantage(self):
        """Test Fire < Water disadvantage (0.5x)"""
        dmg, eff = calculate_damage(self.attacker, self.defender, 50, atk_element='fire', def_element='water')
        self.assertEqual(eff, 0.5)
        # 46 * 0.5 = 23
        self.assertEqual(dmg, 23)

    def test_buff_impact(self):
        """Test Buff increasing ATK"""
        self.attacker['status_effects'].append({
            "type": "buff", "stat": "atk", "value": 10, "id": "buff_atk"
        })
        # New ATK = 60
        # (60 * 0.5 * 2) - 4 = 60 - 4 = 56
        dmg, eff = calculate_damage(self.attacker, self.defender, 50, atk_element='normal', def_element='normal')
        self.assertEqual(dmg, 56)

if __name__ == '__main__':
    unittest.main()
