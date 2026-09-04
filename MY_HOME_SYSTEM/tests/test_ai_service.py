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
import asyncio
import os
import queue
import sys
import threading
import time
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


def test_strip_sql_comments_removes_block_and_line_comments():
    sql = "SELECT * FROM/**/quest_users--trailing comment\nWHERE 1=1"
    stripped = ai_service._strip_sql_comments(sql)
    assert "/*" not in stripped
    assert "--" not in stripped
    assert "quest_users" in stripped


def test_extract_referenced_tables_bypass_via_block_comment_is_closed():
    """
    B3: `FROM/**/tablename` のようにFROM直後を空白なしのブロックコメントで埋めると、
    旧実装は `FROM\\s+テーブル名` を要求する正規表現がマッチせずテーブル名を検出
    できなかった(=許可テーブル判定を素通りするバイパス)。tool_search_dbは実行前に
    _strip_sql_comments でコメントを除去するため、そちらを通した文字列であれば
    検出できることを確認する。
    """
    sql = "SELECT secret FROM child_health_records WHERE 1=0 UNION SELECT pwd FROM/**/secret_admin_table--"
    stripped = ai_service._strip_sql_comments(sql)
    tables = ai_service._extract_referenced_tables(stripped)
    assert "child_health_records" in tables
    assert "secret_admin_table" in tables


@pytest.mark.asyncio
async def test_tool_search_db_blocks_disallowed_table_hidden_behind_block_comment():
    """B3の回帰防止: FROM直後のブロックコメントで隠された許可外テーブルを素通りさせない"""
    table = next(iter(ai_service.ALLOWED_SEARCH_TABLES))
    sql = f"SELECT * FROM {table} WHERE 1=0 UNION SELECT * FROM/**/quest_users--"
    result = await ai_service.tool_search_db({"sql_query": sql})
    assert "許可されていない" in result


@pytest.mark.asyncio
async def test_tool_search_db_allows_documented_table_with_harmless_comment(monkeypatch):
    """コメント除去は誤検知(許可テーブルのみの正常クエリの拒否)を起こさないこと"""
    table = next(iter(ai_service.ALLOWED_SEARCH_TABLES))
    monkeypatch.setattr(ai_service, "_execute_restricted_read_query", lambda sql, params=(): "OK")
    result = await ai_service.tool_search_db({"sql_query": f"SELECT * FROM {table} /* comment */ WHERE 1=1"})
    assert result == "OK"


def test_extract_referenced_tables_comma_join_with_alias_catches_second_table():
    """Issue #224: 1つ目のテーブルにエイリアスが付いたカンマ結合(暗黙CROSS JOIN)
    でも2つ目のテーブルを抽出できること。エイリアスが無い場合(H-6)は直後がカンマ
    になるため検出できていたが、`FROM a x, b y`のようにエイリアスが挟まると、
    識別子の直後がカンマではなくエイリアス文字列になり抽出漏れしていた。"""
    sql = "SELECT s.* FROM power_usage c, quest_users s WHERE c.id = s.id"
    tables = ai_service._extract_referenced_tables(sql)
    assert "power_usage" in tables
    assert "quest_users" in tables


def test_extract_referenced_tables_comma_join_with_as_alias_catches_second_table():
    """Issue #224: `AS`付きエイリアスのカンマ結合でも2つ目のテーブルを抽出できること"""
    sql = "SELECT s.* FROM power_usage AS c, quest_users AS s WHERE c.id = s.id"
    tables = ai_service._extract_referenced_tables(sql)
    assert "power_usage" in tables
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
async def test_tool_search_db_blocks_comma_joined_disallowed_table_with_alias():
    """Issue #224の回帰防止: 1つ目のテーブルにエイリアスが付いたカンマ結合で
    quest_users を素通りさせない(Issueの再現条件そのもの)"""
    table = next(iter(ai_service.ALLOWED_SEARCH_TABLES))
    result = await ai_service.tool_search_db(
        {"sql_query": f"SELECT s.* FROM {table} c, quest_users s WHERE c.id = s.id"}
    )
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
    monkeypatch.setattr(ai_service, "_execute_restricted_read_query", lambda sql, params=(): "OK")
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
    """_execute_restricted_read_query(旧common.execute_read_query)は0件時、実際には(空リストではなく)
    "該当するデータはありませんでした。"という非空文字列を返す(core/database.py参照)。
    その文字列がそのまま呼び出し元へ返ることを確認する。"""
    monkeypatch.setattr(
        ai_service, "_execute_restricted_read_query",
        lambda sql, params=(): "該当するデータはありませんでした。",
    )
    table = next(iter(ai_service.ALLOWED_SEARCH_TABLES))
    result = await ai_service.tool_search_db({"sql_query": f"SELECT * FROM {table}"})
    assert "ありませんでした" in result


