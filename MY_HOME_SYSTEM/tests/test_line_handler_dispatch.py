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
    # #358: LINE経由のFamily Questコマンド(ステータス/クエスト/承認/却下)は、
    # LINE ID を quest_users.user_id へマッピングする仕組みが無く本番で機能しない
    # デッドコードだったため撤去した(オーナー判断)。これらの文言はAIフォールバックへ
    # 委ねられることを regression として確認しておく。
    async def test_status_like_text_falls_back_to_ai(self, monkeypatch):
        mock_ai = AsyncMock(return_value="AI reply")
        monkeypatch.setattr(line_handler.ai_service, "analyze_text_and_execute", mock_ai)
        monkeypatch.setattr(line_handler, "reply_message", MagicMock())

        await line_handler._process_message_async("U1", "太郎", "ステータス", "tok")

        mock_ai.assert_called_once_with("U1", "太郎", "ステータス")

    async def test_approve_prefix_text_falls_back_to_ai(self, monkeypatch):
        mock_ai = AsyncMock(return_value="AI reply")
        monkeypatch.setattr(line_handler.ai_service, "analyze_text_and_execute", mock_ai)
        monkeypatch.setattr(line_handler, "reply_message", MagicMock())

        await line_handler._process_message_async("U1", "太郎", "承認 5", "tok")

        mock_ai.assert_called_once_with("U1", "太郎", "承認 5")

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
        mock_logger = MagicMock()
        monkeypatch.setattr(line_handler, "logger", mock_logger)

        line_handler.reply_message("tok", TextMessage(text="hi"))  # 例外が外に漏れないこと

        # C-L4 (Issue #414): 例外経路に実際に到達し、握りつぶさずログに残していること
        fake_api.reply_message.assert_called_once()
        mock_logger.error.assert_called_once()
        assert "LINE Reply Failed" in mock_logger.error.call_args[0][0]


class TestHandleMessageWrapper:
    def test_parses_event_strips_text_and_dispatches(self, monkeypatch):
        mock_process = AsyncMock()
        monkeypatch.setattr(line_handler, "_process_message_async", mock_process)

        event = MagicMock()
        event.source.user_id = "U1"
        event.message.text = " こんにちは "
        event.reply_token = "tok"

        line_handler.handle_message(event)

        mock_process.assert_called_once()
        call_args = mock_process.call_args[0]
        assert call_args[0] == "U1"
        assert call_args[2] == "こんにちは"
        assert call_args[3] == "tok"


class TestHandlePostbackWrapper:
    # #358: approve:/reject: postback の専用処理(LINE経由のクエスト承認/却下)は、
    # それを生成する送信元がリポジトリ内に存在しないデッドコードだったため撤去した。
    # そのようなデータが届いても既存ロジック(line_logic.py)への委譲に流れることを
    # regression として確認する。
    def test_approve_like_postback_delegates_to_line_logic(self, monkeypatch):
        mock_delegate = MagicMock()
        monkeypatch.setattr(line_handler.line_logic, "handle_postback", mock_delegate)

        event = MagicMock()
        event.source.user_id = "U1"
        event.postback.data = "approve:42"
        event.reply_token = "tok"

        line_handler.handle_postback(event)

        mock_delegate.assert_called_once_with(event, line_handler.line_bot_api)

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
        mock_delegate = MagicMock(side_effect=Exception("boom"))
        monkeypatch.setattr(line_handler.line_logic, "handle_postback", mock_delegate)
        mock_logger = MagicMock()
        monkeypatch.setattr(line_handler, "logger", mock_logger)

        event = MagicMock()
        event.source.user_id = "U1"
        event.postback.data = "some_other_action"
        event.reply_token = "tok"

        line_handler.handle_postback(event)  # 例外が外に漏れないこと

        # C-L4 (Issue #414): 委譲先に到達したうえで例外を捕捉し、ログに残していること
        mock_delegate.assert_called_once()
        mock_logger.error.assert_called_once()
        assert "Logic Delegation Error" in mock_logger.error.call_args[0][0]


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
            line_handler.line_logic, "handle_postback", MagicMock(side_effect=RuntimeError("boom"))
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
            line_handler.ai_service, "analyze_text_and_execute", AsyncMock(return_value="s")
        )
        mock_reply = MagicMock()
        monkeypatch.setattr(line_handler, "reply_message", mock_reply)

        await line_handler._process_message_async("U9", "太郎", "こんにちは", "tok")

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


