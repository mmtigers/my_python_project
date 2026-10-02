# MY_HOME_SYSTEM/services/system_maintenance_service.py
"""システムページ(かんたん表示)の「サービス再起動」「最新に更新して再起動」操作。

#829: 以前はStreamlit版ダッシュボード(`views/dashboard/log_tab.py`)の中に直接
書かれていたが、Streamlit版廃止に伴いシステムページ(`routers/dashboard_router.py`)の
POSTエンドポイントから呼べるサービス関数として独立させた。ロジック自体
(sudo systemctl restart, timeout付き)は変更していない。

「最新に更新して再起動」(`trigger_deploy_async`): スマホのダッシュボードから実機の
`git pull --ff-only` を実行し、新しいコミットが入ったときだけ再起動する。
- 取り込みに失敗した場合(手元の変更・fast-forwardできない履歴・ネットワーク断・
  認証失敗等)は**再起動せず**、失敗理由を結果として返す。
- 新しいコミットが無ければ再起動しない(再起動だけしたいときは既存の再起動ボタン)。
- `git pull` は post-merge フック(family-quest の再ビルドとクエストマスタ同期)を
  含むため数分かかりうる。バックアップと同じく、起動したことを即座に返して
  バックグラウンドスレッドで実行し、画面は `get_deploy_state` をポーリングする。
- 再起動でこのプロセス自身が入れ替わるため、結果は状態ファイルへ保存する。
  再起動後の新プロセスが「再起動を要求した時刻より後に起動した」ことを根拠に
  成功へ確定させる(`get_deploy_state`)。
- 実行できるのは origin の最新を fast-forward で取り込む操作だけで、ブランチ・
  コマンド・引数を呼び出し側から受け取らない。認可チェックは無い(`/api/system/*`
  共通の既知の妥協点。CLAUDE.md「アーキテクチャ」参照)ため、実行の都度
  Discordへ記録を残す。
"""
import os
import subprocess
import threading
import time
from typing import Any

import config
from core import state_file
from core.logger import setup_logging
from core.utils import get_now_jst

from services.notification_service import send_push

logger = setup_logging("system_maintenance")

# #651: systemctl 等の外部コマンドが応答しない場合にサーバーを固めないための上限(秒)。
RESTART_TIMEOUT_SEC: int = 30

# リポジトリルート(MY_HOME_SYSTEM の1つ上。health_watch.REPO_ROOT と同じ求め方)。
REPO_ROOT: str = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
# `git pull` は post-merge フックの family-quest 再ビルド(npm ci + vite build)を
# 含むためラズパイでは数分かかる。home_system.service の TimeoutStartSec と同じ桁に揃える。
GIT_PULL_TIMEOUT_SEC: int = 900
GIT_QUICK_TIMEOUT_SEC: int = 30
# 失敗時に画面へ載せる git の出力(末尾)の最大文字数
GIT_OUTPUT_TAIL_CHARS: int = 300
# 実行状態の保存先(再起動をまたいで結果を引き継ぐため)
DEPLOY_STATE_FILE: str = os.path.join(config.BASE_DIR, "dashboard_deploy_state.json")

# このプロセスの起動時刻。再起動を要求した時刻より後なら「再起動後の新プロセス」と判定する。
_PROCESS_STARTED_AT: float = time.time()
_deploy_lock = threading.Lock()
_deploy_running: bool = False

PHASE_RUNNING = "running"        # git pull 実行中
PHASE_RESTARTING = "restarting"  # 再起動を要求済み(このプロセスは間もなく入れ替わる)
PHASE_DONE = "done"              # 終了(success で成否)


