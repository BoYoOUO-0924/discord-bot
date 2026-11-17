"""更新日誌：從 git commit 紀錄整理出「更新了哪些功能」，給 /changelog 分頁顯示。

- 讀本機的 git log（bot 是用 git clone 下載、由自動更新拉新版本的），看到的就是目前執行中這個版本的內容
- 合併（merge）commit 不列；Co-Authored-By、Claude-Session 這類署名行會拿掉
- 讀到的結果快取幾分鐘，翻頁不會每次都跑 git

這個模組不依賴 Discord，方便測試。
"""
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Sequence

from core.timeutil import TAIPEI
from core.updater import UpdateError, git, parse_github_repo

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAX_COMMITS = 500         # 最多讀幾筆，夠看很久的歷史
PER_PAGE = 5              # 每頁幾筆更新
CACHE_SECONDS = 300
MAX_DETAILS = 8           # 每筆最多列幾行細節
MAX_ENTRY_CHARS = 700     # 每筆最多幾個字，避免一頁超過 Discord 的長度限制

FIELD_SEP, RECORD_SEP = "\x1f", "\x1e"
LOG_FORMAT = f"%H{FIELD_SEP}%cI{FIELD_SEP}%B{RECORD_SEP}"
TRAILER = re.compile(r"^(Co-Authored-By|Claude-Session|Signed-off-by|Reviewed-by|Change-Id)\s*:", re.IGNORECASE)
BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)、])\s*")


class ChangelogError(Exception):
    """讀不到更新紀錄（沒有 git、不是 git clone 下載的…），訊息直接給使用者看。"""


@dataclass
class Entry:
    sha: str
    when: datetime
    title: str
    details: List[str] = field(default_factory=list)

    @property
    def short_sha(self) -> str:
        return self.sha[:7]


def parse_message(message: str):
    """commit 訊息 → (標題, 細節清單)。第一行是標題，其餘每行一點，署名行與空行丟掉。"""
    lines = [line.rstrip() for line in message.strip().splitlines()]
    title = lines[0].strip() if lines else ""
    details: List[str] = []
    for line in lines[1:]:
        text = line.strip()
        if not text or TRAILER.match(text):
            continue
        if details and line[:1].isspace() and not BULLET.match(line):
            details[-1] += text          # 同一點換行寫的接回去
            continue
        details.append(BULLET.sub("", text))
    return title, details


def parse_log(output: str) -> List[Entry]:
    entries = []
    for record in output.split(RECORD_SEP):
        record = record.strip("\n")
        if not record.strip():
            continue
        parts = record.split(FIELD_SEP, 2)
        if len(parts) != 3:
            continue
        sha, date, message = parts
        try:
            when = datetime.fromisoformat(date.strip()).astimezone(TAIPEI)
        except ValueError:
            continue
        title, details = parse_message(message)
        if title:
            entries.append(Entry(sha.strip(), when, title, details))
    return entries


def page_count(total: int, per_page: int = PER_PAGE) -> int:
    return max(1, -(-total // per_page))


def page_slice(entries: Sequence[Entry], page: int, per_page: int = PER_PAGE) -> List[Entry]:
    """第 page 頁（從 0 開始，第 0 頁是最新的）；超出範圍會夾到第一頁或最後一頁。"""
    page = max(0, min(page, page_count(len(entries), per_page) - 1))
    return list(entries[page * per_page:(page + 1) * per_page])


def format_entry(entry: Entry, repo: Optional[str] = None) -> str:
    sha = f"[`{entry.short_sha}`](https://github.com/{repo}/commit/{entry.sha})" if repo else f"`{entry.short_sha}`"
    head = f"**{entry.when:%Y/%m/%d}**・{sha}\n**{entry.title}**"
    lines = [f"• {d}" for d in entry.details[:MAX_DETAILS]]
    if len(entry.details) > MAX_DETAILS:
        lines.append(f"…還有 {len(entry.details) - MAX_DETAILS} 項")
    text = "\n".join([head, *lines])
    if len(text) > MAX_ENTRY_CHARS:
        text = text[:MAX_ENTRY_CHARS - 1].rstrip() + "…"
    return text


async def read_log(repo_dir: str = PROJECT_ROOT, limit: int = MAX_COMMITS) -> List[Entry]:
    if not os.path.isdir(os.path.join(repo_dir, ".git")):
        raise ChangelogError("這台機器上的 bot 不是用 git 下載的，讀不到更新紀錄。")
    try:
        output = await git(repo_dir, "log", "--no-merges", f"--max-count={limit}", f"--format={LOG_FORMAT}")
    except UpdateError as e:
        raise ChangelogError("讀取更新紀錄失敗，請稍後再試。") from e
    return parse_log(output)


async def read_repo(repo_dir: str = PROJECT_ROOT) -> Optional[str]:
    """GitHub 的 owner/repo，用來把 commit 做成連結；沒有就回傳 None。"""
    try:
        return parse_github_repo(await git(repo_dir, "remote", "get-url", "origin"))
    except UpdateError:
        return None


class Changelog:
    """更新紀錄的快取。"""

    def __init__(self, repo_dir: str = PROJECT_ROOT, ttl: float = CACHE_SECONDS):
        self.repo_dir = repo_dir
        self.ttl = ttl
        self.entries: List[Entry] = []
        self.repo: Optional[str] = None
        self.loaded_at = 0.0

    async def get(self) -> List[Entry]:
        if not self.loaded_at or time.monotonic() - self.loaded_at > self.ttl:
            self.entries = await read_log(self.repo_dir)
            self.repo = await read_repo(self.repo_dir)
            self.loaded_at = time.monotonic()
        return self.entries
