#!/bin/bash

# Anker SoundCore 2 のMACアドレス
MAC="F4:4E:FC:B6:65:D4"

# ログ保存先 (MY_HOME_SYSTEMのログフォルダ)
LOGFILE="/home/masahiro/develop/MY_HOME_SYSTEM/logs/bluetooth_monitor.log"

# 接続状態を確認 (Connected: yes が含まれていない場合のみ実行)
if ! bluetoothctl info "$MAC" | grep -q "Connected: yes"; then
    echo "$(date) - [INFO] Speaker disconnected. Trying to reconnect..." >> "$LOGFILE"
    
    # 接続試行
    bluetoothctl connect "$MAC" >> "$LOGFILE" 2>&1
    
    # 結果確認
    if bluetoothctl info "$MAC" | grep -q "Connected: yes"; then
        echo "$(date) - [SUCCESS] Reconnection successful." >> "$LOGFILE"
    else
        echo "$(date) - [ERROR] Failed to reconnect." >> "$LOGFILE"
    fi
fi