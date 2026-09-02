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


class TestSendLinePushFlexDictConversion:
    """Issue #322の回帰テスト: 辞書形式のflexメッセージが黙って破棄されないこと。

    以前は _send_line_push 内の flex 分岐が pass で、辞書形式のflexメッセージは
    無言で破棄されていた(テキスト混在時は送信自体が成功するため気づけない)。
    現在は FlexContainer.from_dict でv3オブジェクトへ変換して送信し、変換不能な
    場合・未対応型の場合は内容つきのログを残す。
    """

    _VALID_FLEX_DICT = {
        "type": "flex",
        "altText": "テスト通知",
        "contents": {
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [{"type": "text", "text": "hello"}],
            },
        },
    }

    def test_flex_dict_is_converted_to_flex_message_and_sent(self, monkeypatch):
        fake_api = _install_fake_line_sdk(monkeypatch)

        result = notification_service._send_line_push("dad", [self._VALID_FLEX_DICT])

        assert result is True
        push_request = fake_api.push_message.call_args.args[0]
        assert len(push_request.messages) == 1
        sent = push_request.messages[0]
        assert isinstance(sent, notification_service.FlexMessage)
        assert sent.alt_text == "テスト通知"

    def test_invalid_flex_dict_is_dropped_with_error_log_but_text_still_sent(self, monkeypatch):
        fake_api = _install_fake_line_sdk(monkeypatch)
        fake_logger = MagicMock()
        monkeypatch.setattr(notification_service, "logger", fake_logger)

        broken_flex = {"type": "flex", "altText": "壊れたflex", "contents": {"type": "unknown_container"}}
        result = notification_service._send_line_push(
            "dad", [broken_flex, {"type": "text", "text": "hi"}]
        )

        # テキストメッセージは送信され、壊れたflexは内容つきエラーログとともに破棄される
        assert result is True
        push_request = fake_api.push_message.call_args.args[0]
        assert len(push_request.messages) == 1
        assert isinstance(push_request.messages[0], notification_service.TextMessage)
        assert fake_logger.error.called
        assert "壊れたflex" in fake_logger.error.call_args.args[0]

    def test_unsupported_dict_type_is_dropped_with_warning_log(self, monkeypatch):
        fake_api = _install_fake_line_sdk(monkeypatch)
        fake_logger = MagicMock()
        monkeypatch.setattr(notification_service, "logger", fake_logger)

        result = notification_service._send_line_push("dad", [{"type": "sticker", "packageId": "1"}])

        assert result is False
        fake_api.push_message.assert_not_called()
        warning_messages = [c.args[0] for c in fake_logger.warning.call_args_list]
        assert any("未対応のメッセージ型" in m for m in warning_messages)


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


class TestSendPushSignatureRedesign:
    """Issue #289の回帰テスト: send_pushの宛先解決(LINE user_idの決定)を
    関数内に一元化した。target="discord"のみの呼び出しではuser_idが完全に
    不要であること、target に "line"/"both" を含む場合はuser_id省略時に
    config.LINE_USER_ID へフォールバックすることを確認する。"""

    def test_discord_only_call_does_not_require_user_id(self, monkeypatch):
        monkeypatch.setattr(config, "DISCORD_WEBHOOK_NOTIFY", "https://discord.example/webhook")
        monkeypatch.setattr(
            notification_service.requests, "post", lambda *a, **kw: MagicMock(status_code=204)
        )

        result = notification_service.send_push(
            [{"type": "text", "text": "hi"}], target="discord"
        )
        assert result is True

    def test_line_target_without_user_id_falls_back_to_config_line_user_id(self, monkeypatch):
        monkeypatch.setattr(config, "LINE_USER_ID", "configured-user")
        fake_api = _install_fake_line_sdk(monkeypatch)

        result = notification_service.send_push(
            [{"type": "text", "text": "hi"}], target="line"
        )

        assert result is True
        fake_api.push_message.assert_called_once()
        push_request = fake_api.push_message.call_args.args[0]
        assert push_request.to == "configured-user"

    def test_line_target_without_user_id_or_config_fallback_fails_gracefully(self, monkeypatch):
        monkeypatch.setattr(config, "LINE_USER_ID", None)
        fake_api = _install_fake_line_sdk(monkeypatch)

        result = notification_service.send_push(
            [{"type": "text", "text": "hi"}], target="line"
        )

        assert result is False
        fake_api.push_message.assert_not_called()

    def test_explicit_user_id_overrides_config_line_user_id(self, monkeypatch):
        monkeypatch.setattr(config, "LINE_USER_ID", "default-user")
        fake_api = _install_fake_line_sdk(monkeypatch)

        result = notification_service.send_push(
            [{"type": "text", "text": "hi"}], target="line", user_id="explicit-user"
        )

        assert result is True
        push_request = fake_api.push_message.call_args.args[0]
        assert push_request.to == "explicit-user"


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
