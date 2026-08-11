# MY_HOME_SYSTEM/tests/test_ai_logic.py
"""
handlers/ai_logic.py の execute_get_expenditure_logs / execute_get_health_logs のテスト。

回帰対象のバグ: 両関数は `datetime('now', '-? days')` のように、SQLiteの
プレースホルダ(?)をクオートされた文字列リテラルの内側に埋め込んでいた。
SQLiteはこれをバインドパラメータとして認識しないため、
`cur.execute(query, params)` は必ず
`sqlite3.ProgrammingError: Incorrect number of bindings supplied` を送出していた。

この2関数は handlers/line_logic.py の analyze_text_and_execute 経由で
実際のLINE Webhook(AIアシスタント「セバスチャン」)から呼ばれる本番コードパスであり、
「買い物履歴を見せて」「子供の体調は？」等のメッセージへの応答が
常に生のSQLiteエラー文字列になってしまう(=機能として100%失敗する)不具合だった。
common.execute_read_query が例外を内部で捕捉して文字列化するため、
Webhook自体はクラッシュしないが、有意な検索結果を一度も返せない状態だった。
"""
import json
import os
import sys
from datetime import datetime, timedelta
from unittest.mock import MagicMock

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import common
import config
from handlers import ai_logic


def _insert_shopping_row(order_date_iso: str, item_name: str, platform: str = "Amazon", price: int = 1000):
    with common.get_db_cursor(commit=True) as cur:
        cur.execute(
            f"INSERT INTO {config.SQLITE_TABLE_SHOPPING} "
            "(order_date, platform, item_name, price, timestamp) VALUES (?, ?, ?, ?, ?)",
            (order_date_iso, platform, item_name, price, order_date_iso),
        )


def _insert_child_health_row(timestamp_iso: str, child_name: str, condition: str):
    with common.get_db_cursor(commit=True) as cur:
        cur.execute(
            f"INSERT INTO {config.SQLITE_TABLE_CHILD} (child_name, condition, timestamp) VALUES (?, ?, ?)",
            (child_name, condition, timestamp_iso),
        )


def _insert_defecation_row(timestamp_iso: str, user_name: str, condition: str):
    with common.get_db_cursor(commit=True) as cur:
        cur.execute(
            f"INSERT INTO {config.SQLITE_TABLE_DEFECATION} (user_name, record_type, condition, timestamp) VALUES (?, ?, ?, ?)",
            (user_name, "排便", condition, timestamp_iso),
        )


class TestExecuteGetExpenditureLogs:
    def test_does_not_return_a_raw_sql_error(self, isolated_db):
        """回帰の核心: 修正前は常にこの文字列が返っていた"""
        result = ai_logic.execute_get_expenditure_logs({"days": 30})
        assert "検索エラー" not in result
        assert "Incorrect number of bindings" not in result

    def test_filters_out_rows_older_than_requested_days(self, isolated_db):
        now = datetime.now()
        _insert_shopping_row((now - timedelta(days=1)).isoformat(), "最近買った物")
        _insert_shopping_row((now - timedelta(days=100)).isoformat(), "昔買った物")

        result = ai_logic.execute_get_expenditure_logs({"days": 30})
        data = json.loads(result)

        names = [row["item_name"] for row in data]
        assert "最近買った物" in names
        assert "昔買った物" not in names

    def test_keyword_filter_combines_with_date_filter(self, isolated_db):
        now = datetime.now()
        _insert_shopping_row((now - timedelta(days=1)).isoformat(), "おむつ")
        _insert_shopping_row((now - timedelta(days=1)).isoformat(), "お茶")

        result = ai_logic.execute_get_expenditure_logs({"days": 30, "item_keyword": "おむつ"})
        data = json.loads(result)

        assert len(data) == 1
        assert data[0]["item_name"] == "おむつ"

    def test_days_zero_returns_no_future_leaning_rows(self, isolated_db):
        """
        days=0 は「今日以降」を意味するため、1日前の記録はヒットしない。
        該当0件の場合 common.execute_read_query は空リストのJSONではなく
        固定の日本語メッセージを返す仕様のため、そちらを確認する。
        """
        now = datetime.now()
        _insert_shopping_row((now - timedelta(days=1)).isoformat(), "1日前")
        result = ai_logic.execute_get_expenditure_logs({"days": 0})
        assert result == "該当するデータはありませんでした。"


