# MY_HOME_SYSTEM/models/switchbot.py
from pydantic import BaseModel, Field
from typing import Optional, Union, Dict, Any

class SwitchBotContext(BaseModel):
    """Webhookで送られてくる詳細コンテキスト"""
    deviceMac: str
    detectionState: Optional[str] = None
    brightness: Optional[str] = None
    timeOfSample: Optional[int] = None
    # 電力計などのフィールド
    power: Optional[str] = None
    voltage: Optional[float] = None
    weight: Optional[float] = None
    watt: Optional[float] = None

class SwitchBotWebhookBody(BaseModel):
    """SwitchBot Webhookのエントリポイント"""
    eventType: str
    eventVersion: str
    context: SwitchBotContext
    deviceType: Optional[str] = None

class DeviceStatusResponse(BaseModel):
    """API経由で取得したデバイス状態（GET /v1.1/devices/{id}/status 用）"""
    statusCode: int
    message: str
    body: Dict[str, Any]  # デバイスにより中身が激しく変わるため一旦Any