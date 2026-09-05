# MY_HOME_SYSTEM/tests/test_migrations.py
"""
core/migrations.py (バージョン管理されたスキーママイグレーション) のテスト。
"""
import os
import sqlite3
import sys
from unittest.mock import patch

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.migrations import apply_pending_migrations, _split_statements, _strip_line_comment


def _make_minimal_schema(conn: sqlite3.Connection) -> None:
    """0001以降のマイグレーションSQLが対象とする最低限のテーブルだけを、
    旧スキーマ(マイグレーション未適用)状態で用意する。

    Issue #330で追加された 0000_baseline_schema.sql は全文 CREATE TABLE IF NOT EXISTS の
    ため、ここで作った既存テーブルはそのまま維持され(=旧スキーマDBの再現が保たれ)、
    ここに無いテーブルはベースラインが補完する。device_records の timestamp は
    ベースラインのインデックス(idx_device_records_device_ts)が参照するため、
    実際の旧DBと同様に最初から持たせておく。"""
    conn.executescript("""
        CREATE TABLE quest_users (user_id TEXT PRIMARY KEY, name TEXT);
        CREATE TABLE quest_master (quest_id INTEGER PRIMARY KEY, title TEXT);
        CREATE TABLE reward_master (reward_id INTEGER PRIMARY KEY, title TEXT);
        CREATE TABLE quest_history (id INTEGER PRIMARY KEY, user_id TEXT, quest_id INTEGER);
        CREATE TABLE device_records (id INTEGER PRIMARY KEY, device_id TEXT, timestamp DATETIME);
        CREATE TABLE weather_history (id INTEGER PRIMARY KEY, date TEXT UNIQUE, min_temp REAL, max_temp REAL, weather_desc TEXT, recorded_at TEXT);
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

        cols = [row[1] for row in conn.execute("PRAGMA table_info(weather_history)").fetchall()]
        assert "location" in cols
        assert "max_pop" in cols
        assert "umbrella_level" in cols

        role = conn.execute("SELECT role FROM quest_users WHERE user_id='dad'").fetchone()[0]
        assert role == "role_adult"

        applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations").fetchall()}
        assert "0001_add_quest_users_role.sql" in applied
        assert "0002_add_quest_master_reset_period.sql" in applied
        assert "0003_add_reward_master_description.sql" in applied
        assert "0004_add_coop_quest_link.sql" in applied
        assert "0007_add_weather_history_location_columns.sql" in applied
    finally:
        conn.close()


def test_migration_0008_fixes_quest_master_reset_period_column_default():
    """
    Issue #329: 0002 で焼き付いたカラムDEFAULT 'weekly_monday' は ALTER TABLE では
    変更できないため、0008 がテーブル再作成方式で 'daily' に修正すること。
    既存行のデータが失われず、DEFAULT未指定のINSERTにも 'daily' が入ること。
    """
    conn = sqlite3.connect(":memory:")
    try:
        _make_minimal_schema(conn)
        conn.execute("INSERT INTO quest_master (quest_id, title) VALUES (1, 'そうじ')")
        conn.commit()

        apply_pending_migrations(conn)

        # カラムDEFAULTが 'daily' に修正されていること
        defaults = {row[1]: row[4] for row in conn.execute("PRAGMA table_info(quest_master)").fetchall()}
        assert defaults["reset_period"] == "'daily'"

        # 既存行が保持され、0002由来のDEFAULT('weekly_monday')は0005/0008で補正済みであること
        row = conn.execute("SELECT title, reset_period FROM quest_master WHERE quest_id = 1").fetchone()
        assert row == ("そうじ", "daily")

        # reset_period 未指定でINSERTした新規行にDEFAULTの 'daily' が入ること
        conn.execute("INSERT INTO quest_master (title) VALUES ('あたらしいクエスト')")
        val = conn.execute(
            "SELECT reset_period FROM quest_master WHERE title = 'あたらしいクエスト'"
        ).fetchone()[0]
        assert val == "daily"

        # 作業用テーブルが残っていないこと
        leftover = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='quest_master_rebuild_0008'"
        ).fetchone()
        assert leftover is None
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


def test_apply_pending_migrations_still_runs_data_migration_after_duplicate_column(tmp_path):
    """
    #99: ALTER TABLE ADD COLUMN が「既に別経路で列だけ追加済み」により
    duplicate column で失敗しても、同一マイグレーションファイル内の後続の
    データ移行文(UPDATE)は実行され、そのバージョンが適用済み記録されること。

    以前は conn.executescript() でスクリプト全体を一度に実行していたため、
    ALTER TABLE の失敗でスクリプト全体の実行が即座に中断され、後続のUPDATE文が
    1文も実行されないまま「適用済み」と記録され、恒久的にデータ移行が
    行われなくなっていた。
    """
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "0001_add_role.sql").write_text(
        "ALTER TABLE quest_users ADD COLUMN role TEXT;\n"
        "UPDATE quest_users SET role = 'role_adult' WHERE user_id = 'dad';\n"
    )

    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE TABLE quest_users (user_id TEXT PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO quest_users (user_id, name) VALUES ('dad', 'Dad')")
        # 別経路(旧来の実行時ALTER等)で role カラムだけ先に追加済みの状態を再現
        conn.execute("ALTER TABLE quest_users ADD COLUMN role TEXT")
        conn.commit()

        with patch("core.migrations.MIGRATIONS_DIR", str(migrations_dir)):
            apply_pending_migrations(conn)

        # ALTERはduplicate columnで失敗するが、後続のUPDATEは実行されていること
        role = conn.execute("SELECT role FROM quest_users WHERE user_id='dad'").fetchone()[0]
        assert role == "role_adult"

        applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations").fetchall()}
        assert "0001_add_role.sql" in applied
    finally:
        conn.close()


def test_apply_pending_migrations_reraises_and_does_not_record_unknown_errors(tmp_path):
    """
    M-2: "duplicate column"/"already exists" のような既知の「適用済み」パターン以外の
    OperationalError(SQL誤り・ロック・ディスクフル等)は、適用済みとして追認せず、
    バージョンも記録せずにそのまま例外を再送出すること。
    """
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "0001_broken.sql").write_text(
        "ALTER TABLE this_table_does_not_exist ADD COLUMN foo TEXT;"
    )

    conn = sqlite3.connect(":memory:")
    try:
        with patch("core.migrations.MIGRATIONS_DIR", str(migrations_dir)):
            with pytest.raises(sqlite3.OperationalError):
                apply_pending_migrations(conn)

        applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations").fetchall()}
        assert "0001_broken.sql" not in applied
    finally:
        conn.close()


class TestSplitStatementsRobustness:
    """#411 品質: _split_statementsのロバスト化。

    このリポジトリの規約(コメント・docstringは日本語で書く)ではALTER文の前に
    長い日本語の説明コメントを書くことが多く、以前の単純な';'分割だと、その
    プローズ文中に句点代わりのセミコロンが登場した場合に、コメントを読み切る
    前に誤って文が分割されてしまう恐れがあった。
    """

    def test_semicolon_inside_a_line_comment_does_not_split_the_statement(self):
        sql = (
            "-- この修正ではA;Bのような構成にした。\n"
            "ALTER TABLE foo ADD COLUMN bar TEXT;\n"
        )
        assert _split_statements(sql) == ["ALTER TABLE foo ADD COLUMN bar TEXT"]

    def test_multiple_statements_with_comments_split_correctly(self):
        sql = (
            "-- 1つ目のカラム追加\n"
            "ALTER TABLE foo ADD COLUMN a TEXT;\n"
            "-- 2つ目; セミコロンを含む説明文\n"
            "ALTER TABLE foo ADD COLUMN b TEXT;\n"
            "UPDATE foo SET a = 'x';\n"
        )
        assert _split_statements(sql) == [
            "ALTER TABLE foo ADD COLUMN a TEXT",
            "ALTER TABLE foo ADD COLUMN b TEXT",
            "UPDATE foo SET a = 'x'",
        ]

    def test_blank_and_comment_only_lines_are_ignored(self):
        sql = "\n-- コメントのみの行\n\nALTER TABLE foo ADD COLUMN c TEXT;\n"
        assert _split_statements(sql) == ["ALTER TABLE foo ADD COLUMN c TEXT"]

    def test_strip_line_comment_does_not_treat_hyphens_inside_string_literal_as_comment(self):
        # 文字列リテラル内の "--" はコメント開始として扱わない
        assert _strip_line_comment("UPDATE foo SET note = 'a--b'") == "UPDATE foo SET note = 'a--b'"

    def test_strip_line_comment_removes_trailing_comment(self):
        assert _strip_line_comment("ALTER TABLE foo ADD COLUMN c TEXT -- 説明") == "ALTER TABLE foo ADD COLUMN c TEXT "
