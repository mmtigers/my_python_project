# MY_HOME_SYSTEM/routers/system_router.py
from fastapi import APIRouter, HTTPException
from typing import Dict, Any

from services import backup_service
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