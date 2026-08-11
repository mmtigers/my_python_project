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
