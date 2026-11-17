"""啟動時印一張功能總覽：哪些功能可以用、少了什麼設定。給自己架 bot 的人一眼看出要補什麼。"""
import os
import shutil
from typing import List, Tuple

Row = Tuple[bool, str, str]   # (能不能用, 功能, 說明)


def feature_overview(settings, ai_configured: bool) -> List[Row]:
    cwa = bool(settings.cwa_api_key)
    twfolio = bool(settings.twfolio_db and os.path.exists(settings.twfolio_db))
    webhook = settings.earthquake_webhook_url or settings.weather_webhook_url
    return [
        (True, "錢包、遊戲、提醒、投票、待辦", "不需要額外設定"),
        (cwa, "天氣、地震、颱風豪雨特報", "" if cwa else "缺 CWA_API_KEY（中央氣象署開放資料，免費申請）"),
        (True, "停班停課通知", "不需要額外設定"),
        (bool(shutil.which("ffmpeg")), "音樂", "" if shutil.which("ffmpeg") else "缺 ffmpeg（sudo apt install ffmpeg）"),
        (ai_configured, "AI：嘎哇聊天、海龜湯、週報、我錯過了什麼",
         "" if ai_configured else "缺 OLLAMA_URL（要先在某台電腦裝 Ollama）"),
        (bool(settings.admin_password), "管理後台",
         f"http://localhost:{settings.admin_port}" if settings.admin_password else "缺 ADMIN_PASSWORD"),
        (twfolio, "股票模擬（進階）", "" if twfolio else "缺 twfolio 行情資料庫（TWFOLIO_DB）"),
        (bool(settings.minecraft_guild_ids), "Minecraft 管理（進階）",
         "" if settings.minecraft_guild_ids else "沒設定 MINECRAFT_GUILD_IDS"),
        (bool(webhook), "舊版 Webhook 每日天氣／地震（選用）",
         "" if webhook else "沒設定，改用 /settings 選頻道"),
    ]


def format_overview(rows: List[Row]) -> str:
    lines = ["功能總覽："]
    for ok, name, note in rows:
        lines.append(f"  {'✅' if ok else '➖'} {name}" + (f"：{note}" if note and (not ok or 'http' in note) else ""))
    return "\n".join(lines)
