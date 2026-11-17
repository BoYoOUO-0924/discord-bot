# 嘎哇 Discord Bot：開發說明

全繁體中文（台灣用語）的 Discord 機器人，discord.py 斜線指令版。介面文字、註解、commit 訊息一律用繁體中文。

## 專案結構

- `bot.py`：主程式，`EXTENSIONS` 列出要載入的 cog；`AppTree.interaction_check` 統一擋掉被關閉的功能
- `core/`：不依賴 Discord 的邏輯（方便測試）；`cogs/`：Discord 指令、按鈕、事件
- `core/features.py`：功能開關清單 `FEATURES`（指令、大廳按鈕、嘎哇意圖 → 功能）
- `core/catalog.py`：`/help` 指令說明（`HELP_SECTIONS`）與遊戲目錄
- `core/assistant.py` + `cogs/assistant.py`：@嘎哇 聊天、意圖判斷與按鈕
- `core/i18n.py`：斜線指令與參數的中文名稱
- `webadmin/`：管理後台（功能開關分頁直接讀 `FEATURES`）。前端是純 JS（CSP 只允許同網域，不能用 CDN）：
  `static/app.js` 頁面、`static/motion.js` 動畫特效、`static/style.css` 任務控制中心風格；新頁面要加進 `index.html` 側邊選單與 `app.js` 的 `PAGES`
- `core/persona.py`：AI 個性設定（管理後台「AI 與個性」）；聊天、叫功能台詞、週報與我錯過了什麼的語氣都從這裡讀，不要在程式裡寫死機器人名稱
- 文件：`README.md` 只放簡介與快速開始；完整指令與功能在 `docs/features.md`，需要額外程式的設定在 `docs/advanced.md`，
  程式架構與檔案對照在 `docs/architecture.md`；`docs/index.html` 是給玩家看的功能介紹網頁
- 測試：`python -m unittest discover -s tests -t .`（CI 也會跑 `python -m compileall -q bot.py core cogs`）

## 新功能開發檢查點（每個新功能都必須做到這三項）

### 1. 使用者可以用指令呼叫
- [ ] 在 `cogs/` 新增斜線指令，並把 cog 加進 `bot.py` 的 `EXTENSIONS`
- [ ] `core/i18n.py` 的 `COMMAND_NAMES`（必要時 `PARAMETER_NAMES`）加上中文名稱
- [ ] `core/catalog.py` 的 `HELP_SECTIONS` 加上說明，`/help` 才看得到
- [ ] 適合的話在大廳（`cogs/hub.py` 的 `HubView` 按鈕與 `Hub.handle`）加入口

### 2. @bot 名稱（@嘎哇）可以對話，並依意圖呼叫功能
- [ ] `core/assistant.py` 的 `INTENTS` 加上意圖與給 AI 看的說明；需要時在 `intent_prompt` 判斷原則補一句、
      在 `apply_guards` 加規則補強小模型容易判錯的說法
- [ ] `cogs/assistant.py`：`LINES` 加預設台詞（用通用語氣，不要寫死角色名稱；後台可以覆寫）、
      `webadmin/server.py` 的 `INTENT_LABELS` 加中文名稱、`buttons_for` 回傳按鈕、`run_action` 執行按鈕
      （會動到錢或資料的事只給按鈕，本人按下才執行；AI 不能直接改資料）
- [ ] `tools/assistant_ai_check.py` 的 `CASES` 加幾句實測句子；`tests/test_assistant.py` 加規則的單元測試

### 3. 管理開發後台可以開關功能
- [ ] `core/features.py` 的 `FEATURES` 加一筆 `Feature(key, emoji, 名稱, 說明, 指令, 大廳按鈕, 嘎哇意圖, category=分類)`，
      指令、大廳按鈕、嘎哇意圖都要對應到這個 key；`category` 用 `CATEGORIES` 裡的分類（沒有合適的就新增一個）
      （管理後台與 `/settings features` 會自動依分類列出）
- [ ] `feature_for_action` 認得這個功能的按鈕動作（大廳、嘎哇按鈕被關掉時會擋下）
- [ ] 背景工作（定時通知、自動傳送）執行前用 `enabled(bot, key, guild_id)` 檢查
- [ ] `tests/test_features.py` 的「每個 /help 指令都對應到功能開關」測試要通過

### 收尾
- [ ] 新增測試並跑完整測試
- [ ] 更新 `docs/features.md`（指令一覽、說明段落）、`docs/architecture.md`（功能與檔案對照表）、
      `docs/index.html`（功能卡片、嘎哇可以這樣說）；需要新設定的話也更新 `.env.example`、`core/overview.py`、README 的「加更多功能」表
- [ ] commit 訊息第一行寫「新增／修正 …」，其餘每行「- 」一點寫給玩家看得懂的變更：
      `/changelog` 更新日誌直接顯示 commit 訊息
