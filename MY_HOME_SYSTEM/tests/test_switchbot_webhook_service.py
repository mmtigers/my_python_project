# MY_HOME_SYSTEM/tests/test_switchbot_webhook_service.py
"""services/switchbot_webhook_service.py の単体テスト(Issue #954)。

ルーター経由の挙動は test_webhook_router.py / test_switchbot_webhook_contract.py が検証する。
ここでは切り出した純粋な判定(トークン検証・イベント抽出)を直接確認する。
"""
import pytest
from fastapi import HTTPException

import config
from models.switchbot import SwitchBotContext, SwitchBotWebhookBody
from services import switchbot_webhook_service as svc


def _body(device_type, **ctx_fields):
    ctx = SwitchBotContext(deviceMac="AABBCCDDEEFF", deviceType=device_type, **ctx_fields)
    return SwitchBotWebhookBody(eventType="changeReport", eventVersion="1", context=ctx)


def test_extract_event_ignores_unsupported_device_type():
    assert svc.extract_event(_body("WoHand")) is None


def test_extract_event_contact_sensor_uses_open_state():
    event = svc.extract_event(_body("WoContact", openState="open", detectionState="NOT_DETECTED"))
    assert event == svc.WebhookEvent(mac="AABBCCDDEEFF", device_type="WoContact", state="open")


def test_extract_event_motion_sensor_uses_detection_state():
    event = svc.extract_event(_body("WoPresence", detectionState="DETECTED"))
    assert event.state == "detected"


def test_verify_token_rejects_non_ascii_token_with_401_not_500(monkeypatch):
    monkeypatch.setattr(config, "SWITCHBOT_WEBHOOK_TOKEN", "secret", raising=False)
    with pytest.raises(HTTPException) as exc:
        svc.verify_token("é")
    assert exc.value.status_code == 401


def test_verify_token_accepts_matching_token(monkeypatch):
    monkeypatch.setattr(config, "SWITCHBOT_WEBHOOK_TOKEN", "secret", raising=False)
    svc.verify_token("secret")  # 例外にならない
