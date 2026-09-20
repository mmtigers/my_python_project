# MY_HOME_SYSTEM/tests/test_health_watch_orphaned_rows.py
"""health_watch.check_orphaned_rows の回帰テスト (Issue #747 / AUDIT-018)。

`PRAGMA foreign_keys=ON` を設定しているのに外部キー宣言は1つ
(`user_inventory.reward_id`)しかなく、「本来DBが防げる不整合」への防御が
サービス層の個別の None チェックとして散在している(根本原因 RC-5)。
FK を足すには SQLite ではテーブル再作成が必要で、その前提として
「今どれだけ孤児行があるか」を測る必要がある。本チェックはその測定である。

このファイルが固定する要点は**2層の切り分け**:

- 親が `quest_users` の関係は、実行コードに `DELETE FROM quest_users` が
  存在しないため孤児は事故しかありえない → **通知する**
- 親が `quest_master` / `reward_master` の関係は、`sync_master_data` の
  `DELETE ... WHERE quest_id NOT IN (...)` が設計どおりマスタ行を消して
  履歴を残す(#700 で退役6件を実際に削除)ため、孤児は想定内
  → **通知しない**(毎時cronで恒久的な誤検知になるため)
"""
import os
import sqlite3
import sys

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.database import get_db_cursor
from monitors import health_watch

TS = "2026-09-19 12:00:00"


def _insert_user(cur, user_id="u1"):
    cur.execute(
        "INSERT INTO quest_users (user_id, name, level, exp, gold) VALUES (?, ?, 1, 0, 0)",
        (user_id, "テスト"),
    )


@pytest.fixture
def clean_db(isolated_db):
    """孤児が1件も無い状態の DB。"""
    with get_db_cursor(commit=True) as cur:
        _insert_user(cur)
        cur.execute(
            "INSERT INTO quest_master (quest_id, title, quest_type) VALUES (?, ?, ?)",
            (1, "テストクエスト", "daily"),
        )
        cur.execute(
            "INSERT INTO reward_master (reward_id, title, cost_gold) VALUES (?, ?, ?)",
            (10, "テスト報酬", 5),
        )
    return isolated_db


class TestNoOrphans:
    def test_clean_db_reports_nothing(self, clean_db):
        assert health_watch.check_orphaned_rows() is None

    def test_rows_with_valid_parents_report_nothing(self, clean_db):
        with get_db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO quest_history (user_id, quest_id, completed_at, status) "
                "VALUES (?, ?, ?, ?)",
                ("u1", 1, TS, "approved"),
            )
            cur.execute(
                "INSERT INTO reward_history (user_id, reward_id, cost_gold, redeemed_at) "
                "VALUES (?, ?, ?, ?)",
                ("u1", 10, 5, TS),
            )
        assert health_watch.check_orphaned_rows() is None

    def test_item_use_pseudo_quest_id_zero_is_not_an_orphan(self, clean_db):
        """quest_id=0 はアイテム使用ログでマスタを参照しない疑似IDなので
        孤児として数えないこと(inventory_service が使う)。"""
        with get_db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO quest_history (user_id, quest_id, completed_at, status) "
                "VALUES (?, ?, ?, ?)",
                ("u1", 0, TS, "approved"),
            )
        assert health_watch.check_orphaned_rows() is None

    def test_null_linked_history_id_is_not_an_orphan(self, clean_db):
        with get_db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO quest_history (user_id, quest_id, completed_at, status, linked_history_id) "
                "VALUES (?, ?, ?, ?, NULL)",
                ("u1", 1, TS, "approved"),
            )
        assert health_watch.check_orphaned_rows() is None


