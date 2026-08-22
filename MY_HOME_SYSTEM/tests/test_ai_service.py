# MY_HOME_SYSTEM/tests/test_ai_service.py
"""
services/ai_service.py のテスト。

- tool_search_db のテーブル許可リスト(既存)
- SimpleRateLimiter のウィンドウ制御
- analyze_text_and_execute のオーケストレーション分岐(APIキー無し/レート制限/
  ツール呼び出しディスパッチ/未知ツール/空応答/ResourceExhausted/GoogleAPIError/
  汎用例外)
- tool_record_child_health / tool_record_food のline_serviceへの委譲
- _call_gemini_api_with_retry のtenacityリトライ挙動

実際のGemini API・LINE APIへは一切アクセスしない。
_call_gemini_api_with_retry自体を直接差し替えることで、analyze_text_and_execute
の分岐ロジックをgoogle.generativeaiの内部構造から切り離してテストする。
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from google.api_core.exceptions import GoogleAPIError, ResourceExhausted

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from services import ai_service


def make_response(function_call=None, text="hello", parts_present=True):
    """genai の GenerateContentResponse を模したモックを作る。
    function_call=None の場合は必ず明示的にNoneを設定する(未設定のMagicMockは
    truthyなので「関数呼び出し無し」の分岐を誤って通ってしまうため)。"""
    resp = MagicMock()
    resp.text = text
    if not parts_present:
        resp.parts = []
        return resp
    part = MagicMock()
    part.function_call = function_call
    resp.parts = [part]
    return resp


def make_function_call(name, args):
    fc = MagicMock()
    fc.name = name
    fc.args = args
    return fc


@pytest.fixture
def no_retry_sleep(monkeypatch):
    """tenacityの実待機を無効化する(stop_after_attempt等のリトライ回数ロジックは実物のまま)。"""
    monkeypatch.setattr(
        ai_service._call_gemini_api_with_retry.retry, "sleep", AsyncMock(return_value=None)
    )


@pytest.fixture
def ai_configured(monkeypatch):
    """APIキー設定済み状態を模す(モジュールimport時に決まるMODEL_NAMEを上書き)。"""
    monkeypatch.setattr(config, "GEMINI_API_KEY", "fake-key-for-test")
    monkeypatch.setattr(ai_service, "MODEL_NAME", "gemini-2.0-flash")


def test_extract_referenced_tables_simple_select():
    sql = "SELECT * FROM child_health_records WHERE child_name = 'A'"
    assert ai_service._extract_referenced_tables(sql) == ["child_health_records"]


def test_extract_referenced_tables_with_join():
    sql = "SELECT a.* FROM food_records a JOIN quest_users b ON a.user_id = b.user_id"
    tables = ai_service._extract_referenced_tables(sql)
    assert "food_records" in tables
    assert "quest_users" in tables


def test_extract_referenced_tables_comma_join_catches_second_table():
    """H-6: 暗黙CROSS JOIN(カンマ結合)の2つ目以降のテーブルも抽出できること"""
    sql = "SELECT * FROM child_health_records, quest_users"
    tables = ai_service._extract_referenced_tables(sql)
    assert "child_health_records" in tables
    assert "quest_users" in tables


def test_extract_referenced_tables_comma_join_three_tables():
    sql = "SELECT * FROM a, b, c WHERE a.id = b.id"
    tables = ai_service._extract_referenced_tables(sql)
    assert tables == ["a", "b", "c"]


def test_extract_referenced_tables_subquery_is_detected():
    """H-6: サブクエリ内のFROMも抽出できること(隠れたテーブル参照のバイパス防止)"""
    sql = "SELECT * FROM (SELECT * FROM quest_users) AS hidden"
    tables = ai_service._extract_referenced_tables(sql)
    assert "quest_users" in tables


@pytest.mark.asyncio
async def test_tool_search_db_rejects_non_select():
    result = await ai_service.tool_search_db({"sql_query": "DELETE FROM quest_users"})
    assert "許可されていません" in result


@pytest.mark.asyncio
async def test_tool_search_db_blocks_disallowed_table():
    # quest_users はドキュメント記載の許可テーブル(child/food/shopping/power_usage)に含まれない
    result = await ai_service.tool_search_db({"sql_query": "SELECT * FROM quest_users"})
    assert "許可されていない" in result


@pytest.mark.asyncio
async def test_tool_search_db_blocks_comma_joined_disallowed_table():
    """H-6の回帰防止: 許可テーブルとのカンマ結合で quest_users を素通りさせない"""
    table = next(iter(ai_service.ALLOWED_SEARCH_TABLES))
    result = await ai_service.tool_search_db({"sql_query": f"SELECT * FROM {table}, quest_users"})
    assert "許可されていない" in result


@pytest.mark.asyncio
async def test_tool_search_db_blocks_subquery_disallowed_table():
    """H-6の回帰防止: サブクエリ経由で quest_users を素通りさせない"""
    table = next(iter(ai_service.ALLOWED_SEARCH_TABLES))
    result = await ai_service.tool_search_db(
        {"sql_query": f"SELECT * FROM (SELECT * FROM quest_users) AS hidden, {table}"}
    )
    assert "許可されていない" in result


@pytest.mark.asyncio
async def test_tool_search_db_allows_documented_table(monkeypatch):
    monkeypatch.setattr(ai_service.common, "execute_read_query", lambda sql, params=(): "OK")
    table = next(iter(ai_service.ALLOWED_SEARCH_TABLES))
    result = await ai_service.tool_search_db({"sql_query": f"SELECT * FROM {table}"})
    assert result == "OK"


@pytest.mark.asyncio
async def test_tool_search_db_empty_query_returns_guidance_message():
    result = await ai_service.tool_search_db({})
    assert "指定されていません" in result


@pytest.mark.asyncio
async def test_tool_search_db_without_from_or_join_returns_error():
    result = await ai_service.tool_search_db({"sql_query": "SELECT 1"})
    assert "参照テーブルを特定できませんでした" in result


@pytest.mark.asyncio
async def test_tool_search_db_no_matching_rows_returns_not_found_message(monkeypatch):
    monkeypatch.setattr(ai_service.common, "execute_read_query", lambda sql, params=(): [])
    table = next(iter(ai_service.ALLOWED_SEARCH_TABLES))
    result = await ai_service.tool_search_db({"sql_query": f"SELECT * FROM {table}"})
    assert "見つかりませんでした" in result


@pytest.mark.asyncio
async def test_tool_search_db_query_exception_is_caught_and_returns_error_string(monkeypatch):
    def _raise(sql, params=()):
        raise Exception("malformed SQL")

    monkeypatch.setattr(ai_service.common, "execute_read_query", _raise)
    table = next(iter(ai_service.ALLOWED_SEARCH_TABLES))
    result = await ai_service.tool_search_db({"sql_query": f"SELECT * FROM {table}"})
    assert "DB検索エラー" in result


class TestSimpleRateLimiter:
    @pytest.mark.asyncio
    async def test_allows_requests_under_limit(self):
        limiter = ai_service.SimpleRateLimiter(limit=2)
        assert await limiter.allow_request() is True
        assert await limiter.allow_request() is True

    @pytest.mark.asyncio
    async def test_denies_request_once_limit_reached(self):
        limiter = ai_service.SimpleRateLimiter(limit=2)
        await limiter.allow_request()
        await limiter.allow_request()
        assert await limiter.allow_request() is False

    @pytest.mark.asyncio
    async def test_resets_after_60_seconds_window(self, monkeypatch):
        limiter = ai_service.SimpleRateLimiter(limit=1)
        fake_now = [1_700_000_000.0]
        monkeypatch.setattr(ai_service.time, "time", lambda: fake_now[0])
        limiter.last_reset_time = fake_now[0]

        assert await limiter.allow_request() is True
        assert await limiter.allow_request() is False  # 上限到達

        fake_now[0] += 61  # 60秒経過
        assert await limiter.allow_request() is True  # リセットされ再度許可される


class TestToolRecordFunctions:
    @pytest.mark.asyncio
    async def test_tool_record_child_health_delegates_to_line_service(self, monkeypatch):
        mock_log = AsyncMock(return_value=MagicMock(text="保存しました"))
        monkeypatch.setattr(ai_service.line_service, "log_child_health", mock_log)

        result = await ai_service.tool_record_child_health(
            "U1", "太郎", {"child_name": "智矢", "condition": "元気"}
        )

        mock_log.assert_called_once_with("U1", "太郎", "智矢", "元気")
        assert result == "記録完了: 保存しました"

    @pytest.mark.asyncio
    async def test_tool_record_food_delegates_to_line_service(self, monkeypatch):
        mock_log = AsyncMock(return_value=MagicMock(text="カレー保存済み"))
        monkeypatch.setattr(ai_service.line_service, "log_food_record", mock_log)

        result = await ai_service.tool_record_food(
            "U1", "太郎", {"item": "カレー", "category": "夕食"}
        )

        mock_log.assert_called_once_with("U1", "太郎", "夕食", "カレー", is_manual=True)
        assert result == "記録完了: カレー保存済み"

    @pytest.mark.asyncio
    async def test_tool_record_food_defaults_category_when_missing(self, monkeypatch):
        mock_log = AsyncMock(return_value=MagicMock(text="保存済み"))
        monkeypatch.setattr(ai_service.line_service, "log_food_record", mock_log)

        await ai_service.tool_record_food("U1", "太郎", {"item": "カレー"})

        mock_log.assert_called_once_with("U1", "太郎", "その他", "カレー", is_manual=True)


class TestAnalyzeTextAndExecute:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_api_key_configured(self, monkeypatch):
        monkeypatch.setattr(config, "GEMINI_API_KEY", None)
        monkeypatch.setattr(ai_service, "MODEL_NAME", None)

        result = await ai_service.analyze_text_and_execute("U1", "太郎", "こんにちは")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_fallback_message_when_rate_limited(self, ai_configured, monkeypatch):
        monkeypatch.setattr(ai_service.rate_limiter, "allow_request", AsyncMock(return_value=False))
        mock_retry = AsyncMock()
        monkeypatch.setattr(ai_service, "_call_gemini_api_with_retry", mock_retry)

        result = await ai_service.analyze_text_and_execute("U1", "太郎", "こんにちは")

        assert result == ai_service.FALLBACK_MESSAGE
        mock_retry.assert_not_called()

    @pytest.mark.asyncio
    async def test_plain_chat_response_returns_response_text(self, ai_configured, monkeypatch):
        monkeypatch.setattr(ai_service.rate_limiter, "allow_request", AsyncMock(return_value=True))
        monkeypatch.setattr(
            ai_service,
            "_call_gemini_api_with_retry",
            AsyncMock(return_value=make_response(function_call=None, text="世間話の返事")),
        )

        result = await ai_service.analyze_text_and_execute("U1", "太郎", "元気？")

        assert result == "世間話の返事"

    @pytest.mark.asyncio
    async def test_empty_response_parts_returns_error_string(self, ai_configured, monkeypatch):
        monkeypatch.setattr(ai_service.rate_limiter, "allow_request", AsyncMock(return_value=True))
        monkeypatch.setattr(
            ai_service,
            "_call_gemini_api_with_retry",
            AsyncMock(return_value=make_response(parts_present=False)),
        )

        result = await ai_service.analyze_text_and_execute("U1", "太郎", "こんにちは")

        assert "応答が空でした" in result

    @pytest.mark.asyncio
    async def test_resource_exhausted_on_first_call_returns_fallback_message(
        self, ai_configured, monkeypatch
    ):
        monkeypatch.setattr(ai_service.rate_limiter, "allow_request", AsyncMock(return_value=True))
        monkeypatch.setattr(
            ai_service,
            "_call_gemini_api_with_retry",
            AsyncMock(side_effect=ResourceExhausted("quota exceeded")),
        )

        result = await ai_service.analyze_text_and_execute("U1", "太郎", "こんにちは")

        assert result == ai_service.FALLBACK_MESSAGE

    @pytest.mark.asyncio
    async def test_generic_google_api_error_returns_apology_string(self, ai_configured, monkeypatch):
        monkeypatch.setattr(ai_service.rate_limiter, "allow_request", AsyncMock(return_value=True))
        monkeypatch.setattr(
            ai_service,
            "_call_gemini_api_with_retry",
            AsyncMock(side_effect=GoogleAPIError("fatal error")),
        )

        result = await ai_service.analyze_text_and_execute("U1", "太郎", "こんにちは")

        assert "予期せぬエラー" in result

    @pytest.mark.asyncio
    async def test_unexpected_exception_is_caught_and_returns_generic_apology(
        self, ai_configured, monkeypatch
    ):
        """rate_limiterチェック自体は外側のtry/exceptの外にあるため、
        意図的にtryブロック内部(genai.GenerativeModel構築時)で例外を起こして
        汎用exceptフォールバックを検証する。"""
        monkeypatch.setattr(ai_service.rate_limiter, "allow_request", AsyncMock(return_value=True))
        monkeypatch.setattr(
            ai_service.genai, "GenerativeModel", MagicMock(side_effect=Exception("boom"))
        )

        result = await ai_service.analyze_text_and_execute("U1", "太郎", "こんにちは")

        assert "処理中にエラーが発生しました" in result

    @pytest.mark.asyncio
    async def test_rate_limiter_exception_itself_propagates_uncaught(self, ai_configured, monkeypatch):
        """rate_limiter.allow_request()の呼び出しは外側try/exceptの外にあるため、
        ここで例外が起きると(通常は起こり得ない想定だが)外へそのまま伝播する。
        この非対称な保護範囲を明示する回帰テスト。"""
        monkeypatch.setattr(
            ai_service.rate_limiter, "allow_request", AsyncMock(side_effect=Exception("boom"))
        )

        with pytest.raises(Exception, match="boom"):
            await ai_service.analyze_text_and_execute("U1", "太郎", "こんにちは")

    @pytest.mark.asyncio
    async def test_dispatches_record_child_health_tool_and_returns_final_text(
        self, ai_configured, monkeypatch
    ):
        monkeypatch.setattr(ai_service.rate_limiter, "allow_request", AsyncMock(return_value=True))
        fc = make_function_call("record_child_health", {"child_name": "智矢", "condition": "元気"})
        first_response = make_response(function_call=fc)
        final_response = make_response(function_call=None, text="承知いたしました。記録しました。")
        mock_retry = AsyncMock(side_effect=[first_response, final_response])
        monkeypatch.setattr(ai_service, "_call_gemini_api_with_retry", mock_retry)
        mock_tool = AsyncMock(return_value="記録完了: 太郎")
        monkeypatch.setattr(ai_service, "tool_record_child_health", mock_tool)

        result = await ai_service.analyze_text_and_execute("U1", "太郎", "智矢は元気です")

        mock_tool.assert_called_once_with("U1", "太郎", {"child_name": "智矢", "condition": "元気"})
        assert result == "承知いたしました。記録しました。"
        assert mock_retry.call_count == 2

    @pytest.mark.asyncio
    async def test_dispatches_record_food_tool(self, ai_configured, monkeypatch):
        monkeypatch.setattr(ai_service.rate_limiter, "allow_request", AsyncMock(return_value=True))
        fc = make_function_call("record_food", {"item": "カレー"})
        mock_retry = AsyncMock(
            side_effect=[make_response(function_call=fc), make_response(function_call=None, text="OK")]
        )
        monkeypatch.setattr(ai_service, "_call_gemini_api_with_retry", mock_retry)
        mock_tool = AsyncMock(return_value="記録完了: カレー")
        monkeypatch.setattr(ai_service, "tool_record_food", mock_tool)

        result = await ai_service.analyze_text_and_execute("U1", "太郎", "カレー食べた")

        mock_tool.assert_called_once_with("U1", "太郎", {"item": "カレー"})
        assert result == "OK"

    @pytest.mark.asyncio
    async def test_dispatches_search_db_tool(self, ai_configured, monkeypatch):
        monkeypatch.setattr(ai_service.rate_limiter, "allow_request", AsyncMock(return_value=True))
        fc = make_function_call("search_db", {"sql_query": "SELECT * FROM food_records"})
        mock_retry = AsyncMock(
            side_effect=[make_response(function_call=fc), make_response(function_call=None, text="OK")]
        )
        monkeypatch.setattr(ai_service, "_call_gemini_api_with_retry", mock_retry)
        mock_tool = AsyncMock(return_value="検索結果です")
        monkeypatch.setattr(ai_service, "tool_search_db", mock_tool)

        await ai_service.analyze_text_and_execute("U1", "太郎", "食事の履歴教えて")

        mock_tool.assert_called_once_with({"sql_query": "SELECT * FROM food_records"})

    @pytest.mark.asyncio
    async def test_unknown_tool_name_feeds_error_back_to_model_instead_of_direct_reply(
        self, ai_configured, monkeypatch
    ):
        monkeypatch.setattr(ai_service.rate_limiter, "allow_request", AsyncMock(return_value=True))
        fc = make_function_call("bogus_tool", {})
        mock_retry = AsyncMock(
            side_effect=[make_response(function_call=fc), make_response(function_call=None, text="最終応答")]
        )
        monkeypatch.setattr(ai_service, "_call_gemini_api_with_retry", mock_retry)

        result = await ai_service.analyze_text_and_execute("U1", "太郎", "何かして")

        # 未知ツールのエラー文字列はユーザーへ直接返らず、2回目の呼び出し(最終応答生成)を経由する
        assert result == "最終応答"
        assert mock_retry.call_count == 2

    @pytest.mark.asyncio
    async def test_resource_exhausted_during_final_response_returns_tool_result_with_note(
        self, ai_configured, monkeypatch
    ):
        monkeypatch.setattr(ai_service.rate_limiter, "allow_request", AsyncMock(return_value=True))
        fc = make_function_call("record_food", {"item": "カレー"})
        mock_retry = AsyncMock(
            side_effect=[make_response(function_call=fc), ResourceExhausted("quota exceeded")]
        )
        monkeypatch.setattr(ai_service, "_call_gemini_api_with_retry", mock_retry)
        monkeypatch.setattr(
            ai_service, "tool_record_food", AsyncMock(return_value="記録完了: カレー")
        )

        result = await ai_service.analyze_text_and_execute("U1", "太郎", "カレー食べた")

        assert "記録完了: カレー" in result
        assert "制限を超過" in result


class TestCallGeminiApiWithRetry:
    @pytest.mark.asyncio
    async def test_succeeds_without_retry_on_first_call(self, no_retry_sleep):
        chat_session = MagicMock()
        chat_session.send_message.return_value = make_response(text="ok")

        result = await ai_service._call_gemini_api_with_retry(chat_session, "prompt")

        assert result.text == "ok"
        assert chat_session.send_message.call_count == 1

    @pytest.mark.asyncio
    async def test_retries_on_resource_exhausted_then_succeeds(self, no_retry_sleep):
        chat_session = MagicMock()
        chat_session.send_message.side_effect = [
            ResourceExhausted("x"),
            ResourceExhausted("x"),
            make_response(text="ok after retries"),
        ]

        result = await ai_service._call_gemini_api_with_retry(chat_session, "prompt")

        assert result.text == "ok after retries"
        assert chat_session.send_message.call_count == 3

    @pytest.mark.asyncio
    async def test_reraises_after_max_attempts_exhausted(self, no_retry_sleep):
        chat_session = MagicMock()
        chat_session.send_message.side_effect = ResourceExhausted("always exhausted")

        with pytest.raises(ResourceExhausted):
            await ai_service._call_gemini_api_with_retry(chat_session, "prompt")

        assert chat_session.send_message.call_count == ai_service.MAX_RETRIES

    @pytest.mark.asyncio
    async def test_does_not_retry_on_non_resource_exhausted_exception(self, no_retry_sleep):
        chat_session = MagicMock()
        chat_session.send_message.side_effect = GoogleAPIError("fatal, non-retryable")

        with pytest.raises(GoogleAPIError):
            await ai_service._call_gemini_api_with_retry(chat_session, "prompt")

        assert chat_session.send_message.call_count == 1
