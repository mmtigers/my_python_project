# MY_HOME_SYSTEM/tests/test_webhook_router.py
"""
routers/webhook_router.py の SwitchBot Webhook 共有シークレット検証、および
LINE Webhook (/callback/line) の署名検証結果によるレスポンス分岐のテスト。
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import BackgroundTasks, HTTPException
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
    実際のLINE SDKのWebhookHandlerは使わず、`line_handler.parser.parse()` の
    呼び出し結果のみをモックする。

    Issue #376: 以前は `WebhookHandler.handle(body, signature)` が署名検証・パース・
    ディスパッチを同期的に一括で行っていたが、実処理(ディスパッチ)を応答後の
    BackgroundTasksへ切り出したため、ここでモックする対象は `handle` ではなく
    `parser.parse`(署名検証+パースのみを担う、応答前に同期実行される部分)になる。
    """

    def test_returns_501_when_line_bot_not_configured(self, api_client, monkeypatch):
        monkeypatch.setattr(webhook_router.line_handler, "line_handler", None)
        res = api_client.post("/callback/line", content=b"{}", headers={"X-Line-Signature": "sig"})
        assert res.status_code == 501

    def test_returns_ok_when_signature_is_valid(self, api_client, monkeypatch):
        fake_handler = MagicMock()
        fake_handler.parser.parse.return_value = []
        monkeypatch.setattr(webhook_router.line_handler, "line_handler", fake_handler)

        res = api_client.post("/callback/line", content=b'{"events": []}', headers={"X-Line-Signature": "valid-sig"})

        assert res.status_code == 200
        assert res.text == '"OK"'
        fake_handler.parser.parse.assert_called_once()
        called_body, called_sig = fake_handler.parser.parse.call_args[0]
        assert called_body == '{"events": []}'
        assert called_sig == "valid-sig"
        # handle()自体はもう呼ばれない(署名検証+パースはparser.parseへ切り出した)
        fake_handler.handle.assert_not_called()

    def test_parsed_events_are_handed_to_dispatch_events_in_background(self, api_client, monkeypatch):
        """署名検証後にパースされたイベント一覧が、BackgroundTasks経由でdispatch_eventsに渡ること"""
        fake_handler = MagicMock()
        sentinel_events = [MagicMock(name="parsed_event")]
        fake_handler.parser.parse.return_value = sentinel_events
        monkeypatch.setattr(webhook_router.line_handler, "line_handler", fake_handler)
        mock_dispatch = MagicMock()
        monkeypatch.setattr(webhook_router.line_handler, "dispatch_events", mock_dispatch)

        res = api_client.post("/callback/line", content=b'{"events": []}', headers={"X-Line-Signature": "valid-sig"})

        assert res.status_code == 200
        mock_dispatch.assert_called_once_with(sentinel_events)

    def test_returns_400_on_invalid_signature(self, api_client, monkeypatch):
        fake_handler = MagicMock()
        fake_handler.parser.parse.side_effect = InvalidSignatureError("bad signature")
        monkeypatch.setattr(webhook_router.line_handler, "line_handler", fake_handler)

        res = api_client.post("/callback/line", content=b"{}", headers={"X-Line-Signature": "wrong-sig"})

        assert res.status_code == 400

    def test_unexpected_exception_is_logged_and_still_returns_ok(self, api_client, monkeypatch):
        """
        LINE側の一時的な処理エラーでWebhook全体を500にしてしまうと、LINEプラットフォーム側の
        リトライ挙動に巻き込まれる可能性があるため、想定外の例外はログのみで200を返す設計。
        """
        fake_handler = MagicMock()
        fake_handler.parser.parse.side_effect = RuntimeError("unexpected internal error")
        monkeypatch.setattr(webhook_router.line_handler, "line_handler", fake_handler)

        res = api_client.post("/callback/line", content=b"{}", headers={"X-Line-Signature": "sig"})

        assert res.status_code == 200
        assert res.text == '"OK"'


