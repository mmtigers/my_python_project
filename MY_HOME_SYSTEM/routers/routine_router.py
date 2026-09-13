# MY_HOME_SYSTEM/routers/routine_router.py
from typing import Any, Dict

from fastapi import APIRouter, Query

from models.routine import RoutineCompleteAction
from services.routine_service import routine_service

router = APIRouter()


@router.get("/today")
def get_today_state(user_id: str = Query(min_length=1, max_length=64)) -> Dict[str, Any]:
    return routine_service.get_today_state(user_id)


@router.post("/complete")
def complete_step(action: RoutineCompleteAction) -> Dict[str, Any]:
    return routine_service.complete_step(action.user_id, action.flow_key, action.step_key)
