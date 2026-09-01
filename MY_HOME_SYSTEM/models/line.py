# MY_HOME_SYSTEM/models/line.py
from pydantic import BaseModel
from typing import List, Optional, Any

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