class TestLineCallbackReturnsBeforeBackgroundWorkCompletes:
    """
    Issue #376 (a): HTTP応答が、重いイベント処理(AI呼び出し・DB書き込み等)の完了より
    先に返ること。

    starlette TestClient は ASGI呼び出し全体(BackgroundTasksの実行を含む)が完了する
    まで `.post()` がブロックすることを事前のスモークテストで確認済み(TestClientは
    `with` ブロック外でも、リクエストごとのASGIサイクルの一部としてBackgroundTasksを
    実行するため、lifespanの起動有無とは無関係)。そのため `.post()` の戻り時刻の比較
    では「応答が先に返った」ことを直接観測できない。

    代わりに、ルーター関数を `BackgroundTasks()` を自前で渡して直接呼び出し、
    関数が "OK" を返した時点で `background_tasks.tasks` に実処理がスケジュール
    「済み」だが「未実行」であることを確認する。これはFastAPIのBackgroundTasksの
    契約(スケジュールされたコールバックはレスポンス送信後に実行される)そのものを
    検証しており、応答が実処理の完了を待たないことの直接的な証拠になる。
    """

    @pytest.mark.asyncio
    async def test_response_is_returned_before_dispatch_events_runs(self, monkeypatch):
        order = []

        def slow_dispatch(events):
            order.append("dispatch_ran")

        fake_handler = MagicMock()
        fake_handler.parser.parse.return_value = ["evt1"]
        monkeypatch.setattr(webhook_router.line_handler, "line_handler", fake_handler)
        monkeypatch.setattr(webhook_router.line_handler, "dispatch_events", slow_dispatch)

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"events": []}')
        background_tasks = BackgroundTasks()

        result = await webhook_router.callback_line(request, background_tasks, x_line_signature="sig")
        order.append("handler_returned")

        # ルーター関数が値を返した時点では、まだ dispatch_events は実行されていない
        # (スケジュールされているだけ)。
        assert result == "OK"
        assert order == ["handler_returned"]
        assert len(background_tasks.tasks) == 1

        # 実際のASGIサーバーがレスポンス送信後に行う処理を模して、スケジュール済みの
        # バックグラウンドタスクをここで初めて実行する。
        await background_tasks()
        assert order == ["handler_returned", "dispatch_ran"]


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


@pytest.fixture(autouse=True)
def _reset_seen_event_ids():
    """
    Issue #376: webhookEventIdベースの冪等化キャッシュ(_SEEN_EVENT_IDS)がテスト間で
    干渉しないようにリセットする。本ファイル内の複数テストが同じ event_id("e1"/"e2"等)
    を使い回すため、autouseでリセットしないと実行順によって「既に処理済み」扱いされ
    誤ってスキップされてしまう。
    """
    _line_handler_module._SEEN_EVENT_IDS.clear()
    yield
    _line_handler_module._SEEN_EVENT_IDS.clear()


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


class TestLineCallbackWebhookEventIdIdempotency:
    """
    Issue #376 (b): 同一 webhookEventId が複数回配信されても、実処理(DB書き込み・
    LINE返信)が1回しか行われないこと。isRedelivery フラグに頼らない冪等化。
    """

    def test_same_event_id_in_two_separate_requests_processed_once(
        self, api_client, real_webhook_handler, monkeypatch
    ):
        """LINE基盤からのリトライ配信を模し、全く同じイベントを2回POSTする"""
        processed = []

        async def fake_process(user_id, user_name, msg_text, reply_token):
            processed.append((user_id, msg_text))

        monkeypatch.setattr(_line_handler_module, "_process_message_async", fake_process)
        body = json.dumps({"destination": "Ubot", "events": [
            _text_event("U1", "ステータス", "tok1", event_id="dup-evt-1"),
        ]})
        headers = {"X-Line-Signature": _sign(body)}

        res1 = api_client.post("/callback/line", content=body.encode("utf-8"), headers=headers)
        res2 = api_client.post("/callback/line", content=body.encode("utf-8"), headers=headers)

        # どちらのリクエストも(再送であっても)署名検証は通るため200を返す
        assert res1.status_code == 200
        assert res2.status_code == 200
        # 実処理は1回のみ
        assert processed == [("U1", "ステータス")]

    def test_same_event_id_within_single_batch_processed_once(
        self, api_client, real_webhook_handler, monkeypatch
    ):
        """1回のWebhook配信内(events配列)に同一webhookEventIdが重複しているケース"""
        processed = []

        async def fake_process(user_id, user_name, msg_text, reply_token):
            processed.append((user_id, msg_text))

        monkeypatch.setattr(_line_handler_module, "_process_message_async", fake_process)
        body = json.dumps({"destination": "Ubot", "events": [
            _text_event("U1", "ステータス", "tok1", event_id="dup-evt-2"),
            _text_event("U1", "ステータス", "tok1", event_id="dup-evt-2"),
        ]})

        res = api_client.post("/callback/line", content=body.encode("utf-8"), headers={"X-Line-Signature": _sign(body)})

        assert res.status_code == 200
        assert processed == [("U1", "ステータス")]

    def test_different_event_ids_are_both_processed(self, api_client, real_webhook_handler, monkeypatch):
        """冪等化キーが異なれば、通常通りどちらも処理されること(過剰スキップの回帰防止)"""
        processed = []

        async def fake_process(user_id, user_name, msg_text, reply_token):
            processed.append((user_id, msg_text))

        monkeypatch.setattr(_line_handler_module, "_process_message_async", fake_process)
        body = json.dumps({"destination": "Ubot", "events": [
            _text_event("U1", "ステータス", "tok1", event_id="evt-a"),
            _text_event("U1", "ステータス", "tok2", event_id="evt-b"),
        ]})

        res = api_client.post("/callback/line", content=body.encode("utf-8"), headers={"X-Line-Signature": _sign(body)})

        assert res.status_code == 200
        assert processed == [("U1", "ステータス"), ("U1", "ステータス")]
