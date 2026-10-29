# 將此檔案複製成 config.py，並填入你的金鑰
# 切勿將真正的 config.py 上傳到版本控管或公開儲存庫

# 必填：Discord Bot Token（Discord Developer Portal → Bot → Reset Token）
DISCORD_TOKEN = "YOUR_DISCORD_BOT_TOKEN"

# 海龜湯的 AI 湯主（二選一，都沒設定的話海龜湯會停用，其他功能照常）
#
# 選項 1：Ollama（自己架的模型，可以透過 Tailscale 連到別台電腦）
# OLLAMA_URL = "http://100.123.111.120:11434"
# OLLAMA_MODEL = "gemma4:e4b-it-q4_K_M"
#
# 選項 2：Gemini（Google 雲端；新專案可能需要開啟付費）
GEMINI_API_KEY = "YOUR_GEMINI_API_KEY"
# GEMINI_MODEL = "gemini-flash-latest"
#
# 兩個都有設定時，預設用 Ollama；想指定的話設定 AI_PROVIDER = "ollama" 或 "gemini"

# 選填：管理後台密碼（至少 8 個字元）。設定後可以用瀏覽器開 http://這台電腦的IP:8080 管理積分等資料；
# 沒設定就不會啟動後台。只在家裡的網路使用，不要把這個連接埠開放到網際網路。
# ADMIN_PASSWORD = "請改成你自己的密碼"
# ADMIN_PORT = 8080

# 選填：開發用伺服器 ID。設定後斜線指令只同步到這個伺服器，修改後會立刻生效
# （右鍵伺服器圖示 → 複製伺服器 ID，需要先在 Discord 設定開啟開發者模式）
# DEV_GUILD_ID = 123456789012345678
