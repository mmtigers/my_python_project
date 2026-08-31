# MY_HOME_SYSTEM/tests/test_notification_service.py
"""
services/notification_service.py の通知経路のテスト。

実際のDiscord/LINE APIには一切アクセスしない(requests.post・LINE SDKをモック)。
send_push(target="both") は「Discordが失敗してもLINEへはフォールバックしない」
「LINEが失敗したらDiscordのエラーチャンネルへフォールバックする」という
非対称な設計になっており、この既存の意図した挙動を回帰テストとして固定する。
"""
import os
import sys
from unittest.mock import MagicMock


sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from services import notification_service


class _FakeApiClient:
    def __init__(self, cfg):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def _install_fake_line_sdk(monkeypatch, push_side_effect=None):
    monkeypatch.setattr(notification_service, "line_configuration", object())
    monkeypatch.setattr(notification_service, "ApiClient", _FakeApiClient)

    fake_api = MagicMock()
    if push_side_effect:
        fake_api.push_message.side_effect = push_side_effect
    monkeypatch.setattr(notification_service, "MessagingApi", lambda client: fake_api)
    return fake_api


class TestSendDiscordWebhook:
    def test_returns_false_when_no_url_configured_for_channel(self, monkeypatch):
        monkeypatch.setattr(config, "DISCORD_WEBHOOK_NOTIFY", None)
        monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", None)
        result = notification_service._send_discord_webhook([{"type": "text", "text": "hi"}])
        assert result is False

    def test_returns_false_on_non_success_status_code(self, monkeypatch):
        monkeypatch.setattr(config, "DISCORD_WEBHOOK_NOTIFY", "https://discord.example/webhook")

        fake_response = MagicMock(status_code=500, text="Internal Server Error")
        monkeypatch.setattr(
            notification_service.requests, "post", lambda *a, **kw: fake_response
        )

        result = notification_service._send_discord_webhook([{"type": "text", "text": "hi"}])
        assert result is False

    def test_returns_true_on_204(self, monkeypatch):
        monkeypatch.setattr(config, "DISCORD_WEBHOOK_NOTIFY", "https://discord.example/webhook")
        fake_response = MagicMock(status_code=204)
        monkeypatch.setattr(
            notification_service.requests, "post", lambda *a, **kw: fake_response
        )

        result = notification_service._send_discord_webhook([{"type": "text", "text": "hi"}])
        assert result is True

    def test_network_exception_is_caught_and_returns_false(self, monkeypatch):
        monkeypatch.setattr(config, "DISCORD_WEBHOOK_NOTIFY", "https://discord.example/webhook")

        def _raise(*a, **kw):
            raise ConnectionError("simulated network failure")

        monkeypatch.setattr(notification_service.requests, "post", _raise)

        result = notification_service._send_discord_webhook([{"type": "text", "text": "hi"}])
        assert result is False


class TestSendPushFallbackBehavior:
    def test_both_succeed(self, monkeypatch):
        monkeypatch.setattr(config, "DISCORD_WEBHOOK_NOTIFY", "https://discord.example/webhook")
        monkeypatch.setattr(
            notification_service.requests, "post", lambda *a, **kw: MagicMock(status_code=204)
        )
        _install_fake_line_sdk(monkeypatch)

        result = notification_service.send_push(
            user_id="dad", messages=[{"type": "text", "text": "hi"}], target="both"
        )
        assert result is True

    def test_line_failure_falls_back_to_discord_error_channel(self, monkeypatch):
        """LINE送信失敗時は Discord のエラーチャンネルへフォールバック通知される"""
        discord_calls = []

        def _fake_post(url, **kwargs):
            discord_calls.append((url, kwargs))
            return MagicMock(status_code=204)

        monkeypatch.setattr(config, "DISCORD_WEBHOOK_ERROR", "https://discord.example/error-channel")
        monkeypatch.setattr(notification_service.requests, "post", _fake_post)
        _install_fake_line_sdk(monkeypatch, push_side_effect=Exception("LINE API down"))

        result = notification_service.send_push(
            user_id="dad", messages=[{"type": "text", "text": "hi"}], target="line"
        )

        assert result is False
        assert len(discord_calls) == 1  # フォールバック通知が1回飛んでいること

    def test_discord_failure_does_not_fall_back_to_line(self, monkeypatch):
        """
        Discord失敗時はLINEへのフォールバックは行われない(非対称な設計)。
        target="discord" のみの呼び出しではLINE送信自体が試みられないことを確認する。
        """
        monkeypatch.setattr(config, "DISCORD_WEBHOOK_NOTIFY", "https://discord.example/webhook")
        monkeypatch.setattr(
            notification_service.requests, "post", lambda *a, **kw: MagicMock(status_code=500, text="err")
        )
        fake_api = _install_fake_line_sdk(monkeypatch)

        result = notification_service.send_push(
            user_id="dad", messages=[{"type": "text", "text": "hi"}], target="discord"
        )

        assert result is False
        fake_api.push_message.assert_not_called()


class TestSendReply:
    def test_returns_false_when_line_not_configured(self, monkeypatch):
        monkeypatch.setattr(notification_service, "line_configuration", None)
        assert notification_service.send_reply("token", [{"type": "text", "text": "hi"}]) is False

    def test_success_returns_true(self, monkeypatch):
        fake_api = _install_fake_line_sdk(monkeypatch)
        result = notification_service.send_reply("reply-token", [{"type": "text", "text": "hi"}])
        assert result is True
        fake_api.reply_message.assert_called_once()

    def test_sdk_exception_is_caught_and_returns_false(self, monkeypatch):
        fake_api = _install_fake_line_sdk(monkeypatch)
        fake_api.reply_message.side_effect = Exception("LINE reply failed")
        result = notification_service.send_reply("reply-token", [{"type": "text", "text": "hi"}])
        assert result is False

    def test_non_text_dict_messages_are_filtered_out_and_fails_gracefully(self, monkeypatch):
        """
        テキスト以外のdict型メッセージ(スタンプ等)は変換対象外のため除外される。
        結果としてmessagesが空になり、ReplyMessageRequestのバリデーション(最低1件必須)に
        引っかかるが、例外は外に漏れずFalseを返すこと。
        """
        fake_api = _install_fake_line_sdk(monkeypatch)
        result = notification_service.send_reply("reply-token", [{"type": "sticker", "packageId": "1"}])
        assert result is False
        fake_api.reply_message.assert_not_called()


class TestGetLineMessageQuota:
    def test_returns_none_when_not_configured(self, monkeypatch):
        monkeypatch.setattr(notification_service, "line_configuration", None)
        assert notification_service.get_line_message_quota() is None

    def test_returns_quota_on_success(self, monkeypatch):
        fake_api = _install_fake_line_sdk(monkeypatch)
        fake_api.get_message_quota.return_value = {"type": "limited", "value": 1000}
        result = notification_service.get_line_message_quota()
        assert result == {"type": "limited", "value": 1000}

    def test_exception_is_caught_and_returns_none(self, monkeypatch):
        fake_api = _install_fake_line_sdk(monkeypatch)
        fake_api.get_message_quota.side_effect = Exception("API error")
        assert notification_service.get_line_message_quota() is None
