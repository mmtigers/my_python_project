# MY_HOME_SYSTEM/routers/ui_log_router.py
from fastapi import APIRouter
from models.ui_log import UiTapBatch, UiTapBatchResponse
from services.ui_event_service import ui_event_service

router = APIRouter()


@router.post("/events", response_model=UiTapBatchResponse, status_code=202)
def post_tap_events(batch: UiTapBatch) -> dict[str, int]:
    """画面タップのバッチを追記する(UX改善用の操作ログ)。

    クライアント(family-quest の tapLogger)が20件または30秒ごと、および画面を
    閉じる/隠すときに送る。event_id で冪等なので、再送しても二重には記録されない。
    """
    return ui_event_service.record_batch(batch)
