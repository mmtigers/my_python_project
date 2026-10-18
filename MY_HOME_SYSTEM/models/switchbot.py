# MY_HOME_SYSTEM/models/switchbot.py
from pydantic import BaseModel, ConfigDict, Field
from typing import Optional, Union

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

class DeviceStatusBody(BaseModel):
    """GET /v1.1/devices/{id}/status の `body`。

    デバイス種別ごとに中身が大きく変わるため、**システム内で実際に読んでいる**フィールド
    (`services/switchbot_service.get_tv_power_watts`・`monitors/switchbot_power_monitor.
    fetch_device_status_sync`)だけを定義し、それ以外のキーは `extra="allow"` でそのまま通す。

    型は意図的に緩くしてある。Plug Mini は電力を `weight`(数値)や `power`(`"on"` のような
    文字列)で返すなど、同じ名前でも機種によって型が違い、呼び出し側が個別に `float()` で
    変換して不正値を読み飛ばしているため。ここで厳密にすると、1つのフィールドの形が違うだけで
    ステータス全体が取れなくなる。
    """
    model_config = ConfigDict(extra="allow")

    # 電力: Plug Mini は weight、機種により power / watt
    weight: Optional[Union[float, str]] = None
    power: Optional[Union[float, str]] = None
    watt: Optional[Union[float, str]] = None
    # 温湿度計
    temperature: Optional[Union[float, str]] = None
    humidity: Optional[Union[float, str]] = None
    # ON/OFF を文字列で返す機種
    powerState: Optional[str] = None


class DeviceStatusResponse(BaseModel):
    """API経由で取得したデバイス状態（GET /v1.1/devices/{id}/status 用）"""
    statusCode: int
    message: str
    # 失敗レスポンス(statusCode != 100)では body が空・欠落しうるため既定は空。
    body: DeviceStatusBody = Field(default_factory=DeviceStatusBody)