class TestHandleMessageUserIdNoneGuard:
    """L-L6 (#410) の回帰テスト: グループ発言でuser_idが取得できない(None)場合、
    get_profile(None)の例外を握り潰したまま処理を続行しuser_id=NULLでDB保存
    してしまわないよう、早期にスキップすること。"""

    def _group_event_without_user_id(self, text="ステータス"):
        event = MagicMock()
        event.source.user_id = None
        event.message.text = text
        event.reply_token = "tok"
        event.delivery_context.is_redelivery = False
        return event

    def test_message_without_user_id_is_skipped_before_processing(self, monkeypatch):
        mock_process = AsyncMock()
        monkeypatch.setattr(line_handler, "_process_message_async", mock_process)
        mock_display_name = MagicMock()
        monkeypatch.setattr(line_handler, "_get_display_name", mock_display_name)

        line_handler.handle_message(self._group_event_without_user_id())

        mock_process.assert_not_called()
        mock_display_name.assert_not_called()

    def test_message_with_user_id_present_is_processed_normally(self, monkeypatch):
        mock_process = AsyncMock()
        monkeypatch.setattr(line_handler, "_process_message_async", mock_process)
        event = MagicMock()
        event.source.user_id = "U1"
        event.message.text = "ステータス"
        event.reply_token = "tok"
        event.delivery_context.is_redelivery = False

        line_handler.handle_message(event)

        mock_process.assert_called_once()


class TestProfileCacheBounding:
    """保守性(#410) の回帰テスト: _profile_cacheが上限を超えて無制限に成長しないこと。"""

    def setup_method(self):
        line_handler._profile_cache.clear()

    def teardown_method(self):
        line_handler._profile_cache.clear()

    def test_cache_does_not_exceed_max_size(self, monkeypatch):
        fake_api = MagicMock()
        fake_api.get_profile.side_effect = lambda uid: MagicMock(display_name=f"User_{uid}")
        monkeypatch.setattr(line_handler, "line_bot_api", fake_api)
        monkeypatch.setattr(line_handler, "_PROFILE_CACHE_MAX_SIZE", 5)

        for i in range(10):
            line_handler._get_display_name(f"U{i}")

        assert len(line_handler._profile_cache) == 5

    def test_oldest_entries_are_evicted_first(self, monkeypatch):
        fake_api = MagicMock()
        fake_api.get_profile.side_effect = lambda uid: MagicMock(display_name=f"User_{uid}")
        monkeypatch.setattr(line_handler, "line_bot_api", fake_api)
        monkeypatch.setattr(line_handler, "_PROFILE_CACHE_MAX_SIZE", 3)

        base_time = 1_700_000_000.0
        for i in range(5):
            monkeypatch.setattr(line_handler.time, "time", lambda t=base_time, i=i: t + i)
            line_handler._get_display_name(f"U{i}")

        # 最初の2件(U0, U1)は最も古いため削除され、直近3件(U2,U3,U4)が残る
        assert set(line_handler._profile_cache.keys()) == {"U2", "U3", "U4"}

    def test_cache_hit_does_not_trigger_unnecessary_eviction_churn(self, monkeypatch):
        """キャッシュヒット時はAPIを叩かず_profile_cacheへの書き込みも発生しないため、
        エントリ数が増えないこと(既存の動作の確認)。"""
        fake_api = MagicMock()
        fake_api.get_profile.return_value = MagicMock(display_name="太郎")
        monkeypatch.setattr(line_handler, "line_bot_api", fake_api)
        monkeypatch.setattr(line_handler, "_PROFILE_CACHE_MAX_SIZE", 500)

        line_handler._get_display_name("U1")
        line_handler._get_display_name("U1")
        line_handler._get_display_name("U1")

        assert len(line_handler._profile_cache) == 1
        assert fake_api.get_profile.call_count == 1


# ==========================================
# Issue #376: webhookEventIdベースの冪等化 (_is_duplicate_event) と
# dispatch_events (BackgroundTasksから呼ばれる実処理エントリポイント)
# ==========================================

from linebot.v3.webhooks import MessageEvent, PostbackEvent


class TestIsDuplicateEvent:
    def setup_method(self):
        line_handler._SEEN_EVENT_IDS.clear()

    def teardown_method(self):
        line_handler._SEEN_EVENT_IDS.clear()

    def test_first_occurrence_is_not_duplicate(self):
        event = MagicMock()
        event.webhook_event_id = "evt-1"

        assert line_handler._is_duplicate_event(event) is False
        assert "evt-1" in line_handler._SEEN_EVENT_IDS

    def test_second_occurrence_of_same_id_is_duplicate(self):
        event = MagicMock()
        event.webhook_event_id = "evt-1"

        assert line_handler._is_duplicate_event(event) is False
        assert line_handler._is_duplicate_event(event) is True

    def test_missing_webhook_event_id_is_never_treated_as_duplicate(self):
        """冪等化キーが取得できないイベント(想定外の形式)は誤って処理を止めない"""
        event = MagicMock(spec=[])  # webhook_event_id属性を持たない

        assert line_handler._is_duplicate_event(event) is False
        assert line_handler._is_duplicate_event(event) is False

    def test_cache_does_not_exceed_max_size(self, monkeypatch):
        monkeypatch.setattr(line_handler, "_SEEN_EVENT_IDS_MAX_SIZE", 5)

        for i in range(10):
            event = MagicMock()
            event.webhook_event_id = f"evt-{i}"
            line_handler._is_duplicate_event(event)

        assert len(line_handler._SEEN_EVENT_IDS) == 5

    def test_oldest_entries_are_evicted_first(self, monkeypatch):
        monkeypatch.setattr(line_handler, "_SEEN_EVENT_IDS_MAX_SIZE", 3)

        base_time = 1_700_000_000.0
        for i in range(5):
            monkeypatch.setattr(line_handler.time, "time", lambda t=base_time, i=i: t + i)
            event = MagicMock()
            event.webhook_event_id = f"evt-{i}"
            line_handler._is_duplicate_event(event)

        assert set(line_handler._SEEN_EVENT_IDS.keys()) == {"evt-2", "evt-3", "evt-4"}


