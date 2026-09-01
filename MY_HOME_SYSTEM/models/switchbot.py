# MY_HOME_SYSTEM/models/switchbot.py
from pydantic import BaseModel, Field
from typing import Optional, Union, Dict, Any

class SwitchBotContext(BaseModel):
    """Webhookで送られてくる詳細コンテキスト"""
    deviceMac: str
    # SwitchBot公式Webhookのペイロードでは deviceType はトップレベルではなく
    # この context 内に入る(例: "WoContact", "WoPresence")。以前はここに
    # フィールドが無く、pydanticが未定義フィールドを黙って捨てるため
    # ctx.deviceType が常にフォールバック(トップレベルのNone)になっていた。
    deviceType: Optional[str] = None
    detectionState: Optional[str] = None
    # WoContact(開閉センサー)の実際の開閉状態を表すフィールド("open"/"close"/
    # "timeOutNotClose")。SwitchBot公式Webhookドキュメント(OpenWonderLabs/SwitchBotAPI
    # README-v1.0.md)によると、同デバイスの detectionState は内蔵PIRのモーション検知
    # 結果("DETECTED"/"NOT_DETECTED")であり、開閉状態そのものではないため別フィールドと
    # して定義する(Issue #251)。
    openState: Optional[str] = None
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