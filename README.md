# 嘎哇 Discord Bot

繁體中文的 Discord 機器人，全部用**斜線指令**和按鈕操作：

- 💰 **新台幣錢包**：簽到、打工、每日任務、排行榜
- 🎮 **遊戲**：21 點、拉霸機、骰寶、猜數字、井字遊戲、德州撲克（可加電腦對手）、AI 海龜湯
- 🌦️ **台灣生活**：全台鄉鎮天氣、颱風豪雨特報、停班停課、地震通報
- 📋 **待辦與專案**：任務卡片、截止提醒、週報、GitHub 整合
- 🐸 **AI**：@嘎哇 用講的叫功能、「我錯過了什麼」整理聊天重點
- 🎵 **音樂**、⏰ 提醒、📊 投票、📰 更新日誌、🖥️ 網頁管理後台（可調整 AI 個性）

輸入 `/menu` 打開大廳，所有功能都能用按鈕操作；Discord 介面是中文的話會看到中文指令（例如 `/簽到`）。
每個伺服器的管理員可以用 `/settings` 自己決定開哪些功能、天氣看哪裡、通知發到哪個頻道。

## 邀請機器人

### ➕ [邀請嘎哇到我的伺服器](https://discord.com/oauth2/authorize?client_id=1435614669623529573&scope=bot+applications.commands&permissions=274881145920)

免費，不用自己架設。你需要有那個伺服器的「管理伺服器」權限；加入後輸入 `/settings` 設定、`/menu` 打開大廳。
功能長什麼樣子可以先看[功能介紹網頁](https://boyoouo-0924.github.io/discord-bot/)。

要求的權限：檢視頻道、傳送訊息（含討論串）、嵌入連結、新增反應、讀取訊息紀錄；連接與說話用在放音樂，管理訊息用在 `/clear`。
不需要管理員權限，授權時也可以取消勾選用不到的。想自己架一台，請看下面的快速開始。

📖 **[功能說明](docs/features.md)**・🧩 [進階說明](docs/advanced.md)・🏗️ [架構](docs/architecture.md)・🗺️ [路線圖](ROADMAP.md)

## 快速開始

只要一個 Discord Bot Token 就能跑起來，其他功能之後再慢慢加。

1. **建立 Bot**：到 [Discord Developer Portal](https://discord.com/developers/applications) 建立應用程式。
   - Bot 頁面按 Reset Token 複製 Token，並開啟 **Message Content Intent**（猜數字、海龜湯、嘎哇要讀訊息）。
   - OAuth2 → URL Generator 勾 `bot` 和 `applications.commands`，權限至少要
     Send Messages、Embed Links、Add Reactions、Read Message History（`/clear` 另需 Manage Messages），用產生的網址邀請進伺服器。
2. **安裝**（需要 Python 3.10 以上）：
   ```bash
   git clone https://github.com/BoYoOUO-0924/discord-bot
   cd discord-bot
   python -m venv .venv
   source .venv/bin/activate        # Windows：.venv\Scripts\activate
   pip install -r requirements.txt
   ```
3. **設定**：把 `.env.example` 複製成 `.env`，填入 `DISCORD_TOKEN=你的 Token`。
4. **啟動**：`python bot.py`。紀錄裡會印出**功能總覽**，告訴你哪些功能已經能用、還缺什麼設定：
   ```
   功能總覽：
     ✅ 錢包、遊戲、提醒、投票、待辦
     ➖ 天氣、地震、颱風豪雨特報：缺 CWA_API_KEY（中央氣象署開放資料，免費申請）
     ✅ 停班停課通知
     ➖ AI：嘎哇聊天、海龜湯、週報、我錯過了什麼：缺 OLLAMA_URL（要先在某台電腦裝 Ollama）
     …
   ```
5. **到 Discord 設定**：管理員輸入 `/settings show` 看目前設定，`/settings features` 開關功能。

想 24 小時執行？Linux 看 [deploy/linux](deploy/linux/README.md)，Windows 看 [deploy/windows](deploy/windows/README.md)。

## 加更多功能

都填在 `.env`，改完重新啟動 bot。完整說明在 [`.env.example`](.env.example)。

| 想要 | 填這個 | 去哪裡拿 |
|---|---|---|
| 天氣、颱風豪雨特報、地震 | `CWA_API_KEY` | [中央氣象署開放資料平台](https://opendata.cwa.gov.tw) 登入 → 取得授權碼（免費） |
| 管理後台 | `ADMIN_PASSWORD` | 自己設一個至少 8 字元的密碼，瀏覽器開 `http://這台電腦:8080` |
| 嘎哇聊天、海龜湯、週報、我錯過了什麼 | `OLLAMA_URL`、`OLLAMA_MODEL` | 在電腦上裝 [Ollama](https://ollama.com)，見[進階說明](docs/advanced.md#ai-ollama) |
| 音樂 | （不用填） | 系統安裝 ffmpeg：`sudo apt install ffmpeg` |
| 私人 GitHub repo 整合 | `GITHUB_TOKEN` | GitHub → Settings → Developer settings → Fine-grained token |
| 股票模擬（進階） | `TWFOLIO_DB`、`TWFOLIO_CONFIG` | 需要另外安裝 twfolio，見[進階說明](docs/advanced.md#股票模擬) |
| Minecraft 管理（進階） | `MINECRAFT_GUILD_IDS` | 需要自己的控制腳本，見[進階說明](docs/advanced.md#minecraft-伺服器管理) |

沒填的功能會自動停用，不會影響其他功能。

## 開發

- 測試：`python -m unittest discover -s tests -t .`
- 開發時在 `.env` 設定 `DEV_GUILD_ID=你的伺服器ID`，斜線指令只同步到那個伺服器，修改後會立刻生效。
- 程式架構和「怎麼加一個新功能」：[docs/architecture.md](docs/architecture.md)
- `.env` 不會上傳到 GitHub；設定也可以用同名的環境變數（環境變數優先）。
