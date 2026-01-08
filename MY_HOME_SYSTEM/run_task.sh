#!/bin/bash

# ==========================================
# Cron Job Wrapper Script
# ==========================================

# ベースディレクトリの設定
PROJECT_ROOT="/home/masahiro/develop/MY_HOME_SYSTEM"
VENV_PYTHON="${PROJECT_ROOT}/.venv/bin/python3"
LOG_DIR="${PROJECT_ROOT}/logs"

# 引数チェック
if [ $# -lt 1 ]; then
    echo "Usage: $0 <script_name> [args...]"
    exit 1
fi

SCRIPT_NAME=$1
shift # 最初の引数(スクリプト名)をずらして、残りを引数として渡す

# ログファイル名をスクリプト名から自動生成 (例: script.py -> script.log)
LOG_FILE="${LOG_DIR}/$(basename "${SCRIPT_NAME}" .py).log"

# プロジェクトルートに移動 (相対パスimport対策)
cd "${PROJECT_ROOT}" || exit 1

# 実行 & ログ出力 (タイムスタンプ付与も検討できますが、今回はシンプルに追記)
# 必要であれば .env の読み込みもここで行います
# source .env

echo "--- [$(date '+%Y-%m-%d %H:%M:%S')] Start: ${SCRIPT_NAME} ---" >> "${LOG_FILE}"

# Pythonスクリプトの実行
"${VENV_PYTHON}" "${SCRIPT_NAME}" "$@" >> "${LOG_FILE}" 2>&1

EXIT_CODE=$?

if [ ${EXIT_CODE} -ne 0 ]; then
    echo "--- [$(date '+%Y-%m-%d %H:%M:%S')] ERROR: Exit Code ${EXIT_CODE} ---" >> "${LOG_FILE}"
    # ここにDiscordへの緊急通知処理を入れると堅牢性が増します
else
    echo "--- [$(date '+%Y-%m-%d %H:%M:%S')] Success ---" >> "${LOG_FILE}"
fi

exit ${EXIT_CODE}