# MY_HOME_SYSTEM/tests/test_webhook_router.py
"""
routers/webhook_router.py の SwitchBot Webhook 共有シークレット検証、および
LINE Webhook (/callback/line) の署名検証結果によるレスポンス分岐のテスト。
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from linebot.v3.exceptions import InvalidSignatureError

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from routers import webhook_router
from services import sensor_service
from models.switchbot import SwitchBotContext, SwitchBotWebhookBody


@pytest.fixture(autouse=True)
def _reset_dedupe_cache():
    """switchbot_webhookの重複排除キャッシュがテスト間で干渉しないようにリセットする"""
    sensor_service.EVENT_CACHE.clear()
    yield
    sensor_service.EVENT_CACHE.clear()


def _make_body(mac="mac_webhook_test", state="open", device_type="Contact Sensor"):
    return SwitchBotWebhookBody(
        eventType="changeReport",
        eventVersion="1.0",
        context=SwitchBotContext(deviceMac=mac, detectionState=state),
        deviceType=device_type,
    )


@pytest.fixture
def configured_token():
    original = config.SWITCHBOT_WEBHOOK_TOKEN
    config.SWITCHBOT_WEBHOOK_TOKEN = "secret123"
    yield "secret123"
    config.SWITCHBOT_WEBHOOK_TOKEN = original


@pytest.mark.asyncio
async def test_rejects_missing_token_when_configured(configured_token):
    with pytest.raises(HTTPException) as exc_info:
        await webhook_router.switchbot_webhook(_make_body(), token=None)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_rejects_wrong_token_when_configured(configured_token):
    with pytest.raises(HTTPException) as exc_info:
        await webhook_router.switchbot_webhook(_make_body(), token="wrong-token")
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_allows_correct_token(configured_token):
    with patch("routers.webhook_router.save_log_async", new=AsyncMock(return_value=True)), \
         patch.object(webhook_router.sensor_service, "process_sensor_data", new=AsyncMock(return_value=None)), \
         patch.object(webhook_router.sb_tool, "get_device_name_by_id", return_value="玄関ドア"):
        result = await webhook_router.switchbot_webhook(_make_body(), token=configured_token)

    assert result["status"] == "success"


@pytest.mark.asyncio
async def test_no_token_required_when_not_configured():
    """SWITCHBOT_WEBHOOK_TOKEN 未設定時は従来通り検証なしで通ること(後方互換)"""
    assert config.SWITCHBOT_WEBHOOK_TOKEN is None
    with patch("routers.webhook_router.save_log_async", new=AsyncMock(return_value=True)), \
         patch.object(webhook_router.sensor_service, "process_sensor_data", new=AsyncMock(return_value=None)), \
         patch.object(webhook_router.sb_tool, "get_device_name_by_id", return_value="玄関ドア"):
        result = await webhook_router.switchbot_webhook(_make_body(), token=None)

    assert result["status"] == "success"


class TestLineCallback:
    """
    /callback/line の署名検証結果によるレスポンス分岐。
    実際のLINE SDKのWebhookHandlerは使わず、handleメソッドの呼び出し結果のみをモックする。
    """

    def test_returns_501_when_line_bot_not_configured(self, api_client, monkeypatch):
        monkeypatch.setattr(webhook_router.line_handler, "line_handler", None)
        res = api_client.post("/callback/line", content=b"{}", headers={"X-Line-Signature": "sig"})
        assert res.status_code == 501

    def test_returns_ok_when_signature_is_valid(self, api_client, monkeypatch):
        fake_handler = MagicMock()
        monkeypatch.setattr(webhook_router.line_handler, "line_handler", fake_handler)

        res = api_client.post("/callback/line", content=b'{"events": []}', headers={"X-Line-Signature": "valid-sig"})

        assert res.status_code == 200
        assert res.text == '"OK"'
        fake_handler.handle.assert_called_once()
        called_body, called_sig = fake_handler.handle.call_args[0]
        assert called_body == '{"events": []}'
        assert called_sig == "valid-sig"

    def test_returns_400_on_invalid_signature(self, api_client, monkeypatch):
        fake_handler = MagicMock()
        fake_handler.handle.side_effect = InvalidSignatureError("bad signature")
        monkeypatch.setattr(webhook_router.line_handler, "line_handler", fake_handler)

        res = api_client.post("/callback/line", content=b"{}", headers={"X-Line-Signature": "wrong-sig"})

        assert res.status_code == 400

    def test_unexpected_exception_is_logged_and_still_returns_ok(self, api_client, monkeypatch):
        """
        LINE側の一時的な処理エラーでWebhook全体を500にしてしまうと、LINEプラットフォーム側の
        リトライ挙動に巻き込まれる可能性があるため、想定外の例外はログのみで200を返す設計。
        """
        fake_handler = MagicMock()
        fake_handler.handle.side_effect = RuntimeError("unexpected internal error")
        monkeypatch.setattr(webhook_router.line_handler, "line_handler", fake_handler)

        res = api_client.post("/callback/line", content=b"{}", headers={"X-Line-Signature": "sig"})

        assert res.status_code == 200
        assert res.text == '"OK"'