class TestStrictOrphansAreReported:
    """親が quest_users の孤児は通知する。"""

    def test_quest_history_with_missing_user_is_reported(self, clean_db):
        with get_db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO quest_history (user_id, quest_id, completed_at, status) "
                "VALUES (?, ?, ?, ?)",
                ("消えたユーザー", 1, TS, "approved"),
            )
        result = health_watch.check_orphaned_rows()
        assert result is not None
        assert "quest_history.user_id: 1行" in result
        # 自動修正していないことを本文で明言していること
        assert "自動修正はしていません" in result

    @pytest.mark.parametrize("table,sql", [
        ("reward_history.user_id", (
            "INSERT INTO reward_history (user_id, reward_id, cost_gold, redeemed_at) "
            f"VALUES ('ghost', 10, 5, '{TS}')"
        )),
        ("user_inventory.user_id", (
            "INSERT INTO user_inventory (user_id, reward_id, status, purchased_at) "
            f"VALUES ('ghost', 10, 'owned', '{TS}')"
        )),
        ("routine_progress.user_id", (
            "INSERT INTO routine_progress "
            "(user_id, flow_key, progress_date, current_step_index, created_at, updated_at) "
            f"VALUES ('ghost', 'morning', '2026-09-19', 0, '{TS}', '{TS}')"
        )),
        ("routine_step_events.user_id", (
            "INSERT INTO routine_step_events "
            "(user_id, flow_key, progress_date, step_key, to_status, source, occurred_at) "
            f"VALUES ('ghost', 'morning', '2026-09-19', 'wake', 'done', 'test', '{TS}')"
        )),
    ])
    def test_each_user_relation_is_checked(self, clean_db, table, sql):
        with get_db_cursor(commit=True) as cur:
            cur.execute(sql)
        result = health_watch.check_orphaned_rows()
        assert result is not None, table
        assert f"{table}: 1行" in result, result

    def test_dangling_linked_history_id_is_reported(self, clean_db):
        """取消時に張る自己参照リンクの参照先が消えていれば整合性の破れ。"""
        with get_db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO quest_history (user_id, quest_id, completed_at, status, linked_history_id) "
                "VALUES (?, ?, ?, ?, ?)",
                ("u1", 1, TS, "cancelled", 999999),
            )
        result = health_watch.check_orphaned_rows()
        assert result is not None
        assert "quest_history.linked_history_id: 1行" in result

    def test_multiple_relations_are_all_listed(self, clean_db):
        with get_db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO quest_history (user_id, quest_id, completed_at, status) "
                "VALUES ('ghost', 1, ?, 'approved')", (TS,),
            )
            cur.execute(
                "INSERT INTO user_inventory (user_id, reward_id, status, purchased_at) "
                "VALUES ('ghost', 10, 'owned', ?)", (TS,),
            )
        result = health_watch.check_orphaned_rows()
        assert "quest_history.user_id: 1行" in result
        assert "user_inventory.user_id: 1行" in result


class TestExpectedOrphansAreNotReported:
    """マスタ退役による孤児は通知しない(毎時cronでの恒久的な誤検知を避ける)。"""

    def test_retired_quest_leaves_history_without_alarming(self, clean_db):
        """`sync_master_data` の DELETE ... NOT IN は設計どおりマスタ行を消し、
        履歴は残す(#700 で退役6件を実際に削除している)。これを異常として
        報告すると恒久的な誤検知になる。"""
        with get_db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO quest_history (user_id, quest_id, completed_at, status) "
                "VALUES (?, ?, ?, ?)",
                ("u1", 12345, TS, "approved"),  # マスタに無い quest_id
            )
        assert health_watch.check_orphaned_rows() is None

    def test_retired_reward_leaves_history_without_alarming(self, clean_db):
        with get_db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO reward_history (user_id, reward_id, cost_gold, redeemed_at) "
                "VALUES (?, ?, ?, ?)",
                ("u1", 99999, 5, TS),  # マスタに無い reward_id
            )
        assert health_watch.check_orphaned_rows() is None

    def test_expected_counts_are_still_logged(self, clean_db, monkeypatch):
        """通知しないが、FK を張れるかの判断材料としてログには必ず残すこと。"""
        with get_db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO quest_history (user_id, quest_id, completed_at, status) "
                "VALUES (?, ?, ?, ?)",
                ("u1", 12345, TS, "approved"),
            )

        logged: list[str] = []
        monkeypatch.setattr(health_watch.logger, "info", lambda msg, *a: logged.append(str(msg)))
        health_watch.check_orphaned_rows()

        joined = "\n".join(logged)
        assert "quest_history.quest_id=1" in joined, joined
        assert "通知しません" in joined


class TestUnavailableDatabaseIsTolerated:
    def test_operational_error_is_skipped_not_reported(self, clean_db, monkeypatch):
        """DBが読めない環境(初回セットアップ中・一時的なロック)では
        「異常」ではなく「今回は判定できない」として扱うこと。
        毎時cronで走るため、DB不在の環境で恒久的に失敗し続けてはならない。"""
        def _boom(*args, **kwargs):
            raise sqlite3.OperationalError("no such table: quest_history")

        monkeypatch.setattr(health_watch, "get_ro_connection", _boom)
        assert health_watch.check_orphaned_rows() is None


class TestRegisteredInRunChecks:
    def test_orphaned_rows_is_part_of_run_checks(self, monkeypatch):
        """チェックを書いても run_checks に登録し忘れると一切動かない。
        `test_quest_authorization.py` があるのに sync_master が対象外だった
        (#739)のと同じ漏れを防ぐ。"""
        import inspect
        source = inspect.getsource(health_watch.run_checks)
        assert '("orphaned_rows", check_orphaned_rows)' in source

    def test_docstring_lists_the_new_check(self):
        assert "参照整合性" in health_watch.__doc__