class TestDispatchEvents:
    """
    Issue #376: /callback/line がHTTP応答を返した後、BackgroundTasksから呼ばれる
    dispatch_events の振り分け・冪等化・イベント単位の例外隔離。
    """

    def setup_method(self):
        line_handler._SEEN_EVENT_IDS.clear()

    def teardown_method(self):
        line_handler._SEEN_EVENT_IDS.clear()

    def _message_event(self, event_id="evt-msg", text="ステータス", user_id="U1"):
        # spec付きMagicMockはクラス自体(dir(MessageEvent))に無い属性の代入・参照が
        # できない(pydanticフィールドはインスタンス生成時にしか現れないため)ため、
        # SDKのfrom_dict()で本物のインスタンスを組み立てる
        # (isinstance()判定・webhook_event_id参照を正しく満たすため)。
        return MessageEvent.from_dict({
            "type": "message",
            "mode": "active",
            "timestamp": 1700000000000,
            "source": {"type": "user", "userId": user_id},
            "webhookEventId": event_id,
            "deliveryContext": {"isRedelivery": False},
            "replyToken": "tok",
            "message": {"id": "m1", "type": "text", "text": text, "quoteToken": "q"},
        })

    def _postback_event(self, event_id="evt-pb", data="show_health_input", user_id="U1"):
        return PostbackEvent.from_dict({
            "type": "postback",
            "mode": "active",
            "timestamp": 1700000000000,
            "source": {"type": "user", "userId": user_id},
            "webhookEventId": event_id,
            "deliveryContext": {"isRedelivery": False},
            "replyToken": "tok",
            "postback": {"data": data},
        })

    def test_message_event_is_routed_to_handle_message(self, monkeypatch):
        mock_handle_message = MagicMock()
        monkeypatch.setattr(line_handler, "handle_message", mock_handle_message)
        event = self._message_event()

        line_handler.dispatch_events([event])

        mock_handle_message.assert_called_once_with(event)

    def test_postback_event_is_routed_to_handle_postback(self, monkeypatch):
        mock_handle_postback = MagicMock()
        monkeypatch.setattr(line_handler, "handle_postback", mock_handle_postback)
        event = self._postback_event()

        line_handler.dispatch_events([event])

        mock_handle_postback.assert_called_once_with(event)

    def test_unknown_event_type_is_ignored(self, monkeypatch):
        """MessageEvent/PostbackEvent以外(follow等)は元のWebhookHandlerと同様に無視される"""
        mock_handle_message = MagicMock()
        mock_handle_postback = MagicMock()
        monkeypatch.setattr(line_handler, "handle_message", mock_handle_message)
        monkeypatch.setattr(line_handler, "handle_postback", mock_handle_postback)
        other_event = MagicMock()  # MessageEvent/PostbackEventいずれのspecでもない

        line_handler.dispatch_events([other_event])

        mock_handle_message.assert_not_called()
        mock_handle_postback.assert_not_called()

    def test_duplicate_webhook_event_id_is_skipped(self, monkeypatch):
        mock_handle_message = MagicMock()
        monkeypatch.setattr(line_handler, "handle_message", mock_handle_message)
        event1 = self._message_event(event_id="dup", text="ステータス")
        event2 = self._message_event(event_id="dup", text="ステータス")

        line_handler.dispatch_events([event1, event2])

        mock_handle_message.assert_called_once_with(event1)

    def test_exception_in_one_event_does_not_block_the_rest(self, monkeypatch):
        """dispatch_events自身のループレベルでもイベント単位の例外隔離を二重に保証する"""
        mock_handle_message = MagicMock(side_effect=[RuntimeError("boom"), None])
        monkeypatch.setattr(line_handler, "handle_message", mock_handle_message)
        event1 = self._message_event(event_id="e1")
        event2 = self._message_event(event_id="e2")

        line_handler.dispatch_events([event1, event2])  # 例外が外に漏れないこと

        assert mock_handle_message.call_count == 2

    def test_empty_events_list_is_a_noop(self):
        line_handler.dispatch_events([])  # 例外が出ないこと
