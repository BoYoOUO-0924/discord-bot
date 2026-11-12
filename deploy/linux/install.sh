#!/usr/bin/env bash
# 在 Linux 雲端主機（Ubuntu 24.04）安裝嘎哇 Bot，並設定成開機自動啟動、當掉自動重開。
# 用法：在專案資料夾執行  bash deploy/linux/install.sh
# 可以重複執行：已經做過的步驟會略過。
set -euo pipefail

cd "$(dirname "$0")/../.."
APP_DIR="$(pwd)"
APP_USER="$(id -un)"
SERVICE=gawa-bot

if [ "$APP_USER" = "root" ]; then
    echo "[錯誤] 請用一般使用者執行（需要時腳本會自己用 sudo），不要用 root。"
    exit 1
fi

echo "== 1/6 安裝系統套件"
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-venv git curl openssl ca-certificates ffmpeg

echo "== 2/6 時區設成台北（紀錄的時間比較好看）"
sudo timedatectl set-timezone Asia/Taipei || true

echo "== 3/6 記憶體小於 2 GB 時加 1 GB 置換空間，避免安裝套件或尖峰時被系統砍掉"
mem_kb=$(awk '/MemTotal/ {print $2}' /proc/meminfo)
if [ "$mem_kb" -lt 2000000 ] && ! swapon --show | grep -q /swapfile; then
    sudo fallocate -l 1G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile >/dev/null
    sudo swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
    echo "   已加上 1 GB 置換空間"
fi

echo "== 4/6 建立虛擬環境、安裝 Python 套件"
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/python -m pip install -q --disable-pip-version-check -r requirements.txt
mkdir -p data logs
if [ ! -f .env ]; then
    cp .env.example .env
    chmod 600 .env
    echo "   已用 .env.example 建立 .env，請之後把舊電腦的 .env 內容貼進來"
fi

echo "== 5/6 中央氣象署憑證"
# opendata.cwa.gov.tw 沒有送出中繼憑證。Windows 會自動補抓，Linux 不會，
# 所以照憑證裡寫的下載網址（AIA）抓下中繼憑證，加進系統信任清單。
host=opendata.cwa.gov.tw
if curl -s -o /dev/null --max-time 15 "https://$host/"; then
    echo "   連線正常，不需要處理"
else
    tmp=$(mktemp -d)
    openssl s_client -connect "$host:443" -servername "$host" </dev/null 2>/dev/null \
        | openssl x509 >"$tmp/leaf.pem"
    url=$(openssl x509 -in "$tmp/leaf.pem" -noout -ext authorityInfoAccess 2>/dev/null \
        | sed -n 's/.*CA Issuers - URI:\(.*\)/\1/p' | head -1)
    if [ -z "$url" ]; then
        echo "   [警告] 找不到中繼憑證的下載網址，天氣和地震功能可能無法使用"
    else
        curl -s --max-time 30 -o "$tmp/inter.crt" "$url"
        openssl x509 -inform DER -in "$tmp/inter.crt" -out "$tmp/inter.pem" 2>/dev/null \
            || cp "$tmp/inter.crt" "$tmp/inter.pem"
        sudo cp "$tmp/inter.pem" /usr/local/share/ca-certificates/cwa-intermediate.crt
        sudo update-ca-certificates >/dev/null
        if curl -s -o /dev/null --max-time 15 "https://$host/"; then
            echo "   已補上中繼憑證，連線正常"
        else
            echo "   [警告] 補上憑證後還是連不上 $host，天氣和地震功能可能無法使用"
        fi
    fi
    rm -rf "$tmp"
fi

echo "== 6/6 設定成系統服務（開機自動啟動、當掉 10 秒後重開）"
sudo tee /etc/systemd/system/$SERVICE.service >/dev/null <<UNIT
[Unit]
Description=Gawa Discord Bot
After=network-online.target
Wants=network-online.target

[Service]
User=$APP_USER
WorkingDirectory=$APP_DIR
Environment=AUTO_UPDATE=1
Environment=PYTHONUNBUFFERED=1
# 新版本可能需要新套件；裝失敗（例如網路斷了）也照樣啟動
ExecStartPre=-$APP_DIR/.venv/bin/python -m pip install -q --disable-pip-version-check -r requirements.txt
# YouTube 常改版，每次啟動順便把 yt-dlp 更新到最新
ExecStartPre=-$APP_DIR/.venv/bin/python -m pip install -q --disable-pip-version-check -U yt-dlp[default]
ExecStart=$APP_DIR/.venv/bin/python bot.py
# 結束代碼 3 = 自動更新完要重開；其他代碼 = 當掉，一樣重開
Restart=always
RestartSec=10
# 結束代碼 4 = 這台已經有另一個 bot 在跑，不要一直重開
RestartPreventExitStatus=4

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable $SERVICE >/dev/null

echo
if grep -E '^DISCORD_TOKEN=.{20,}' .env | grep -qv 'YOUR_'; then
    sudo systemctl restart $SERVICE
    echo "完成！bot 已經啟動。查看狀態：sudo systemctl status $SERVICE"
else
    echo "完成！.env 還沒有 DISCORD_TOKEN：填好後執行  sudo systemctl start $SERVICE"
fi
echo "看即時紀錄：tail -f $APP_DIR/logs/bot.log"
