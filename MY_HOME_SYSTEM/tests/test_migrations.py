# MY_HOME_SYSTEM/tests/test_migrations.py
"""
core/migrations.py (バージョン管理されたスキーママイグレーション) のテスト。
"""
import os
import sqlite3
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.migrations import apply_pending_migrations


def _make_minimal_schema(conn: sqlite3.Connection) -> None:
    """migrations/ 配下のSQLが対象とする最低限のテーブルだけを用意する"""
    conn.executescript("""
        CREATE TABLE quest_users (user_id TEXT PRIMARY KEY, name TEXT);
        CREATE TABLE quest_master (quest_id INTEGER PRIMARY KEY, title TEXT);
        CREATE TABLE reward_master (reward_id INTEGER PRIMARY KEY, title TEXT);
        CREATE TABLE quest_history (id INTEGER PRIMARY KEY, user_id TEXT, quest_id INTEGER);
    """)
    conn.commit()


def test_apply_pending_migrations_adds_expected_columns():
    conn = sqlite3.connect(":memory:")
    try:
        _make_minimal_schema(conn)
        conn.execute("INSERT INTO quest_users (user_id, name) VALUES ('dad', 'Dad')")
        conn.commit()

        apply_pending_migrations(conn)

        cols = [row[1] for row in conn.execute("PRAGMA table_info(quest_users)").fetchall()]
        assert "role" in cols

        cols = [row[1] for row in conn.execute("PRAGMA table_info(quest_master)").fetchall()]
        assert "reset_period" in cols

        cols = [row[1] for row in conn.execute("PRAGMA table_info(reward_master)").fetchall()]
        assert "description" in cols

        cols = [row[1] for row in conn.execute("PRAGMA table_info(quest_history)").fetchall()]
        assert "linked_history_id" in cols

        role = conn.execute("SELECT role FROM quest_users WHERE user_id='dad'").fetchone()[0]
        assert role == "role_adult"

        applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations").fetchall()}
        assert "0001_add_quest_users_role.sql" in applied
        assert "0002_add_quest_master_reset_period.sql" in applied
        assert "0003_add_reward_master_description.sql" in applied
        assert "0004_add_coop_quest_link.sql" in applied
    finally:
        conn.close()


def test_apply_pending_migrations_is_idempotent():
    conn = sqlite3.connect(":memory:")
    try:
        _make_minimal_schema(conn)

        apply_pending_migrations(conn)
        first_count = conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]

        # 2回目の実行はエラーにならず、レコードも増えない
        apply_pending_migrations(conn)
        second_count = conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]

        assert first_count == second_count
        assert first_count >= 4
    finally:
        conn.close()


def test_apply_pending_migrations_tolerates_column_already_added_elsewhere():
    """
    旧来の実行時ALTER TABLE(services/quest_service.py)等、別経路で
    既にカラムが追加済みの環境でも、マイグレーション適用が起動を止めないこと。
    """
    conn = sqlite3.connect(":memory:")
    try:
        _make_minimal_schema(conn)
        # 別経路で先に role カラムが追加済みの状態を再現
        conn.execute("ALTER TABLE quest_users ADD COLUMN role TEXT")
        conn.commit()

        # 例外を送出せず完走すること
        apply_pending_migrations(conn)

        applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations").fetchall()}
        assert "0001_add_quest_users_role.sql" in applied
    finally:
        conn.close()
