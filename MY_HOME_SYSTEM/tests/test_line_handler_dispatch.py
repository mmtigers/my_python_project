# MY_HOME_SYSTEM/tests/test_line_handler_dispatch.py
"""
handlers/line_handler.py のディスパッチロジック
(_process_message_async / handle_message / handle_postback) のテスト。

これらの関数は元々 `if line_handler:` ブロック内で条件付き定義されており、
LINE_CHANNEL_ACCESS_TOKEN/SECRET が設定されていない環境(CI含む)では
モジュール属性としてすら存在しないためテスト不能だった。
本セッションでの修正で、SDKへの登録(line_handler.add)のみを条件付きにし、
ロジック自体は常に定義されるよう分離したため、ここで直接テストできる。

実際のLINE API・AI(Gemini)サービスへは一切アクセスせず、呼び出し先を
全てモックし、主要な会話ステート遷移(コマンド分岐)が壊れていないことを検知する。
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from linebot.v3.messaging import TextMessage

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from handlers import line_handler


@pytest.fixture(autouse=True)
def _clean_profile_cache():
    line_handler._profile_cache.clear()
    yield
    line_handler._profile_cache.clear()


@pytest.mark.asyncio
class TestProcessMessageAsyncDispatch:
    async def test_status_command_dispatches_to_get_user_status_message(self, monkeypatch):
        mock_fn = AsyncMock(return_value=MagicMock(text="status-reply"))
        monkeypatch.setattr(line_handler.line_service, "get_user_status_message", mock_fn)
        mock_reply = MagicMock()
        monkeypatch.setattr(line_handler, "reply_message", mock_reply)

        await line_handler._process_message_async("U1", "太郎", "ステータス", "tok")

        mock_fn.assert_called_once_with("U1")
        assert mock_reply.call_args[0][0] == "tok"

    async def test_quest_command_dispatches_to_get_active_quests_message(self, monkeypatch):
        mock_fn = AsyncMock(return_value=MagicMock(text="quest-reply"))
        monkeypatch.setattr(line_handler.line_service, "get_active_quests_message", mock_fn)
        monkeypatch.setattr(line_handler, "reply_message", MagicMock())

        await line_handler._process_message_async("U1", "太郎", "クエスト", "tok")

        mock_fn.assert_called_once_with("U1")

    async def test_approve_prefix_dispatches_to_process_approval_command(self, monkeypatch):
        mock_fn = AsyncMock(return_value=MagicMock(text="approved"))
        monkeypatch.setattr(line_handler.line_service, "process_approval_command", mock_fn)
        monkeypatch.setattr(line_handler, "reply_message", MagicMock())

        await line_handler._process_message_async("U1", "太郎", "承認 5", "tok")

        mock_fn.assert_called_once_with("U1", "承認 5")

    async def test_reject_prefix_dispatches_to_process_approval_command(self, monkeypatch):
        mock_fn = AsyncMock(return_value=MagicMock(text="rejected"))
        monkeypatch.setattr(line_handler.line_service, "process_approval_command", mock_fn)
        monkeypatch.setattr(line_handler, "reply_message", MagicMock())

        await line_handler._process_message_async("U1", "太郎", "却下 5", "tok")

        mock_fn.assert_called_once_with("U1", "却下 5")

    async def test_child_health_message_detects_member_and_condition(self, monkeypatch):
        mock_fn = AsyncMock(return_value=MagicMock(text="logged"))
        monkeypatch.setattr(line_handler.line_service, "log_child_health", mock_fn)
        monkeypatch.setattr(line_handler, "reply_message", MagicMock())

        await line_handler._process_message_async("U1", "パパ", "体調 智矢 元気", "tok")

        mock_fn.assert_called_once_with("U1", "パパ", "智矢", "元気")

    async def test_child_health_message_detects_unwell_condition(self, monkeypatch):
        mock_fn = AsyncMock(return_value=MagicMock(text="logged"))
        monkeypatch.setattr(line_handler.line_service, "log_child_health", mock_fn)
        monkeypatch.setattr(line_handler, "reply_message", MagicMock())

        await line_handler._process_message_async("U1", "パパ", "子供記録 涼花 風邪", "tok")

        mock_fn.assert_called_once_with("U1", "パパ", "涼花", "風邪")

    async def test_child_health_message_defaults_to_unknown_condition(self, monkeypatch):
        mock_fn = AsyncMock(return_value=MagicMock(text="logged"))
        monkeypatch.setattr(line_handler.line_service, "log_child_health", mock_fn)
        monkeypatch.setattr(line_handler, "reply_message", MagicMock())

        await line_handler._process_message_async("U1", "パパ", "体調 智矢", "tok")

        mock_fn.assert_called_once_with("U1", "パパ", "智矢", "不明")

    async def test_health_keyword_without_known_member_falls_back_to_ai(self, monkeypatch):
        """「体調」を含んでいてもFAMILY_SETTINGSに無い名前ならAIフォールバックに委ねること。"""
        mock_health = AsyncMock()
        monkeypatch.setattr(line_handler.line_service, "log_child_health", mock_health)
        mock_ai = AsyncMock(return_value="AI reply")
        monkeypatch.setattr(line_handler.ai_service, "analyze_text_and_execute", mock_ai)
        monkeypatch.setattr(line_handler, "reply_message", MagicMock())

        await line_handler._process_message_async("U1", "パパ", "体調どう？", "tok")

        mock_health.assert_not_called()
        mock_ai.assert_called_once()

    async def test_falls_back_to_ai_when_no_known_command_matches(self, monkeypatch):
        mock_ai = AsyncMock(return_value="AIからの返答")
        monkeypatch.setattr(line_handler.ai_service, "analyze_text_and_execute", mock_ai)
        mock_reply = MagicMock()
        monkeypatch.setattr(line_handler, "reply_message", mock_reply)

        await line_handler._process_message_async("U1", "太郎", "こんにちは", "tok")

        mock_ai.assert_called_once_with("U1", "太郎", "こんにちは")
        mock_reply.assert_called_once()
        reply_arg = mock_reply.call_args[0][1]
        assert reply_arg.text == "AIからの返答"

    async def test_ai_returns_falsy_text_sends_no_reply(self, monkeypatch):
        mock_ai = AsyncMock(return_value="")
        monkeypatch.setattr(line_handler.ai_service, "analyze_text_and_execute", mock_ai)
        mock_reply = MagicMock()
        monkeypatch.setattr(line_handler, "reply_message", mock_reply)

        await line_handler._process_message_async("U1", "太郎", "こんにちは", "tok")

        mock_reply.assert_not_called()

    async def test_ai_exception_sends_generic_error_reply_without_raising(self, monkeypatch):
        mock_ai = AsyncMock(side_effect=Exception("Gemini API down"))
        monkeypatch.setattr(line_handler.ai_service, "analyze_text_and_execute", mock_ai)
        mock_reply = MagicMock()
        monkeypatch.setattr(line_handler, "reply_message", mock_reply)

        await line_handler._process_message_async("U1", "太郎", "こんにちは", "tok")

        mock_reply.assert_called_once()
        reply_arg = mock_reply.call_args[0][1]
        assert "うまく処理できませんでした" in reply_arg.text


class TestReplyMessage:
    def test_noop_when_line_api_not_configured(self, monkeypatch):
        monkeypatch.setattr(line_handler, "line_bot_api", None)
        # 例外を出さずに何もせず戻ること
        line_handler.reply_message("tok", TextMessage(text="hi"))

    def test_wraps_single_message_in_list_and_calls_api(self, monkeypatch):
        fake_api = MagicMock()
        monkeypatch.setattr(line_handler, "line_bot_api", fake_api)

        single_message = TextMessage(text="hi")
        line_handler.reply_message("tok", single_message)

        fake_api.reply_message.assert_called_once()
        request_arg = fake_api.reply_message.call_args[0][0]
        assert request_arg.reply_token == "tok"
        assert request_arg.messages == [single_message]

    def test_sdk_exception_is_caught_and_does_not_raise(self, monkeypatch):
        fake_api = MagicMock()
        fake_api.reply_message.side_effect = Exception("LINE API down")
        monkeypatch.setattr(line_handler, "line_bot_api", fake_api)

        line_handler.reply_message("tok", TextMessage(text="hi"))  # 例外が外に漏れないこと


class TestHandleMessageWrapper:
    def test_parses_event_strips_text_and_dispatches(self, monkeypatch):
        mock_fn = AsyncMock(return_value=MagicMock(text="reply"))
        monkeypatch.setattr(line_handler.line_service, "get_user_status_message", mock_fn)
        monkeypatch.setattr(line_handler, "reply_message", MagicMock())

        event = MagicMock()
        event.source.user_id = "U1"
        event.message.text = " ステータス "
        event.reply_token = "tok"

        line_handler.handle_message(event)

        mock_fn.assert_called_once_with("U1")


class TestHandlePostbackWrapper:
    def test_approve_postback_dispatches_as_approve_command(self, monkeypatch):
        mock_fn = AsyncMock(return_value=MagicMock(text="approved"))
        monkeypatch.setattr(line_handler.line_service, "process_approval_command", mock_fn)

        event = MagicMock()
        event.source.user_id = "U1"
        event.postback.data = "approve:42"
        event.reply_token = "tok"

        line_handler.handle_postback(event)

        mock_fn.assert_called_once_with("U1", "承認 42")

    def test_reject_postback_dispatches_as_reject_command(self, monkeypatch):
        mock_fn = AsyncMock(return_value=MagicMock(text="rejected"))
        monkeypatch.setattr(line_handler.line_service, "process_approval_command", mock_fn)

        event = MagicMock()
        event.source.user_id = "U1"
        event.postback.data = "reject:7"
        event.reply_token = "tok"

        line_handler.handle_postback(event)

        mock_fn.assert_called_once_with("U1", "却下 7")

    def test_malformed_approval_postback_is_caught_without_raising(self, monkeypatch):
        mock_fn = AsyncMock()
        monkeypatch.setattr(line_handler.line_service, "process_approval_command", mock_fn)

        event = MagicMock()
        event.source.user_id = "U1"
        event.postback.data = "approve:1:extra"
        event.reply_token = "tok"

        line_handler.handle_postback(event)

        mock_fn.assert_not_called()

    def test_non_approval_postback_delegates_to_line_logic(self, monkeypatch):
        mock_delegate = MagicMock()
        monkeypatch.setattr(line_handler.line_logic, "handle_postback", mock_delegate)

        event = MagicMock()
        event.source.user_id = "U1"
        event.postback.data = "show_health_input"
        event.reply_token = "tok"

        line_handler.handle_postback(event)

        mock_delegate.assert_called_once_with(event, line_handler.line_bot_api)

    def test_line_logic_delegation_exception_is_caught_silently(self, monkeypatch):
        monkeypatch.setattr(
            line_handler.line_logic, "handle_postback", MagicMock(side_effect=Exception("boom"))
        )

        event = MagicMock()
        event.source.user_id = "U1"
        event.postback.data = "some_other_action"
        event.reply_token = "tok"

        line_handler.handle_postback(event)  # 例外が外に漏れないこと


# ==========================================
# Issue #375: 「元気ない」の否定判定と2名併記時の全員記録
# ==========================================

@pytest.mark.asyncio
class TestHealthKeywordNegationAndMultipleNames:
    @pytest.mark.parametrize("phrase", ["元気ない", "元気がない", "元気なし", "元気じゃない"])
    async def test_negated_genki_is_not_recorded_as_genki(self, phrase, monkeypatch):
        mock_fn = AsyncMock(return_value=MagicMock(text="logged"))
        monkeypatch.setattr(line_handler.line_service, "log_child_health", mock_fn)
        monkeypatch.setattr(line_handler, "reply_message", MagicMock())

        await line_handler._process_message_async("U1", "パパ", f"体調 智矢 {phrase}", "tok")

        mock_fn.assert_called_once_with("U1", "パパ", "智矢", line_handler.CONDITION_NOT_GENKI)
        assert mock_fn.call_args.args[3] != "元気"

    async def test_plain_genki_still_recorded_as_genki(self, monkeypatch):
        mock_fn = AsyncMock(return_value=MagicMock(text="logged"))
        monkeypatch.setattr(line_handler.line_service, "log_child_health", mock_fn)
        monkeypatch.setattr(line_handler, "reply_message", MagicMock())

        await line_handler._process_message_async("U1", "パパ", "体調 智矢 元気いっぱい", "tok")

        mock_fn.assert_called_once_with("U1", "パパ", "智矢", "元気")

    async def test_two_names_with_different_conditions_are_both_recorded(self, monkeypatch):
        mock_fn = AsyncMock(side_effect=[MagicMock(text="r1"), MagicMock(text="r2")])
        monkeypatch.setattr(line_handler.line_service, "log_child_health", mock_fn)
        mock_reply = MagicMock()
        monkeypatch.setattr(line_handler, "reply_message", mock_reply)

        await line_handler._process_message_async("U1", "パパ", "体調 智矢 元気 涼花 風邪", "tok")

        assert mock_fn.call_count == 2
        assert mock_fn.call_args_list[0].args[2:] == ("智矢", "元気")
        assert mock_fn.call_args_list[1].args[2:] == ("涼花", "風邪")
        # 返信は1回にまとめ、2件分のメッセージを含む
        mock_reply.assert_called_once()
        assert len(mock_reply.call_args.args[1]) == 2

    async def test_two_names_share_condition_written_before_the_names(self, monkeypatch):
        """「体調 元気 智矢 涼花」のように名前より前にキーワードがある書き方は全員に適用"""
        mock_fn = AsyncMock(return_value=MagicMock(text="logged"))
        monkeypatch.setattr(line_handler.line_service, "log_child_health", mock_fn)
        monkeypatch.setattr(line_handler, "reply_message", MagicMock())

        await line_handler._process_message_async("U1", "パパ", "体調 元気 智矢 涼花", "tok")

        assert mock_fn.call_count == 2
        assert {c.args[2] for c in mock_fn.call_args_list} == {"智矢", "涼花"}
        assert all(c.args[3] == "元気" for c in mock_fn.call_args_list)

    async def test_two_names_one_negated_one_positive(self, monkeypatch):
        mock_fn = AsyncMock(return_value=MagicMock(text="logged"))
        monkeypatch.setattr(line_handler.line_service, "log_child_health", mock_fn)
        monkeypatch.setattr(line_handler, "reply_message", MagicMock())

        await line_handler._process_message_async("U1", "パパ", "体調 智矢 元気ない 涼花 元気", "tok")

        conds = {c.args[2]: c.args[3] for c in mock_fn.call_args_list}
        assert conds == {"智矢": line_handler.CONDITION_NOT_GENKI, "涼花": "元気"}

    async def test_save_failure_message_for_one_child_is_included_in_reply(self, monkeypatch):
        """#373 の失敗メッセージがそのまま返信に含まれ、成功分と一緒に送られること"""
        mock_fn = AsyncMock(side_effect=[MagicMock(text="ok"), MagicMock(text="⚠️ 記録に失敗しました")])
        monkeypatch.setattr(line_handler.line_service, "log_child_health", mock_fn)
        mock_reply = MagicMock()
        monkeypatch.setattr(line_handler, "reply_message", mock_reply)

        await line_handler._process_message_async("U1", "パパ", "体調 智矢 元気 涼花 元気", "tok")

        texts = [m.text for m in mock_reply.call_args.args[1]]
        assert texts == ["ok", "⚠️ 記録に失敗しました"]


