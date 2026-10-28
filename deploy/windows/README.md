# 在舊筆電上 24 小時執行嘎哇 Bot（Windows 10 / 11）

設定完成後，筆電開機就會自動啟動 bot。之後只要有新版本推到 GitHub，而且 GitHub 上的自動測試通過，筆電會在 10 分鐘內自動更新並重新啟動。

## 一次性設定

### 1. 在主電腦準備兩個檔案
1. **先關掉主電腦上的 bot**。同一時間只能有一個 bot 在線，不然每個指令都會被回應兩次。
2. 把這兩個檔案複製到隨身碟：
   - `config.py`：裡面有 Bot Token，**不要傳給別人，也不要放進公開的地方**
   - `data\bot.db`：大家的積分、簽到等資料。如果旁邊有 `bot.db-wal`，也一起複製。

### 2. 在筆電安裝 Python 和 Git
在筆電打開 **PowerShell**，執行：
```powershell
winget install Python.Python.3.12
winget install Git.Git
```
安裝完成後，**關掉 PowerShell 再重新打開**，新安裝的程式才會生效。

### 3. 下載程式
```powershell
git clone https://github.com/BoYoOUO-0924/discord-bot C:\GawaBot
```
> 建議放在 `C:\GawaBot`，不要放在桌面：桌面常被 OneDrive 同步，資料庫一直被同步容易出問題。

### 4. 放回設定和資料
- `config.py` → 放到 `C:\GawaBot\config.py`
- `bot.db`（和 `bot.db-wal`）→ 放到 `C:\GawaBot\data\`

### 5. 依序執行腳本
打開 `C:\GawaBot\deploy\windows`，依序雙擊：

| 腳本 | 做什麼 |
| --- | --- |
| `1_install.bat` | 建立虛擬環境、安裝套件，並在桌面建立「Gawa Bot」捷徑 |
| 桌面的 **Gawa Bot** | 啟動 bot。可以到 Discord 試一下 `/menu` 確認正常 |
| `3_autostart.bat` | 之後每次登入 Windows，bot 都會自動啟動（縮小到工作列） |
| `4_keep_awake.bat` | 選用：插電時不睡眠、蓋上螢幕不休眠。不影響用電池時的設定 |

### 6. 筆電重新開機後
`3_autostart.bat` 會在**登入 Windows 後**才啟動 bot。如果筆電重開後停在登入畫面（例如 Windows 更新後自動重開），bot 就不會啟動。

如果希望完全不用管，可以讓 Windows 開機自動登入。筆電放在家裡的話通常可以接受，但任何人拿到筆電就能直接進去，請自己衡量。

## 自動更新怎麼運作
- bot 每 10 分鐘檢查一次 GitHub 的 `main` 分支。
- 有新版本時，會先確認 GitHub 上的**自動測試通過**才會更新；測試失敗或還在跑的版本不會裝。
- 有人正在玩撲克、21 點、猜數字或海龜湯時，會**等遊戲結束再更新**，最多延後 1 小時。
- 更新完會自動重新啟動，大約 10–20 秒後 bot 就會回到線上。新版本需要的套件也會自動安裝。
- 如果筆電上的程式被手動改過，就不會自動覆蓋，紀錄裡會寫「本機有自己的修改」。

## 日常操作
- **看狀態**：工作列上的 bot 視窗，或 `C:\GawaBot\logs\bot.log`
- **停止**：關掉 bot 視窗
- **取消開機自動啟動**：執行 `remove_autostart.bat`
- **備份資料**：先關掉 bot，再複製 `data\bot.db`

## 常見問題
**每個指令都被回應兩次**
主電腦和筆電的 bot 同時在線。關掉其中一台。

**一直沒有自動更新**
打開 `logs\bot.log`，搜尋「自動更新」看原因：
- 「本機有自己的修改」：筆電上的程式被改過。如果確定不需要那些修改，在 `C:\GawaBot` 開 PowerShell 執行 `git reset --hard origin/main`。這只會還原程式檔，`config.py` 和 `data\` 裡的資料不受影響。
- 「找不到 git」：回到步驟 2 安裝 Git。

**bot 一直重啟**
看 `logs\bot.log` 最後的錯誤訊息。常見原因是 `config.py` 的 Token 填錯或過期，要到 Discord Developer Portal 重新產生。
