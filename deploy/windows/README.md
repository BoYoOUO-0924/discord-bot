# 在舊筆電上 24 小時執行嘎哇 Bot（Windows 10 / 11）

設定完成後，筆電開機就會自動啟動 bot。之後只要有新版本推到 GitHub，而且 GitHub 上的自動測試通過，筆電會在 10 分鐘內自動更新並重新啟動。

## 一次性設定

### 1. 在主電腦準備兩個檔案
1. **先關掉主電腦上的 bot**。同一時間只能有一個 bot 在線，不然每個指令都會被回應兩次。
2. 把這兩個檔案複製到隨身碟：
   - `.env`：裡面有 Bot Token 和各種 API 金鑰，**不要傳給別人，也不要放進公開的地方**
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
- `.env` → 放到 `C:\GawaBot\.env`（還沒有的話，`1_install.bat` 會用 `.env.example` 幫你建一個並用記事本打開）
- `bot.db`（和 `bot.db-wal`）→ 放到 `C:\GawaBot\data\`

### 5. 依序執行腳本
打開 `C:\GawaBot\deploy\windows`，依序雙擊：

| 腳本 | 做什麼 |
| --- | --- |
| `1_install.bat` | 建立虛擬環境、安裝套件，並在桌面建立「Gawa Bot」捷徑 |
| 桌面的 **Gawa Bot** | 啟動 bot。可以到 Discord 試一下 `/menu` 確認正常 |
| `3_autostart.bat` | 之後每次登入 Windows，bot 都會自動啟動（縮小到工作列） |
| `4_keep_awake.bat` | **建議**：插電時不睡眠、蓋上螢幕不休眠，並偵測到「新式待命」時可關閉（見下方常見問題）。會要求系統管理員權限、自動判斷是否需要關閉新式待命，不影響用電池時的設定 |

### 6. 筆電重新開機後
`3_autostart.bat` 會在**登入 Windows 後**才啟動 bot。如果筆電重開後停在登入畫面（例如 Windows 更新後自動重開），bot 就不會啟動。

如果希望完全不用管，可以讓 Windows 開機自動登入。筆電放在家裡的話通常可以接受，但任何人拿到筆電就能直接進去，請自己衡量。

## 自動更新怎麼運作
- bot 每 10 分鐘檢查一次 GitHub 的 `main` 分支。
- 有新版本時，會先確認 GitHub 上的**自動測試通過**才會更新；測試失敗或還在跑的版本不會裝。
- 有人正在玩撲克、21 點、猜數字或海龜湯時，會**等遊戲結束再更新**，最多延後 1 小時。
- 更新完會自動重新啟動，大約 10–20 秒後 bot 就會回到線上。新版本需要的套件也會自動安裝。
- 如果筆電上的程式被手動改過，就不會自動覆蓋，紀錄裡會寫「本機有自己的修改」。

## 海龜湯的 AI 湯主（Ollama）
海龜湯的題目來自題庫，AI 只負責回答是非題和判斷猜答。筆電可以透過 Tailscale 使用另一台電腦上的 Ollama：

1. 筆電安裝 Tailscale 並登入**同一個帳號**，確認筆電能連到 Ollama 那台電腦（`100.123.111.120`）。
2. 在筆電的 `.env` 加上：
   ```
   OLLAMA_URL=http://100.123.111.120:11434
   OLLAMA_MODEL=gemma4:e4b-it-q4_K_M
   ```
3. 重新啟動 bot。`logs\bot.log` 會出現「AI（ollama …）狀態：正常」，管理後台右上角也會顯示「AI 湯主正常」。

注意：
- Ollama 那台電腦關機或沒有回應時，海龜湯會自動顯示「暫停中」，其他功能不受影響；恢復後 5 分鐘內會自動重新開放。
- Ollama 那台電腦需要讓 Ollama 接受其他裝置連線（設定環境變數 `OLLAMA_HOST=0.0.0.0` 後重開 Ollama）。

## 管理後台（網頁）
可以用瀏覽器查看所有人的積分、收支紀錄、統計圖表，並調整積分、修正連續簽到、結束卡住的牌局。

1. 在筆電的 `.env` 加一行，設定至少 8 個字元的密碼：
   ```
   ADMIN_PASSWORD=你自己想的密碼
   ```
2. 重新啟動 bot（關掉視窗再打開桌面的 Gawa Bot）。第一次啟動時，Windows 防火牆可能會詢問是否允許 Python 連線，請勾選 **私人網路** 並允許。
3. bot 視窗或 `logs\bot.log` 會出現「管理後台已啟動：http://192.168.x.x:8080」，在同一個 Wi-Fi 下的電腦或手機用瀏覽器打開這個網址，輸入密碼即可。

注意：
- 後台只給家裡的網路使用，**不要在路由器把 8080 連接埠轉發到網際網路**。
- 所有管理操作（調整積分、結束牌局等）都會記在後台的「操作紀錄」。
- bot 出問題時，打開後台的「**伺服器紀錄**」：可以只看錯誤、搜尋，或按「下載完整紀錄檔」傳給開發者。
  bot 當機重啟、套件安裝失敗也會記在這裡。
- 想換連接埠的話，在 `.env` 加上 `ADMIN_PORT=8090` 之類的設定。

## 日常操作
- **看狀態**：工作列上的 bot 視窗，或 `C:\GawaBot\logs\bot.log`
- **停止**：關掉 bot 視窗
- **取消開機自動啟動**：執行 `remove_autostart.bat`
- **備份資料**：先關掉 bot，再複製 `data\bot.db`

## 常見問題
**每個指令都被回應兩次，或常出現「Unknown interaction」**
有兩個 bot 同時在線，常見原因是主電腦和筆電都開著，關掉其中一台。
同一台電腦重複開啟（例如開機自動啟動後又雙擊桌面捷徑）的話，第二個視窗會顯示
「Gawa Bot is already running in another window」並停下來，不會真的啟動第二個，把那個視窗關掉就好。

**紀錄裡一直出現 `getaddrinfo failed`、`Attempting a reconnect`**
筆電暫時斷網（Wi-Fi 斷線或網卡省電），bot 會自動重連，網路恢復後就正常。
常常發生的話，參考下方「蓋上螢幕後 bot 還是離線」的 Wi-Fi 省電設定。

**一直沒有自動更新**
打開 `logs\bot.log`，搜尋「自動更新」看原因：
- 「本機有自己的修改」：筆電上的程式被改過。如果確定不需要那些修改，在 `C:\GawaBot` 開 PowerShell 執行 `git reset --hard origin/main`。這只會還原程式檔，`.env` 和 `data\` 裡的資料不受影響。
- 「找不到 git」：回到步驟 2 安裝 Git。

**明明設定「蓋上螢幕不執行任何動作」，蓋上後 bot 還是離線**
很多較新的筆電使用「新式待命」（Modern Standby，S0 低電源閒置）：只要螢幕關掉，Windows 就會進入待命，
「不執行任何動作」只代表蓋上時不主動睡眠，螢幕一關還是會待命，bot 就斷線了。
1. 在 PowerShell 執行 `powercfg /a`，如果「可用」的狀態裡有 **待命 (S0 低電源閒置)**，就是這個原因。
2. 執行 `4_keep_awake.bat`，腳本偵測到新式待命時會詢問「Turn off Modern Standby」，選 **Y**，然後**重新開機**。
3. 之後蓋上螢幕，bot 會持續在線。要還原，以系統管理員身分執行
   `reg delete HKLM\SYSTEM\CurrentControlSet\Control\Power /v PlatformAoAcOverride /f` 再重開機。

其他也會讓 bot 斷線的情況：
- 拔掉電源改用電池：這些設定只套用在插電時。
- 筆電廠商的電源管理程式（例如 Lenovo Vantage、MyASUS）或切換電源計畫，可能把設定改回去。重新執行 `4_keep_awake.bat` 即可。
- 無線網卡省電：裝置管理員 → 網路介面卡 → 你的 Wi-Fi → 內容 → 電源管理，取消勾選「允許電腦關閉這個裝置以節省電源」。

另外，bot 執行時本身也會阻止 Windows 因「閒置」而自動睡眠，但擋不住蓋上螢幕造成的待命。

**紀錄裡出現「這些設定還寫在舊的 config.py」**
設定檔已經改成 `.env`。舊的 `config.py` 還能用，但請把裡面的設定照 `.env.example` 的格式搬到 `.env`
（例如 `DISCORD_TOKEN = "abc"` 改寫成 `DISCORD_TOKEN=abc`），重新啟動確認沒有這行提醒後，就可以刪掉 `config.py`。

**bot 一直重啟**
看 `logs\bot.log` 最後的錯誤訊息。常見原因是 `.env` 的 `DISCORD_TOKEN` 填錯或過期，要到 Discord Developer Portal 重新產生。
