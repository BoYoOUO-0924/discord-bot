"""音樂播放的共用邏輯（不依賴 Discord，方便測試）：播放清單、點歌檢查、用 yt-dlp 找歌。

安全考量：只接受 YouTube 網址或歌名（歌名用 YouTube 搜尋）。其他網址一律拒絕，
避免有人叫 bot 去連家裡網路的其他裝置或奇怪的網站。
"""
import asyncio
import logging
import os
import re
from collections import deque
from dataclasses import dataclass
from typing import Dict, List, Optional

MAX_QUEUE = 100        # 播放清單最多幾首
MAX_PLAYLIST = 50      # 貼 YouTube 播放清單時最多加入幾首
MAX_QUERY = 200
SEARCH_TIMEOUT = 40    # 秒
DOWNLOAD_TIMEOUT = 180
MAX_DURATION = 3 * 3600          # 超過 3 小時的影片不放
CACHE_LIMIT = 2 * 1024 ** 3      # 歌曲快取最多 2 GB

log = logging.getLogger(__name__)

YOUTUBE_URL = re.compile(r"https?://((www|m|music)\.)?(youtube\.com|youtu\.be)/[^\s<>]+", re.IGNORECASE)
ANY_URL = re.compile(r"https?://", re.IGNORECASE)

# 用講的控制播放（@嘎哇 下一首）。只在有音樂播放時才當成控制，不會誤判一般聊天
CONTROLS = {
    "下一首": "next", "跳過": "next", "切歌": "next", "換一首": "next", "下一首歌": "next",
    "上一首": "prev", "上一首歌": "prev",
    "暫停": "pause", "暫停音樂": "pause", "暫停播放": "pause",
    "繼續": "resume", "繼續播放": "resume", "繼續放": "resume",
    "停止": "stop", "停止播放": "stop", "停止音樂": "stop", "不要放了": "stop", "別放了": "stop", "關掉音樂": "stop",
    "播放清單": "queue", "歌單": "queue", "現在放什麼": "queue", "這首是什麼": "queue",
}

# ffmpeg 播放本機快取檔的設定：只要聲音
FFMPEG_BEFORE = "-nostdin -loglevel warning"
MIN_PLAY_SECONDS = 5   # 開播不到幾秒就結束，當成播放失敗
FFMPEG_OPTIONS = "-vn"


class MusicError(Exception):
    """要直接顯示給玩家看的錯誤。"""


@dataclass
class Track:
    title: str
    url: str                      # YouTube 影片網址（真正的串流網址播放前才取得，因為幾小時後會失效）
    duration: Optional[int] = None
    requester_id: int = 0
    requester_name: str = ""
    thumbnail: str = ""


def fmt_duration(seconds: Optional[int]) -> str:
    if not seconds:
        return "直播"
    seconds = int(seconds)
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def check_query(text: str) -> str:
    """整理玩家輸入的歌名或網址；不能用的話丟出 MusicError。"""
    text = (text or "").strip().strip("<>")
    if not text:
        raise MusicError("要放什麼歌？輸入歌名、歌手，或貼上 YouTube 網址。")
    url = YOUTUBE_URL.search(text)
    if url:
        return url.group(0).rstrip(">")
    if ANY_URL.search(text):
        raise MusicError("只能放 YouTube 的網址，或直接輸入歌名搜尋。")
    return text[:MAX_QUERY]


def parse_control(text: str) -> Optional[str]:
    """「下一首」「暫停」這類控制播放的話 → next/prev/pause/resume/stop/queue；不是就回傳 None。"""
    text = re.sub(r"[\s，。！!？?~～、.]+", "", text or "")
    text = re.sub(r"^(幫我|請|嘎哇)+", "", text)
    text = re.sub(r"(一下|啦|吧|喔|好嗎|嘎)+$", "", text)
    return CONTROLS.get(text)


