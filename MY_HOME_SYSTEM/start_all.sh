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
pkill -f scheduler.py
pkill -f "streamlit run"

# プロセスが消えるまで最大10秒待機 (ここが重要)
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
    # ここでexit 1するとSystemdが無限再起動するので、
    # NASなしでもサーバーだけは起動させるようにする（あるいはここで待機ループ）
  else
    echo "✅ NAS Mounted."
  fi
fi

# --- Phase 2: Frontend Build (Build Skip Logic) ---
# ※Systemdタイムアウト回避のため、自動ビルドは一旦コメントアウト推奨
# echo "--- Check Frontend ---"
# if [ -d "$QUEST_DIR" ]; then
#   (cd "$QUEST_DIR" && npm install >> ../MY_HOME_SYSTEM/logs/quest_build.log 2>&1 && npm run build >> ../MY_HOME_SYSTEM/logs/quest_build.log 2>&1)
# fi

# --- Phase 3: 初期化 ---
echo "--- Fix Webhook ---"
$PYTHON_EXEC switchbot_webhook_fix.py

# --- Phase 4: 常駐プロセス起動 ---
echo "--- Start Background Services ---"
$PYTHON_EXEC camera_monitor.py >> logs/camera.log 2>&1 &
$PYTHON_EXEC scheduler.py >> logs/scheduler.log 2>&1 &

source .venv/bin/activate
nohup streamlit run dashboard.py > /dev/null 2>&1 &
deactivate

# --- Phase 5: メインサーバー起動 (exec使用) ---
echo "🚀 Starting Unified Server..."
echo "Logs: logs/server.log"

# ★重要: execを使うことで、シェルのプロセスがPythonプロセスに置き換わります。
# これによりSystemdからのシグナル(停止命令)が直接Pythonに届くようになり、管理が安定します。
exec $PYTHON_EXEC unified_server.py >> logs/server.log 2>&1