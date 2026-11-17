"""更新日誌：解析 git log、分頁、翻頁按鈕、大廳與嘎哇入口、功能開關。"""
import os
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from cogs import assistant as assistant_cog
from cogs import changelog as changelog_cog
from core import assistant as asst
from core import changelog as cl
from core.features import feature_for_action, feature_for_command

SEP, END = cl.FIELD_SEP, cl.RECORD_SEP


def record(sha: str, date: str, message: str) -> str:
    return f"{sha}{SEP}{date}{SEP}{message}{END}\n"


class ParseTest(unittest.TestCase):
    def test_message_drops_trailers_and_bullets(self):
        title, details = cl.parse_message(
            "新增更新日誌\n\n- /changelog 分頁\n- 嘎哇也能叫\n  接續的一行\n\n"
            "Co-Authored-By: Someone <x@example.com>\nClaude-Session: https://example.com/s\n")
        self.assertEqual(title, "新增更新日誌")
        self.assertEqual(details, ["/changelog 分頁", "嘎哇也能叫接續的一行"])

    def test_parse_log(self):
        output = (record("a" * 40, "2026-11-16T08:53:39+00:00", "第二版\n\n- 細節")
                  + record("b" * 40, "2026-11-15T10:00:00+08:00", "第一版")
                  + record("c" * 40, "壞掉的日期", "略過"))
        entries = cl.parse_log(output)
        self.assertEqual([e.title for e in entries], ["第二版", "第一版"])
        self.assertEqual(entries[0].when.hour, 16)            # 換成台灣時間
        self.assertEqual(entries[0].details, ["細節"])
        self.assertEqual(entries[0].short_sha, "aaaaaaa")

    def test_pages(self):
        entries = list(range(12))
        self.assertEqual(cl.page_count(0), 1)
        self.assertEqual(cl.page_count(12), 3)
        self.assertEqual(cl.page_slice(entries, 0), [0, 1, 2, 3, 4])
        self.assertEqual(cl.page_slice(entries, 2), [10, 11])
        self.assertEqual(cl.page_slice(entries, 99), [10, 11])    # 超出範圍夾到最後一頁
        self.assertEqual(cl.page_slice(entries, -3), [0, 1, 2, 3, 4])

    def test_format_entry_links_and_clips(self):
        entry = cl.parse_log(record("d" * 40, "2026-11-16T08:00:00+08:00",
                                    "標題\n" + "\n".join(f"- 第{i}點" for i in range(12))))[0]
        text = cl.format_entry(entry, "owner/repo")
        self.assertIn("https://github.com/owner/repo/commit/" + "d" * 40, text)
        self.assertIn("…還有 4 項", text)
        entry.details = ["很長" * 500]
        self.assertLessEqual(len(cl.format_entry(entry)), cl.MAX_ENTRY_CHARS)


class ReadLogTest(unittest.IsolatedAsyncioTestCase):
    async def test_reads_real_repo_without_merges(self):
        with tempfile.TemporaryDirectory() as repo:
            def run(*args):
                subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
            run("init", "-q", "-b", "main")
            run("config", "user.email", "t@example.com")
            run("config", "user.name", "t")
            run("remote", "add", "origin", "https://github.com/owner/repo.git")
            for i in range(3):
                with open(os.path.join(repo, "f.txt"), "w") as f:
                    f.write(str(i))
                run("add", ".")
                run("commit", "-q", "-m", f"更新 {i}\n\n- 細節 {i}\n\nCo-Authored-By: x <x@example.com>")
            log = cl.Changelog(repo)
            entries = await log.get()
            self.assertEqual([e.title for e in entries], ["更新 2", "更新 1", "更新 0"])
            self.assertEqual(entries[0].details, ["細節 2"])
            self.assertEqual(log.repo, "owner/repo")

    async def test_not_a_git_repo(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(cl.ChangelogError):
                await cl.read_log(folder)


def make_entries(n: int):
    return [cl.Entry(f"{i:040d}", cl.parse_log(record("x", "2026-11-16T08:00:00+08:00", "x"))[0].when, f"更新 {i}")
            for i in range(n)]


class ViewTest(unittest.IsolatedAsyncioTestCase):
    async def test_buttons_follow_page(self):
        view = changelog_cog.ChangelogView(make_entries(12), None, owner_id=1)
        self.assertTrue(view.previous.disabled and view.newest.disabled)
        self.assertFalse(view.next.disabled)
        self.assertEqual(view.indicator.label, "1 / 3")

        interaction = SimpleNamespace(response=SimpleNamespace(edit_message=AsyncMock()))
        await view.go(interaction, 1)
        self.assertEqual(view.indicator.label, "2 / 3")
        self.assertFalse(view.previous.disabled or view.next.disabled)
        embed = interaction.response.edit_message.call_args.kwargs["embed"]
        self.assertIn("更新 5", embed.description)
        self.assertNotIn("更新 4", embed.description)

        await view.go(interaction, 99)
        self.assertEqual(view.page, 2)
        self.assertTrue(view.next.disabled and view.oldest.disabled)
        self.assertIn("第 3 / 3 頁", interaction.response.edit_message.call_args.kwargs["embed"].footer.text)

    def test_empty(self):
        embed = changelog_cog.page_embed([], 0)
        self.assertIn("還沒有", embed.description)

    async def test_send_and_error(self):
        cog = changelog_cog.Changelog(SimpleNamespace())
        cog.log.get = AsyncMock(return_value=make_entries(7))
        interaction = MagicMock()
        interaction.user.id = 1
        interaction.response.send_message = AsyncMock()
        interaction.original_response = AsyncMock()
        await cog.send(interaction, page=1)
        kwargs = interaction.response.send_message.call_args.kwargs
        self.assertTrue(kwargs["ephemeral"])
        self.assertEqual(kwargs["view"].page, 1)

        cog.log.get = AsyncMock(side_effect=cl.ChangelogError("讀不到"))
        interaction.response.is_done = MagicMock(return_value=False)
        interaction.response.send_message = AsyncMock()
        await cog.send(interaction)
        self.assertIn("讀不到", interaction.response.send_message.call_args.kwargs["embed"].description)


class EntryPointsTest(unittest.TestCase):
    def test_feature_mapping(self):
        self.assertEqual(feature_for_command("changelog"), "changelog")
        self.assertEqual(feature_for_action("changelog"), "changelog")

    def test_assistant_offers_button(self):
        self.assertIn("changelog", asst.INTENTS)
        self.assertEqual(assistant_cog.buttons_for(asst.Intent("changelog")), [("changelog", "看更新日誌", "📰")])
        self.assertIn("changelog", assistant_cog.LINES)
        self.assertEqual(asst.parse_intent({"intent": "changelog"}).name, "changelog")


if __name__ == "__main__":
    unittest.main()
