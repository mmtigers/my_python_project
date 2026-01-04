#!/bin/bash

# ==========================================
# 設定
# ==========================================
MAC="F4:4E:FC:B6:65:D4"
PROJECT_DIR="/home/masahiro/develop/MY_HOME_SYSTEM"
ENV_FILE="$PROJECT_DIR/.env"
LOGFILE="$PROJECT_DIR/logs/bluetooth_monitor.log"
MAX_RETRIES=3

# ==========================================
# 環境変数の読み込み
# ==========================================
if [ -f "$ENV_FILE" ]; then
    set -a
    source "$ENV_FILE"
    set +a
fi

WEBHOOK_URL="${DISCORD_WEBHOOK_ERROR:-$DISCORD_WEBHOOK_NOTIFY}"

# ==========================================
# 関数定義
# ==========================================
send_discord() {
    local message="$1"
    if [ -n "$WEBHOOK_URL" ]; then
        # JSONエスケープ処理 (改行等を安全に送る)
        # jqがあれば使うが、簡易的にpythonを使用
        escaped_message=$(python3 -c "import json, sys; print(json.dumps(sys.argv[1]))" "$message")
        # 両端のダブルクォートを除去
        escaped_message="${escaped_message#\"}"
        escaped_message="${escaped_message%\"}"
        
        curl -H "Content-Type: application/json" \
             -X POST \
             -d "{\"content\": \"$message\"}" \
             "$WEBHOOK_URL" >/dev/null 2>&1
    fi
}

log_message() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" >> "$LOGFILE"
}

# ==========================================
# メイン処理
# ==========================================

# 接続確認
if ! bluetoothctl info "$MAC" | grep -q "Connected: yes"; then
    log_message "[WARN] Speaker disconnected. Starting reconnection sequence..."
    send_discord "⚠️ **Bluetoothスピーカー切断検知**\n再接続プロセスを開始します（最大${MAX_RETRIES}回試行）"

    # 念のため信頼設定を更新
    bluetoothctl trust "$MAC" >> "$LOGFILE" 2>&1

    success=false
    
    for ((i=1; i<=MAX_RETRIES; i++)); do
        log_message "Attempt $i/$MAX_RETRIES: Connecting to $MAC..."
        
        # 接続コマンド実行結果を変数に格納
        output=$(bluetoothctl connect "$MAC" 2>&1)
        
        # 結果判定
        if echo "$output" | grep -q "Connection successful"; then
            log_message "[SUCCESS] Reconnection successful on attempt $i."
            send_discord "✅ **再接続に成功しました** (試行回数: $i)"
            success=true
            
            # 音声出力先を再設定（念のため）
            pactl set-default-sink "bluez_output.${MAC//:/_}.1" >/dev/null 2>&1
            pactl set-sink-volume "bluez_output.${MAC//:/_}.1" 100% >/dev/null 2>&1
            break
        else
            log_message "[FAIL] Attempt $i failed. Output: $output"
            # 失敗したら少し待機
            sleep 5
        fi
    done

    # 全リトライ失敗時
    if [ "$success" = false ]; then
        log_message "[ERROR] All reconnection attempts failed."
        send_discord "🚨 **再接続に失敗しました**\n最後のログ:\n\`\`\`\n$output\n\`\`\`\nスピーカーの電源または他デバイスとの接続を確認してください。"
    fi
fi