class TestDetectConditionKeyword:
    def test_negation_takes_priority_over_positive(self):
        assert line_handler._detect_condition_keyword("元気がない") == line_handler.CONDITION_NOT_GENKI

    def test_positive(self):
        assert line_handler._detect_condition_keyword("今日は元気") == "元気"

    def test_cold(self):
        assert line_handler._detect_condition_keyword("風邪気味") == "風邪"

    def test_unknown(self):
        assert line_handler._detect_condition_keyword("特になし") == "不明"

    def test_extract_targets_returns_empty_when_no_member(self):
        assert line_handler._extract_health_targets("体調どう？") == []


# ==========================================
# Issue #376: 再配信スキップ・イベント単位の例外隔離・AI経路の時間上限・reply→push フォールバック
# ==========================================
import asyncio as _asyncio


class TestIsRedelivery:
    def test_true_only_when_sdk_flag_is_strictly_true(self):
        event = MagicMock()
        event.delivery_context.is_redelivery = True
        assert line_handler._is_redelivery(event) is True

    def test_false_when_flag_false(self):
        event = MagicMock()
        event.delivery_context.is_redelivery = False
        assert line_handler._is_redelivery(event) is False

    def test_false_when_context_missing(self):
        event = MagicMock(spec=[])
        assert line_handler._is_redelivery(event) is False

    def test_unset_magicmock_attribute_is_not_treated_as_redelivery(self):
        """既存テストの MagicMock イベント(属性未設定)が誤って再配信扱いされないこと"""
        assert line_handler._is_redelivery(MagicMock()) is False