def restart_home_system() -> tuple[bool, str]:
    """`home_system` サービスを再起動する。戻り値は (成功したか, メッセージ)。"""
    try:
        subprocess.run(
            ["sudo", "systemctl", "restart", "home_system"],
            check=True,
            timeout=RESTART_TIMEOUT_SEC,
        )
        return True, "再起動コマンドを送信しました"
    except subprocess.TimeoutExpired:
        logger.error(f"サービス再起動が {RESTART_TIMEOUT_SEC} 秒以内に完了しませんでした")
        return False, (
            f"再起動コマンドが {RESTART_TIMEOUT_SEC} 秒以内に完了しませんでした。"
            "systemctl 側の状態を確認してください。"
        )
    except (subprocess.CalledProcessError, OSError) as e:
        logger.error(f"サービス再起動に失敗しました: {e}")
        return False, f"エラー: {e}"



def _read_deploy_state() -> dict[str, Any]:
    saved = state_file.read_json(DEPLOY_STATE_FILE, default=None)
    state: dict[str, Any] = {
        "run_id": 0, "phase": PHASE_DONE, "success": None, "message": "",
        "from": "", "to": "", "restart_requested_at": 0.0, "finished_at": None,
    }
    if isinstance(saved, dict):
        state.update({k: saved[k] for k in state if k in saved})
    return state


def _write_deploy_state(state: dict[str, Any]) -> None:
    state_file.write_json_atomic(DEPLOY_STATE_FILE, state)


def _git(*args: str, timeout: int) -> subprocess.CompletedProcess:
    """リポジトリルートに対して git を実行する。認証の入力待ちでハングさせない。"""
    return subprocess.run(
        ["git", "-C", REPO_ROOT, *args],
        capture_output=True, text=True, check=False, timeout=timeout,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )


def _tail(text: str) -> str:
    text = (text or "").strip()
    return text[-GIT_OUTPUT_TAIL_CHARS:]


def _finish(run_id: int, success: bool, message: str, before: str = "", after: str = "") -> None:
    with _deploy_lock:
        _write_deploy_state({
            "run_id": run_id, "phase": PHASE_DONE, "success": success, "message": message,
            "from": before, "to": after, "restart_requested_at": 0.0,
            "finished_at": get_now_jst().isoformat(timespec="seconds"),
        })


def _run_deploy(run_id: int) -> None:
    """`git pull --ff-only` → (新しいコミットがあれば)再起動。失敗時は再起動しない。"""
    global _deploy_running
    before = after = ""
    try:
        head = _git("rev-parse", "--short", "HEAD", timeout=GIT_QUICK_TIMEOUT_SEC)
        if head.returncode != 0:
            _finish(run_id, False, f"現在のバージョンを取得できませんでした: {_tail(head.stderr)}")
            return
        before = head.stdout.strip()

        try:
            pull = _git("pull", "--ff-only", timeout=GIT_PULL_TIMEOUT_SEC)
        except subprocess.TimeoutExpired:
            logger.error(f"git pull が {GIT_PULL_TIMEOUT_SEC} 秒以内に完了しませんでした(再起動はしません)")
            _finish(run_id, False, f"取り込みが {GIT_PULL_TIMEOUT_SEC} 秒以内に終わりませんでした。再起動はしていません。", before)
            return
        if pull.returncode != 0:
            detail = _tail(pull.stderr) or _tail(pull.stdout)
            logger.error(f"git pull --ff-only に失敗しました(再起動はしません): {detail}")
            _finish(run_id, False, f"取り込みに失敗しました。再起動はしていません。\n{detail}", before)
            return

        head = _git("rev-parse", "--short", "HEAD", timeout=GIT_QUICK_TIMEOUT_SEC)
        after = head.stdout.strip() if head.returncode == 0 else ""
        if not after or after == before:
            _finish(run_id, True, f"すでに最新です({before})。再起動はしていません。", before, before)
            return

        count = _git("rev-list", "--count", f"{before}..{after}", timeout=GIT_QUICK_TIMEOUT_SEC)
        n = count.stdout.strip() if count.returncode == 0 else "?"
        summary = f"{n}件のコミットを取り込みました({before} → {after})"
        logger.info(f"ダッシュボードから更新: {summary}。再起動します")
        try:
            send_push(
                [{"type": "text", "text": f"🔄 ダッシュボードから更新して再起動します\n{summary}"}],
                target="discord", channel="report",
            )
        except Exception as e:  # noqa: BLE001 (記録の送信失敗で更新自体を止めない)
            logger.warning(f"更新の記録通知に失敗しました: {e}")

        with _deploy_lock:
            _write_deploy_state({
                "run_id": run_id, "phase": PHASE_RESTARTING, "success": None, "message": summary,
                "from": before, "to": after, "restart_requested_at": time.time(), "finished_at": None,
            })
        # `systemctl restart` はこのプロセス自身を停止させてから戻るため、正常なら
        # この呼び出しは戻らない(プロセスごと入れ替わり、結果は get_deploy_state が
        # 状態ファイルから確定させる)。戻ってきた場合は異常なので、実行中のまま
        # 画面が待ち続けないよう、失敗として確定させる。
        ok, msg = restart_home_system()
        if ok:
            _finish(run_id, False, f"{summary}。再起動を要求しましたが、サービスが入れ替わりませんでした。実機の状態を確認してください。", before, after)
        else:
            _finish(run_id, False, f"{summary}が、再起動に失敗しました。{msg}", before, after)
    except Exception as e:  # noqa: BLE001 (スレッド内の例外を握りつぶさず、画面に失敗として返す)
        logger.error(f"ダッシュボードからの更新で予期しない例外: {e}")
        _finish(run_id, False, f"予期しないエラー: {e}", before, after)
    finally:
        with _deploy_lock:
            _deploy_running = False


