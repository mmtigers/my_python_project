#!/bin/bash

# プロジェクトのディレクトリへ移動
cd /home/masahiro/develop/MY_HOME_SYSTEM || exit

# ログディレクトリの準備
mkdir -p logs

echo "--- 0. 古いプロセスを掃除します ---"
# ngrokはSystemdで管理しているので殺さない
# pkill ngrok
pkill -f unified_server.py
pkill -f camera_monitor.py
pkill -f scheduler.py         # ★追加: スケジューラーも停止
pkill -f "streamlit run"

# ★ プロセスが完全に死ぬのを少し待つ
sleep 3

# ▼▼▼ NASマウント待機処理 ▼▼▼
echo "--- 0.5. NASのマウントを確認します ---"
MAX_RETRIES=10
COUNT=0
MOUNT_POINT="/mnt/nas"

# mountpointコマンドがあるか確認し、なければスキップ(Mac開発環境等用)
if command -v mountpoint >/dev/null 2>&1; then
  while ! mountpoint -q "$MOUNT_POINT"; do
    echo "⏳ NASがまだマウントされていません... (試行 $COUNT/$MAX_RETRIES)"
    sleep 3
    COUNT=$((COUNT+1))
    
    if [ $COUNT -ge $MAX_RETRIES ]; then
      echo "❌ NASのマウントに失敗しました。処理を中断します。"
      exit 1
    fi
  done
  echo "✅ NASマウント確認OK"
else
  echo "⚠️ mountpointコマンドが見つかりません。チェックをスキップします。"
fi

# 仮想環境のPythonパス (環境に合わせて自動検出または固定)
if [ -f ".venv/bin/python3" ]; then
    PYTHON_EXEC=".venv/bin/python3"
else
    PYTHON_EXEC="python3"
fi
echo "🐍 Using Python: $PYTHON_EXEC"

echo "--- 1. 初期化処理 ---"
# Webhookアドレス更新 (起動時1回のみ)
$PYTHON_EXEC switchbot_webhook_fix.py

echo "--- 2. 常駐プロセスを起動します ---"

# (A) カメラ監視 (常駐)
echo "   - Camera Monitor"
$PYTHON_EXEC camera_monitor.py >> logs/camera.log 2>&1 &

# (B) タスクスケジューラー (★追加: 定期実行スクリプトの管理)
echo "   - Task Scheduler (Monitor, Car, NAS, etc.)"
$PYTHON_EXEC scheduler.py >> logs/scheduler.log 2>&1 &

# (C) ダッシュボード (Streamlit)
echo "   - Dashboard"
source .venv/bin/activate
nohup streamlit run dashboard.py > /dev/null 2>&1 &
deactivate

echo "--- 3. Pythonサーバーを起動します ---"
# サーバーはメインとして最後に起動 (ログ追記モード)
$PYTHON_EXEC unified_server.py >> logs/server.log 2>&1