class TestHandleMessageIsolationAndRedelivery:
    def _event(self, text="ステータス", is_redelivery=None):
        event = MagicMock()
        event.source.user_id = "U1"
        event.message.text = text
        event.reply_token = "tok"
        if is_redelivery is not None:
            event.delivery_context.is_redelivery = is_redelivery
        return event

    def test_redelivered_message_is_skipped(self, monkeypatch):
        mock_process = AsyncMock()
        monkeypatch.setattr(line_handler, "_process_message_async", mock_process)

        line_handler.handle_message(self._event(is_redelivery=True))

        mock_process.assert_not_called()

    def test_non_redelivered_message_is_processed(self, monkeypatch):
        mock_process = AsyncMock()
        monkeypatch.setattr(line_handler, "_process_message_async", mock_process)

        line_handler.handle_message(self._event(is_redelivery=False))

        mock_process.assert_called_once()

    def test_exception_inside_processing_does_not_propagate(self, monkeypatch):
        """L-L1: 1件目の例外で SDK の handle() ループが止まらないよう、ハンドラ内で握る"""
        monkeypatch.setattr(line_handler, "_process_message_async", AsyncMock(side_effect=RuntimeError("boom")))

        line_handler.handle_message(self._event())  # 例外が外に漏れないこと

    def test_postback_redelivery_is_skipped(self, monkeypatch):
        mock_delegate = MagicMock()
        monkeypatch.setattr(line_handler.line_logic, "handle_postback", mock_delegate)
        event = MagicMock()
        event.source.user_id = "U1"
        event.postback.data = "action=check_status"
        event.reply_token = "tok"
        event.delivery_context.is_redelivery = True

        line_handler.handle_postback(event)

        mock_delegate.assert_not_called()

    def test_postback_exception_outside_inner_try_does_not_propagate(self, monkeypatch):
        monkeypatch.setattr(
            line_handler.line_service, "process_approval_command", AsyncMock(side_effect=RuntimeError("boom"))
        )
        event = MagicMock()
        event.source.user_id = "U1"
        event.postback.data = "approve:42"
        event.reply_token = "tok"

        line_handler.handle_postback(event)  # 例外が外に漏れないこと


