# MY_HOME_SYSTEM/models/quest.py
from pydantic import BaseModel
from typing import Optional

# ==========================================
# Domain Models (Pydantic)
# ==========================================

class MasterUser(BaseModel):
    user_id: str
    name: str
    job_class: str
    level: int = 1
    exp: int = 0
    gold: int = 50
    avatar: str = '🙂'

class MasterQuest(BaseModel):
    id: int
    title: str
    desc: Optional[str] = None
    type: str
    target: str = 'all'
    exp: int
    gold: int
    icon: str
    days: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    chance: Optional[float] = 1.0
    start_time: Optional[str] = None
    end_time: Optional[str] = None

class MasterReward(BaseModel):
    id: int
    title: str
    category: str
    cost_gold: int
    icon_key: str
    desc: Optional[str] = None

class MasterEquipment(BaseModel):
    id: int
    name: str
    type: str
    power: int
    cost: int
    icon: str

# Request Models
class UserAction(BaseModel):
    user_id: str

class QuestAction(BaseModel):
    user_id: str
    quest_id: int

class RewardAction(BaseModel):
    user_id: str
    reward_id: int

class EquipAction(BaseModel):
    user_id: str
    equipment_id: int

class HistoryAction(BaseModel):
    user_id: str
    history_id: int

class ApproveAction(BaseModel):
    approver_id: str
    history_id: int

class UpdateUserAction(BaseModel):
    user_id: str
    avatar_url: str

class SoundTestRequest(BaseModel):
    sound_key: str

# Response Models
class SyncResponse(BaseModel):
    status: str
    message: str

class CompleteResponse(BaseModel):
    status: str
    leveledUp: bool
    newLevel: int
    earnedGold: int
    earnedExp: int
    earnedMedals: int = 0
    message: Optional[str] = None
    bossEffect: Optional[dict] = None 

class CancelResponse(BaseModel):
    status: str

class PurchaseResponse(BaseModel):
    status: str
    newGold: int

class AdminBossUpdate(BaseModel):
    max_hp: Optional[int] = None
    current_hp: Optional[int] = None
    is_defeated: Optional[bool] = None

# Inventory Models
class InventoryItem(BaseModel):
    id: int             # inventory ID
    reward_id: int      # master ID
    title: str
    desc: Optional[str] = None
    icon: str
    status: str         # owned, pending, consumed
    purchased_at: str
    used_at: Optional[str] = None

class UseItemResponse(BaseModel):
    status: str
    message: str

class UseItemAction(BaseModel):
    user_id: str
    inventory_id: int

class ConsumeItemAction(BaseModel):
    approver_id: str    # 親のID
    inventory_id: int