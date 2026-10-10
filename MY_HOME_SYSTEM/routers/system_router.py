# MY_HOME_SYSTEM/routers/system_router.py
from fastapi import APIRouter, HTTPException, Request
from typing import Dict, Any

from core.logger import setup_logging
from services import backup_service, system_info_service, system_maintenance_service

logger = setup_logging("system_router")

router = APIRouter()

@router.post("/backup")
def manual_backup() -> Dict[str, Any]:
    """手動バックアップトリガー。

    #408: perform_backup() は sqlite の backup API と NAS への shutil.copy2 を同期実行する
    ブロッキング処理のため、`async def` の中で直接呼ぶとイベントループ全体が止まり、
    /webhook/switchbot や /callback/line を含む全リクエストが数秒〜数十秒停止していた。
    通常の `def` にすることで FastAPI がスレッドプール上で実行する。

    **(不具合修正)** 以前は`perform_backup()`の完了(NAS転送・場合によってはオフサイト
    複製で最大30分)をこのレスポンスとして待たせており、「タップしても完了したか
    分からない」原因になっていた。`backup_service.trigger_manual_backup_async`で
    バックグラウンド実行に切り替え、開始した旨を即座に返す。完了(成功/失敗)は
    Discordへの通知で確認できる。
    """
    started = backup_service.trigger_manual_backup_async()
    run_id = backup_service.get_manual_backup_state()["run_id"]
    if not started:
        return {"status": "running", "run_id": run_id, "message": "バックアップは既に実行中です。"}
    return {"status": "started", "run_id": run_id,
            "message": "バックアップを開始しました。完了するとDiscordに通知します。"}


@router.get("/backup/status")
def backup_status() -> dict[str, Any]:
    """システムページが「実行中か」「結果」「最新のバックアップ時刻」を表示するために参照する。

    `latest_backup`はNAS上のファイルを列挙するため、manual_backupと同じ理由で
    `async def`にしない(NASが遅いときにイベントループを止めない)。
    """
    state = backup_service.get_manual_backup_state()
    return {
        "running": state["running"],
        "run_id": state["run_id"],
        "last_result": state["last_result"],
        "latest_backup": backup_service.get_latest_backup_info(),
    }


@router.post("/restart")
def manual_restart() -> dict[str, Any]:
    """システムページ(かんたん表示)の「サービス再起動」ボタンから呼ばれる。

    #829: 以前はStreamlit版ダッシュボードのスクリプト実行スレッド内で直接
    `subprocess.run` していたが、システムページのHTMLはこのサーバー自身が返す
    ようになったため、通常のAPIエンドポイントとして切り出した。
    `restart_home_system` は同期のブロッキング呼び出し(timeout付き)のため、
    manual_backup と同じ理由で `async def` にしない。
    """
    success, msg = system_maintenance_service.restart_home_system()
    if not success:
        raise HTTPException(status_code=500, detail=msg)
    return {"status": "success", "message": msg}


@router.post("/deploy")
def manual_deploy(request: Request) -> dict[str, Any]:
    """システムページの「最新に更新して再起動」ボタンから呼ばれる。

    実機で `git pull --ff-only` を実行し、新しいコミットが入ったときだけ再起動する。
    取り込みに失敗したら再起動しない。post-merge フック(family-quest の再ビルド等)で
    数分かかりうるため、manual_backup と同じくバックグラウンド実行にして開始した旨を
    即座に返す。完了は `GET /api/system/deploy/status` をポーリングして確認する
    (再起動でサーバーが一時的に応答しなくなる)。

    リクエストは引数を一切受け取らない(ブランチ・コマンドを呼び出し側が指定できない)。
    認可チェックは `/api/system/*` 共通で無いため、実行元IPをログに残す。
    """
    client = request.client.host if request.client else "unknown"
    started = system_maintenance_service.trigger_deploy_async()
    run_id = system_maintenance_service.get_deploy_state()["run_id"]
    logger.info(f"POST /api/system/deploy from {client}: {'started' if started else 'already running'}")
    if not started:
        return {"status": "running", "run_id": run_id, "message": "更新は既に実行中です。"}
    return {"status": "started", "run_id": run_id,
            "message": "更新を開始しました。取り込みに失敗した場合は再起動しません。"}


@router.get("/git/status")
def git_status() -> dict[str, Any]:
    """システムページの「Gitの状態」が、ページ表示後にJSで取得する(`git fetch` を含み遅いため
    ページ本体には含めない)。起動中のコード・手元の最新・GitHubの最新の3段と最近のコミットを返す。
    結果は5分キャッシュされる。失敗しても200で `ok: false` と `error` を返す(画面に理由を出す)。
    `async def` にしない(git の外部コマンドでイベントループを止めないため)。
    """
    return system_info_service.get_git_status()


@router.get("/deploy/status")
def deploy_status() -> dict[str, Any]:
    """システムページが「実行中か」「結果」を表示するためにポーリングする。"""
    return system_maintenance_service.get_deploy_state()
