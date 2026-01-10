# AI-Powered Discord Bot

這是一個搭載了多種遊戲功能的 Discord 機器人，其中的「海龜湯」遊戲更是由強大的 Gemini AI 驅動，能夠動態生成謎題並進行智慧判斷，帶來無限的遊戲樂趣。

## 功能與特色

- **模組化設計**: 使用 `cogs` 來管理不同的功能，易於擴充與維護。
- **AI 驅動遊戲**: 海龜湯遊戲完全由 Gemini AI 生成與互動，每一局都是獨一無二的體驗。
- **多樣化遊戲庫**: 除了 AI 海龜湯，還內建了猜數字、二十一點、井字遊戲等多種經典遊戲。
- **環境感知**: 自動偵測 `config.py` 或環境變數，安全地載入金鑰。

### 目前支援的遊戲
- `!seatortoise` / `!海龜湯`: 開始一場由 AI 生成的懸疑推理遊戲。
- `!guess` / `!猜數字`: 猜一個 1 到 100 之間的祕密數字。
- `!blackjack` / `!二十一點`: 和莊家對決，看誰的點數最接近 21。
- `!poker`: 簡易的德州撲克遊戲。
- `!tictactoe` / `!井字遊戲`: 與另一位玩家進行一場井字對決。
- `!checkin` / `!簽到`: 每日簽到領取獎勵。

## 環境設定與啟動

這是一個通用的 Python Discord Bot 專案。

1.  **安裝相依套件**
    請確保您已安裝 Python，然後執行：
    ```bash
    pip install -r requirements.txt
    ```

2.  **設定 API 金鑰**
    - 複製範例設定檔：
      ```bash
      cp config.example.py config.py
      ```
      (Windows 用戶請手動複製改名，或使用 `copy config.example.py config.py`)
    - 編輯 `config.py`，並填入你的真實金鑰：
      ```python
      # config.py
      DISCORD_TOKEN = "YOUR_DISCORD_BOT_TOKEN"
      GEMINI_API_KEY = "YOUR_GEMINI_API_KEY"
      ```
    - **重要**: `config.py` 已被加入 `.gitignore`，不會被上傳到 Git。

3.  **啟動 Bot**
    ```bash
    python bot.py
    ```

## 專案結構

```
discord-bot/
├── bot.py              # Bot 主程式
├── config.example.py   # 設定檔範本
├── requirements.txt    # 相依套件列表
├── README.md           # 就是你現在在看的這個檔案
└── cogs/                 # 功能模組 (Cogs)
    ├── __init__.py
    ├── blackjack.py
    ├── checkin.py
    ├── guess_number.py
    ├── help.py
    ├── poker.py
    ├── seatortoise.py    # AI 海龜湯
    └── tictactoe.py
```
