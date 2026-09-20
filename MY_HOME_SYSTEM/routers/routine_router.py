# MY_HOME_SYSTEM/routers/routine_router.py
from typing import Any, Dict

from fastapi import APIRouter, Query

from models.routine import RoutineCompleteAction
from services.routine_service import routine_service

router = APIRouter()


@router.get("/today")
def get_today_state(user_id: str = Query(min_length=1, max_length=64)) -> Dict[str, Any]:
    """当日の状態を返す(読み取り専用)。

    Issue #738 (AUDIT-008): このエンドポイントは全端末が15秒間隔でポーリングする。
    以前はサービス層が書き込みトランザクションを開き、締切超過の強制遷移と
    ボーナス付与まで行っていたが、その処理は下の
    `POST /deadlines/process`(スケジューラが60秒ごとに叩く)へ移した。
    """
    return routine_service.get_today_state(user_id)


@router.post("/deadlines/process")
def process_deadlines() -> dict[str, Any]:
    """締切(チェックポイント時刻)超過による強制遷移を全ユーザー分適用する。

    Issue #738 (AUDIT-008): 呼び出し元は `monitors/routine_deadline_job.py`
    (スケジューラの定期タスク、60秒間隔)。スケジューラは unified_server とは
    別プロセスのため、`quest_users` を直接書かずこのAPI経由で
    サーバープロセス内のユーザー残高ロックに参加する(`reset_game.py` と同じ方針)。

    判定の基準時刻はサーバー側の現在時刻(JST)で、リクエストからは指定できない。
    締切前に叩いても何も起こらない(冪等)。
    """
    return routine_service.process_deadlines()


@router.post("/complete")
def complete_step(action: RoutineCompleteAction) -> Dict[str, Any]:
    return routine_service.complete_step(action.user_id, action.flow_key, action.step_key)