@pytest.mark.asyncio
async def test_tool_search_db_query_exception_is_caught_and_returns_error_string(monkeypatch):
    def _raise(sql, params=()):
        raise Exception("malformed SQL")

    monkeypatch.setattr(ai_service, "_execute_restricted_read_query", _raise)
    table = next(iter(ai_service.ALLOWED_SEARCH_TABLES))
    result = await ai_service.tool_search_db({"sql_query": f"SELECT * FROM {table}"})
    assert "DB検索エラー" in result


@pytest.mark.asyncio
async def test_tool_search_db_internal_error_string_is_not_passed_through_as_data(monkeypatch):
    """Issue #180の回帰テスト: _execute_restricted_read_query(旧common.execute_read_query)は不正なSQL等の実行時例外を
    自身の内部でキャッチし、送出せず"検索エラー: ..."という非空文字列として返す設計
    (core/database.py参照)。以前はこれが正常な検索結果と区別されずAIへそのまま
    渡っていた(かつ`if not rows:`は非空文字列に対して常に偽となるデッドコードだった)。
    このエラー文字列が検出され、DB検索エラーとして扱われることを確認する。"""
    monkeypatch.setattr(
        ai_service, "_execute_restricted_read_query",
        lambda sql, params=(): "検索エラー: no such table: xyz",
    )
    table = next(iter(ai_service.ALLOWED_SEARCH_TABLES))
    result = await ai_service.tool_search_db({"sql_query": f"SELECT * FROM {table}"})
    assert "DB検索エラー" in result
    assert "no such table: xyz" in result


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

    @pytest.mark.asyncio
    async def test_generic_google_api_error_during_final_response_returns_tool_result_with_note(
        self, ai_configured, monkeypatch
    ):
        """Issue #232の回帰テスト: 1回目呼び出しにはGoogleAPIError専用メッセージが
        あるが、ツール実行後の2回目呼び出しは以前ResourceExhausted用フォールバック
        しか持たず、それ以外のGoogleAPIErrorは関数末尾の汎用except Exception
        (「処理中にエラーが発生しました」)まで伝播していた。この時点でツール(DB書き込み)は
        既に成功しているため、ユーザーには保存が失敗したかのように見え、再送信による
        重複記録を誘発しうる不具合があった。ResourceExhaustedと同様にtool_resultを
        返し、実行結果を正しく伝えることを確認する。"""
        monkeypatch.setattr(ai_service.rate_limiter, "allow_request", AsyncMock(return_value=True))
        fc = make_function_call("record_food", {"item": "カレー"})
        mock_retry = AsyncMock(
            side_effect=[make_response(function_call=fc), GoogleAPIError("fatal, non-retryable")]
        )
        monkeypatch.setattr(ai_service, "_call_gemini_api_with_retry", mock_retry)
        monkeypatch.setattr(
            ai_service, "tool_record_food", AsyncMock(return_value="記録完了: カレー")
        )

        result = await ai_service.analyze_text_and_execute("U1", "太郎", "カレー食べた")

        assert "記録完了: カレー" in result
        assert "処理中にエラーが発生しました" not in result


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


