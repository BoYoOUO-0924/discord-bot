"""從 GitHub 自動更新：只套用 GitHub 自動測試（CI）通過的新版本。

這裡只放不依賴 Discord 的邏輯（git 操作、CI 狀態判斷、是否更新的決策），方便測試。
"""
import asyncio
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

GIT_TIMEOUT = 120
GITHUB_API = "https://api.github.com"


class UpdateError(Exception):
    """更新過程失敗，訊息會寫進紀錄。"""


async def git(repo_dir: str, *args: str) -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", *args, cwd=repo_dir, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(proc.communicate(), GIT_TIMEOUT)
    except (OSError, asyncio.TimeoutError) as e:
        raise UpdateError(f"無法執行 git {' '.join(args)}：{e}") from e
    if proc.returncode != 0:
        raise UpdateError(f"git {' '.join(args)} 失敗：{err.decode(errors='replace').strip()}")
    return out.decode(errors="replace").strip()


def parse_github_repo(url: str) -> Optional[str]:
    """https://github.com/owner/repo(.git) 或 git@github.com:owner/repo.git → owner/repo"""
    match = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?/?$", url.strip())
    return match.group(1) if match else None


@dataclass
class RepoStatus:
    branch: str
    current: str
    latest: str
    ahead: int    # 本機有、遠端沒有的 commit
    behind: int   # 遠端有、本機還沒有的 commit
    dirty: bool   # 本機有未提交的修改
    changed_files: List[str] = field(default_factory=list)   # 被改過的檔案，顯示在紀錄裡


async def check(repo_dir: str, remote: str = "origin", branch: str = "main") -> RepoStatus:
    await git(repo_dir, "fetch", "--quiet", remote, branch)
    target = f"{remote}/{branch}"
    changes = await git(repo_dir, "status", "--porcelain", "--untracked-files=no")
    ahead, behind = (int(n) for n in (await git(repo_dir, "rev-list", "--left-right", "--count",
                                                f"HEAD...{target}")).split())
    return RepoStatus(
        branch=await git(repo_dir, "rev-parse", "--abbrev-ref", "HEAD"),
        current=await git(repo_dir, "rev-parse", "HEAD"),
        latest=await git(repo_dir, "rev-parse", target),
        ahead=ahead,
        behind=behind,
        dirty=bool(changes),
        # 每行是「狀態 檔名」；第一行開頭的空白可能被 strip 掉，所以用空白切開
        changed_files=[line.split(maxsplit=1)[-1] for line in changes.splitlines() if line.strip()],
    )


async def apply(repo_dir: str, remote: str = "origin", branch: str = "main") -> None:
    # 只允許快轉合併，絕不覆蓋或改寫本機的修改
    await git(repo_dir, "merge", "--ff-only", "--quiet", f"{remote}/{branch}")


def summarize_check_runs(data: Dict) -> str:
    """GitHub check-runs API 的回應 → 'success'、'pending'、'failure' 或 'none'。"""
    runs = data.get("check_runs") or []
    if not runs:
        return "none"
    if any(run.get("status") != "completed" for run in runs):
        return "pending"
    if all(run.get("conclusion") in ("success", "neutral", "skipped") for run in runs):
        return "success"
    return "failure"


async def ci_state(session, repo_slug: str, sha: str) -> str:
    url = f"{GITHUB_API}/repos/{repo_slug}/commits/{sha}/check-runs"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "gawa-discord-bot-updater"}
    try:
        async with session.get(url, headers=headers) as resp:
            if resp.status != 200:
                raise UpdateError(f"查詢 CI 狀態失敗（HTTP {resp.status}）")
            return summarize_check_runs(await resp.json())
    except UpdateError:
        raise
    except Exception as e:
        raise UpdateError(f"查詢 CI 狀態失敗：{e}") from e


def decide(status: RepoStatus, ci: Optional[str], branch: str = "main") -> Tuple[str, str]:
    """回傳 (動作, 原因)。動作：'update'、'wait'（之後再檢查）、'skip'（不更新）、'current'（已是最新）。"""
    if status.branch != branch:
        return "skip", f"目前在 {status.branch} 分支，只會自動更新 {branch}"
    if status.behind == 0:
        return "current", "已經是最新版本"
    if status.dirty:
        files = "、".join(status.changed_files[:5]) or "?"
        hint = ""
        if ".env.example" in status.changed_files:
            hint = "。看起來把設定填在 .env.example 了：請複製一份改名成 .env，再還原 .env.example"
        return "skip", f"本機改過這些檔案：{files}，為了不覆蓋，略過自動更新{hint}"
    if status.ahead:
        return "skip", "本機有自己的修改，為了不覆蓋，略過自動更新"
    if ci == "success":
        return "update", f"有 {status.behind} 個新版本，自動測試已通過"
    if ci == "failure":
        return "skip", "新版本的自動測試沒有通過，先不更新"
    return "wait", "新版本的自動測試還在跑，稍後再檢查"
