#!/bin/bash

# ==========================================
# 設定
# ==========================================
# ログファイルは接続監視と同じ場所に出力して一元管理します
LOGFILE="/home/masahiro/develop/MY_HOME_SYSTEM/logs/bluetooth_monitor.log"
SOUND_FILE="/mnt/nas/home_system/assets/sounds/silent.mp3"

# 日時取得
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

# ==========================================
# 再生処理
# ==========================================
# ログ記録: ハートビート開始
echo "$TIMESTAMP - [INFO] 💓 Sending heartbeat (silent audio)..." >> "$LOGFILE"

# 再生実行 (エラー時のみ標準出力を変数に取る)
OUTPUT=$(/usr/bin/mpg123 -o pulse "$SOUND_FILE" 2>&1)
EXIT_CODE=$?

# ==========================================
# 結果判定
# ==========================================
if [ $EXIT_CODE -ne 0 ]; then
    # 失敗時: エラー内容をログに書き込む
    echo "$(date '+%Y-%m-%d %H:%M:%S') - [ERROR] Heartbeat failed (Code: $EXIT_CODE). Reason: $OUTPUT" >> "$LOGFILE"
else
    # 成功時: 成功ログを残す（もしログが多すぎるようなら、この行はコメントアウトしてもOKです）
    echo "$(date '+%Y-%m-%d %H:%M:%S') - [SUCCESS] Heartbeat sent." >> "$LOGFILE"
fi