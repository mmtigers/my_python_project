#!/bin/bash
# docs/ を Google Drive へ同期する(毎日 03:00。crontab から run_task.sh 経由で起動)。
#
# Issue #947: 以前は crontab に `rclone sync ... > /dev/null 2>&1` と直書きされており、
# 出力を全て捨てるため認証切れ・ネットワーク断・クォータ超過などの失敗が常に無言だった。
# run_task.sh は終了コードが非0のときだけ通知する(tools/notify_task_failure.py)ので、
# ここでは rclone の終了コードをそのまま返す。出力は run_task.sh がログへ追記する。
set -u

SRC="/home/masahiro/develop/docs"
DEST="gdrive:お家開発/docs"

/usr/bin/rclone sync "${SRC}" "${DEST}" -v
