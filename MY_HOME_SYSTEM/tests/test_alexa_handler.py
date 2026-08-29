# MY_HOME_SYSTEM/tests/test_alexa_handler.py
"""
handlers/alexa_handler.py のテスト。

- _build_family_datasource() が quest_service の実データから正しいビューモデルを作ること
- LaunchRequestHandler が APL対応デバイスにはRenderDocumentディレクティブを、
  非対応デバイスには読み上げのみを返すこと
"""
import os
import sys
from unittest.mock import MagicMock

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import common
from handlers import alexa_handler


def _seed_users_and_pending():
    with common.get_db_cursor(commit=True) as cur:
        cur.execute("""
            INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, avatar, role)
            VALUES ('dad', 'パパ', 'warrior', 3, 40, 120, '🦸', 'role_adult')
        """)
        cur.execute("""
            INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, avatar, role)
            VALUES ('kid', 'たろう', 'mage', 2, 10, 30, '🧒', 'role_child')
        """)
        cur.execute("""
            INSERT INTO quest_history (user_id, quest_id, quest_title, exp_earned, gold_earned, completed_at, status)
            VALUES ('kid', 1, 'テスト', 5, 5, '2026-08-29T00:00:00', 'pending')
        """)


class TestBuildFamilyDatasource:
    def test_includes_all_users_with_expected_fields(self, isolated_db):
        _seed_users_and_pending()

        result = alexa_handler._build_family_datasource()

        assert result["title"] == "ファミリークエスト"
        assert result["pendingTotal"] == 1
        by_id = {u["userId"]: u for u in result["users"]}
        assert set(by_id) == {"dad", "kid"}
        assert by_id["dad"]["name"] == "パパ"
        assert by_id["dad"]["level"] == 3
        assert by_id["dad"]["gold"] == 120
        assert by_id["dad"]["pendingCount"] == 0
        assert by_id["kid"]["pendingCount"] == 1
        assert 0 <= by_id["dad"]["expPercent"] <= 100

    def test_no_users_returns_empty_list(self, isolated_db):
        result = alexa_handler._build_family_datasource()
        assert result["users"] == []
        assert result["pendingTotal"] == 0


def _make_handler_input(supports_apl: bool):
    handler_input = MagicMock()
    handler_input.request_envelope.request.object_type = "LaunchRequest"
    supported_interfaces = MagicMock()
    supported_interfaces.alexa_presentation_apl = object() if supports_apl else None
    handler_input.request_envelope.context.system.device.supported_interfaces = supported_interfaces
    handler_input.response_builder = MagicMock()
    handler_input.response_builder.speak.return_value = handler_input.response_builder
    handler_input.response_builder.add_directive.return_value = handler_input.response_builder
    handler_input.response_builder.set_should_end_session.return_value = handler_input.response_builder
    return handler_input


def _make_intent_handler_input(intent_name: str):
    from ask_sdk_model import IntentRequest, Intent

    handler_input = MagicMock()
    handler_input.request_envelope.request = IntentRequest(
        request_id="amzn1.echo-api.request.x",
        intent=Intent(name=intent_name),
    )
    handler_input.response_builder = MagicMock()
    handler_input.response_builder.speak.return_value = handler_input.response_builder
    handler_input.response_builder.set_should_end_session.return_value = handler_input.response_builder
    return handler_input


class TestLaunchRequestHandler:
    def test_can_handle_launch_request_only(self):
        handler = alexa_handler.LaunchRequestHandler()
        launch_input = _make_handler_input(supports_apl=True)
        assert handler.can_handle(launch_input) is True

        other_input = _make_handler_input(supports_apl=True)
        other_input.request_envelope.request.object_type = "SessionEndedRequest"
        assert handler.can_handle(other_input) is False

    def test_adds_apl_directive_when_supported(self, isolated_db):
        _seed_users_and_pending()
        handler = alexa_handler.LaunchRequestHandler()
        handler_input = _make_handler_input(supports_apl=True)

        handler.handle(handler_input)

        handler_input.response_builder.add_directive.assert_called_once()
        directive = handler_input.response_builder.add_directive.call_args[0][0]
        assert directive.datasources["payload"]["familyData"]["pendingTotal"] == 1
        speech = handler_input.response_builder.speak.call_args[0][0]
        assert "承認待ちのクエストが1件" in speech

    def test_falls_back_to_speech_when_apl_unsupported(self, isolated_db):
        _seed_users_and_pending()
        handler = alexa_handler.LaunchRequestHandler()
        handler_input = _make_handler_input(supports_apl=False)

        handler.handle(handler_input)

        handler_input.response_builder.add_directive.assert_not_called()
        speech = handler_input.response_builder.speak.call_args[0][0]
        assert "パパさんはレベル3" in speech
        assert "たろうさんはレベル2" in speech


class TestHelpIntentHandler:
    def test_can_handle_help_intent_only(self):
        handler = alexa_handler.HelpIntentHandler()
        assert handler.can_handle(_make_intent_handler_input("AMAZON.HelpIntent")) is True
        assert handler.can_handle(_make_intent_handler_input("AMAZON.StopIntent")) is False

    def test_speaks_help_and_keeps_session_open(self):
        handler = alexa_handler.HelpIntentHandler()
        handler_input = _make_intent_handler_input("AMAZON.HelpIntent")

        handler.handle(handler_input)

        handler_input.response_builder.speak.assert_called_once()
        handler_input.response_builder.set_should_end_session.assert_called_once_with(False)


class TestCancelOrStopIntentHandler:
    def test_can_handle_cancel_and_stop_intents(self):
        handler = alexa_handler.CancelOrStopIntentHandler()
        assert handler.can_handle(_make_intent_handler_input("AMAZON.CancelIntent")) is True
        assert handler.can_handle(_make_intent_handler_input("AMAZON.StopIntent")) is True
        assert handler.can_handle(_make_intent_handler_input("AMAZON.HelpIntent")) is False

    def test_ends_session(self):
        handler = alexa_handler.CancelOrStopIntentHandler()
        handler_input = _make_intent_handler_input("AMAZON.StopIntent")

        handler.handle(handler_input)

        handler_input.response_builder.set_should_end_session.assert_called_once_with(True)


class TestFallbackIntentHandler:
    def test_can_handle_fallback_intent_only(self):
        handler = alexa_handler.FallbackIntentHandler()
        assert handler.can_handle(_make_intent_handler_input("AMAZON.FallbackIntent")) is True
        assert handler.can_handle(_make_intent_handler_input("AMAZON.HelpIntent")) is False

    def test_speaks_and_keeps_session_open(self):
        handler = alexa_handler.FallbackIntentHandler()
        handler_input = _make_intent_handler_input("AMAZON.FallbackIntent")

        handler.handle(handler_input)

        handler_input.response_builder.speak.assert_called_once()
        handler_input.response_builder.set_should_end_session.assert_called_once_with(False)


class TestNavigateHomeIntentHandler:
    def test_can_handle_navigate_home_intent_only(self):
        handler = alexa_handler.NavigateHomeIntentHandler()
        assert handler.can_handle(_make_intent_handler_input("AMAZON.NavigateHomeIntent")) is True
        assert handler.can_handle(_make_intent_handler_input("AMAZON.HelpIntent")) is False

    def test_ends_session_without_speaking(self):
        handler = alexa_handler.NavigateHomeIntentHandler()
        handler_input = _make_intent_handler_input("AMAZON.NavigateHomeIntent")

        handler.handle(handler_input)

        handler_input.response_builder.speak.assert_not_called()
        handler_input.response_builder.set_should_end_session.assert_called_once_with(True)
