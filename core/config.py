"""讀取設定：所有 Token、API 金鑰、網址都放在專案根目錄的 .env（範本見 .env.example）。

讀取順序：環境變數 → .env → 舊版 config.py（相容用，之後會提示搬到 .env）。
"""
import difflib
import os
from dataclasses import dataclass, field
from typing import List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(ROOT, ".env")

KNOWN_KEYS = [
    "DISCORD_TOKEN", "GEMINI_API_KEY", "GEMINI_MODEL", "DEV_GUILD_ID", "DATABASE_PATH", "AUTO_UPDATE",
    "ADMIN_PASSWORD", "ADMIN_HOST", "ADMIN_PORT", "AI_PROVIDER", "OLLAMA_URL", "OLLAMA_MODEL",
    "CWA_API_KEY", "WEATHER_WEBHOOK_URL", "WEATHER_TIME",
]


@dataclass
class EnvStatus:
    """.env 讀取結果，啟動時寫進紀錄，設定沒生效時一看就知道原因。"""
    path: str
    found: bool = False
    keys: List[str] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)


def _decode(raw: bytes) -> str:
    # 記事本存 UTF-8；PowerShell 5 的 echo > .env 會存成 UTF-16
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")) or raw[1:2] == b"\x00":
        return raw.decode("utf-16")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp950", errors="replace")   # 繁體中文 Windows 的 ANSI


def load_dotenv(path: str) -> EnvStatus:
    """讀取 .env 的 KEY=VALUE 到環境變數；已經存在的環境變數不覆蓋。"""
    status = EnvStatus(path)
    try:
        with open(path, "rb") as f:
            text = _decode(f.read())
    except OSError:
        folder = os.path.dirname(path)
        misnamed = [name for name in (".env.txt", "env", "env.txt", ".env.example.txt")
                    if os.path.exists(os.path.join(folder, name))]
        if misnamed:
            status.problems.append(f"找不到 .env，但有 {'、'.join(misnamed)}：請把檔名改成 .env"
                                   "（檔案總管要開啟「副檔名」才看得到 .txt）")
        return status
    status.found = True
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            status.problems.append(f".env 第 {number} 行沒有「=」，已略過（格式是 名稱=值）")
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if not key:
            continue
        status.keys.append(key)
        if key not in KNOWN_KEYS:
            guess = difflib.get_close_matches(key.upper(), KNOWN_KEYS, n=1, cutoff=0.6)
            status.problems.append(f".env 第 {number} 行的 {key} 不是認得的設定名稱"
                                   + (f"，是不是要寫 {guess[0]}？" if guess else ""))
        elif not value or value.startswith(("YOUR_", "PUT_YOUR_")):
            status.problems.append(f".env 第 {number} 行的 {key} 還沒有填值")
        os.environ.setdefault(key, value)
    return status


env_status = load_dotenv(ENV_PATH)

try:
    import config as _legacy_config  # 舊版的 config.py（已被 .gitignore 排除），只為了相容
except ImportError:
    _legacy_config = None

# 從舊版 config.py 讀到的設定名稱，啟動時提醒搬到 .env
legacy_keys: List[str] = []


def _is_placeholder(value) -> bool:
    # 還沒改掉的範本佔位字串視同未設定
    return isinstance(value, str) and (value.startswith("YOUR_") or value.startswith("PUT_YOUR_"))


def _read(name: str, default=None):
    value = os.getenv(name)
    if value in (None, "") or _is_placeholder(value):
        value = getattr(_legacy_config, name, None) if _legacy_config else None
        if value in (None, "") or _is_placeholder(value):
            return default
        if name not in legacy_keys:
            legacy_keys.append(name)
    return value


@dataclass(frozen=True)
class Settings:
    discord_token: Optional[str]
    gemini_api_key: Optional[str]
    gemini_model: str
    dev_guild_id: Optional[int]
    database_path: str
    auto_update: bool
    admin_password: Optional[str]
    admin_host: str
    admin_port: int
    ai_provider: Optional[str]
    ollama_url: Optional[str]
    ollama_model: str
    cwa_api_key: Optional[str]
    weather_webhook_url: Optional[str]
    weather_time: str


def load_settings() -> Settings:
    root = ROOT
    dev_guild = _read("DEV_GUILD_ID")
    return Settings(
        discord_token=_read("DISCORD_TOKEN"),
        gemini_api_key=_read("GEMINI_API_KEY"),
        # 別名會自動指向最新的 Flash 模型，舊型號下架時不必改程式
        gemini_model=_read("GEMINI_MODEL", "gemini-flash-latest"),
        dev_guild_id=int(dev_guild) if dev_guild else None,
        database_path=_read("DATABASE_PATH", os.path.join(root, "data", "bot.db")),
        # 從 GitHub 自動更新；deploy/windows/2_start.bat 會設定 AUTO_UPDATE=1
        auto_update=str(_read("AUTO_UPDATE", "0")).strip().lower() in ("1", "true", "yes", "on"),
        # 管理後台：有設定密碼才會啟動；預設開放給同一個網路裡的裝置連線
        admin_password=_read("ADMIN_PASSWORD"),
        admin_host=_read("ADMIN_HOST", "0.0.0.0"),
        admin_port=int(_read("ADMIN_PORT", 8080)),
        # 海龜湯的 AI：gemini 或 ollama；沒指定時，有設定 OLLAMA_URL 就用 Ollama
        ai_provider=_read("AI_PROVIDER"),
        ollama_url=_read("OLLAMA_URL"),
        ollama_model=_read("OLLAMA_MODEL", "gemma4:e4b-it-q4_K_M"),
        # 每日天氣預報：中央氣象署開放資料 API 授權碼、要傳送的 Discord Webhook、傳送時間（台灣時間）
        cwa_api_key=_read("CWA_API_KEY"),
        weather_webhook_url=_read("WEATHER_WEBHOOK_URL"),
        weather_time=str(_read("WEATHER_TIME", "06:00")),
    )
