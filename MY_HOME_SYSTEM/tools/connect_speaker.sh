#!/bin/bash

# ==========================================
# 設定
# ==========================================
MAC="F4:4E:FC:B6:65:D4"
PROJECT_DIR="/home/masahiro/develop/MY_HOME_SYSTEM"
ENV_FILE="$PROJECT_DIR/.env"
LOGFILE="$PROJECT_DIR/logs/bluetooth_monitor.log"
STATUS_FILE="/tmp/speaker_connection_status" # 前回の状態を保存するファイル
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
# ヘルパー関数
# ==========================================

log_message() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" >> "$LOGFILE"
}

send_discord() {
    local message="$1"
    # 通知先URLがある場合のみ送信
    if [ -n "$WEBHOOK_URL" ]; then
        # 簡易JSONエスケープ
        escaped_message=$(python3 -c "import json, sys; print(json.dumps(sys.argv[1]))" "$message")
        escaped_message="${escaped_message#\"}"
        escaped_message="${escaped_message%\"}"
        
        curl -H "Content-Type: application/json" \
             -X POST \
             -d "{\"content\": \"$message\"}" \
             "$WEBHOOK_URL" >/dev/null 2>&1
    fi
}

# 自動調査機能：あらゆるログを収集して保存
run_diagnostics() {
    log_message "=== 🚑 AUTOMATIC DIAGNOSTICS START ==="
    {
        echo "--- [1] Bluetooth Service Status ---"
        systemctl status bluetooth --no-pager
        
        echo -e "\n--- [2] RFKill Status (Hardware Block) ---"
        rfkill list
        
        echo -e "\n--- [3] Bluetooth Device Info ---"
        bluetoothctl info "$MAC"
        
        echo -e "\n--- [4] Kernel Logs (Last 30 lines related to Bluetooth) ---"
        dmesg | grep -i "blue" | tail -n 30
        
        echo -e "\n--- [5] PulseAudio Sinks ---"
        pactl list sinks short
        
        echo -e "\n--- [6] Process List (PulseAudio) ---"
        pgrep -a pulse
    } >> "$LOGFILE" 2>&1
    log_message "=== 🚑 AUTOMATIC DIAGNOSTICS END ==="
}

# ==========================================
# メイン処理
# ==========================================

# ステータスファイルがなければ初期作成
if [ ! -f "$STATUS_FILE" ]; then
    echo "UNKNOWN" > "$STATUS_FILE"
fi

LAST_STATUS=$(cat "$STATUS_FILE")
CURRENT_STATUS="UNKNOWN"

# 1. 接続状態チェック
if bluetoothctl info "$MAC" | grep -q "Connected: yes"; then
    CURRENT_STATUS="OK"
    
    # 切断状態から復旧した場合のみ通知
    if [ "$LAST_STATUS" = "NG" ]; then
        log_message "[RECOVERY] Speaker connection restored."
        send_discord "✅ **Bluetoothスピーカーが復旧しました**"
        
        # 音声出力先を再設定（念のため）
        pactl set-default-sink "bluez_output.${MAC//:/_}.1" >/dev/null 2>&1
        pactl set-sink-volume "bluez_output.${MAC//:/_}.1" 100% >/dev/null 2>&1
    fi

    # 状態を保存して終了
    echo "OK" > "$STATUS_FILE"
    exit 0
fi

# 2. 切断検知時の処理
CURRENT_STATUS="NG"

# 初回検知時のみ通知する（連発防止）
if [ "$LAST_STATUS" != "NG" ]; then
    log_message "[WARN] Speaker disconnected. Starting recovery sequence..."
    send_discord "⚠️ **Bluetoothスピーカー切断を検知**\n自動修復プロセスを開始します..."
else
    # すでにNG状態なら、ログには残すが通知はしない
    log_message "[INFO] Speaker still disconnected. Retrying..."
fi

# 3. リトライ処理
SUCCESS=false
bluetoothctl trust "$MAC" >> "$LOGFILE" 2>&1 # 信頼設定を念押し

for ((i=1; i<=MAX_RETRIES; i++)); do
    log_message "Attempt $i/$MAX_RETRIES: Connecting..."
    
    # 接続試行
    bluetoothctl connect "$MAC" >> "$LOGFILE" 2>&1
    sleep 5 # 接続確立待ち
    
    if bluetoothctl info "$MAC" | grep -q "Connected: yes"; then
        SUCCESS=true
        log_message "[SUCCESS] Reconnected successfully on attempt $i."
        
        send_discord "✅ **再接続に成功しました** (試行回数: $i)"
        
        # 出力先設定
        pactl set-default-sink "bluez_output.${MAC//:/_}.1" >/dev/null 2>&1
        pactl set-sink-volume "bluez_output.${MAC//:/_}.1" 100% >/dev/null 2>&1
        
        echo "OK" > "$STATUS_FILE"
        exit 0
    fi
done

# 4. 全リトライ失敗時
if [ "$SUCCESS" = false ]; then
    log_message "[ERROR] All reconnection attempts failed."
    
    # 診断を実行
    run_diagnostics

    # 初回失敗時のみアラート通知（連発防止）
    # ※ ずっと切れたままなら毎分通知はせず、ログだけ残す
    if [ "$LAST_STATUS" != "NG" ]; then
        send_discord "🚨 **再接続に失敗しました**\n自動診断ログを記録しました。\n\`logs/bluetooth_monitor.log\` を確認してください。"
    fi
    
    echo "NG" > "$STATUS_FILE"
fi