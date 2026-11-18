"""AI 連線狀態：離線一陣子後要能自動恢復，不用重啟機器人。"""
import time
import unittest

from core import ai


class AvailableTest(unittest.TestCase):
    def setUp(self):
        self.client = ai.OllamaClient("http://example:11434", "model")

    def test_not_configured(self):
        self.assertFalse(ai.OllamaClient(None, "model").available)

    def test_unchecked_is_available(self):
        self.assertTrue(self.client.available)

    def test_just_failed_is_unavailable(self):
        self.client._mark(False, "連不到")
        self.assertFalse(self.client.available)

    def test_retry_after_a_while(self):
        self.client._mark(False, "連不到")
        self.client.checked_at = time.time() - ai.RETRY_AFTER - 1
        self.assertTrue(self.client.available)

    def test_recovers(self):
        self.client._mark(False, "連不到")
        self.client._mark(True, "正常")
        self.assertTrue(self.client.available)


if __name__ == "__main__":
    unittest.main()
