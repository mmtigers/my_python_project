# MY_HOME_SYSTEM/tests/test_core_database.py
"""
core/database.py の get_db_cursor (リトライ・ロールバック) と
save_log_generic / save_log_async / execute_read_query のテスト。

既存 tests/test_core_database.py は PRAGMA foreign_keys=ON の確認のみを
行っていたが、本ファイルはそれに加えて以下のDB操作そのものの挙動を検証する:
- "database is locked" 時のリトライ・最終的な成功
- リトライ上限到達時の挙動(既知の粗さ: contextmanagerがyieldせず
  RuntimeErrorになる。callerはこれを想定していないため、記録目的でテストする)
- "locked" 以外のOperationalError・想定外の例外でのrollback
- save_log_generic の成功/失敗、save_log_asyncのラッパー動作
"""
import os
import sqlite3
import sys
from unittest.mock import patch

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
import core.database as db


def _seed_table(cur):
    cur.execute("""
        INSERT INTO quest_users (user_id, name, job_class, level, exp, gold)
        VALUES ('dad', 'Dad', 'Warrior', 1, 0, 0)
    """)


def test_foreign_keys_pragma_is_enabled():
    original_db_path = config.SQLITE_DB_PATH
    config.SQLITE_DB_PATH = ":memory:"
    try:
        with db.get_db_cursor() as cur:
            cur.execute("PRAGMA foreign_keys")
            value = cur.fetchone()[0]
            assert value == 1
    finally:
        config.SQLITE_DB_PATH = original_db_path


class TestGetDbCursorRetry:
    def test_succeeds_immediately_when_no_lock(self, isolated_db):
        with db.get_db_cursor(commit=True) as cur:
            _seed_table(cur)
        with db.get_db_cursor() as cur:
            row = cur.execute("SELECT * FROM quest_users WHERE user_id='dad'").fetchone()
        assert row is not None

    def test_retries_on_locked_error_then_succeeds(self, isolated_db):
        real_connect = sqlite3.connect
        call_count = {"n": 0}

        def _flaky_connect(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise sqlite3.OperationalError("database is locked")
            return real_connect(*args, **kwargs)

        with patch("core.database.time.sleep", return_value=None), \
             patch("core.database.sqlite3.connect", side_effect=_flaky_connect):
            with db.get_db_cursor(commit=True) as cur:
                _seed_table(cur)

        assert call_count["n"] == 3
        with db.get_db_cursor() as cur:
            row = cur.execute("SELECT * FROM quest_users WHERE user_id='dad'").fetchone()
        assert row is not None

    def test_retry_limit_reached_raises_runtime_error(self, isolated_db):
        """
        既知の粗さの記録: "locked"が5回連続すると、contextmanagerが一度もyieldせずに
        終了するため、Pythonの仕様により RuntimeError("generator didn't yield") になる。
        呼び出し元(save_log_generic等)はこれを想定した例外処理をしていない。
        この挙動が変わった場合に気付けるよう、現状を固定するテストとして残す。
        """
        def _always_locked(*args, **kwargs):
            raise sqlite3.OperationalError("database is locked")

        with patch("core.database.time.sleep", return_value=None), \
             patch("core.database.sqlite3.connect", side_effect=_always_locked):
            with pytest.raises(RuntimeError):
                with db.get_db_cursor() as cur:
                    pass  # pragma: no cover - 到達しない

    def test_non_lock_operational_error_rolls_back_and_reraises_immediately(self, isolated_db):
        with pytest.raises(sqlite3.OperationalError):
            with db.get_db_cursor(commit=True) as cur:
                cur.execute("SELECT * FROM this_table_does_not_exist")

    def test_exception_inside_with_block_rolls_back_and_reraises(self, isolated_db):
        """with文内(呼び出し元)で発生した例外もrollbackされた上で再送出されること"""
        with db.get_db_cursor(commit=True) as cur:
            _seed_table(cur)

        with pytest.raises(sqlite3.IntegrityError):
            with db.get_db_cursor(commit=True) as cur:
                # user_idはPRIMARY KEYのため重複INSERTでIntegrityError
                cur.execute(
                    "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold) "
                    "VALUES ('dad', 'Dad2', 'Warrior', 1, 0, 0)"
                )

        # rollbackされているため元のレコードは変化していないこと
        with db.get_db_cursor() as cur:
            row = cur.execute("SELECT name FROM quest_users WHERE user_id='dad'").fetchone()
        assert row["name"] == "Dad"


class TestSaveLogGeneric:
    def test_saves_row_successfully(self, isolated_db):
        result = db.save_log_generic(
            config.SQLITE_TABLE_SENSOR,
            ["timestamp", "device_name", "device_id", "device_type", "contact_state"],
            ("2026-01-01T00:00:00", "テストセンサー", "mac1", "Contact Sensor", "open"),
        )
        assert result is True
        with db.get_db_cursor() as cur:
            row = cur.execute(
                f"SELECT * FROM {config.SQLITE_TABLE_SENSOR} WHERE device_id='mac1'"
            ).fetchone()
        assert row is not None
        assert row["device_name"] == "テストセンサー"

    def test_returns_false_on_invalid_table(self, isolated_db):
        result = db.save_log_generic("table_that_does_not_exist", ["col"], ("value",))
        assert result is False

    def test_returns_false_on_column_mismatch(self, isolated_db):
        result = db.save_log_generic(
            config.SQLITE_TABLE_SENSOR, ["nonexistent_column"], ("value",)
        )
        assert result is False


class TestSaveLogAsync:
    @pytest.mark.asyncio
    async def test_delegates_to_save_log_generic(self, isolated_db):
        result = await db.save_log_async(
            config.SQLITE_TABLE_SENSOR,
            ["timestamp", "device_name", "device_id", "device_type", "contact_state"],
            ("2026-01-01T00:00:00", "非同期センサー", "mac2", "Contact Sensor", "closed"),
        )
        assert result is True
        with db.get_db_cursor() as cur:
            row = cur.execute(
                f"SELECT * FROM {config.SQLITE_TABLE_SENSOR} WHERE device_id='mac2'"
            ).fetchone()
        assert row is not None


class TestExecuteReadQuery:
    def test_returns_no_data_message_when_empty(self, isolated_db):
        result = db.execute_read_query("SELECT * FROM quest_users WHERE user_id = ?", ("nobody",))
        assert result == "該当するデータはありませんでした。"

    def test_returns_json_rows_when_found(self, isolated_db):
        with db.get_db_cursor(commit=True) as cur:
            _seed_table(cur)
        result = db.execute_read_query("SELECT user_id, name FROM quest_users WHERE user_id = ?", ("dad",))
        assert '"user_id": "dad"' in result
        assert '"name": "Dad"' in result

    def test_returns_error_message_on_malformed_sql(self, isolated_db):
        result = db.execute_read_query("SELECT * FROM nonexistent_table_xyz")
        assert result.startswith("検索エラー:")
