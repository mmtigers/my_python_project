#!/bin/bash

# ==========================================
# MY_HOME_SYSTEM 起動スクリプト (Final Stable)
# ==========================================

PROJECT_DIR="/home/masahiro/develop/MY_HOME_SYSTEM"
QUEST_DIR="/home/masahiro/develop/family-quest"
cd "$PROJECT_DIR" || exit 1

# Pythonパス
if [ -f ".venv/bin/python3" ]; then
    PYTHON_EXEC=".venv/bin/python3"
else
    PYTHON_EXEC="python3"
fi

# ログディレクトリ
mkdir -p logs

# --- Phase 0: 徹底的なクリーンアップ ---
echo "--- Cleanup Old Processes ---"
# まずは優しく停止
pkill -f unified_server.py
pkill -f camera_monitor.py
pkill -f bluetooth_monitor.py  # ★追加
pkill -f scheduler.py
pkill -f "streamlit run"

# プロセスが消えるまで最大10秒待機
for i in {1..10}; do
  if ! pgrep -f unified_server.py > /dev/null; then
    echo "✅ Old server stopped."
    break
  fi
  echo "⏳ Waiting for shutdown... ($i/10)"
  sleep 1
done

# まだ生きていたら強制終了
if pgrep -f unified_server.py > /dev/null; then
  echo "💀 Force killing server..."
  pkill -9 -f unified_server.py
fi

# --- Phase 1: NASマウント確認 ---
echo "--- Check NAS Mount ---"
MOUNT_POINT="/mnt/nas"
if command -v mountpoint >/dev/null 2>&1; then
  if ! mountpoint -q "$MOUNT_POINT"; then
    echo "⚠️ NAS is NOT mounted. Skipping checks to avoid hang."
  else
    echo "✅ NAS Mounted."
  fi
fi

# --- Phase 2: Frontend Build (Build Skip Logic) ---
# echo "--- Check Frontend ---"
# if [ -d "$QUEST_DIR" ]; then
#   (cd "$QUEST_DIR" && npm install >> ../MY_HOME_SYSTEM/logs/quest_build.log 2>&1 && npm run build >> ../MY_HOME_SYSTEM/logs/quest_build.log 2>&1)
# fi

# --- Phase 3: 初期化 ---
echo "--- Fix Webhook ---"
$PYTHON_EXEC switchbot_webhook_fix.py

# --- Phase 4: 常駐プロセス起動 ---
echo "--- Start Background Services ---"
$PYTHON_EXEC unified_server.py > logs/server_boot.log 2>&1 &
echo "🚀 Server started."

$PYTHON_EXEC camera_monitor.py > logs/camera_boot.log 2>&1 &
echo "📷 Camera Monitor started."

# ★追加: Bluetoothモニター起動
$PYTHON_EXEC bluetooth_monitor.py > logs/bluetooth_boot.log 2>&1 &
echo "🎧 Bluetooth Monitor started."

$PYTHON_EXEC scheduler.py > logs/scheduler_boot.log 2>&1 &
echo "⏰ Scheduler started."

echo "✅ All systems go!"