class Playlist:
    """播放清單。播過的歌會留著，才能按「上一首」。index 是正在播（或最後播過）的那首。"""

    def __init__(self):
        self.tracks: List[Track] = []
        self.index = -1

    @property
    def current(self) -> Optional[Track]:
        return self.tracks[self.index] if 0 <= self.index < len(self.tracks) else None

    @property
    def has_next(self) -> bool:
        return self.index + 1 < len(self.tracks)

    @property
    def has_prev(self) -> bool:
        return self.index > 0

    @property
    def upcoming(self) -> List[Track]:
        return self.tracks[self.index + 1:]

    def room(self) -> int:
        return MAX_QUEUE - len(self.tracks)

    def add(self, tracks: List[Track]) -> int:
        """加到清單最後，回傳實際加入幾首（清單滿了就不再加）。"""
        added = tracks[:max(self.room(), 0)]
        self.tracks.extend(added)
        return len(added)

    def lines(self, limit: int = 15) -> List[str]:
        """顯示用：從目前這首的前兩首開始列。"""
        start = max(self.index - 2, 0)
        result = []
        for i, track in enumerate(self.tracks[start:start + limit], start):
            mark = "▶️" if i == self.index else ("✔️" if i < self.index else "　")
            result.append(f"{mark} `{i + 1}.` {track.title[:60]}（{fmt_duration(track.duration)}）")
        more = len(self.tracks) - (start + limit)
        if more > 0:
            result.append(f"……還有 {more} 首")
        return result


# ---------------------------------------------------------------------- yt-dlp
SEARCH_OPTIONS = {
    "quiet": True, "no_warnings": True, "skip_download": True,
    "noplaylist": True,                 # 影片網址後面帶 &list= 時只放那一首
    "extract_flat": "in_playlist",      # 播放清單只抓標題，不一首一首解析（快很多）
    "playlistend": MAX_PLAYLIST,
}
DOWNLOAD_OPTIONS = {
    "quiet": True, "no_warnings": True, "noplaylist": True, "noprogress": True,
    "format": "bestaudio[acodec=opus]/bestaudio/best",
    "max_filesize": 300 * 1024 * 1024,
}


def _thumbnail(entry: Dict) -> str:
    if entry.get("thumbnail"):
        return entry["thumbnail"]
    video_id = entry.get("id")
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg" if video_id else ""


def _video_url(entry: Dict) -> str:
    url = entry.get("webpage_url") or entry.get("url") or ""
    if url.startswith("http"):
        return url
    video_id = entry.get("id") or url
    return f"https://www.youtube.com/watch?v={video_id}" if video_id else ""


def tracks_from_info(info: Dict, requester_id: int = 0, requester_name: str = "") -> List[Track]:
    """把 yt-dlp 的結果（單一影片、搜尋結果或播放清單）轉成 Track。"""
    entries = info.get("entries")
    items = [e for e in entries if e] if entries is not None else [info]
    tracks = []
    for entry in items[:MAX_PLAYLIST]:
        url = _video_url(entry)
        title = entry.get("title") or ""
        if not url or not YOUTUBE_URL.match(url) or title in ("[Private video]", "[Deleted video]"):
            continue
        tracks.append(Track(title=title or url, url=url, duration=entry.get("duration"),
                            requester_id=requester_id, requester_name=requester_name,
                            thumbnail=_thumbnail(entry)))
    return tracks


def _extract(target: str, options: Dict) -> Dict:
    import yt_dlp   # 只有真的要放歌才載入
    with yt_dlp.YoutubeDL(options) as ydl:
        return ydl.extract_info(target, download="outtmpl" in options)


async def _run(target: str, options: Dict, timeout: float = SEARCH_TIMEOUT) -> Dict:
    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(loop.run_in_executor(None, _extract, target, options), timeout)
    except asyncio.TimeoutError:
        raise MusicError("YouTube 回應太慢，等一下再試試看。") from None
    except Exception as e:   # yt-dlp 的錯誤種類很多，一律轉成看得懂的訊息
        log.warning("yt-dlp 失敗（%s）：%s", target[:80], e)
        raise MusicError(explain_error(str(e))) from e


def explain_error(text: str) -> str:
    lower = text.lower()
    if "not a bot" in lower:
        return "YouTube 暫時擋住了這台伺服器，晚點再試。"
    if "confirm your age" in lower or "age-restricted" in lower or "age restricted" in lower:
        return "這部影片有年齡限制，放不了。"
    if "private video" in lower or "video unavailable" in lower or "has been removed" in lower:
        return "這部影片不存在或不公開。"
    if "unable to connect" in lower or "timed out" in lower or "unable to download" in lower:
        return "連不上 YouTube，檢查一下網路或晚點再試。"
    return "找歌的時候出錯了，換一首或晚點再試。"


