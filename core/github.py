"""GitHub 整合（專案管理第三階段）：定時讀 GitHub API，把 issue、PR、CI 失敗通知到專案頻道，
commit 訊息寫「task#12」或「待辦#12」就完成對應的待辦。

用輪詢（polling）而不是 Webhook：bot 跑在家裡的電腦上，GitHub 連不進來。
沒有 GITHUB_TOKEN 時用匿名 API（每小時 60 次），只能讀公開 repo，所以輪詢間隔拉長。
這個模組不依賴 Discord，方便測試。
"""
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import aiohttp

from . import tasks as tk

log = logging.getLogger(__name__)

API = "https://api.github.com"
POLL_MINUTES_WITH_TOKEN = 5
POLL_MINUTES_ANONYMOUS = 10      # 匿名每小時 60 次，每個 repo 每次輪詢用 3 次
RUN_LOOKBACK = timedelta(days=1)  # 只通知一天內的 CI 失敗
SEEN_KEEP = 100

_REPO_RE = re.compile(r"^(?:https?://github\.com/)?([A-Za-z0-9-]+)/([A-Za-z0-9._-]+?)(?:\.git)?/?$")


class GitHubError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


def normalize_repo(text: str) -> Optional[str]:
    """「owner/repo」或 GitHub 網址 → 「owner/repo」；格式不對回傳 None。"""
    match = _REPO_RE.match((text or "").strip())
    return f"{match.group(1)}/{match.group(2)}" if match else None


@dataclass
class State:
    """上次讀到哪裡，存在 projects.github_state（JSON）。"""
    event_id: int = 0
    runs_seen: List[int] = field(default_factory=list)
    commits_seen: List[str] = field(default_factory=list)
    commit_since: str = ""            # ISO 時間，只讀這之後的 commit
    initialized: bool = False

    @classmethod
    def load(cls, text: Optional[str]) -> "State":
        try:
            data = json.loads(text or "{}")
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except (ValueError, TypeError):
            return cls()

    def dump(self) -> str:
        self.runs_seen = self.runs_seen[-SEEN_KEEP:]
        self.commits_seen = self.commits_seen[-SEEN_KEEP:]
        return json.dumps(self.__dict__, ensure_ascii=False)


@dataclass
class Notice:
    text: str
    url: str = ""


@dataclass
class PollResult:
    notices: List[Notice] = field(default_factory=list)
    task_refs: Dict[int, str] = field(default_factory=dict)   # 待辦編號 → 完成它的 commit（短 SHA）


def _clip(text: str, limit: int = 80) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def event_notices(events: List[dict], last_id: int) -> List[Notice]:
    """issue 開／關、PR 開／合併。events 是 GitHub 回傳的順序（新到舊），回傳舊到新。"""
    notices = []
    for event in reversed(events):
        if int(event.get("id", 0)) <= last_id:
            continue
        kind, payload = event.get("type"), event.get("payload") or {}
        actor = (event.get("actor") or {}).get("login", "?")
        action = payload.get("action")
        if kind == "IssuesEvent" and action in ("opened", "closed"):
            issue = payload.get("issue") or {}
            verb = "🐛 新 issue" if action == "opened" else "✔️ issue 已關閉"
            notices.append(Notice(f"{verb} #{issue.get('number')}：{_clip(issue.get('title'))}（{actor}）",
                                  issue.get("html_url", "")))
        elif kind == "PullRequestEvent" and action in ("opened", "closed"):
            pr = payload.get("pull_request") or {}
            if action == "closed" and not pr.get("merged"):
                continue
            verb = "🔀 新 PR" if action == "opened" else "✅ PR 已合併"
            notices.append(Notice(f"{verb} #{pr.get('number')}：{_clip(pr.get('title'))}（{actor}）", pr.get("html_url", "")))
    return notices


