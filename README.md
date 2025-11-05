# Discord Bot

## 安裝與啟動

1. 建立虛擬環境（建議）
   ```powershell
   py -3.13 -m venv .venv
   .\.venv\Scripts\python -m pip install -U pip
   .\.venv\Scripts\python -m pip install -r requirements.txt
   ```

2. 設定 Token（擇一）
   - 環境變數（推薦）
     ```powershell
     $env:DISCORD_TOKEN = <你的Bot Token>
     ```
   - 本機檔案（不會被提交）
     ```powershell
     Copy-Item config.example.py config.py
     # 編輯 config.py 並填入 TOKEN
     ```

3. 執行
   ```powershell
   .\.venv\Scripts\python .\bot.py
   ```

## Git 與安全

- `config.py`、`.env` 已在 `.gitignore` 中忽略，切勿提交真實憑證。
- 若誤提交祕密，請重置 Discord Bot Token，並用 `git filter-repo` 清理歷史後強制推送。

## 專案結構

```
discord bot/
  ├─ bot.py
  ├─ config.example.py
  ├─ requirements.txt
  ├─ cogs/
  │   └─ __init__.py
  └─ ...
```


