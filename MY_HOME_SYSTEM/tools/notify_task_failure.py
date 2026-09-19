#!/usr/bin/env python3
"""cron タスク(run_task.sh 経由)の失敗を Discord へ通知する小さなCLI。

Issue #751 (AUDIT-022): `run_task.sh` は失敗を exit code とログファイルに書くだけで
通知していなかった。`deploy/cron/crontab` には `MAILTO=` も無く、全エントリの出力が
ログファイルへリダイレクトされているため cron のメール通知も機能しない。つまり
**自前で通知しないタスクの失敗は完全に無音**だった(自前で通知しているのは
backup_service / health_watch / memory_monitor / server_watchdog の4つだけ)。

`logger.error` を出して終了する Python スクリプトは `core.logger.DiscordErrorHandler`
経由で救われている。救われていないのは **Python が起動する前に失敗する場合**
(ImportError・`.venv` の破損・ファイル不在)で、Issue #736 の依存欠落はまさにこれに
該当する。したがって「数か月気づかない」が現実的なシナリオだった。

## 通知量について

`tools/keep_alive_anker.sh` は5分毎に動くため、失敗し続けると1日288通になる。
`DiscordErrorHandler` 側の重複排除(Issue #759)は**プロセスローカル**で、cron は毎回
新しいプロセスを起こすため効かない。そこでここではタスクごとのクールダウンを
状態ファイルで持つ(`memory_monitor.check_cooldown` / `server_watchdog` のロック
ファイル方式と同じ発想を、`core/state_file.py` の原子的な読み書きで実装する)。

使い方(`run_task.sh` から呼ばれる想定):

    python3 tools/notify_task_failure.py <script_name> <exit_code> [log_file]
"""
import os
import sys
import time

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config
from core import state_file
from core.logger import get_logger

logger = get_logger("run_task")

# 同一タスクの失敗を再通知するまでの最短間隔(秒)。keep_alive_anker.sh(5分毎)が
# 失敗し続けても1時間に1通に収まる。cron は毎回別プロセスなので DiscordErrorHandler の
# 重複排除(Issue #759)は効かず、ここで状態ファイルを使って抑制する必要がある。
COOLDOWN_SEC: int = 3600
# 通知に載せるログ末尾の最大文字数(Discord の content 上限に対する余裕を見た値)。
LOG_TAIL_LIMIT: int = 1200
# 通知に載せるログ末尾の行数。
LOG_TAIL_LINES: int = 20


def _state_path(script_name: str) -> str:
    """タスクごとのクールダウン状態ファイルのパス。

    スクリプト名にはディレクトリ区切りが含まれる(`monitors/log_analyzer.py` 等)ため、
    ファイル名として安全な形へ潰す。
    """
    safe = script_name.replace(os.sep, "_").replace("/", "_").replace(".", "_")
    return os.path.join(config.LOG_DIR, f".task_failure_{safe}")


def should_notify(script_name: str, now: float) -> bool:
    """このタスクの失敗を今回通知してよいか(クールダウン判定)。

    状態ファイルが読めない・壊れている場合は**通知する側に倒す**
    (memory_monitor.check_cooldown と同じ方針。抑制の誤りで無音になるより、
    多めに通知するほうが安全)。
    """
    raw = state_file.read_text(_state_path(script_name))
    if not raw:
        return True
    try:
        last = float(raw)
    except ValueError:
        return True
    return now - last > COOLDOWN_SEC


def record_notification(script_name: str, now: float) -> None:
    if not state_file.write_text_atomic(_state_path(script_name), str(now)):
        logger.warning(f"失敗通知の抑制状態を記録できませんでした: {script_name}")


def read_log_tail(log_file: str) -> str:
    """ログファイルの末尾を読む。読めなければ空文字(通知自体は続行する)。"""
    if not log_file or not os.path.isfile(log_file):
        return ""
    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError as e:
        return f"(ログを読めませんでした: {e})"
    return "".join(lines[-LOG_TAIL_LINES:])[-LOG_TAIL_LIMIT:]


def notify(script_name: str, exit_code: str, log_file: str, now: float) -> bool:
    """失敗を通知する。抑制された場合は False を返す。

    通知の実体は `logger.error` で、`core.logger.DiscordErrorHandler` が
    error チャンネルへ送る。**変動するログ末尾を `%s` の引数側に置く**のは、
    `DiscordErrorHandler` の重複排除キーがフォーマット**前**の `record.msg` で
    決まるため(Issue #759)。こうするとキーが「スクリプト名 + 終了コード」で安定し、
    同一プロセス内で複数回呼ばれた場合も正しく束ねられる。
    """
    if not should_notify(script_name, now):
        logger.info(f"同一タスクの失敗通知をクールダウン中のため抑制しました: {script_name}")
        return False

    logger.error(
        f"🚨 cron タスクが失敗しました: {script_name} (exit={exit_code})\nログ末尾:\n%s",
        read_log_tail(log_file) or "(ログなし)",
    )
    record_notification(script_name, now)
    return True


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2:
        print("Usage: notify_task_failure.py <script_name> <exit_code> [log_file]", file=sys.stderr)
        return 2
    script_name, exit_code = args[0], args[1]
    log_file = args[2] if len(args) > 2 else ""
    notify(script_name, exit_code, log_file, time.time())
    # 通知の成否で cron タスク自体の終了コードを変えない(呼び出し元が本来の
    # EXIT_CODE を返す。ここは「最後の砦」であって本処理ではない)。
    return 0


if __name__ == "__main__":
    sys.exit(main())
