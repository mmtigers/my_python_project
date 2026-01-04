#!/bin/bash

# ==========================================
# 設定
# ==========================================
MAC="F4:4E:FC:B6:65:D4"
PROJECT_DIR="/home/masahiro/develop/MY_HOME_SYSTEM"
ENV_FILE="$PROJECT_DIR/.env"
LOGFILE="$PROJECT_DIR/logs/bluetooth_monitor.log"

# ==========================================
# 環境変数の読み込み (.env)
# ==========================================
if [ -f "$ENV_FILE" ]; then
    set -a
    source "$ENV_FILE"
    set +a
fi

# 通知先 (エラー用Webhookを優先、なければ通知用を使用)
WEBHOOK_URL="${DISCORD_WEBHOOK_ERROR:-$DISCORD_WEBHOOK_NOTIFY}"

# ==========================================
# Discord通知関数
# ==========================================
send_discord() {
    local message="$1"
    if [ -n "$WEBHOOK_URL" ]; then
        curl -H "Content-Type: application/json" \
             -X POST \
             -d "{\"content\": \"$message\"}" \
             "$WEBHOOK_URL" >/dev/null 2>&1
    fi
}

# ==========================================
# メイン処理
# ==========================================

# 接続状態を確認 (Connected: yes が含まれていない場合のみ実行)
if ! bluetoothctl info "$MAC" | grep -q "Connected: yes"; then
    TIMESTAMP=$(date "+%Y-%m-%d %H:%M:%S")
    
    # ログ記録
    echo "$TIMESTAMP - [WARN] Speaker disconnected. Trying to reconnect..." >> "$LOGFILE"
    
    # Discord通知 (切断検知)
    send_discord "⚠️ **Bluetoothスピーカーの切断を検知しました**\n自動再接続を試みます..."

    # 接続試行
    bluetoothctl connect "$MAC" >> "$LOGFILE" 2>&1
    
    # 少し待機して結果確認
    sleep 5
    if bluetoothctl info "$MAC" | grep -q "Connected: yes"; then
        echo "$TIMESTAMP - [SUCCESS] Reconnection successful." >> "$LOGFILE"
        send_discord "✅ **Bluetoothスピーカーの再接続に成功しました**"
    else
        echo "$TIMESTAMP - [ERROR] Failed to reconnect." >> "$LOGFILE"
        send_discord "🚨 **Bluetoothスピーカーの再接続に失敗しました**\n手動での確認が必要です。\nMAC: $MAC"
    fi
fi