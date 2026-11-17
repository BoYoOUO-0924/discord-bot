import ast
import pathlib
import unittest

from discord.ext import commands

ROOT = pathlib.Path(__file__).resolve().parent.parent


class BotAttributeTest(unittest.TestCase):
    def test_init_does_not_shadow_discord_attributes(self):
        """Bot.__init__ 自己加的屬性不能跟 discord.py 內建的撞名（例如 activity 是 bot 狀態，設錯型別會讓 bot 無法啟動）。"""
        tree = ast.parse((ROOT / "bot.py").read_text(encoding="utf-8"))
        bot_cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Bot")
        init = next(n for n in bot_cls.body if isinstance(n, ast.FunctionDef) and n.name == "__init__")
        names = {t.attr for node in ast.walk(init) if isinstance(node, ast.Assign) for t in node.targets
                 if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self"}
        self.assertIn("command_stats", names)
        clash = sorted(n for n in names if hasattr(commands.Bot, n))
        self.assertEqual(clash, [], f"這些名稱 discord.py 已經在用：{clash}")


if __name__ == "__main__":
    unittest.main()
