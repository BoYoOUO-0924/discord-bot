"""音樂功能檢查：在筆電上確認 ffmpeg、deno、yt-dlp 都能用，而且真的抓得到 YouTube 的聲音。

用法（在專案資料夾執行）：
    .venv/bin/python tools/music_check.py
    .venv/bin/python tools/music_check.py "五月天 倔強"

不會連 Discord，也不會動到資料庫。
"""
import asyncio
import os
import shutil
import subprocess
import sys
import sysconfig
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import music  # noqa: E402


def find(name: str):
    local = os.path.join(sysconfig.get_path("scripts"), name)
    return local if os.path.exists(local) else shutil.which(name)


async def main(query: str) -> int:
    ok = True
    ffmpeg = shutil.which("ffmpeg")
    print(f"{'✅' if ffmpeg else '❌'} ffmpeg：{ffmpeg or '沒有安裝（sudo apt install -y ffmpeg）'}")
    ok &= bool(ffmpeg)
    deno = find("deno")
    print(f"{'✅' if deno else '⚠️'} deno：{deno or '沒有安裝（pip install -r requirements.txt）'}")
    try:
        import yt_dlp
        print(f"✅ yt-dlp：{yt_dlp.version.__version__}")
    except ImportError:
        print("❌ yt-dlp：沒有安裝（pip install -r requirements.txt）")
        return 1
    try:
        import nacl  # noqa: F401
        import davey  # noqa: F401
        print("✅ 語音套件（PyNaCl、davey）")
    except ImportError as e:
        print(f"❌ 語音套件缺少 {e.name}（pip install -r requirements.txt）")
        ok = False

    print(f"\n搜尋「{query}」…")
    start = time.perf_counter()
    try:
        [track, *_] = await music.search(query)
        print(f"✅ 找到：{track.title}（{music.fmt_duration(track.duration)}）{track.url}　{time.perf_counter() - start:.1f} 秒")
        start = time.perf_counter()
        url = await music.stream_url(track)
        print(f"✅ 取得音訊網址　{time.perf_counter() - start:.1f} 秒")
    except music.MusicError as e:
        print(f"❌ {e}（詳細原因：{e.__cause__}）")
        return 1
    if ffmpeg:
        print("用 ffmpeg 試著解碼 5 秒…")
        cmd = [ffmpeg, *music.FFMPEG_BEFORE.split(), "-i", url, "-t", "5", *music.FFMPEG_OPTIONS.split(),
               "-c:a", "libopus", "-f", "null", "-"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode == 0:
            print("✅ ffmpeg 解碼成功，可以播放")
        else:
            print("❌ ffmpeg 解碼失敗：\n" + result.stderr[-800:])
            ok = False
    print("\n全部正常！" if ok else "\n有項目沒通過，照上面的提示處理。")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(" ".join(sys.argv[1:]) or "周杰倫 晴天")))
