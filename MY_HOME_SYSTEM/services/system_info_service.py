# MY_HOME_SYSTEM/services/system_info_service.py
"""システムページの「起動履歴」と「Gitの状態」。

- 起動履歴: `unified_server` の起動時に `record_boot` が `server_boot_events` へ1行追記する。
  読み出しは `get_boot_history`。再起動のきっかけ(`reason`)は、起動の直前にダッシュボードが
  残した記録から推定する(`_determine_boot_reason`)。記録が無ければ `unknown`。
- Gitの状態: 「起動中のコード」「手元(実機の作業ツリー)の最新」「GitHubの最新」の3段を比べる
  (`get_git_status`)。GitHubの最新を知るには `git fetch` が要り、外部通信で時間がかかるため、
  ページ表示では呼ばず、ページ表示後にJSが専用API(`GET /api/system/git/status`)から取る。
  結果は `GIT_STATUS_CACHE_TTL_SEC` だけキャッシュし、`git fetch` にはタイムアウトを付ける。

どの関数も例外を送出しない(起動処理・ページ表示を落とさない)。失敗は warning ログにして
空/エラー入りの結果を返す。
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any

from core import state_file
from core.database import get_db_cursor
from core.logger import setup_logging
from core.utils import get_now_iso

logger = setup_logging("system_info")

REASON_UPDATE = "update"    # ダッシュボードの「最新に更新して再起動」
REASON_MANUAL = "manual"    # ダッシュボードの「システム再起動」
REASON_UNKNOWN = "unknown"  # 上記以外(自動復旧・電源投入等)
REASON_LABELS: dict[str, str] = {
    REASON_UPDATE: "ダッシュボードから更新",
    REASON_MANUAL: "ダッシュボードから再起動",
    REASON_UNKNOWN: "不明(自動復旧・電源など)",
}

# 再起動の要求から、この秒数以内に起動したプロセスを「その要求による再起動」とみなす。
# `systemctl restart` は停止+起動で実測40秒超かかる(#886)ため、余裕を持たせる。
BOOT_REASON_WINDOW_SEC: float = 900.0
# 起動履歴の画面に出す件数
BOOT_HISTORY_DISPLAY_LIMIT: int = 10

GIT_STATUS_CACHE_TTL_SEC: float = 300.0
GIT_FETCH_TIMEOUT_SEC: int = 20
GIT_QUICK_TIMEOUT_SEC: int = 10
# 「最近のコミット」に出す件数
RECENT_COMMITS_LIMIT: int = 10
# `git log` の区切り(コミット件名に現れない制御文字)
_FIELD_SEP = "\x1f"

_git_cache_lock = threading.Lock()
_git_cache: tuple[float, dict[str, Any]] | None = None


def _git(*args: str, timeout: int = GIT_QUICK_TIMEOUT_SEC) -> str | None:
    """リポジトリに対して git を実行し、標準出力(前後の空白除去)を返す。失敗は None。"""
    from services import (
        system_maintenance_service as maintenance,  # 遅延import(循環・起動時負荷の回避)
    )

    try:
        res = maintenance._git(*args, timeout=timeout)
    except Exception as e:  # noqa: BLE001 (git無し・タイムアウト等でページ/起動を落とさない)
        logger.warning(f"git {' '.join(args)} に失敗しました: {type(e).__name__}: {e}")
        return None
    if res.returncode != 0:
        logger.warning(f"git {' '.join(args)} が失敗しました: {(res.stderr or '').strip()[-200:]}")
        return None
    return (res.stdout or "").strip()


# --------------------------------------------------------------------------
# 起動履歴
# --------------------------------------------------------------------------

def _determine_boot_reason(now_ts: float) -> str:
    """直前にダッシュボードが残した記録から、この起動のきっかけを推定する。

    「最新に更新して再起動」の状態ファイル(再起動を要求した時刻つき)→「システム再起動」の
    記録の順に調べ、`BOOT_REASON_WINDOW_SEC` 以内のものがあれば対応するきっかけにする。
    """
    from services import system_maintenance_service as maintenance

    try:
        deploy = maintenance._read_deploy_state()
        requested = float(deploy.get("restart_requested_at") or 0.0)
        if deploy.get("phase") == maintenance.PHASE_RESTARTING and 0 < now_ts - requested < BOOT_REASON_WINDOW_SEC:
            return REASON_UPDATE
        marker = state_file.read_json(maintenance.RESTART_MARKER_FILE, default=None)
        if isinstance(marker, dict):
            requested = float(marker.get("requested_at") or 0.0)
            if 0 < now_ts - requested < BOOT_REASON_WINDOW_SEC:
                return REASON_MANUAL
    except Exception as e:  # noqa: BLE001 (きっかけが分からなくても起動は記録する)
        logger.warning(f"起動のきっかけの判定に失敗しました: {e}")
    return REASON_UNKNOWN


def record_boot() -> bool:
    """起動した事実(時刻・コミット・きっかけ・PID)を `server_boot_events` へ追記する。

    `unified_server` の `lifespan`(マイグレーション成功後)から1回だけ呼ぶ。
    失敗してもサーバーの起動は止めない(警告ログのみで False を返す)。
    """
    try:
        sha = _git("rev-parse", "--short", "HEAD")
        subject = _git("log", "-1", "--format=%s") if sha else None
        branch = _git("rev-parse", "--abbrev-ref", "HEAD") if sha else None
        reason = _determine_boot_reason(time.time())
        with get_db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO server_boot_events (booted_at, commit_sha, commit_subject, branch, pid, reason) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (get_now_iso(), sha, subject, branch, os.getpid(), reason),
            )
        logger.info(f"起動を記録しました (commit={sha}, reason={reason})")
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"起動履歴の記録に失敗しました(起動は続行します): {e}")
        return False


def get_boot_history(limit: int = BOOT_HISTORY_DISPLAY_LIMIT) -> list[dict[str, Any]]:
    """起動履歴を新しい順で返す。テーブルが無い・読めないときは空リスト。"""
    try:
        with get_db_cursor() as cur:
            cur.execute(
                "SELECT booted_at, commit_sha, commit_subject, branch, pid, reason "
                "FROM server_boot_events ORDER BY booted_at DESC, id DESC LIMIT ?",
                (int(limit),),
            )
            return [dict(row) for row in cur.fetchall()]
    except Exception as e:  # noqa: BLE001
        logger.warning(f"起動履歴の取得に失敗しました: {e}")
        return []


# --------------------------------------------------------------------------
# Gitの状態
# --------------------------------------------------------------------------

def _parse_log_lines(output: str | None) -> list[dict[str, str]]:
    """`git log --format=%h<SEP>%cd<SEP>%s` の出力を辞書のリストにする。"""
    commits = []
    for line in (output or "").splitlines():
        parts = line.split(_FIELD_SEP, 2)
        if len(parts) == 3:
            commits.append({"sha": parts[0], "date": parts[1], "subject": parts[2]})
    return commits


def _compute_git_status() -> dict[str, Any]:
    """git を実際に実行して状態を組み立てる(`git fetch` を含む。キャッシュなし)。"""
    status: dict[str, Any] = {
        "ok": False, "error": "", "fetch_ok": None, "branch": None, "upstream": None,
        "head": None, "remote": None, "running": None,
        "behind": None, "ahead": None, "pending_restart": None, "recent": [],
    }
    head_sha = _git("rev-parse", "--short", "HEAD")
    if head_sha is None:
        status["error"] = "Gitの情報を取得できませんでした(リポジトリが見つかりません)"
        return status

    fmt = f"--format=%h{_FIELD_SEP}%cd{_FIELD_SEP}%s"
    date_fmt = "--date=format:%m/%d %H:%M"
    head_log = _parse_log_lines(_git("log", "-1", fmt, date_fmt, "HEAD"))
    status["head"] = head_log[0] if head_log else {"sha": head_sha, "date": "", "subject": ""}
    status["branch"] = _git("rev-parse", "--abbrev-ref", "HEAD")
    status["ok"] = True

    # 起動中のコード(最後に記録した起動時点のコミット)
    boots = get_boot_history(1)
    if boots and boots[0].get("commit_sha"):
        status["running"] = {
            "sha": boots[0]["commit_sha"], "subject": boots[0].get("commit_subject") or "",
            "booted_at": boots[0]["booted_at"],
        }
        status["pending_restart"] = boots[0]["commit_sha"] != head_sha

    # GitHubの最新(追跡先ブランチがあるときだけ)
    upstream = _git("rev-parse", "--abbrev-ref", "@{u}")
    status["upstream"] = upstream
    if upstream is None:
        status["error"] = "追跡先のブランチが設定されていないため、GitHubの最新とは比べられません"
        return status

    fetched = _git("fetch", "--quiet", timeout=GIT_FETCH_TIMEOUT_SEC)
    status["fetch_ok"] = fetched is not None
    if fetched is None:
        status["error"] = "GitHubへ問い合わせできませんでした(最後に取得できた状態と比べています)"

    remote_log = _parse_log_lines(_git("log", "-1", fmt, date_fmt, "@{u}"))
    status["remote"] = remote_log[0] if remote_log else None
    behind = _git("rev-list", "--count", "HEAD..@{u}")
    ahead = _git("rev-list", "--count", "@{u}..HEAD")
    status["behind"] = int(behind) if behind and behind.isdigit() else None
    status["ahead"] = int(ahead) if ahead and ahead.isdigit() else None

    # 最近のコミット: 追跡先の先頭から並べ、手元に取り込み済みかを印付けする
    unpulled = set((_git("rev-list", "--abbrev-commit", "HEAD..@{u}") or "").split())
    running_sha = (status["running"] or {}).get("sha")
    for c in _parse_log_lines(_git("log", f"-n{RECENT_COMMITS_LIMIT}", fmt, date_fmt, "@{u}")):
        c["pulled"] = c["sha"] not in unpulled
        c["running"] = c["sha"] == running_sha
        status["recent"].append(c)
    return status


def get_git_status(force: bool = False) -> dict[str, Any]:
    """Gitの状態を返す(`GIT_STATUS_CACHE_TTL_SEC` のキャッシュ付き。失敗はキャッシュしない)。

    `ok`=False のときは `error` に理由が入る。例外は送出しない。
    """
    global _git_cache
    now = time.monotonic()
    with _git_cache_lock:
        if not force and _git_cache is not None and now - _git_cache[0] < GIT_STATUS_CACHE_TTL_SEC:
            return _git_cache[1]
    try:
        status = _compute_git_status()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Gitの状態の取得に失敗しました: {e}")
        return {"ok": False, "error": "Gitの状態を取得できませんでした", "recent": []}
    if status["ok"]:
        with _git_cache_lock:
            _git_cache = (time.monotonic(), status)
    return status