def _parse_time(text: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat((text or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def failed_runs(runs: List[dict], seen: List[int], now: datetime) -> List[Notice]:
    """一天內、還沒通知過、已經結束而且失敗的 GitHub Actions。"""
    notices = []
    for run in reversed(runs):
        if run.get("id") in seen or run.get("status") != "completed":
            continue
        if run.get("conclusion") not in ("failure", "timed_out", "startup_failure"):
            continue
        created = _parse_time(run.get("created_at"))
        if created and now - created > RUN_LOOKBACK:
            continue
        notices.append(Notice(f"❌ GitHub Actions 失敗：{_clip(run.get('name') or run.get('display_title'), 50)}"
                              f"（{run.get('head_branch', '?')}）", run.get("html_url", "")))
    return notices


def commit_refs(commits: List[dict], seen: List[str]) -> Dict[int, str]:
    """commit 訊息裡的 task#N → 完成它的 commit 短 SHA。舊的 commit 先處理。"""
    refs: Dict[int, str] = {}
    for commit in reversed(commits):
        sha = commit.get("sha", "")
        if not sha or sha in seen:
            continue
        message = (commit.get("commit") or {}).get("message", "")
        for number in tk.task_refs(message):
            refs.setdefault(number, sha[:7])
    return refs


class GitHubClient:
    def __init__(self, token: Optional[str] = None, session: Optional[aiohttp.ClientSession] = None):
        self.token = token
        self._session = session
        self.rate_limited_until = 0.0

    @property
    def poll_minutes(self) -> int:
        return POLL_MINUTES_WITH_TOKEN if self.token else POLL_MINUTES_ANONYMOUS

    async def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def get(self, path: str, **params):
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "gawa-discord-bot",
                   "X-GitHub-Api-Version": "2022-11-28"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        session = await self.session()
        try:
            async with session.get(f"{API}{path}", headers=headers, params=params) as response:
                if response.status == 200:
                    return await response.json()
                if response.status in (403, 429) and response.headers.get("X-RateLimit-Remaining") == "0":
                    reset = float(response.headers.get("X-RateLimit-Reset", 0) or 0)
                    self.rate_limited_until = reset
                    raise GitHubError("GitHub API 次數用完了，設定 GITHUB_TOKEN 可以提高上限", response.status)
                if response.status == 404:
                    raise GitHubError("找不到這個 repo（私人 repo 需要設定 GITHUB_TOKEN）", 404)
                if response.status == 401:
                    raise GitHubError("GITHUB_TOKEN 無效或已過期", 401)
                raise GitHubError(f"GitHub 回應 {response.status}", response.status)
        except aiohttp.ClientError as e:
            raise GitHubError(f"連不到 GitHub：{e}") from e

    async def repo(self, repo: str) -> dict:
        return await self.get(f"/repos/{repo}")

    async def poll(self, repo: str, state: State, now: Optional[datetime] = None) -> PollResult:
        """讀一次 repo 的新動態並更新 state。第一次（剛綁定）只記錄目前位置，不通知舊的東西。"""
        now = now or datetime.now(timezone.utc)
        events = await self.get(f"/repos/{repo}/events", per_page=30)
        runs = (await self.get(f"/repos/{repo}/actions/runs", per_page=20)).get("workflow_runs", [])
        since = state.commit_since or (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        commits = await self.get(f"/repos/{repo}/commits", since=since, per_page=30)

        result = PollResult()
        if state.initialized:
            result.notices += event_notices(events, state.event_id)
            result.notices += failed_runs(runs, state.runs_seen, now)
            result.task_refs = commit_refs(commits, state.commits_seen)

        if events:
            state.event_id = max(state.event_id, max(int(e.get("id", 0)) for e in events))
        state.runs_seen += [r["id"] for r in runs if r.get("status") == "completed" and r["id"] not in state.runs_seen]
        state.commits_seen += [c["sha"] for c in reversed(commits) if c.get("sha") and c["sha"] not in state.commits_seen]
        dates = [d for d in (_parse_time(((c.get("commit") or {}).get("committer") or {}).get("date", ""))
                             for c in commits) if d]
        if dates:
            state.commit_since = max(dates).strftime("%Y-%m-%dT%H:%M:%SZ")
        elif not state.commit_since:
            state.commit_since = since
        state.initialized = True
        return result