def trigger_deploy_async() -> bool:
    """「最新に更新して再起動」をバックグラウンドスレッドで起動する。

    実行中にもう一度呼ばれた場合は二重に起動せず `False` を返す(起動したら `True`)。
    """
    global _deploy_running
    with _deploy_lock:
        if _deploy_running:
            return False
        _deploy_running = True
        run_id = _read_deploy_state()["run_id"] + 1
        _write_deploy_state({
            "run_id": run_id, "phase": PHASE_RUNNING, "success": None, "message": "",
            "from": "", "to": "", "restart_requested_at": 0.0, "finished_at": None,
        })
    threading.Thread(target=_run_deploy, args=(run_id,), daemon=True).start()
    return True


def get_deploy_state() -> dict[str, Any]:
    """実行中か(`running`)・最後に起動した実行のID(`run_id`)・直近の結果(`last_result`)。

    再起動後の新プロセスでは、保存された状態が「再起動を要求済み」で、かつこのプロセスが
    その要求より後に起動していれば成功へ確定させる。プロセスが入れ替わったのに
    「実行中」のまま残っている状態(取り込み中にサービスが再起動された等)は中断扱いにする。
    """
    with _deploy_lock:
        state = _read_deploy_state()
        running_here = _deploy_running
        phase = state["phase"]
        if phase == PHASE_RESTARTING and _PROCESS_STARTED_AT > float(state["restart_requested_at"]):
            state.update(
                phase=PHASE_DONE, success=True,
                message=f"更新して再起動しました({state['from']} → {state['to']})",
                finished_at=get_now_jst().isoformat(timespec="seconds"),
            )
            _write_deploy_state(state)
        elif phase in (PHASE_RUNNING, PHASE_RESTARTING) and not running_here:
            state.update(
                phase=PHASE_DONE, success=False,
                message="更新の途中でサービスが再起動されたため、結果を確認できませんでした。",
                finished_at=get_now_jst().isoformat(timespec="seconds"),
            )
            _write_deploy_state(state)

    done = state["phase"] == PHASE_DONE
    return {
        "running": not done,
        "run_id": state["run_id"],
        "last_result": {
            "run_id": state["run_id"], "success": bool(state["success"]),
            "message": state["message"], "finished_at": state["finished_at"],
        } if done and state["run_id"] else None,
    }