class TestToolSchemaTimestampFormatMatchesActualData:
    """
    M-5-1: search_db ツールschemaのtimestamp列の説明が実データの保存形式と
    一致していることの回帰テスト。

    従来 tools_schema の sql_query 説明は「'YYYY-MM-DD HH:MM:SS' 形式の文字列」と
    書かれていたが、実データ(child_health_records等)は core.utils.get_now_iso() で
    保存されており、実際は ISO8601 + JSTオフセット('T'区切り、'+09:00'付き)である。
    説明と実データの形式が食い違うと、AIが生成する BETWEEN 等の文字列比較検索が
    ズレて意図した範囲の行を取りこぼす。
    """

    def test_schema_description_reflects_iso8601_format_not_space_separated(self):
        sql_query_desc = (
            ai_service.tools_schema[0]["function_declarations"][2]["parameters"]
            ["properties"]["sql_query"]["description"]
        )
        assert ai_service.tools_schema[0]["function_declarations"][2]["name"] == "search_db"

        # 実データが実際に使っている形式(ISO8601, 'T'区切り, +09:00オフセット)への
        # 言及があること。
        assert "T" in sql_query_desc
        assert "+09:00" in sql_query_desc

        # 修正前に書かれていた、実データと矛盾するスペース区切り形式の表記が
        # 残っていないこと。
        assert "YYYY-MM-DD HH:MM:SS" not in sql_query_desc

    def test_schema_description_example_matches_get_now_iso_shape(self):
        """
        スキーマ説明文中に埋め込まれた例示フォーマットが、実際の
        get_now_iso() の出力形状(ISO8601 + マイクロ秒 + '+09:00')と
        一致することを、実際の出力を正規表現化して検証する。
        """
        import re
        from core.utils import get_now_iso

        sql_query_desc = (
            ai_service.tools_schema[0]["function_declarations"][2]["parameters"]
            ["properties"]["sql_query"]["description"]
        )
        sample = get_now_iso()
        shape_pattern = re.sub(r"\d", r"\\d", re.escape(sample))
        assert re.search(shape_pattern, sql_query_desc), (
            f"schema description does not contain a timestamp example matching "
            f"get_now_iso() shape: {sample!r}"
        )


class TestRateLimiterLockIsThreadSafeAcrossEventLoops:
    """
    M-5-3: SimpleRateLimiter の内部ロック(_lock)は、handlers/line_handler.py の
    handle_message が着信メッセージごとに `asyncio.run(...)` で新しいイベントループを
    生成する運用を前提にすると、別スレッド上の別イベントループから同時に
    ロック獲得を試みる状況が発生しうる。

    asyncio.Lock はコンテンション時、獲得待ちのFutureをその時点の実行中ループに
    紐付ける。別スレッドの別ループがそのFutureの解決を待つと、Futureを解決する
    はずのループ側コールバックは永遠に呼ばれず、レート制限チェック自体が
    無期限にハングしてしまう(=リクエストが応答不能になる)。
    threading.Lock であればスレッドを跨いでも正しくブロック/解放される。
    """

    @staticmethod
    def _hold_lock_from_this_thread(lock, acquired_event: threading.Event, released_event: threading.Event):
        """lockの型(threading.Lock / asyncio.Lock)を問わず、このスレッド上でlockを
        保持し続けるヘルパー。修正前後どちらの実装でも同じテストコードで検証できるように、
        型に応じて獲得方法を切り替える。"""
        if isinstance(lock, type(threading.Lock())):
            lock.acquire()
            try:
                acquired_event.set()
                released_event.wait(timeout=5)
            finally:
                lock.release()
        else:
            async def _hold():
                async with lock:
                    acquired_event.set()
                    while not released_event.is_set():
                        await asyncio.sleep(0.01)
            asyncio.run(_hold())

    def test_lock_is_a_threading_lock_not_an_asyncio_lock(self):
        limiter = ai_service.SimpleRateLimiter()
        assert isinstance(limiter._lock, type(threading.Lock())), (
            "SimpleRateLimiter._lock should be threading.Lock, not asyncio.Lock, "
            "because it is shared across the per-request asyncio.run() event loops "
            "created by handlers/line_handler.py:handle_message"
        )

    def test_allow_request_does_not_hang_when_contended_from_another_threads_loop(self):
        limiter = ai_service.SimpleRateLimiter()
        released_event = threading.Event()
        acquired_event = threading.Event()

        holder = threading.Thread(
            target=self._hold_lock_from_this_thread,
            args=(limiter._lock, acquired_event, released_event),
            daemon=True,  # 万一ハングしても後続テスト・プロセス終了をブロックしない
        )
        holder.start()
        assert acquired_event.wait(timeout=2), "holder thread failed to acquire the lock"

        result_queue = queue.Queue()

        def requester():
            try:
                result_queue.put(("ok", asyncio.run(limiter.allow_request())))
            except Exception as e:  # noqa: BLE001
                result_queue.put(("error", e))

        req_thread = threading.Thread(target=requester, daemon=True)
        req_thread.start()
        # requester が実際にロック獲得待ちで止まる時間を作ってから解放する。
        # (解放前に結果を待つと、単に「保持時間分だけ遅い」だけで区別できないため)
        time.sleep(0.2)
        released_event.set()
        holder.join(timeout=2)

        try:
            outcome = result_queue.get(timeout=2)
        except queue.Empty:
            pytest.fail(
                "allow_request() did not return within 2s of the lock being released by "
                "another thread's event loop (asyncio.Lock cross-loop wakeup is not "
                "thread-safe, so the waiting thread's loop never notices the release)"
            )

        kind, value = outcome
        assert kind == "ok", f"allow_request() raised: {value!r}"
        assert value is True


