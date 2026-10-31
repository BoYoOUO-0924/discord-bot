import os
import shutil
import subprocess
import tempfile
import unittest

from core import updater
from core.updater import RepoStatus, decide, parse_github_repo, summarize_check_runs


def status(**overrides) -> RepoStatus:
    base = dict(branch="main", current="a" * 40, latest="b" * 40, ahead=0, behind=1, dirty=False)
    base.update(overrides)
    return RepoStatus(**base)


class DecisionTest(unittest.TestCase):
    def test_parse_repo_url(self):
        self.assertEqual(parse_github_repo("https://github.com/BoYoOUO-0924/discord-bot"), "BoYoOUO-0924/discord-bot")
        self.assertEqual(parse_github_repo("https://github.com/a/b.git"), "a/b")
        self.assertEqual(parse_github_repo("git@github.com:a/b.git"), "a/b")
        self.assertIsNone(parse_github_repo("https://gitlab.com/a/b"))

    def test_check_runs_summary(self):
        ok = {"status": "completed", "conclusion": "success"}
        self.assertEqual(summarize_check_runs({"check_runs": [ok]}), "success")
        self.assertEqual(summarize_check_runs({"check_runs": [ok, {"status": "in_progress"}]}), "pending")
        self.assertEqual(summarize_check_runs({"check_runs": [ok, {"status": "completed", "conclusion": "failure"}]}),
                         "failure")
        self.assertEqual(summarize_check_runs({"check_runs": []}), "none")

    def test_decisions(self):
        self.assertEqual(decide(status(behind=0), None)[0], "current")
        self.assertEqual(decide(status(), "success")[0], "update")
        self.assertEqual(decide(status(), "pending")[0], "wait")
        self.assertEqual(decide(status(), "none")[0], "wait")
        self.assertEqual(decide(status(), "failure")[0], "skip")
        # 本機有修改、或不在 main，就算測試通過也不更新
        self.assertEqual(decide(status(dirty=True), "success")[0], "skip")
        self.assertEqual(decide(status(ahead=1), "success")[0], "skip")
        self.assertEqual(decide(status(branch="dev"), "success")[0], "skip")


@unittest.skipUnless(shutil.which("git"), "需要 git")
class GitFlowTest(unittest.IsolatedAsyncioTestCase):
    """模擬：開發機推了新版到 GitHub，筆電抓下來快轉更新。"""

    def run_git(self, cwd, *args):
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                   GIT_COMMITTER_EMAIL="t@t")
        subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True)

    def commit(self, repo, name, content):
        with open(os.path.join(repo, name), "w", encoding="utf-8") as f:
            f.write(content)
        self.run_git(repo, "add", name)
        self.run_git(repo, "commit", "-q", "-m", f"update {name}")

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.remote, self.dev, self.laptop = (os.path.join(self.tmp, n) for n in ("remote.git", "dev", "laptop"))
        self.run_git(self.tmp, "init", "-q", "--bare", "-b", "main", self.remote)
        self.run_git(self.tmp, "clone", "-q", self.remote, self.dev)
        self.run_git(self.dev, "checkout", "-q", "-b", "main")
        self.commit(self.dev, "bot.py", "v1")
        self.run_git(self.dev, "push", "-q", "origin", "main")
        self.run_git(self.tmp, "clone", "-q", "-b", "main", self.remote, self.laptop)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    async def test_fast_forward_update(self):
        self.assertEqual((await updater.check(self.laptop)).behind, 0)
        self.commit(self.dev, "bot.py", "v2")
        self.run_git(self.dev, "push", "-q", "origin", "main")

        result = await updater.check(self.laptop)
        self.assertEqual((result.behind, result.ahead, result.dirty), (1, 0, False))
        await updater.apply(self.laptop)
        with open(os.path.join(self.laptop, "bot.py"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "v2")
        self.assertEqual((await updater.check(self.laptop)).behind, 0)

    async def test_local_changes_are_detected(self):
        self.commit(self.dev, "bot.py", "v2")
        self.run_git(self.dev, "push", "-q", "origin", "main")
        with open(os.path.join(self.laptop, "bot.py"), "w", encoding="utf-8") as f:
            f.write("我自己改的")
        result = await updater.check(self.laptop)
        self.assertTrue(result.dirty)
        self.assertEqual(result.changed_files, ["bot.py"])
        action, reason = decide(result, "success")
        self.assertEqual(action, "skip")
        self.assertIn("bot.py", reason)


if __name__ == "__main__":
    unittest.main()
