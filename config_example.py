# 將此檔案複製成 config.py，並填入你的金鑰
# 切勿將真正的 config.py 上傳到版本控管或公開儲存庫

# 必填：Discord Bot Token（Discord Developer Portal → Bot → Reset Token）
DISCORD_TOKEN = "YOUR_DISCORD_BOT_TOKEN"

# 選填：Gemini API Key，沒填的話 AI 海龜湯會停用，其他功能照常
GEMINI_API_KEY = "YOUR_GEMINI_API_KEY"

# 選填：使用的 Gemini 模型（預設 gemini-flash-latest，會自動使用最新的 Flash 模型）
# GEMINI_MODEL = "gemini-flash-latest"

# 選填：管理後台密碼（至少 8 個字元）。設定後可以用瀏覽器開 http://這台電腦的IP:8080 管理積分等資料；
# 沒設定就不會啟動後台。只在家裡的網路使用，不要把這個連接埠開放到網際網路。
# ADMIN_PASSWORD = "請改成你自己的密碼"
# ADMIN_PORT = 8080

# 選填：開發用伺服器 ID。設定後斜線指令只同步到這個伺服器，修改後會立刻生效
# （右鍵伺服器圖示 → 複製伺服器 ID，需要先在 Discord 設定開啟開發者模式）
# DEV_GUILD_ID = 123456789012345678
