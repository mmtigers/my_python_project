#!/bin/bash

# プロジェクトのディレクトリへ移動
cd /home/masahiro/develop/MY_HOME_SYSTEM

echo "--- 0. 古いプロセスを掃除します ---"
# ngrokはSystemdで管理しているので殺さない (コメントアウト)
# pkill ngrok
pkill -f unified_server.py
pkill -f camera_monitor.py
pkill -f "streamlit run"

# ngrokはSystemdで自動起動しているのでここでは起動しない
# echo "--- 1. ngrokを起動します ---"
# バックグラウンドで起動
# ngrok http 8000 > /dev/null &

# echo "--- 2. ngrokの立ち上がりを待ちます (5秒) ---"
# sleep 5

# 仮想環境のPythonパス
PYTHON_EXEC="/home/masahiro/develop/MY_HOME_SYSTEM/.venv/bin/python3"

echo "--- 3. Webhookアドレスを更新します ---"
# 既存のngrok(Systemd)のAPIを見に行きます
$PYTHON_EXEC switchbot_webhook_fix.py

echo "--- 4. カメラ監視を起動します ---"
$PYTHON_EXEC camera_monitor.py &

echo "--- 5. ダッシュボードを起動します ---"
source .venv/bin/activate
# nohup を使ってログアウト後も落ちないように強化
nohup streamlit run dashboard.py > /dev/null 2>&1 &
deactivate

echo "--- 6. Pythonサーバーを起動します ---"
$PYTHON_EXEC unified_server.py