@pytest.mark.asyncio
class TestAiPathTimeBudget:
    async def test_ai_exceeding_budget_gets_timeout_reply_instead_of_silence(self, monkeypatch):
        async def slow_ai(*args, **kwargs):
            await _asyncio.sleep(1)
            return "遅い応答"

        monkeypatch.setattr(line_handler.ai_service, "analyze_text_and_execute", slow_ai)
        monkeypatch.setattr(line_handler, "AI_REPLY_TIMEOUT_SEC", 0.05)
        mock_reply = MagicMock()
        monkeypatch.setattr(line_handler, "reply_message", mock_reply)

        await line_handler._process_message_async("U1", "太郎", "こんにちは", "tok")

        mock_reply.assert_called_once()
        assert "中断" in mock_reply.call_args.args[1].text
        assert mock_reply.call_args.kwargs.get("user_id") == "U1"

    async def test_ai_within_budget_replies_normally(self, monkeypatch):
        monkeypatch.setattr(line_handler.ai_service, "analyze_text_and_execute", AsyncMock(return_value="OK"))
        monkeypatch.setattr(line_handler, "AI_REPLY_TIMEOUT_SEC", 5)
        mock_reply = MagicMock()
        monkeypatch.setattr(line_handler, "reply_message", mock_reply)

        await line_handler._process_message_async("U1", "太郎", "こんにちは", "tok")

        assert mock_reply.call_args.args[1].text == "OK"

    async def test_default_budget_is_about_20_seconds(self):
        assert 10 <= line_handler.AI_REPLY_TIMEOUT_SEC <= 30

    async def test_every_reply_path_passes_user_id_for_push_fallback(self, monkeypatch):
        monkeypatch.setattr(
            line_handler.line_service, "get_user_status_message", AsyncMock(return_value=MagicMock(text="s"))
        )
        mock_reply = MagicMock()
        monkeypatch.setattr(line_handler, "reply_message", mock_reply)

        await line_handler._process_message_async("U9", "太郎", "ステータス", "tok")

        assert mock_reply.call_args.kwargs.get("user_id") == "U9"


