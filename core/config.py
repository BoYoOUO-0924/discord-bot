"""讀取設定：優先使用 config.py，其次是環境變數。"""
import os
from dataclasses import dataclass
from typing import Optional

try:
    import config as _local_config  # 本地 config.py（已被 .gitignore 排除）
except ImportError:
    _local_config = None


def _read(name: str, default=None):
    value = getattr(_local_config, name, None) if _local_config else None
    if value in (None, ""):
        value = os.getenv(name)
    # 還沒改掉的範本佔位字串視同未設定
    if isinstance(value, str) and (value.startswith("YOUR_") or value.startswith("PUT_YOUR_")):
        value = None
    return default if value in (None, "") else value


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


def load_settings() -> Settings:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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
    )
