# MY_HOME_SYSTEM/models/ui_log.py
"""画面タップログ(POST /api/ui-log/events)のリクエストモデル。"""
import config
from pydantic import BaseModel, Field

# ==========================================
# Request Models
# ==========================================


class UiTapEvent(BaseModel):
    """1タップ分。フィールドの長さ上限は、LAN内クライアントの不具合や悪用で
    DBが肥大化しないための歯止め(認可は行わない。CLAUDE.md の認可設計参照)。"""
    event_id: str = Field(min_length=8, max_length=64)
    # タップ時刻(クライアントの Date.now() のミリ秒)。サーバー側でJSTのISOへ変換する。
    occurred_at_ms: int = Field(ge=0)
    user_id: str | None = Field(default=None, max_length=64)
    screen: str = Field(min_length=1, max_length=32)
    element_id: str | None = Field(default=None, max_length=64)
    element_tag: str | None = Field(default=None, max_length=16)
    is_interactive: bool
    x_pct: float | None = Field(default=None, ge=0, le=100)
    y_pct: float | None = Field(default=None, ge=0, le=100)
    layout_mode: str | None = Field(default=None, max_length=16)


class UiTapBatch(BaseModel):
    session_id: str = Field(min_length=8, max_length=64)
    events: list[UiTapEvent] = Field(min_length=1, max_length=config.UI_TAP_LOG_MAX_BATCH)


# ==========================================
# Response Models
# ==========================================


class UiTapBatchResponse(BaseModel):
    accepted: int   # 新規に保存した件数
    duplicated: int  # event_id が既に保存済みだった件数(再送)
    rejected: int   # 時刻が範囲外などで保存しなかった件数
