#!/bin/bash

# ==========================================
# MY_HOME_SYSTEM 起動スクリプト (Systemd-Hybrid Fix)
# ==========================================

# ★修正1: 親ディレクトリ(develop)も含めないと "No module named 'MY_HOME_SYSTEM'" エラーになる
export PYTHONPATH="/home/masahiro/develop:/home/masahiro/develop/MY_HOME_SYSTEM"

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
pkill -f bluetooth_monitor.py
pkill -f scheduler.py
pkill -f "streamlit run"

# プロセスが消えるまで最大5秒待機 (10秒は長いので短縮)
for i in {1..5}; do
  if ! pgrep -f unified_server.py > /dev/null; then
    echo "✅ Old server stopped."
    break
  fi
  echo "⏳ Waiting for shutdown... ($i/5)"
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

# --- Phase 3: 初期化 & Webhook修正 ---
echo "--- Fix Webhook (Using Systemd ngrok) ---"

# ★修正2: ngrokは systemd (ngrok.service) 側で管理するため、ここでは起動しない
# nohup ngrok http 8000 > /dev/null 2>&1 &
# echo "🚀 ngrok started..."
# sleep 5

# 既に動いているngrokの情報を取得して更新
$PYTHON_EXEC switchbot_webhook_fix.py > logs/webhook_fix.log 2>&1

# --- Phase 4: 常駐プロセス起動 ---
echo "--- Start Background Services ---"
$PYTHON_EXEC unified_server.py > logs/server_boot.log 2>&1 &
echo "🚀 Server started."

$PYTHON_EXEC monitors/camera_monitor.py > logs/camera_boot.log 2>&1 &
echo "📷 Camera Monitor started."

$PYTHON_EXEC monitors/bluetooth_monitor.py > logs/bluetooth_boot.log 2>&1 &
echo "🎧 Bluetooth Monitor started."

$PYTHON_EXEC scheduler.py > logs/scheduler_boot.log 2>&1 &
echo "⏰ Scheduler started."

# ★修正3: LAN内公開用にアドレス指定を追加
$PYTHON_EXEC -m streamlit run dashboard.py --server.port 8501 --server.address 0.0.0.0 > logs/dashboard_boot.log 2>&1 &
echo "📊 Dashboard started."

echo "✅ All systems go!"