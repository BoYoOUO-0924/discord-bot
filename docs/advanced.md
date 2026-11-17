# 進階說明

這裡是需要額外程式、或是比較少人會用到的設定。一般使用只要看 [README](../README.md) 和[功能說明](features.md)。

- [AI（Ollama）](#ai-ollama)
- [24 小時執行與自動更新](#24-小時執行與自動更新)
- [股票模擬](#股票模擬)（需要 twfolio）
- [Minecraft 伺服器管理](#minecraft-伺服器管理)（需要自己的控制腳本）
- [舊版 Webhook 天氣與地震](#舊版-webhook-天氣與地震)

## AI（Ollama）

嘎哇聊天、海龜湯、週報總結、我錯過了什麼用 [Ollama](https://ollama.com) 在自己的電腦上跑模型，不需要付費的雲端 API。

1. 在有顯示卡（或記憶體夠大）的電腦安裝 Ollama，下載模型：
   ```bash
   ollama pull gemma4:e4b-it-q4_K_M
   ```
   其他支援 JSON 輸出的模型也可以，換模型就改 `OLLAMA_MODEL`。小模型比較快，但判斷和整理的品質會差一些。
2. 在 `.env` 填上：
   ```
   OLLAMA_URL=http://localhost:11434
   OLLAMA_MODEL=gemma4:e4b-it-q4_K_M
   ```
3. **Ollama 跟 bot 不在同一台電腦**：Ollama 那台要設定 `OLLAMA_HOST=0.0.0.0` 讓別台連得到，
   兩台不在同一個網路的話可以用 [Tailscale](https://tailscale.com)，`OLLAMA_URL` 填那台電腦的 Tailscale IP。
   在 bot 那台用 `curl http://那台的IP:11434/api/tags` 測試，有列出模型就代表連得到。
4. 重新啟動 bot，紀錄裡會出現「AI（ollama …）狀態：正常」。

Ollama 那台關機時，AI 功能會顯示暫停或改用簡化版，其他功能照常。
`python tools/assistant_ai_check.py` 可以用真的模型測試嘎哇的判斷正確率。
`python tools/memory_ai_check.py` 可以測試 AI 記憶記得準不準（會不會漏記、亂記、記到別人的事）；換模型後建議跑一次。

## 24 小時執行與自動更新

- **Linux（雲端主機或舊電腦）**：[deploy/linux/README.md](../deploy/linux/README.md)，一個腳本裝好，開機自動啟動、當掉自動重開。
- **Windows 筆電**：[deploy/windows/README.md](../deploy/windows/README.md)，一鍵安裝、自動重啟、開機自動啟動。
- **自動更新**：`.env` 設定 `AUTO_UPDATE=1`（部署腳本會幫你設定）後，每 10 分鐘檢查 GitHub，
  只套用自動測試通過的版本。本機有改過程式（工作目錄不乾淨）或有還沒推上去的 commit 時會暫停，避免蓋掉你的修改。
  fork 之後會從你自己 fork 的 repo 更新。

## 股票模擬

用錢包裡的新台幣買賣台股（上市股票與 ETF）。需要 [twfolio](https://github.com/BoYoOUO-0924/tw-stock-portfolio)
提供股票清單、歷史走勢、本益比與交易日曆；沒有 twfolio 時 `/stock` 會停用，其他功能照常。

- **設定**：`.env` 的 `TWFOLIO_DB`（行情資料庫，唯讀開啟，不會修改）與 `TWFOLIO_CONFIG`（讀手續費設定）。
  twfolio 要每個交易日更新行情（例如工作排程每個平日 18:30）。
- **行情**：報價先查證交所即時行情（約 5 秒更新），查不到時用 twfolio 的每日收盤資料。
- **成交規則**：交易日 9:00–13:30 立刻用即時價成交（買進用最佳賣價、賣出用最佳買價，漲停買不到、跌停賣不掉）；
  其他時間變成委託單，用下一個交易日的**開盤價**成交。委託買進先預扣「參考價 × 1.1」，成交後多的退回；成交或取消會私訊通知。
- **手續費與交易稅**：讀 twfolio `config.yaml` 的 `fees`（手續費 0.1425% 捨去、整股最低 20 元、零股最低 1 元；賣出交易稅股票 0.3%、ETF 0.1%）。

## Minecraft 伺服器管理

讓 Discord 伺服器的管理員開關跑在 bot 同一台電腦上的 Minecraft 伺服器。需要一支自己的控制腳本，支援：

| 腳本參數 | Discord 指令 | 權限 |
|---|---|---|
| `status` | `/mc status`：開著沒、幾個人在線 | 所有人 |
| `start`、`stop`、`restart` | `/mc start`、`/mc stop`、`/mc restart`，結果公開在頻道 | 管理伺服器 |
| `backup` | `/mc backup` | 管理伺服器 |
| `cmd 指令` | `/mc cmd 指令`，例如 `say 大家好`、`op 玩家`（`stop` 請用 `/mc stop`） | 管理伺服器 |
| `log 行數` | `/mc log [行數]`，只有自己看得到 | 管理伺服器 |

- `.env` 設定 `MINECRAFT_SCRIPT`（預設 `~/.local/bin/mc`）和 `MINECRAFT_GUILD_IDS`（逗號分隔）。
  指令只會出現在列出的伺服器，其他伺服器看不到；沒設定就不啟用。
- 開關服同時只能做一件事；每次操作都會記到管理後台的操作紀錄。
- 伺服器跑在 tmux 裡的話：如果 tmux 背景程式還沒在跑，bot 會拒絕開服，
  因為由 bot 開出來的 tmux 會在 bot 重開（例如自動更新）時被一起砍掉，Minecraft 來不及存檔。請先在主機上開一次。

## 舊版 Webhook 天氣與地震

`/settings` 出現之前，天氣和地震是用 Webhook 傳到固定頻道。現在建議改用 `/settings weather`、`/settings earthquake`，
舊的設定仍然可以用：

- `WEATHER_WEBHOOK_URL` + `CWA_API_KEY`：每天 `WEATHER_TIME`（預設 06:00）傳台北、淡水的天氣。
  傳送時間剛好關機的話，開機後中午前會補送；紀錄在 `data/weather_last_sent.txt`。
- `EARTHQUAKE_WEBHOOK_URL`（沒填就用天氣的 Webhook）：台北震度 4 級以上時通報。
- Webhook 不屬於任何伺服器，所以只能在管理後台的「所有伺服器」關閉。