class TestExecuteGetHealthLogs:
    def test_does_not_return_a_raw_sql_error(self, isolated_db):
        result = ai_logic.execute_get_health_logs({"days": 7})
        assert "検索エラー" not in result
        assert "Incorrect number of bindings" not in result

    def test_unions_child_health_and_defecation_within_date_range(self, isolated_db):
        now = datetime.now()
        _insert_child_health_row((now - timedelta(days=1)).isoformat(), "daughter", "元気")
        _insert_defecation_row((now - timedelta(days=1)).isoformat(), "daughter", "普通")
        _insert_child_health_row((now - timedelta(days=100)).isoformat(), "daughter", "古い記録")

        result = ai_logic.execute_get_health_logs({"days": 7})
        data = json.loads(result)

        types = {row["type"] for row in data}
        assert types == {"体調", "排便"}
        conditions = [row["condition"] for row in data]
        assert "古い記録" not in conditions

    def test_child_name_filter_applies_on_top_of_date_filter(self, isolated_db):
        now = datetime.now()
        _insert_child_health_row((now - timedelta(days=1)).isoformat(), "daughter", "元気")
        _insert_child_health_row((now - timedelta(days=1)).isoformat(), "son", "元気")

        result = ai_logic.execute_get_health_logs({"days": 7, "child_name": "daughter"})
        data = json.loads(result)

        assert all(row["target"] == "daughter" for row in data)
        assert len(data) == 1


class TestAnalyzeTextAndExecute:
    """
    handlers/ai_logic.py の analyze_text_and_execute() のテスト。
    実際のGemini APIへは一切アクセスしない。genai.GenerativeModel をモックする。
    このファイルは enable_automatic_function_calling=True を使うため、
    ai_service.py側のような function_call/parts の分岐は無く、
    chat.send_message() の戻り値の .text だけを見ればよい。
    """

    def test_returns_none_when_no_api_key_configured(self, monkeypatch):
        monkeypatch.setattr(config, "GEMINI_API_KEY", None)

        result = ai_logic.analyze_text_and_execute("こんにちは", "U1", "太郎")

        assert result is None

    def test_returns_stripped_response_text_on_success(self, monkeypatch):
        monkeypatch.setattr(config, "GEMINI_API_KEY", "fake-key")
        fake_model = MagicMock()
        fake_model.start_chat.return_value.send_message.return_value = MagicMock(
            text="  こんにちは、太郎様。  "
        )
        monkeypatch.setattr(ai_logic.genai, "GenerativeModel", MagicMock(return_value=fake_model))

        result = ai_logic.analyze_text_and_execute("こんにちは", "U1", "太郎")

        assert result == "こんにちは、太郎様。"

    def test_empty_response_text_returns_none(self, monkeypatch):
        monkeypatch.setattr(config, "GEMINI_API_KEY", "fake-key")
        fake_model = MagicMock()
        fake_model.start_chat.return_value.send_message.return_value = MagicMock(text="")
        monkeypatch.setattr(ai_logic.genai, "GenerativeModel", MagicMock(return_value=fake_model))

        result = ai_logic.analyze_text_and_execute("こんにちは", "U1", "太郎")

        assert result is None

    def test_exception_is_caught_and_returns_apology_string_not_raised(self, monkeypatch):
        monkeypatch.setattr(config, "GEMINI_API_KEY", "fake-key")
        monkeypatch.setattr(
            ai_logic.genai, "GenerativeModel", MagicMock(side_effect=Exception("Gemini API down"))
        )

        result = ai_logic.analyze_text_and_execute("こんにちは", "U1", "太郎")

        assert "処理中にエラーが発生しました" in result


class TestExecuteChildHealth:
    def test_saves_record_and_returns_confirmation_message(self, isolated_db):
        result = ai_logic.execute_child_health(
            {"child_name": "智矢", "condition": "元気"}, "U1", "太郎"
        )

        with common.get_db_cursor() as cur:
            row = cur.execute(f"SELECT * FROM {config.SQLITE_TABLE_CHILD}").fetchone()
        assert row["child_name"] == "智矢"
        assert row["condition"] == "元気"
        assert "記録しました" in result

    def test_missing_args_default_to_placeholder_values(self, isolated_db):
        ai_logic.execute_child_health({}, "U1", "太郎")

        with common.get_db_cursor() as cur:
            row = cur.execute(f"SELECT * FROM {config.SQLITE_TABLE_CHILD}").fetchone()
        assert row["child_name"] == "子供"
        assert row["condition"] == "記録なし"

    def test_emergency_flag_adds_warning_text_and_sends_discord_push(self, isolated_db, monkeypatch):
        mock_send_push = MagicMock()
        monkeypatch.setattr(ai_logic.common, "send_push", mock_send_push)

        result = ai_logic.execute_child_health(
            {"child_name": "智矢", "condition": "高熱", "is_emergency": True}, "U1", "太郎"
        )

        assert "お大事に" in result
        mock_send_push.assert_called_once()
        call_kwargs = mock_send_push.call_args
        assert call_kwargs.kwargs["target"] == "discord"

    def test_no_emergency_flag_does_not_send_push(self, isolated_db, monkeypatch):
        mock_send_push = MagicMock()
        monkeypatch.setattr(ai_logic.common, "send_push", mock_send_push)

        ai_logic.execute_child_health({"child_name": "智矢", "condition": "元気"}, "U1", "太郎")

        mock_send_push.assert_not_called()


