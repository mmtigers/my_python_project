# MY_HOME_SYSTEM/models/line.py
from pydantic import BaseModel, Field
from typing import List, Optional, Any
from enum import Enum

# --- Webhookのエントリポイント用モデル (unified_server.py用) ---
class LineSource(BaseModel):
    userId: str
    type: str

class LineMessage(BaseModel):
    id: str
    type: str
    text: Optional[str] = None

class LineEvent(BaseModel):
    type: str
    replyToken: Optional[str] = None
    source: LineSource
    message: Optional[LineMessage] = None
    postback: Optional[Any] = None
    timestamp: int

class LineWebhookBody(BaseModel):
    """これが不足していました"""
    destination: str
    events: List[LineEvent]

# --- Postback解析用モデル (line_logic.py用) ---
class LinePostbackData(BaseModel):
    """
    LINEのボタン操作等で送られてくるデータ構造
    data: "action=child_check&child=太郎&status=fever" 等をパースした後の形
    """
    action: str
    child: Optional[str] = None
    status: Optional[str] = None
    value: Optional[str] = None

class InputMode(str, Enum):
    """入力モードの定義。タイポを物理的に防ぎます。"""
    CHILD_HEALTH = "child_health"
    MEAL = "meal"
    STOMACH = "stomach"

class UserInputState(BaseModel):
    """
    ユーザーが今何を入力している最中かを保持するモデル
    """
    mode: InputMode
    target_name: Optional[str] = None
    category: Optional[str] = None  # 食事カテゴリ等で使用