# ==========================================
# Issue #357: 引用符付き識別子による許可テーブル判定バイパスの回帰テスト
# ==========================================

def _seed_search_db_tables():
    """許可テーブル(food_records)と許可外テーブル(quest_users)に1行ずつ投入する"""
    import common

    with common.get_db_cursor(commit=True) as cur:
        cur.execute(
            f"INSERT INTO {config.SQLITE_TABLE_FOOD} (user_id, user_name, meal_date, meal_time_category, menu_category, timestamp) "
            "VALUES ('U1', '太郎', '2026-09-04', 'Dinner', '夕食: カレー', '2026-09-04T19:00:00+09:00')"
        )
        cur.execute(
            "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold) "
            "VALUES ('secret-line-id', 'SECRET_NAME', 'Warrior', 1, 0, 0)"
        )


# Issue #357 で実証された5つのバイパス形式 + スカラーサブクエリ形式。
# いずれも許可テーブル(food_records)を1つ含めることで旧実装の正規表現判定を素通りしていた。
_BYPASS_SQLS = {
    "double_quote": 'SELECT * FROM food_records WHERE 0 UNION ALL SELECT user_id, name FROM "quest_users"',
    "brackets": "SELECT * FROM food_records WHERE 0 UNION ALL SELECT user_id, name FROM [quest_users]",
    "backticks": "SELECT * FROM food_records WHERE 0 UNION ALL SELECT user_id, name FROM `quest_users`",
    "schema_qualified": 'SELECT * FROM food_records WHERE 0 UNION ALL SELECT user_id, name FROM "main".quest_users',
    "no_space_after_from": 'SELECT * FROM food_records WHERE 0 UNION ALL SELECT user_id, name FROM"quest_users"',
    "scalar_subquery": 'SELECT (SELECT name FROM "quest_users" LIMIT 1) AS leaked FROM food_records',
}


class TestSearchDbQuotedIdentifierBypass:
    """Issue #357: 正規表現層(tool_search_db)の暫定対策。引用符・角括弧・バッククォートを
    含むSQLは、DBへ到達する前に拒否されること。"""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("form", sorted(_BYPASS_SQLS))
    async def test_tool_search_db_rejects_each_bypass_form_before_db_access(self, form, monkeypatch):
        called = []
        monkeypatch.setattr(
            ai_service, "_execute_restricted_read_query", lambda sql, params=(): called.append(sql) or "OK"
        )

        result = await ai_service.tool_search_db({"sql_query": _BYPASS_SQLS[form]})

        assert result.startswith("エラー:")
        assert "引用符" in result
        assert called == [], "引用符付き識別子を含むSQLはDBへ到達してはならない"

    @pytest.mark.asyncio
    async def test_tool_search_db_rejects_unquoted_scalar_subquery_on_disallowed_table(self, monkeypatch):
        """引用符無しのスカラーサブクエリは従来通り正規表現層で許可外テーブルとして拒否されること"""
        monkeypatch.setattr(ai_service, "_execute_restricted_read_query", lambda sql, params=(): "OK")
        sql = "SELECT (SELECT name FROM quest_users LIMIT 1) AS leaked FROM food_records"
        result = await ai_service.tool_search_db({"sql_query": sql})
        assert "許可されていない" in result


