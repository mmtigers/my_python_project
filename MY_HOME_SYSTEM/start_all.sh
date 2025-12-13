#!/bin/bash

# プロジェクトのディレクトリへ移動
cd /home/masahiro/develop/MY_HOME_SYSTEM

echo "--- 0. 古いプロセスを掃除します ---"
# ngrok, サーバー, カメラ監視, ダッシュボードを強制終了
pkill ngrok
pkill -f unified_server.py
pkill -f camera_monitor.py
pkill -f "streamlit run"

echo "--- 1. ngrokを起動します ---"
# バックグラウンドで起動
ngrok http 8000 > /dev/null &

echo "--- 2. ngrokの立ち上がりを待ちます (5秒) ---"
sleep 5

# 仮想環境のPythonパス
PYTHON_EXEC="/home/masahiro/develop/MY_HOME_SYSTEM/.venv/bin/python3"

echo "--- 3. Webhookアドレスを更新します ---"
$PYTHON_EXEC switchbot_webhook_fix.py

echo "--- 4. カメラ監視を起動します ---"
$PYTHON_EXEC camera_monitor.py &

echo "--- 5. ダッシュボードを起動します ---"
source .venv/bin/activate
streamlit run dashboard.py > /dev/null 2>&1 &
deactivate

echo "--- 6. Pythonサーバーを起動します ---"
$PYTHON_EXEC unified_server.py