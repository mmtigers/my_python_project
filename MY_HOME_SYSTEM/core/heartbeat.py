# MY_HOME_SYSTEM/core/heartbeat.py
"""無音で動く監視スクリプトの生存ログ(heartbeat)を、1日1回だけ INFO で出す共通ヘルパー。

Issue #1042: nas_monitor / server_watchdog / nature_remo_monitor は正常時に何もログを
出さず(nature_remo は DEBUG のみ)、ログ調査の時点で「動いていないのか、正常で無音なのか」
を判断できなかった。実行のたびに出すとログが埋まる(nature_remo は5分間隔)ため、
日付(JST)を状態ファイルに残し、同じ日の2回目以降は出さない。

状態は監視ごとに別ファイル(`data/heartbeat/<name>.txt`)へ置く。複数のプロセスが
同じファイルを読み書きすると、後勝ちで他の監視の記録を消してしまうため。
失敗(書き込み不可等)は呼び出し元の監視を落とさない。その場合は実行のたびに出てしまうが、
ログが増えるだけで害は無い。
"""
from __future__ import annotations

import logging
import os
import re
from typing import Optional

import config
from core import state_file
from core.utils import get_today_date_str

_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]")


def _state_path(name: str) -> str:
    safe = _NAME_RE.sub("_", name) or "unnamed"
    return os.path.join(config.BASE_DIR, "data", "heartbeat", f"{safe}.txt")


def log_daily_heartbeat(name: str, logger: logging.Logger, detail: Optional[str] = None) -> bool:
    """今日まだ出していなければ生存ログを INFO で1行出して True を返す。出済みなら False。"""
    path = _state_path(name)
    today = get_today_date_str()
    if state_file.read_text(path) == today:
        return False
    suffix = f" ({detail})" if detail else ""
    logger.info("💓 [heartbeat] %s は稼働中です%s", name, suffix)
    state_file.write_text_atomic(path, today)
    return True
