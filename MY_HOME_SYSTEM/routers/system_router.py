# MY_HOME_SYSTEM/routers/system_router.py
from fastapi import APIRouter, HTTPException
from typing import Dict, Any

from services import backup_service, system_maintenance_service
from core.logger import setup_logging

logger = setup_logging("system_router")

router = APIRouter()

@router.post("/backup")
def manual_backup() -> Dict[str, Any]:
    """手動バックアップトリガー。

    #408: perform_backup() は sqlite の backup API と NAS への shutil.copy2 を同期実行する
    ブロッキング処理のため、`async def` の中で直接呼ぶとイベントループ全体が止まり、
    /webhook/switchbot や /callback/line を含む全リクエストが数秒〜数十秒停止していた。
    通常の `def` にすることで FastAPI がスレッドプール上で実行する。
    """
    success, msg, size = backup_service.perform_backup()
    if not success:
        # 失敗理由(NASパス等の内部情報を含みうる生の例外文字列)はログ側にあるため、
        # クライアントには要約のみ返す。
        logger.error(f"手動バックアップ失敗: {msg}")
        raise HTTPException(status_code=500, detail="バックアップに失敗しました。サーバーログを確認してください。")
    return {"status": "success", "message": msg, "size_mb": size}


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