# MY_HOME_SYSTEM/models/routine.py
from pydantic import BaseModel, Field
from typing import Literal

# ==========================================
# Request Models
# ==========================================


class RoutineCompleteAction(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    flow_key: Literal['am', 'pm']
    step_key: str = Field(min_length=1, max_length=64)
