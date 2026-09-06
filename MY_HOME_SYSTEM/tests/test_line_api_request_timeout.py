# MY_HOME_SYSTEM/tests/test_line_api_request_timeout.py
"""
LINE Messaging API 呼び出しに必ず _request_timeout が付くことの回帰テスト。

line-bot-sdk v3 は _request_timeout 未指定だと urllib3 に timeout=None(無期限
ブロック)を渡す。api.line.me への TCP がブラックホール化した場合、
BackgroundTasks のワーカースレッドが永久に塞がり、anyio のスレッドプールが
枯渇すると同期 def の全エンドポイントまで停止するため、全呼び出しに
config.LINE_API_REQUEST_TIMEOUT を渡す。
"""
import os
import sys
from unittest.mock import MagicMock

from linebot.v3.messaging import TextMessage

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
import handlers.line_handler as line_handler
import handlers.line_logic as line_logic
from services import notification_service


def test_timeout_constant_is_connect_read_pair():
    assert isinstance(config.LINE_API_REQUEST_TIMEOUT, tuple)
    assert len(config.LINE_API_REQUEST_TIMEOUT) == 2
    assert all(t > 0 for t in config.LINE_API_REQUEST_TIMEOUT)


def test_reply_message_passes_request_timeout(monkeypatch):
    fake_api = MagicMock()
    monkeypatch.setattr(line_handler, "line_bot_api", fake_api)
    line_handler.reply_message("token", [TextMessage(text="x")], user_id="U1")
    assert fake_api.reply_message.call_args.kwargs["_request_timeout"] == config.LINE_API_REQUEST_TIMEOUT


def test_push_fallback_passes_request_timeout(monkeypatch):
    fake_api = MagicMock()
    fake_api.reply_message.side_effect = RuntimeError("expired")
    monkeypatch.setattr(line_handler, "line_bot_api", fake_api)
    line_handler.reply_message("token", [TextMessage(text="x")], user_id="U1")
    assert fake_api.push_message.call_args.kwargs["_request_timeout"] == config.LINE_API_REQUEST_TIMEOUT


def test_get_display_name_passes_request_timeout(monkeypatch):
    fake_api = MagicMock()
    fake_api.get_profile.return_value = MagicMock(display_name="太郎")
    monkeypatch.setattr(line_handler, "line_bot_api", fake_api)
    line_handler._profile_cache.pop("Utimeout", None)
    assert line_handler._get_display_name("Utimeout") == "太郎"
    assert fake_api.get_profile.call_args.kwargs["_request_timeout"] == config.LINE_API_REQUEST_TIMEOUT


def test_line_logic_send_reply_text_passes_request_timeout():
    fake_api = MagicMock()
    line_logic.send_reply_text(fake_api, "token", "hello")
    assert fake_api.reply_message.call_args.kwargs["_request_timeout"] == config.LINE_API_REQUEST_TIMEOUT


def test_line_logic_get_user_name_passes_request_timeout():
    fake_api = MagicMock()
    fake_api.get_profile.return_value = MagicMock(display_name="花子")
    event = MagicMock()
    event.source.type = "user"
    event.source.user_id = "U2"
    assert line_logic.get_user_name(event, fake_api) == "花子"
    assert fake_api.get_profile.call_args.kwargs["_request_timeout"] == config.LINE_API_REQUEST_TIMEOUT

    event.source.type = "group"
    event.source.group_id = "G1"
    fake_api.get_group_member_profile.return_value = MagicMock(display_name="次郎")
    assert line_logic.get_user_name(event, fake_api) == "次郎"
    assert fake_api.get_group_member_profile.call_args.kwargs["_request_timeout"] == config.LINE_API_REQUEST_TIMEOUT


def test_notification_service_push_passes_request_timeout(monkeypatch):
    fake_api = MagicMock()

    class _FakeApiClient:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(notification_service, "ApiClient", _FakeApiClient)
    monkeypatch.setattr(notification_service, "MessagingApi", lambda client: fake_api)
    monkeypatch.setattr(notification_service, "line_configuration", object())
    monkeypatch.setattr(config, "LINE_CHANNEL_ACCESS_TOKEN", "dummy")
    assert notification_service._send_line_push("U1", [{"type": "text", "text": "hi"}]) is True
    assert fake_api.push_message.call_args.kwargs["_request_timeout"] == config.LINE_API_REQUEST_TIMEOUT
