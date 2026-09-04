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


# ==========================================
# Issue #376 / L-L1 (#410): 署名ヘッダ欠落・不正バイト列・イベント単位の例外隔離・再配信スキップ
# ==========================================
import base64
import hashlib
import hmac as _hmac
import json

from linebot.v3 import WebhookHandler
from linebot.v3.webhooks import MessageEvent, PostbackEvent, TextMessageContent

from handlers import line_handler as _line_handler_module

_TEST_CHANNEL_SECRET = "test-channel-secret"


def _sign(body: str) -> str:
    digest = _hmac.new(_TEST_CHANNEL_SECRET.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def _text_event(user_id: str, text: str, reply_token: str, is_redelivery: bool = False, event_id: str = "evt"):
    return {
        "type": "message",
        "mode": "active",
        "timestamp": 1700000000000,
        "source": {"type": "user", "userId": user_id},
        "webhookEventId": event_id,
        "deliveryContext": {"isRedelivery": is_redelivery},
        "replyToken": reply_token,
        "message": {"id": "m1", "type": "text", "text": text, "quoteToken": "q"},
    }


@pytest.fixture
def real_webhook_handler(monkeypatch):
    """実際のSDK WebhookHandler(署名検証・イベントループ込み)に本番のハンドラを登録する"""
    wh = WebhookHandler(_TEST_CHANNEL_SECRET)
    wh.add(MessageEvent, message=TextMessageContent)(_line_handler_module.handle_message)
    wh.add(PostbackEvent)(_line_handler_module.handle_postback)
    monkeypatch.setattr(webhook_router.line_handler, "line_handler", wh)
    monkeypatch.setattr(_line_handler_module, "line_bot_api", None)
    _line_handler_module._profile_cache.clear()
    return wh


class TestLineCallbackInputValidation:
    def test_missing_signature_header_returns_400_not_200(self, api_client, monkeypatch):
        """L-L1: 署名ヘッダ欠落時、SDK内部のAttributeErrorが汎用exceptに落ちて 200 になっていた"""
        fake_handler = MagicMock()
        fake_handler.handle.side_effect = AttributeError("'NoneType' object has no attribute 'encode'")
        monkeypatch.setattr(webhook_router.line_handler, "line_handler", fake_handler)

        res = api_client.post("/callback/line", content=b'{"events": []}')

        assert res.status_code == 400
        fake_handler.handle.assert_not_called()

    def test_empty_signature_header_returns_400(self, api_client, monkeypatch):
        fake_handler = MagicMock()
        monkeypatch.setattr(webhook_router.line_handler, "line_handler", fake_handler)

        res = api_client.post("/callback/line", content=b'{"events": []}', headers={"X-Line-Signature": ""})

        assert res.status_code == 400
        fake_handler.handle.assert_not_called()

    def test_invalid_utf8_body_returns_400_not_500(self, api_client, monkeypatch):
        """L-L1: .decode('utf-8') が try の外にあり不正バイト列で 500 になっていた"""
        fake_handler = MagicMock()
        monkeypatch.setattr(webhook_router.line_handler, "line_handler", fake_handler)

        res = api_client.post("/callback/line", content=b"\xff\xfe\xfd", headers={"X-Line-Signature": "sig"})

        assert res.status_code == 400
        fake_handler.handle.assert_not_called()


class TestLineCallbackWithRealSdkHandler:
    """SDKの署名検証とイベントループを実物で通し、ハンドラ側のイベント単位隔離・再配信スキップを検証する"""

    def test_exception_in_first_event_does_not_block_second_event(self, api_client, real_webhook_handler, monkeypatch):
        processed = []

        async def fake_process(user_id, user_name, msg_text, reply_token):
            if user_id == "U_BAD":
                raise RuntimeError("boom in first event")
            processed.append((user_id, msg_text))

        monkeypatch.setattr(_line_handler_module, "_process_message_async", fake_process)
        body = json.dumps({"destination": "Ubot", "events": [
            _text_event("U_BAD", "hello", "tok1", event_id="e1"),
            _text_event("U_GOOD", "ステータス", "tok2", event_id="e2"),
        ]})

        res = api_client.post("/callback/line", content=body.encode("utf-8"), headers={"X-Line-Signature": _sign(body)})

        assert res.status_code == 200
        assert processed == [("U_GOOD", "ステータス")]

    def test_redelivered_event_is_skipped(self, api_client, real_webhook_handler, monkeypatch):
        processed = []

        async def fake_process(user_id, user_name, msg_text, reply_token):
            processed.append((user_id, msg_text))

        monkeypatch.setattr(_line_handler_module, "_process_message_async", fake_process)
        body = json.dumps({"destination": "Ubot", "events": [
            _text_event("U1", "智矢は元気", "tok1", is_redelivery=True, event_id="e1"),
            _text_event("U2", "ステータス", "tok2", is_redelivery=False, event_id="e2"),
        ]})

        res = api_client.post("/callback/line", content=body.encode("utf-8"), headers={"X-Line-Signature": _sign(body)})

        assert res.status_code == 200
        assert processed == [("U2", "ステータス")]

    def test_wrong_signature_with_real_handler_returns_400(self, api_client, real_webhook_handler, monkeypatch):
        called = MagicMock()
        monkeypatch.setattr(_line_handler_module, "_process_message_async", called)
        body = json.dumps({"destination": "Ubot", "events": [_text_event("U1", "hi", "tok1")]})

        res = api_client.post("/callback/line", content=body.encode("utf-8"), headers={"X-Line-Signature": _sign(body + " ")})

        assert res.status_code == 400
        called.assert_not_called()
