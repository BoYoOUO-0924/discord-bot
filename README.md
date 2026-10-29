# 嘎哇 Discord Bot

一個以**斜線指令**操作的 Discord 遊戲與工具機器人：每日簽到、積分排行榜、21 點、拉霸機、猜數字、井字遊戲、德州撲克，以及由 Gemini AI 出題的海龜湯。

輸入 `/menu` 打開大廳，所有功能都能用按鈕操作；介面語言是中文的使用者會看到中文指令名稱（例如 `/簽到`）。

## 功能一覽

| 分類 | 指令 | 說明 |
| --- | --- | --- |
| 入口 | `/menu` | 大廳，按鈕操作所有功能（管理員可用 `公開:True` 發在頻道給大家用） |
| | `/help` | 指令說明，指令可以直接點擊使用 |
| 積分 | `/checkin` | 每日簽到：基礎 100、首次 +500、連續每天再 +20（台灣時間 00:00 換日） |
| | `/work` | 打工賺 80–200 分，每小時一次，10% 機率大成功雙倍 |
| | `/quests` | 每日 3 個任務（80–200 分），全部完成再 +200；在伺服器聊天每分鐘 +5（每天上限 200） |
| | `/profile [使用者]` | 積分、伺服器排名、簽到紀錄、遊戲戰績、最近收支 |
| | `/leaderboard` | 伺服器積分排行榜與連續簽到排行榜 |
| | `/give 對象 金額` | 轉帳給其他人 |
| 遊戲 | `/blackjack [賭注]` | 21 點：每人獨立牌局，可加倍，Blackjack 賠 3:2；不下注就是練習模式 |
| | `/slots 賭注` | 拉霸機，最高 100 倍 |
| | `/guess [範圍]` | 全頻道一起猜，直接在頻道輸入數字，bot 用 ⬆️⬇️ 回應 |
| | `/tictactoe [對手] [難度]` | 邀請朋友對戰，或挑戰三種難度的機器人 |
| | `/poker [大盲注] [帶入籌碼] [電腦對手]` | 2–8 人德州撲克，可加入 4 種個性的電腦對手（籌碼由莊家提供），支援邊池、逾時自動行動、中途入座／離桌；「🃏 我的手牌」私人面板可直接看牌下注、自動更新 |
| | `/seatortoise [難度] [主題]` | AI 海龜湯：頻道裡輸入「？」結尾的問題，湯主回答是／不是／無關 |
| 工具 | `/remind 時間 內容` | 提醒，支援 `10m`、`1h30m`、`18:30`、`明天 9:00` |
| | `/reminders` | 查看或取消提醒 |
| | `/poll [時長]` | 按鈕投票，可設定自動截止 |
| | `/clear 數量` | 清除訊息（需要管理訊息權限） |

## 安裝與啟動

1. **建立虛擬環境並安裝套件**（需要 Python 3.10 以上）
   ```bash
   python -m venv .venv
   .venv\Scripts\activate        # macOS / Linux：source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. **設定金鑰**：把 `config_example.py` 複製成 `config.py`，填入：
   ```python
   DISCORD_TOKEN = "你的 Discord Bot Token"
   GEMINI_API_KEY = "你的 Gemini API Key"   # 選填，沒填只會停用海龜湯
   ```
   `config.py` 已被 `.gitignore` 排除，不會上傳。也可以改用同名的環境變數。

3. **Discord Developer Portal 設定**
   - Bot 頁面開啟 **Message Content Intent**（猜數字與海龜湯要讀取頻道訊息）
   - 邀請時勾選 scope `bot` 與 `applications.commands`，權限至少需要
     Send Messages、Embed Links、Add Reactions、Read Message History（`/clear` 另需 Manage Messages）

4. **啟動**
   ```bash
   python bot.py
   ```
   第一次啟動會自動把舊版 `data/points.json`、`data/checkin.json` 匯入資料庫（原檔不會被修改）。
   開發時可以在 `config.py` 設定 `DEV_GUILD_ID`，斜線指令只同步到該伺服器並立即生效。

## 放在舊筆電上 24 小時執行

`deploy/windows/` 裡有一鍵安裝、自動重啟、開機自動啟動的腳本，並支援從 GitHub 自動更新（只會套用自動測試通過的版本）。
詳細步驟請看 [deploy/windows/README.md](deploy/windows/README.md)。

## 管理後台

在 `config.py` 設定 `ADMIN_PASSWORD`（至少 8 個字元）後，bot 會同時啟動一個網頁後台（預設連接埠 8080），
同一個網路裡的裝置可以用瀏覽器登入：

- **總覽**：使用者數、流通積分、每日發放積分圖表、各遊戲損益
- **使用者**：搜尋、查看收支紀錄與戰績；調整積分、修改連續簽到、重設打工冷卻
- **遊戲**：查看並結束進行中的牌局、退還卡住的賭注
- **紀錄**：管理操作紀錄與 bot 執行紀錄

所有管理操作都會留下紀錄。後台只適合在家裡的網路使用，不要開放到網際網路。

## 專案結構

```
bot.py              主程式：載入功能、同步斜線指令、統一錯誤處理
core/
  config.py         讀取 config.py / 環境變數
  db.py             SQLite 資料層（積分、簽到、下注保管、提醒、投票）
  ui.py             共用的顏色、訊息格式、按鈕介面基底
  ai.py             Gemini（google-genai）包裝
  catalog.py        遊戲與指令目錄（大廳、說明、戰績共用）
  i18n.py           指令名稱中文化
  timeutil.py       台灣時區與時間字串解析
cogs/
  hub.py            /menu、/help
  economy.py        簽到、個人資料、排行榜、轉帳
  reminders.py      提醒
  polls.py          投票
  admin.py          /clear
  blackjack.py  slots.py  guess_number.py  tictactoe.py  seatortoise.py
  poker/            德州撲克（engine.py 是不依賴 Discord 的牌局引擎）
tests/              單元測試：python -m unittest discover -s tests -t .
data/               資料庫 bot.db（不會上傳）
```

## 積分安全機制

- 所有積分異動都經過 SQLite 並序列化處理，多人同時操作也不會互相覆蓋。
- 遊戲開局時先扣下賭注保管，結算時一次發放；bot 中途重啟的話，下次啟動會自動退還所有未結算的賭注。
