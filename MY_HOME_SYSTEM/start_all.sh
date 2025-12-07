#!/bin/bash

# プロジェクトのディレクトリへ移動
cd /home/masahiro/develop/MY_HOME_SYSTEM

echo "--- 0. 古いプロセスを掃除します ---"
# ngrok と pythonサーバーを強制終了してポートを解放
pkill ngrok
pkill -f unified_server.py

echo "--- 1. ngrokを起動します ---"
# バックグラウンドで起動
ngrok http 8000 > /dev/null &

echo "--- 2. ngrokの立ち上がりを待ちます (5秒) ---"
sleep 5

echo "--- 3. Webhookアドレスを更新します ---"
.venv/bin/python3 switchbot_webhook_fix.py

echo "--- 4. Pythonサーバーを起動します ---"
.venv/bin/python3 unified_server.py