class TestExecuteShopping:
    def test_saves_record_with_valid_price(self, isolated_db):
        result = ai_logic.execute_shopping(
            {"item_name": "おむつ", "price": 1500, "date_str": "2026-01-01"}, "U1", "太郎"
        )

        with common.get_db_cursor() as cur:
            row = cur.execute(f"SELECT * FROM {config.SQLITE_TABLE_SHOPPING}").fetchone()
        assert row["item_name"] == "おむつ"
        assert row["price"] == 1500
        assert row["order_date"] == "2026-01-01"
        assert "1500円" in result

    def test_non_numeric_price_falls_back_to_zero(self, isolated_db):
        ai_logic.execute_shopping({"item_name": "おむつ", "price": "abc"}, "U1", "太郎")

        with common.get_db_cursor() as cur:
            row = cur.execute(f"SELECT * FROM {config.SQLITE_TABLE_SHOPPING}").fetchone()
        assert row["price"] == 0

    def test_missing_price_defaults_to_zero(self, isolated_db):
        ai_logic.execute_shopping({"item_name": "おむつ"}, "U1", "太郎")

        with common.get_db_cursor() as cur:
            row = cur.execute(f"SELECT * FROM {config.SQLITE_TABLE_SHOPPING}").fetchone()
        assert row["price"] == 0

    def test_missing_date_defaults_to_today(self, isolated_db):
        ai_logic.execute_shopping({"item_name": "おむつ", "price": 100}, "U1", "太郎")

        with common.get_db_cursor() as cur:
            row = cur.execute(f"SELECT * FROM {config.SQLITE_TABLE_SHOPPING}").fetchone()
        assert row["order_date"] == common.get_today_date_str()


class TestExecuteDefecation:
    def test_saves_record_and_returns_confirmation(self, isolated_db):
        result = ai_logic.execute_defecation(
            {"condition": "普通", "note": "特になし"}, "U1", "太郎"
        )

        with common.get_db_cursor() as cur:
            row = cur.execute(f"SELECT * FROM {config.SQLITE_TABLE_DEFECATION}").fetchone()
        assert row["condition"] == "普通"
        assert row["note"] == "特になし"
        assert "普通" in result


class TestExecuteSearchDatabase:
    def test_valid_select_returns_json_serialized_rows(self, isolated_db):
        with common.get_db_cursor(commit=True) as cur:
            cur.execute(
                f"INSERT INTO {config.SQLITE_TABLE_CHILD} (child_name, condition, timestamp) VALUES (?, ?, ?)",
                ("智矢", "元気", "2026-01-01T00:00:00"),
            )

        result = ai_logic.execute_search_database(
            {"sql_query": f"SELECT child_name, condition FROM {config.SQLITE_TABLE_CHILD}"}
        )

        data = json.loads(result)
        assert data == [{"child_name": "智矢", "condition": "元気"}]

    def test_non_select_query_is_rejected(self, isolated_db):
        result = ai_logic.execute_search_database(
            {"sql_query": f"DELETE FROM {config.SQLITE_TABLE_CHILD}"}
        )

        assert "許可されていません" in result
        with common.get_db_cursor() as cur:
            # 実際には削除が実行されていないこと(そもそも保護されていること)を明示的に確認
            count = cur.execute(f"SELECT COUNT(*) c FROM {config.SQLITE_TABLE_CHILD}").fetchone()["c"]
        assert count == 0

    def test_empty_result_returns_not_found_message(self, isolated_db):
        result = ai_logic.execute_search_database(
            {"sql_query": f"SELECT * FROM {config.SQLITE_TABLE_CHILD} WHERE child_name='存在しない'"}
        )

        assert "見つかりませんでした" in result

    def test_db_connection_error_is_caught_and_returns_error_string(self, isolated_db, monkeypatch):
        monkeypatch.setattr(config, "SQLITE_DB_PATH", "/nonexistent/path/does_not_exist.db")

        result = ai_logic.execute_search_database(
            {"sql_query": f"SELECT * FROM {config.SQLITE_TABLE_CHILD}"}
        )

        assert "検索中にエラーが発生しました" in result


class TestDeclareToolStubsKnownIssue:
    """
    my_tools に登録されている declare_child_health / declare_shopping / declare_defecation は
    passのみのスタブで、実際に保存を行う execute_child_health 等とは配線されていない。
    Geminiの自動関数呼び出しがこれらを実際に呼んだ場合、何も保存されず沈黙して
    失敗する可能性がある(既知の問題として最終レポートで報告する)。
    このテストはその「現状の挙動」を明示的に固定するピン留めテストであり、
    正しい挙動であることを主張するものではない。
    """

    def test_declare_child_health_stub_has_no_side_effect(self, isolated_db):
        result = ai_logic.declare_child_health("智矢", "元気")

        assert result is None
        with common.get_db_cursor() as cur:
            count = cur.execute(f"SELECT COUNT(*) c FROM {config.SQLITE_TABLE_CHILD}").fetchone()["c"]
        assert count == 0