class TestReplyMessagePushFallback:
    def test_falls_back_to_push_when_reply_fails_and_user_id_known(self, monkeypatch):
        fake_api = MagicMock()
        fake_api.reply_message.side_effect = Exception("Invalid reply token")
        monkeypatch.setattr(line_handler, "line_bot_api", fake_api)
        msg = TextMessage(text="結果")

        line_handler.reply_message("expired-tok", msg, user_id="U1")

        fake_api.push_message.assert_called_once()
        req = fake_api.push_message.call_args.args[0]
        assert req.to == "U1"
        assert req.messages == [msg]

    def test_no_push_when_reply_succeeds(self, monkeypatch):
        fake_api = MagicMock()
        monkeypatch.setattr(line_handler, "line_bot_api", fake_api)

        line_handler.reply_message("tok", TextMessage(text="hi"), user_id="U1")

        fake_api.push_message.assert_not_called()

    def test_no_push_when_user_id_unknown(self, monkeypatch):
        fake_api = MagicMock()
        fake_api.reply_message.side_effect = Exception("Invalid reply token")
        monkeypatch.setattr(line_handler, "line_bot_api", fake_api)

        line_handler.reply_message("tok", TextMessage(text="hi"))

        fake_api.push_message.assert_not_called()

    def test_push_failure_is_swallowed(self, monkeypatch):
        fake_api = MagicMock()
        fake_api.reply_message.side_effect = Exception("Invalid reply token")
        fake_api.push_message.side_effect = Exception("quota exceeded")
        monkeypatch.setattr(line_handler, "line_bot_api", fake_api)

        line_handler.reply_message("tok", TextMessage(text="hi"), user_id="U1")  # 例外が外に漏れないこと


