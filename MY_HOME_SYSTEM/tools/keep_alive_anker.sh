#!/bin/bash

# ==========================================
# Anker SoundCore 2 Keep-Alive Script (PipeWire Edition)
# ==========================================

# --- Configuration ---
LOGFILE="/home/masahiro/develop/MY_HOME_SYSTEM/logs/bluetooth_monitor.log"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
SPEAKER_MAC="F4:4E:FC:B6:65:D4" # Anker SoundCore 2 MAC Address
CONNECT_SCRIPT="/home/masahiro/develop/MY_HOME_SYSTEM/connect_speaker.sh"

# --- Environment Setup for PipeWire/PulseAudio (CRUCIAL) ---
# cron実行時でもPipeWireソケットを見つけられるようにする
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=${XDG_RUNTIME_DIR}/bus"

# ログ関数
log() {
    echo "$TIMESTAMP - $1" >> "$LOGFILE"
}

# --- 1. Check Connection ---
# pactlでシンク一覧を取得し、AnkerのMACアドレスが含まれているか確認
if pactl list sinks short | grep -q "${SPEAKER_MAC//:/_}"; then
    STATUS="CONNECTED"
else
    STATUS="DISCONNECTED"
fi

# --- 2. Action based on Status ---
if [ "$STATUS" = "DISCONNECTED" ]; then
    log "[WARN] Speaker not connected. Triggering reconnect script..."
    
    if [ -x "$CONNECT_SCRIPT" ]; then
        # 再接続スクリプトを実行 (出力をログに追記)
        "$CONNECT_SCRIPT" >> "$LOGFILE" 2>&1
        # 再接続待ち時間を少し設ける
        sleep 5
    else
        log "[ERROR] Reconnect script not found at $CONNECT_SCRIPT"
    fi
    
    # 再接続できたか確認（オプション）
    if pactl list sinks short | grep -q "${SPEAKER_MAC//:/_}"; then
        log "[INFO] Reconnection successful."
        STATUS="CONNECTED"
    fi
fi

if [ "$STATUS" = "CONNECTED" ]; then
    # --- 3. Play Inaudible Noise (Keep-Alive) ---
    # 人間の可聴域外(15Hz)の正弦波を2秒間再生する
    # 音量は0.01 (1%) だが、信号としては存在するためアンプがONのまま維持される
    
    # soxで音声を生成し、paplay (PulseAudio client) にパイプで渡す
    if command -v sox &> /dev/null; then
        sox -n -r 48000 -b 16 -c 2 -t wav - synth 2 sin 15 vol 0.01 2>/dev/null | \
        paplay --stream-name="Anker KeepAlive" --property=media.role=event >/dev/null 2>&1
        
        RET=$?
        if [ $RET -eq 0 ]; then
            # 成功ログ（頻繁に出るのでコメントアウト推奨、デバッグ時は有効化）
            # log "[SUCCESS] Keep-alive signal sent (15Hz)."
            :
        else
            log "[ERROR] Failed to play audio via paplay (Exit: $RET)."
        fi
    else
        log "[ERROR] 'sox' command not found. Please install: sudo apt install sox"
    fi
fi