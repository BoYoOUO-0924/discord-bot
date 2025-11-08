# bot.py
import discord
from discord.ext import commands
import os         # 用來讀取資料夾
try:
    import config  # 嘗試載入本地 config.py（不應上傳到 Git）
    TOKEN = getattr(config, "DISCORD_TOKEN", None)
except ImportError:
    TOKEN = None

# 若沒有 config.py 或其中未設定 TOKEN，改從環境變數取得
if not TOKEN:
    TOKEN = os.getenv("DISCORD_TOKEN")
    if not TOKEN:
        raise RuntimeError(
            "找不到 Bot Token，請建立 config.py 設定 TOKEN 或設置環境變數 DISCORD_TOKEN"
        )

# --- Bot 設定 ---

# 啟用所有 Intents
intents = discord.Intents.default()
intents.message_content = True  # 確保 Message Content Intent 仍然開啟

# 建立 Bot 實例
# command_prefix='!' 告訴 Bot 我們的指令都是 '!' 開頭；關閉預設 help 以使用自訂版
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)


# --- Bot 事件 ---

@bot.event
async def on_ready():
    """當 Bot 準備就緒時"""
    print(f'我們已經登入為 {bot.user}')
    print(f'載入了 {len(bot.cogs)} 個 Cogs (功能包)。')


# --- 載入 Cogs 的主要邏輯 ---

async def load_cogs():
    """自動載入所有在 cogs 資料夾底下的 .py 檔案"""
    for filename in os.listdir('./cogs'):
        if filename.endswith('.py') and filename != '__init__.py':
            # 格式會是 cogs.games, cogs.dice
            extension_name = f'cogs.{filename[:-3]}'
            try:
                await bot.load_extension(extension_name)
                print(f'成功載入 {extension_name}')
            except Exception as e:
                print(f'載入 {extension_name} 失敗: {e}')

# --- 啟動 Bot ---
async def main():
    await load_cogs()
    await bot.start(TOKEN)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
