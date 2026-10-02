# MY_HOME_SYSTEM/routers/system_router.py
from fastapi import APIRouter, HTTPException
from typing import Dict, Any

from services import backup_service, system_maintenance_service

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
def backup_status() -> Dict[str, Any]:
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