# MY_HOME_SYSTEM/models/quest.py
from pydantic import BaseModel
from typing import Optional
from typing import Optional, List, Dict

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
    pre_requisite_quest_id: Optional[int] = None

class MasterReward(BaseModel):
    id: int
    title: str
    category: str
    cost_gold: int
    icon_key: str
    desc: Optional[str] = None
    target: str = "all"

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

# Analytics Models
class WeeklyDailyStat(BaseModel):
    date: str           # "YYYY-MM-DD"
    day_label: str      # "Mon", "Tue"...
    users: Dict[str, Dict[str, int]]  # { "user_id": { "exp": 100, "gold": 50 } }

class RankingItem(BaseModel):
    user_id: str
    user_name: str
    avatar: str
    value: int
    label: str          # 表示用単位など

class WeeklyReportResponse(BaseModel):
    startDate: str
    endDate: str
    dailyStats: List[WeeklyDailyStat]
    rankings: Dict[str, List[RankingItem]]
    mvp: Optional[RankingItem] = None
    mostPopularQuest: Optional[str] = None