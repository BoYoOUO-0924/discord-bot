# 將此檔案複製成 config.py，並填入你的金鑰
# 切勿將真正的 config.py 上傳到版本控管或公開儲存庫

# 必填：Discord Bot Token（Discord Developer Portal → Bot → Reset Token）
DISCORD_TOKEN = "YOUR_DISCORD_BOT_TOKEN"

# 選填：Gemini API Key，沒填的話 AI 海龜湯會停用，其他功能照常
GEMINI_API_KEY = "YOUR_GEMINI_API_KEY"

# 選填：使用的 Gemini 模型（預設 gemini-flash-latest，會自動使用最新的 Flash 模型）
# GEMINI_MODEL = "gemini-flash-latest"

# 選填：開發用伺服器 ID。設定後斜線指令只同步到這個伺服器，修改後會立刻生效
# （右鍵伺服器圖示 → 複製伺服器 ID，需要先在 Discord 設定開啟開發者模式）
# DEV_GUILD_ID = 123456789012345678

# 選填：每日天氣預報（也可以寫在專案根目錄的 .env，格式見 README）
# 中央氣象署開放資料平台的 API 授權碼：https://opendata.cwa.gov.tw → 登入 → 取得授權碼
# CWA_API_KEY = "CWA-XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX"
# 要傳送預報的 Discord Webhook：頻道設定 → 整合 → Webhook → 新 Webhook → 複製 Webhook 網址
# WEATHER_WEBHOOK_URL = "https://discord.com/api/webhooks/..."
# 傳送時間（台灣時間），預設 06:00
# WEATHER_TIME = "06:00"
