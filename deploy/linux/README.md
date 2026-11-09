# 在雲端主機 24 小時執行嘎哇 Bot（Linux）

雲端主機不會當機沒回應、不怕停電，也不用開著筆電。這份說明以 **Ubuntu 24.04** 為例。

## 選哪一家（免費方案）

| | Oracle Cloud Always Free | Google Cloud 免費方案 |
| --- | --- | --- |
| 規格 | ARM 主機最多 4 核 / 24 GB（或 AMD 1 核 / 1 GB） | e2-micro：2 核（共享）/ 1 GB |
| 地區 | 可選日本、韓國、新加坡，離台灣近 | 只有美國幾個地區免費 |
| 缺點 | 註冊審核比較嚴；熱門地區常常「容量不足」開不出 ARM 主機 | 記憶體小，但跑這個 bot 夠用 |

兩家註冊都要信用卡驗證，只要留在免費額度內就不會扣款。免費方案的內容會變動，**開主機前請到官網確認目前的免費額度**，並開啟預算或帳單提醒。

> Discord 的伺服器在美國，主機放美國對 bot 的反應速度沒有影響。

## 一次性設定

### 1. 開主機
- 系統選 **Ubuntu 24.04**。
- 防火牆**不用**開任何額外的連接埠，SSH（22）就夠了。bot 是主動連到 Discord，不需要別人連進來。
- 依照網站的說明用 SSH 連進主機。

### 2. 下載程式並安裝
```bash
git clone https://github.com/BoYoOUO-0924/discord-bot ~/discord-bot
cd ~/discord-bot
bash deploy/linux/install.sh
```
腳本會：安裝 Python、時區設成台北、記憶體小時加置換空間、安裝套件、補上中央氣象署的憑證，最後設定成**開機自動啟動、當掉 10 秒後自動重開**的系統服務。

### 3. 搬設定和資料
**先關掉舊筆電上的 bot，並執行 `deploy\windows\remove_autostart.bat`。** 兩台同時在線，每個指令都會被回應兩次。

從舊筆電把這些檔案傳到主機（在筆電的 PowerShell 執行，`使用者` 和 `主機IP` 換成你的）：
```powershell
cd C:\Users\tung0\Desktop\discord-bot   # 舊筆電上 bot 的資料夾
scp .env 使用者@主機IP:~/discord-bot/.env
scp data\bot.db data\weather_last_sent.txt data\earthquake_seen.txt 使用者@主機IP:~/discord-bot/data/
```
有 `data\bot.db-wal` 的話也一起傳。沒有的檔案略過即可。

`.env` 裡有 Bot Token 和各種金鑰，**不要傳給別人，也不要放進公開的地方**。

### 4. 啟動
```bash
sudo systemctl restart gawa-bot
sudo systemctl status gawa-bot      # 看到 active (running) 就是在跑了
tail -f ~/discord-bot/logs/bot.log  # 看即時紀錄，Ctrl+C 離開
```
到 Discord 試一下 `/menu` 確認正常。

## 常用指令
| 做什麼 | 指令 |
| --- | --- |
| 看狀態 | `sudo systemctl status gawa-bot` |
| 重新啟動 | `sudo systemctl restart gawa-bot` |
| 暫停 | `sudo systemctl stop gawa-bot` |
| 看紀錄 | `tail -n 100 ~/discord-bot/logs/bot.log` |

自動更新和 Windows 版一樣：GitHub 上的測試通過後，10 分鐘內會自動更新並重開。

## 海龜湯的 AI 湯主（Ollama）
主機要能連到主電腦的 Ollama，跟筆電一樣用 Tailscale：
```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up       # 照畫面的網址登入「同一個帳號」
curl http://100.123.111.120:11434/api/tags   # 有列出模型就代表連得到
```
`.env` 的 `OLLAMA_URL` 不用改。主電腦關機時海龜湯一樣會顯示「湯主暫時離線」。

## 管理後台
預設的 `ADMIN_HOST=0.0.0.0` 在雲端主機上代表**整個網路都連得到**（只要防火牆有開）。建議：
- 不要在雲端防火牆開 8080 連接埠；
- 主機裝了 Tailscale 的話，在 `.env` 設定 `ADMIN_HOST=` 主機的 Tailscale IP（`tailscale ip -4` 可以查），只有你自己的裝置連得到：`http://Tailscale IP:8080`。

## 股票模擬（twfolio）
`/股票` 需要 twfolio 的資料庫（`TWFOLIO_DB`），它在舊筆電上。搬到雲端後要在主機上另外安裝 twfolio 並設定每日更新，不然 `/股票` 會停用，其他功能不受影響。
