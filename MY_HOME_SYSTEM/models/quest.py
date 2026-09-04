# MY_HOME_SYSTEM/models/quest.py
import re

from pydantic import BaseModel, field_validator
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
    role: Optional[str] = None

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
    reset_period: Optional[str] = 'daily'

class MasterReward(BaseModel):
    id: int
    title: str
    category: str
    cost_gold: int
    icon_key: str
    desc: Optional[str] = None
    target: str = "all"

# Request Models
class UserAction(BaseModel):
    user_id: str

class QuestAction(BaseModel):
    user_id: str
    quest_id: int

class RewardAction(BaseModel):
    user_id: str
    reward_id: int

class HistoryAction(BaseModel):
    user_id: str
    history_id: int

class ApproveAction(BaseModel):
    approver_id: str
    history_id: int
    # 却下理由(プリセット選択、フロントエンドのみで完結していたUXにログ用の裏付けを追加)。
    # 任意項目なので既存クライアント(未送信)との後方互換は崩さない。
    reason: Optional[str] = None

# #372: アップロード経由のアバターURLは routers/quest_router.py の upload_image が生成する
# 「/uploads/<uuid4>.<拡張子>」の形のみを受け付ける。任意の /uploads/ パスを許すと、
# 他ユーザーのアップロード画像を自分のアバターに指定 → 絵文字に戻す、という操作で
# そのファイルが孤立扱いになり削除されてしまう経路が残る。
_UPLOADED_AVATAR_RE = re.compile(
    r"^/uploads/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.(jpg|jpeg|png|gif|webp)$"
)
# 絵文字アバター(結合絵文字・肌色修飾子を含めても十数コードポイント)の上限。
_EMOJI_AVATAR_MAX_LEN = 16


class UpdateUserAction(BaseModel):
    user_id: str
    avatar_url: str

    @field_validator("avatar_url")
    @classmethod
    def _validate_avatar_url(cls, value: str) -> str:
        if _UPLOADED_AVATAR_RE.match(value):
            return value
        # 絵文字などの短い表示用文字列。パス区切り・HTML特殊文字を含むものは拒否する。
        if (
            0 < len(value) <= _EMOJI_AVATAR_MAX_LEN
            and not any(ch in value for ch in "/\\<>\"'")
            and not value.startswith(".")
        ):
            return value
        raise ValueError("avatar_url は /uploads/<uuid>.<ext> 形式か短い絵文字文字列のみ指定できます")

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
    # #238: 兄妹連携クエストのカスケード承認時、相方(自分でタップしなかった方の
    # 子ども)のレベルアップ/メダル獲得演出をフロント側が出せるようにするための
    # フィールド。連携クエストでない承認・完了報告時は常に既定値のまま。
    partnerUserId: Optional[str] = None
    partnerLeveledUp: bool = False
    partnerNewLevel: Optional[int] = None
    partnerEarnedMedals: int = 0

class CancelResponse(BaseModel):
    status: str

class PurchaseResponse(BaseModel):
    status: str
    newGold: int

# Inventory Models
class InventoryItem(BaseModel):
    id: int             # inventory ID
    reward_id: int      # master ID
    title: str
    desc: Optional[str] = None
    icon: str
    status: str         # owned, consumed
    purchased_at: str
    used_at: Optional[str] = None

class UseItemResponse(BaseModel):
    status: str
    message: str

class UseItemAction(BaseModel):
    user_id: str
    inventory_id: int