class TestSearchDbAuthorizer:
    """Issue #357: 構造的対策。`_execute_restricted_read_query` は SQLite の
    set_authorizer により、正規表現層を経由せず直接呼んでも許可外テーブルを読めないこと。"""

    @pytest.mark.parametrize("form", sorted(_BYPASS_SQLS))
    def test_authorizer_denies_each_bypass_form_even_without_regex_layer(self, isolated_db, form):
        _seed_search_db_tables()

        result = ai_service._execute_restricted_read_query(_BYPASS_SQLS[form])

        assert result.startswith("検索エラー:"), result
        assert "SECRET_NAME" not in result
        assert "secret-line-id" not in result

    def test_authorizer_denies_plain_disallowed_table(self, isolated_db):
        _seed_search_db_tables()
        result = ai_service._execute_restricted_read_query("SELECT * FROM quest_users")
        assert result.startswith("検索エラー:")
        assert "SECRET_NAME" not in result

    def test_authorizer_denies_sqlite_master(self, isolated_db):
        result = ai_service._execute_restricted_read_query("SELECT name FROM sqlite_master")
        assert result.startswith("検索エラー:")

    def test_authorizer_denies_pragma_table_valued_function(self, isolated_db):
        result = ai_service._execute_restricted_read_query("SELECT * FROM pragma_table_info('quest_users')")
        assert result.startswith("検索エラー:")

    def test_authorizer_denies_load_extension_function(self, isolated_db):
        result = ai_service._execute_restricted_read_query("SELECT load_extension('evil')")
        assert result.startswith("検索エラー:")

    def test_authorizer_denies_attach(self, isolated_db, tmp_path):
        result = ai_service._execute_restricted_read_query(f"ATTACH '{tmp_path / 'x.db'}' AS x")
        assert result.startswith("検索エラー:")

    def test_authorizer_denies_write_and_leaves_table_untouched(self, isolated_db):
        import common

        result = ai_service._execute_restricted_read_query(
            f"INSERT INTO {config.SQLITE_TABLE_FOOD} (menu_category, timestamp) VALUES ('x', 'y')"
        )
        assert result.startswith("検索エラー:")
        with common.get_db_cursor() as cur:
            count = cur.execute(f"SELECT COUNT(*) FROM {config.SQLITE_TABLE_FOOD}").fetchone()[0]
        assert count == 0

    def test_allowed_table_query_returns_json_rows(self, isolated_db):
        _seed_search_db_tables()
        result = ai_service._execute_restricted_read_query(
            f"SELECT menu_category FROM {config.SQLITE_TABLE_FOOD} ORDER BY id DESC LIMIT 5"
        )
        assert result.startswith("[")
        assert "カレー" in result

    def test_allowed_table_query_with_functions_and_alias_still_works(self, isolated_db):
        _seed_search_db_tables()
        result = ai_service._execute_restricted_read_query(
            f"SELECT COUNT(*) AS n, upper(f.menu_category) AS m, date('now') AS d FROM {config.SQLITE_TABLE_FOOD} f"
        )
        assert '"n": 1' in result

    def test_allowed_table_with_no_rows_returns_not_found_message(self, isolated_db):
        result = ai_service._execute_restricted_read_query(f"SELECT * FROM {config.SQLITE_TABLE_CHILD}")
        assert result == "該当するデータはありませんでした。"


class TestSearchDbEndToEndWithRealDb:
    """tool_search_db を実DB(isolated_db)に対して通し、許可クエリが依然として機能し、
    バイパス形式が正規表現層・認可層のどちらでも漏洩しないことを確認する。"""

    @pytest.mark.asyncio
    async def test_allowed_query_end_to_end_returns_data(self, isolated_db):
        _seed_search_db_tables()
        result = await ai_service.tool_search_db(
            {"sql_query": f"SELECT menu_category, timestamp FROM {config.SQLITE_TABLE_FOOD} WHERE user_name = '太郎'"}
        )
        assert "カレー" in result
        assert not result.startswith("DB検索エラー")

    @pytest.mark.asyncio
    async def test_allowed_join_of_two_allowed_tables_end_to_end(self, isolated_db):
        _seed_search_db_tables()
        sql = (
            f"SELECT f.menu_category FROM {config.SQLITE_TABLE_FOOD} f "
            f"JOIN {config.SQLITE_TABLE_CHILD} c ON c.user_id = f.user_id"
        )
        result = await ai_service.tool_search_db({"sql_query": sql})
        assert result == "該当するデータはありませんでした。"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("form", sorted(_BYPASS_SQLS))
    async def test_bypass_forms_never_leak_end_to_end(self, isolated_db, form):
        _seed_search_db_tables()
        result = await ai_service.tool_search_db({"sql_query": _BYPASS_SQLS[form]})
        assert result.startswith("エラー:")
        assert "SECRET_NAME" not in result


