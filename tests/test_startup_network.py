import socket
import unittest
from unittest.mock import patch

import bot


class WaitForNetworkTest(unittest.TestCase):
    def test_waits_until_dns_works(self):
        calls = [socket.gaierror(-3, "Temporary failure"), socket.gaierror(-3, "Temporary failure"), []]

        def fake_lookup(*args):
            result = calls.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        with patch.object(bot.socket, "getaddrinfo", fake_lookup), patch.object(bot.time, "sleep") as sleep:
            self.assertTrue(bot.wait_for_network(timeout=60))
        self.assertEqual(sleep.call_count, 2)

    def test_gives_up_after_timeout(self):
        with patch.object(bot.socket, "getaddrinfo", side_effect=socket.gaierror(-3, "x")), \
                patch.object(bot.time, "sleep"), patch.object(bot.time, "monotonic", side_effect=[0, 0, 400]):
            self.assertFalse(bot.wait_for_network(timeout=300))


if __name__ == "__main__":
    unittest.main()
