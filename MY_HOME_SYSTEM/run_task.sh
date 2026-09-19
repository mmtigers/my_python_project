#!/bin/bash

# ==========================================
# Cron Job / Task Wrapper Script (Fix)
# ==========================================

# ベースディレクトリの設定
PROJECT_ROOT="/home/masahiro/develop/MY_HOME_SYSTEM"
DEVELOP_ROOT="/home/masahiro/develop"
VENV_PYTHON="${PROJECT_ROOT}/.venv/bin/python3"
LOG_DIR="${PROJECT_ROOT}/logs"

# 引数チェック
if [ $# -lt 1 ]; then
    echo "Usage: $0 <script_name> [args...]"
    exit 1
fi

SCRIPT_NAME=$1
shift # 最初の引数(スクリプト名)をずらして、残りを引数として渡す

# ログファイル名をスクリプト名から自動生成
LOG_FILE="${LOG_DIR}/$(basename "${SCRIPT_NAME}" .py).log"

# プロジェクトルートに移動
cd "${PROJECT_ROOT}" || exit 1

# ★修正: 親ディレクトリ(develop)もパスに追加する
# (cron では PYTHONPATH が未設定のため、末尾の ":" で空要素(=カレントディレクトリ)が
#  暗黙の import ルートに加わらないよう、設定済みのときだけ連結する)
export PYTHONPATH="${DEVELOP_ROOT}:${PROJECT_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"

# 実行 & ログ出力
echo "--- [$(date '+%Y-%m-%d %H:%M:%S')] Start: ${SCRIPT_NAME} ---" >> "${LOG_FILE}"

"${VENV_PYTHON}" "${SCRIPT_NAME}" "$@" >> "${LOG_FILE}" 2>&1

EXIT_CODE=$?

if [ ${EXIT_CODE} -ne 0 ]; then
    echo "--- [$(date '+%Y-%m-%d %H:%M:%S')] ERROR: Exit Code ${EXIT_CODE} ---" >> "${LOG_FILE}"
    # Issue #751 (AUDIT-022): これまで cron タスクの失敗は完全に無音だった。
    # crontab に MAILTO= が無く出力は全てログファイルへリダイレクトされているため
    # cron のメール通知も機能しない。logger.error を出して終了する Python スクリプトは
    # core.logger.DiscordErrorHandler 経由で救われているが、**Python が起動する前に
    # 失敗する場合**(ImportError・.venv の破損・ファイル不在)はそれにも乗らない
    # (Issue #736 の依存欠落はまさにこれ)。ここを最後の砦にする。
    #
    # 通知自体が VENV_PYTHON に依存するため、venv が完全に壊れていれば通知も失敗する。
    # その場合でも本体の EXIT_CODE を保つよう `|| true` で握る(通知は本処理ではない)。
    # 連発するタスク(keep_alive_anker.sh は5分毎)で洪水にならないよう、
    # notify_task_failure.py 側がタスクごとのクールダウンを持つ。
    # 通知側の出力は専用ログへ出す(タスク自身の LOG_FILE は引数として「読む」ため、
    # 同じファイルへ書くと読み書きが交錯する。shellcheck SC2094 もこれを指摘する)。
    # notify_task_failure.py 自身も core.logger 経由で同じ run_task.log に記録する。
    "${VENV_PYTHON}" "${PROJECT_ROOT}/tools/notify_task_failure.py" \
        "${SCRIPT_NAME}" "${EXIT_CODE}" "${LOG_FILE}" >> "${LOG_DIR}/run_task.log" 2>&1 || true
else
    echo "--- [$(date '+%Y-%m-%d %H:%M:%S')] Success ---" >> "${LOG_FILE}"
fi

exit ${EXIT_CODE}