class TestToolRecordFunctionsReportSaveFailure:
    """Issue #373: line_service 側が保存失敗メッセージ(SAVE_FAILED_PREFIX)を返した場合、
    ツール結果は「記録完了:」ではなく「記録失敗:」で始まり、AIが成功と誤認しないこと。
    また必須引数の欠落はDBへ渡さずツール結果で返すこと。"""

    @pytest.mark.asyncio
    async def test_tool_record_child_health_reports_failure_prefix(self, monkeypatch):
        failed_text = f"{ai_service.line_service.SAVE_FAILED_PREFIX}。【智矢】元気 は保存されていません。"
        monkeypatch.setattr(
            ai_service.line_service, "log_child_health", AsyncMock(return_value=MagicMock(text=failed_text))
        )

        result = await ai_service.tool_record_child_health(
            "U1", "太郎", {"child_name": "智矢", "condition": "元気"}
        )

        assert result.startswith("記録失敗:")
        assert "記録完了" not in result

    @pytest.mark.asyncio
    async def test_tool_record_food_reports_failure_prefix(self, monkeypatch):
        failed_text = f"{ai_service.line_service.SAVE_FAILED_PREFIX}。夕食「カレー」は保存されていません。"
        monkeypatch.setattr(
            ai_service.line_service, "log_food_record", AsyncMock(return_value=MagicMock(text=failed_text))
        )

        result = await ai_service.tool_record_food("U1", "太郎", {"item": "カレー", "category": "夕食"})

        assert result.startswith("記録失敗:")
        assert "記録完了" not in result

    @pytest.mark.asyncio
    async def test_tool_record_child_health_missing_condition_is_not_saved(self, monkeypatch):
        mock_log = AsyncMock()
        monkeypatch.setattr(ai_service.line_service, "log_child_health", mock_log)

        result = await ai_service.tool_record_child_health("U1", "太郎", {"child_name": "智矢"})

        assert result.startswith("記録失敗:")
        mock_log.assert_not_called()

    @pytest.mark.asyncio
    async def test_tool_record_food_missing_item_is_not_saved(self, monkeypatch):
        mock_log = AsyncMock()
        monkeypatch.setattr(ai_service.line_service, "log_food_record", mock_log)

        result = await ai_service.tool_record_food("U1", "太郎", {"category": "夕食"})

        assert result.startswith("記録失敗:")
        mock_log.assert_not_called()

    @pytest.mark.asyncio
    async def test_end_to_end_save_failure_reaches_ai_as_tool_result(self, isolated_db, ai_configured, monkeypatch):
        """analyze_text_and_execute 経由でも失敗が tool_result(function_response)に反映されること"""
        import common

        with common.get_db_cursor(commit=True) as cur:
            cur.execute(f"DROP TABLE {config.SQLITE_TABLE_CHILD}")
        monkeypatch.setattr(ai_service.rate_limiter, "allow_request", AsyncMock(return_value=True))
        fc = make_function_call("record_child_health", {"child_name": "智矢", "condition": "元気"})
        mock_retry = AsyncMock(side_effect=[make_response(function_call=fc), ResourceExhausted("quota")])
        monkeypatch.setattr(ai_service, "_call_gemini_api_with_retry", mock_retry)

        result = await ai_service.analyze_text_and_execute("U1", "太郎", "智矢は元気")

        # 2回目呼び出しが失敗した場合は tool_result がそのままユーザーへ返るため、失敗文言が見える
        assert "記録失敗:" in result
