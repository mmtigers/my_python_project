# MY_HOME_SYSTEM/models/quest.py
import re

from pydantic import BaseModel, Field, field_validator
from typing import Literal, Optional

# Q-L4(#409): SQLite の INTEGER は 64bit 符号付き。上限の無い int フィールドに 2**63 以上を渡すと
# sqlite3 が OverflowError を送出して 500 になっていたため、ID 系は 1〜2**63-1 に制限する。
_SQLITE_INT_MAX = 2**63 - 1
_DAY_OF_WEEK_RE = re.compile(r"^[0-6](,[0-6])*$")

# ==========================================
# Domain Models (Pydantic)
# ==========================================

class MasterUser(BaseModel):
    user_id: str
    name: str
    job_class: str
    level: int = Field(default=1, ge=1)
    exp: int = Field(default=0, ge=0)
    # #454: MasterQuest/MasterRewardのgold系フィールドは既にge=0だが、
    # MasterUserのgoldには境界チェックが無かった。業務上あり得ない負値を防ぐ。
    gold: int = Field(default=50, ge=0)
    avatar: str = '🙂'
    role: Optional[str] = None

class MasterQuest(BaseModel):
    # #409: 以前は type/reset_period が自由文字列、exp/gold が負値可、days が未検証で、
    # タイポ('dayly' 等)は「ボーナス無し・周期チェック有り」の中途半端な挙動に、
    # 不正な days('0,,1')は int() の ValueError で GET /data 全体が 500 になっていた。
    id: int = Field(ge=1, le=_SQLITE_INT_MAX)
    title: str = Field(min_length=1, max_length=200)
    desc: Optional[str] = None
    type: Literal['daily', 'special', 'infinite']
    target: str = 'all'
    exp: int = Field(ge=0)
    gold: int = Field(ge=0)
    icon: str
    days: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    chance: Optional[float] = 1.0
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    pre_requisite_quest_id: Optional[int] = None
    reset_period: Optional[Literal['daily', 'weekly', 'monthly']] = 'daily'

    @field_validator("days")
    @classmethod
    def _validate_days(cls, value: Optional[str]) -> Optional[str]:
        if value is None or value == "":
            return None
        if not _DAY_OF_WEEK_RE.match(value):
            raise ValueError("days は '0,3' のようなカンマ区切りの曜日番号(0〜6)で指定してください")
        return value

class MasterReward(BaseModel):
    id: int = Field(ge=1, le=_SQLITE_INT_MAX)
    title: str = Field(min_length=1, max_length=200)
    category: str
    cost_gold: int = Field(ge=0)
    icon_key: str
    desc: Optional[str] = None
    target: Optional[str] = "all"

# Request Models
# (#409: 未使用だった UserAction / InventoryItem は削除)
class QuestAction(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    quest_id: int = Field(ge=1, le=_SQLITE_INT_MAX)

class RewardAction(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    reward_id: int = Field(ge=1, le=_SQLITE_INT_MAX)

class HistoryAction(BaseModel):
    user_id: str
    history_id: int = Field(ge=1, le=_SQLITE_INT_MAX)

class ApproveAction(BaseModel):
    approver_id: str = Field(min_length=1, max_length=64)
    history_id: int = Field(ge=1, le=_SQLITE_INT_MAX)
    # 却下理由(プリセット選択、フロントエンドのみで完結していたUXにログ用の裏付けを追加)。
    # 任意項目なので既存クライアント(未送信)との後方互換は崩さない。
    reason: Optional[str] = Field(default=None, max_length=500)

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


class UseItemResponse(BaseModel):
    status: str
    message: str

class UseItemAction(BaseModel):
    user_id: str
    inventory_id: int