@pytest.mark.asyncio
class TestAiReplyTextLengthLimit:
    """Issue #377: Gemini応答がLINEの5000字制限を超える場合の分割/切り詰め"""

    async def test_long_ai_response_is_split_before_reply(self, monkeypatch):
        long_text = "x" * 12000
        monkeypatch.setattr(line_handler.ai_service, "analyze_text_and_execute", AsyncMock(return_value=long_text))
        mock_reply = MagicMock()
        monkeypatch.setattr(line_handler, "reply_message", mock_reply)

        await line_handler._process_message_async("U1", "太郎", "教えて", "tok")

        sent = mock_reply.call_args.args[1]
        assert isinstance(sent, list)
        assert len(sent) <= line_handler.line_service.LINE_MAX_MESSAGES_PER_REPLY
        for m in sent:
            assert len(m.text) <= line_handler.line_service.LINE_TEXT_MAX_CHARS

    async def test_short_ai_response_is_still_a_single_text_message(self, monkeypatch):
        monkeypatch.setattr(line_handler.ai_service, "analyze_text_and_execute", AsyncMock(return_value="短い返事"))
        mock_reply = MagicMock()
        monkeypatch.setattr(line_handler, "reply_message", mock_reply)

        await line_handler._process_message_async("U1", "太郎", "こんにちは", "tok")

        sent = mock_reply.call_args.args[1]
        assert sent.text == "短い返事"
