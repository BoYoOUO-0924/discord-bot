"""Minecraft 管理（/mc）：用假的控制腳本測試，不會碰到真的伺服器。"""
import os
import stat
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cogs import minecraft as mc


def fake_interaction(admin=True):
    sent = []

    async def send_message(**kwargs):
        sent.append(kwargs)

    user = SimpleNamespace(id=5, display_name="小明", guild_permissions=SimpleNamespace(manage_guild=admin))
    return SimpleNamespace(user=user, sent=sent, channel=None, guild_id=1,
                           response=SimpleNamespace(is_done=lambda: bool(sent), send_message=send_message,
                                                    defer=AsyncMock()),
                           followup=SimpleNamespace(send=AsyncMock(side_effect=lambda **k: sent.append(k))),
                           original_response=AsyncMock())


class HelpersTest(unittest.TestCase):
    def test_check_command(self):
        self.assertEqual(mc.check_command(" /say  大家好 "), ("say 大家好", None))
        self.assertEqual(mc.check_command("op Steve")[0], "op Steve")
        for bad in ("", "stop", "STOP now", "C-c", "Enter", "^C", "x" * 201):
            with self.subTest(bad=bad):
                self.assertIsNone(mc.check_command(bad)[0])

    def test_clean_output(self):
        self.assertEqual(mc.clean_output("\x1b[32m🟢 開著\x1b[0m\r\n"), "🟢 開著")
        self.assertNotIn("```", mc.clean_output("a```b"))
        self.assertLessEqual(len(mc.clean_output("x" * 5000)), mc.MAX_OUTPUT + 2)


class CogTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.calls = os.path.join(self.tmp.name, "calls")
        self.script = os.path.join(self.tmp.name, "mc")
        with open(self.script, "w") as f:
            f.write(f'#!/bin/bash\necho "$@" >> {self.calls}\necho "🟢 伺服器開著 $*"\n')
        os.chmod(self.script, os.stat(self.script).st_mode | stat.S_IEXEC)
        self.bot = SimpleNamespace(db=SimpleNamespace(audit=AsyncMock()))
        self.cog = mc.Minecraft(self.bot, self.script)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    def called(self):
        try:
            with open(self.calls) as f:
                return f.read().splitlines()
        except FileNotFoundError:
            return []

    async def test_run_script_and_timeout(self):
        code, out = await mc.run_script(self.script, "cmd", "say hi; rm -rf /")
        self.assertEqual(code, 0)
        self.assertEqual(self.called(), ["cmd say hi; rm -rf /"])      # 參數直接傳，不經過 shell
        code, _ = await mc.run_script("/bin/sleep", "5", timeout=0.2)
        self.assertEqual(code, -1)

    async def test_status_is_public_and_actions_need_admin(self):
        interaction = fake_interaction(admin=False)
        await mc.Minecraft.status.callback(self.cog, interaction)
        self.assertIn("伺服器開著", interaction.sent[-1]["embed"].description)
        interaction = fake_interaction(admin=False)
        await mc.Minecraft.stop.callback(self.cog, interaction)
        self.assertIn("管理伺服器", interaction.sent[-1]["embed"].description)
        self.assertEqual(self.called(), ["status"])

    async def test_start_refuses_without_safe_tmux(self):
        with patch.object(mc, "tmux_server_ok", AsyncMock(return_value=(False, "tmux 沒在跑"))):
            interaction = fake_interaction()
            await mc.Minecraft.start.callback(self.cog, interaction)
        self.assertIn("tmux 沒在跑", interaction.sent[-1]["embed"].description)
        self.assertEqual(self.called(), [])
        with patch.object(mc, "tmux_server_ok", AsyncMock(return_value=(True, ""))):
            interaction = fake_interaction()
            await mc.Minecraft.start.callback(self.cog, interaction)
        self.assertEqual(self.called(), ["start"])
        self.assertIn("開服", interaction.sent[-1]["embed"].title)
        self.bot.db.audit.assert_awaited()

    async def test_busy_and_cmd(self):
        self.cog.busy = "開服"
        interaction = fake_interaction()
        await mc.Minecraft.backup.callback(self.cog, interaction)
        self.assertIn("正在開服", interaction.sent[-1]["embed"].description)
        interaction = fake_interaction()
        await mc.Minecraft.cmd.callback(self.cog, interaction, "C-c")
        self.assertEqual(self.called(), [])
        await mc.Minecraft.cmd.callback(self.cog, fake_interaction(), "/list")
        self.assertEqual(self.called(), ["cmd list"])


class SetupTest(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_without_guilds(self):
        bot = SimpleNamespace(settings=SimpleNamespace(minecraft_script="/bin/true", minecraft_guild_ids=()),
                              add_cog=AsyncMock())
        await mc.setup(bot)
        bot.add_cog.assert_not_awaited()
        bot.settings = SimpleNamespace(minecraft_script="/bin/true", minecraft_guild_ids=(123,))
        await mc.setup(bot)
        self.assertEqual(bot.add_cog.await_args.kwargs["guilds"][0].id, 123)


if __name__ == "__main__":
    unittest.main()
