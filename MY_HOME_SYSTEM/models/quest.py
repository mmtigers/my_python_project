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
    # #529: 'limited'(start_date/end_date による期間限定)と 'random'(occurrence_chance による
    # 日替わり出現抽選)は services/quest_service.py の _is_quest_currently_active・フロントエンド
    # (useQuestStatus.ts/QuestList.tsx)・仕様書がいずれも対応済みなのに、本 Literal だけが
    # 許容しておらず、quest_data.py に追加した瞬間 sync_master_data が 500 で全体中断していた。
    type: Literal['daily', 'special', 'infinite', 'limited', 'random']
    target: str = 'all'
    exp: int = Field(ge=0)
    gold: int = Field(ge=0)
    icon: str
    days: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    chance: Optional[float] = Field(default=1.0, ge=0.0, le=1.0)
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
    user_id: str = Field(min_length=1, max_length=64)
    history_id: int = Field(ge=1, le=_SQLITE_INT_MAX)

class ApproveAction(BaseModel):
    approver_id: str = Field(min_length=1, max_length=64)
    history_id: int = Field(ge=1, le=_SQLITE_INT_MAX)
    # 却下理由(プリセット選択、フロントエンドのみで完結していたUXにログ用の裏付けを追加)。
    # 任意項目なので既存クライアント(未送信)との後方互換は崩さない。
    reason: Optional[str] = Field(default=None, max_length=500)

# Issue #547: reset_game.py が別プロセスから直接DBを書き換えていたリセット処理を
# サーバーAPI経由に置き換えるための入力モデル。admin_idはApproveAction.approver_id等と
# 同様、サービス層(UserService.reset_user_data)がquest_users.role='role_adult'かどうかを
# 検証する。
class ResetUserAction(BaseModel):
    admin_id: str = Field(min_length=1, max_length=64)
    target_user_id: str = Field(min_length=1, max_length=64)

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
    user_id: str = Field(min_length=1, max_length=64)
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

class ResetUserResponse(BaseModel):
    status: str
    deletedHistoryCount: int
    deletedInventoryCount: int

class PurchaseResponse(BaseModel):
    status: str
    newGold: int


class UseItemResponse(BaseModel):
    status: str
    message: str

class UseItemAction(BaseModel):
    # Q-L4 の上限(2**63-1)が本モデルだけ漏れており、inventory_id=2**64 で
    # sqlite3 の OverflowError → 500 になっていた(/quest/cancel 等は 422)。
    user_id: str = Field(min_length=1, max_length=64)
    inventory_id: int = Field(ge=1, le=_SQLITE_INT_MAX)


# ==========================================
# View Models (GET /api/quest/data のレスポンス契約) — Issue #752 (AUDIT-023)
# ==========================================
# GameSystem.get_all_view_data() は dict[str, Any] を返しており、ルーター側も
# response_model を持っていなかったため、このエンドポイントだけ OpenAPI に
# レスポンス形状が一切出ていなかった。フロントエンド側の型は
# family-quest/src/lib/gameDataSchema.ts の手書き Zod と
# src/types/index.ts の TS interface に二重管理されており、乖離しても
# 実行時にしか分からない(#470 の nextLevelExp、#530 の is_shared_* 等、
# 実際に乖離が発生した記録が gameDataSchema.ts に列挙されている)。
#
# ここでは「生成パイプラインを入れる前に、まずサーバー側の契約を明示する」
# 最小修正として、get_all_view_data が現在返している形をそのまま Pydantic に
# 写す。**意図的に現在の返却値を1フィールドも落とさない**方針であり、
# フロントエンドが既に使っていない hp/maxHp(#327)や logs(#412)も含める
# (response_model は宣言外のキーを落とすため、落とすと挙動変更になる)。
# 宣言漏れ・新カラムの取りこぼしは tests/test_quest_api_type_contract.py が
# SQLite のテーブル定義および gameDataSchema.ts と突き合わせて検出する。


class ViewUser(BaseModel):
    """quest_users の1行 + GameSystem.get_all_view_data が付与する算出フィールド。"""
    user_id: str
    name: str | None = None
    job_class: str | None = None
    level: int = 1
    exp: int = 0
    gold: int = 0
    medal_count: int | None = 0
    avatar: str | None = None
    updated_at: str | None = None
    role: str | None = None
    # game_logic.GameLogic による算出値(DBカラムではない)。
    # #470: nextLevelExp は以前からサーバーが返していたがフロント側の Zod に
    # 含まれておらず、parse 後に無音で消えていた。
    nextLevelExp: int
    # #327: HP 表示 UI は廃止済みでフロントは未使用だが、サーバーは送出し続けて
    # いるため契約としてはここに残す(落とすと実挙動が変わるため)。
    maxHp: int
    hp: int


class ViewQuest(BaseModel):
    """quest_master の1行 + filter_active_quests / ボーナス計算が付与するフィールド。"""
    quest_id: int
    title: str
    description: str | None = None
    quest_type: str | None = None
    exp_gain: int | None = None
    gold_gain: int | None = None
    icon_key: str | None = None
    day_of_week: str | None = None
    target_user: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    occurrence_chance: float | None = None
    start_time: str | None = None
    end_time: str | None = None
    # #474: quest_master.days は TEXT カラムだが、filter_active_quests が
    # day_of_week から組み立てた list[int] | None で上書きしてから返す。
    days: list[int] | None = None
    pre_requisite_quest_id: int | None = None
    reset_period: str | None = None
    # get_all_view_data が閲覧ユーザーの履歴から算出する連続達成ボーナス。
    bonus_gold: int = 0
    bonus_exp: int = 0


class ViewReward(BaseModel):
    """reward_master の1行。#291 によりレガシー列 desc は除いて返す。"""
    reward_id: int
    title: str
    description: str | None = None
    category: str | None = None
    cost_gold: int | None = None
    icon_key: str | None = None
    target: str | None = None


class ViewQuestHistory(BaseModel):
    """quest_history の1行(completedQuests / pendingQuests の要素)。"""
    id: int
    user_id: str | None = None
    quest_id: int | None = None
    quest_title: str | None = None
    # サーバーが生成するのは 'pending' | 'approved' | 'rejected' の3値だが、
    # 想定外の値を 500 に変えないよう Literal ではなく str で受ける
    # (値の列挙はフロント側 gameDataSchema.ts の z.enum が持つ)。
    status: str
    completed_at: str | None = None
    exp_earned: int | None = None
    gold_earned: int | None = None
    linked_history_id: int | None = None
    medals_earned: int | None = None


class ViewAdventureLog(BaseModel):
    """GameSystem._fetch_recent_logs が組み立てる冒険ログの1件。"""
    id: str
    text: str
    dateStr: str
    timestamp: str


class GameDataResponse(BaseModel):
    """GET /api/quest/data のレスポンス全体。"""
    users: list[ViewUser]
    quests: list[ViewQuest]
    rewards: list[ViewReward]
    completedQuests: list[ViewQuestHistory]
    # #412: フロントエンドはどのコンポーネントからも参照していないが、
    # サーバーは返し続けているため契約としては残す。
    logs: list[ViewAdventureLog]
    pendingQuests: list[ViewQuestHistory]