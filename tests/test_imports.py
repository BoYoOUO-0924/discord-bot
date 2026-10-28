import importlib
import unittest


class ImportTest(unittest.TestCase):
    """載入主程式與所有功能模組。

    CI 是在全新下載的程式上跑，如果有檔案沒被 commit（例如被 .gitignore 誤擋），這裡就會失敗，
    筆電的自動更新也就不會裝到壞掉的版本。
    """

    def test_bot_and_all_extensions_import(self):
        bot = importlib.import_module("bot")
        for extension in bot.EXTENSIONS:
            with self.subTest(extension=extension):
                module = importlib.import_module(extension)
                self.assertTrue(hasattr(module, "setup"), f"{extension} 缺少 setup()")


if __name__ == "__main__":
    unittest.main()