async def search(query: str, requester_id: int = 0, requester_name: str = "") -> List[Track]:
    """歌名 → 搜尋 YouTube 第一個結果；YouTube 網址 → 那部影片或播放清單。"""
    query = check_query(query)
    target = query if YOUTUBE_URL.match(query) else f"ytsearch1:{query}"
    tracks = tracks_from_info(await _run(target, SEARCH_OPTIONS), requester_id, requester_name)
    if not tracks:
        raise MusicError(f"找不到「{query[:50]}」，換個關鍵字試試。")
    return tracks


async def download(track: Track, cache_dir: str) -> str:
    """用 yt-dlp 把這首歌的聲音下載到快取資料夾，回傳檔案路徑。

    不讓 ffmpeg 直接連 YouTube 的串流網址：YouTube 會拒絕（403），只有 yt-dlp 的分段下載方式能過。
    已經下載過的歌直接用快取（按「上一首」或重複點歌不用再等）。"""
    if track.duration and track.duration > MAX_DURATION:
        raise MusicError(f"這首超過 {MAX_DURATION // 3600} 小時，太長了。")
    os.makedirs(cache_dir, exist_ok=True)
    options = dict(DOWNLOAD_OPTIONS, outtmpl=os.path.join(cache_dir, "%(id)s.%(ext)s"))
    options["match_filter"] = _no_live
    info = await _run(track.url, options, DOWNLOAD_TIMEOUT)
    if not info or _no_live(info):
        raise MusicError("直播沒辦法放，換一首吧。")
    if not track.duration and info.get("duration"):
        track.duration = info["duration"]
    for item in info.get("requested_downloads") or []:
        path = item.get("filepath")
        if path and os.path.exists(path):
            os.utime(path)   # 標記最近用過，清快取時最後才刪
            return path
    raise MusicError("下載這首歌失敗了。")


def _no_live(info: Dict, *, incomplete: bool = False) -> Optional[str]:
    if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
        return "直播"
    return None


def probe(path: str, ffmpeg: str = "ffmpeg"):
    """用 ffprobe 看檔案的聲音格式，回傳 (codec, kbps)。"""
    import json
    import subprocess
    ffprobe = os.path.join(os.path.dirname(ffmpeg), "ffprobe") if os.path.dirname(ffmpeg) else "ffprobe"
    out = subprocess.run([ffprobe, "-v", "quiet", "-print_format", "json", "-show_streams", "-select_streams", "a:0",
                          "-show_format", path], capture_output=True, text=True, timeout=20, check=True).stdout
    data = json.loads(out)
    stream = (data.get("streams") or [{}])[0]
    bitrate = stream.get("bit_rate") or (data.get("format") or {}).get("bit_rate")
    return stream.get("codec_name"), (int(bitrate) // 1000 if bitrate else None)


def trim_cache(cache_dir: str, limit: int = CACHE_LIMIT) -> int:
    """快取超過上限時，從最久沒用的開始刪，回傳刪了幾個檔案。"""
    try:
        files = [os.path.join(cache_dir, name) for name in os.listdir(cache_dir)]
    except FileNotFoundError:
        return 0
    files = [(os.path.getmtime(f), os.path.getsize(f), f) for f in files if os.path.isfile(f)]
    total = sum(size for _, size, _ in files)
    removed = 0
    for _, size, path in sorted(files):
        if total <= limit:
            break
        try:
            os.remove(path)
        except OSError:
            continue
        total -= size
        removed += 1
    return removed


class ErrorTail:
    """收集 ffmpeg 的錯誤輸出（只留最後幾行），播放失敗時寫進紀錄。"""

    def __init__(self, keep: int = 20):
        self.lines = deque(maxlen=keep)

    def fileno(self):   # 讓 discord.py 用執行緒讀 ffmpeg 的輸出再交給 write()
        raise OSError("not a real file")

    def write(self, data: bytes) -> None:
        if data:
            self.lines.extend(data.decode(errors="ignore").splitlines())

    def text(self) -> str:
        return "\n".join(line for line in self.lines if line.strip())
