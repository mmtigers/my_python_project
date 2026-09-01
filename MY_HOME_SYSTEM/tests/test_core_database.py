# MY_HOME_SYSTEM/tests/test_core_database.py
"""
core/database.py の get_db_cursor (接続リトライ・単発yield・ロールバック) と
save_log_generic / save_log_async / execute_read_query のテスト。

既存 tests/test_core_database.py は PRAGMA foreign_keys=ON の確認のみを
行っていたが、本ファイルはそれに加えて以下のDB操作そのものの挙動を検証する:
- "database is locked" 時の接続リトライ・最終的な成功
- 接続リトライ上限到達時に元のOperationalErrorがそのまま伝播すること
- with文本体実行中(接続確立後)のlockedエラーはリトライされずrollback+再送出されること
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

    def test_retry_limit_reached_raises_operational_error(self, isolated_db):
        """
        接続確立が5回連続で"locked"になった場合、contextmanagerは一度もyieldせず、
        本文中でRuntimeErrorに化けることなく、元のOperationalErrorがそのまま
        呼び出し元に伝播すること(H-1修正の回帰防止)。
        """
        call_count = {"n": 0}

        def _always_locked(*args, **kwargs):
            call_count["n"] += 1
            raise sqlite3.OperationalError("database is locked")

        with patch("core.database.time.sleep", return_value=None), \
             patch("core.database.sqlite3.connect", side_effect=_always_locked):
            with pytest.raises(sqlite3.OperationalError):
                with db.get_db_cursor():
                    pass  # pragma: no cover - 到達しない

        assert call_count["n"] == 5

    def test_non_lock_operational_error_rolls_back_and_reraises_immediately(self, isolated_db):
        with pytest.raises(sqlite3.OperationalError):
            with db.get_db_cursor(commit=True) as cur:
                cur.execute("SELECT * FROM this_table_does_not_exist")

    def test_locked_error_inside_with_block_does_not_retry_and_reraises(self, isolated_db):
        """
        with文本体の実行中(接続確立後)に発生したlockedエラーは、以前は2回目のyieldを
        試みてRuntimeError("generator didn't stop after throw()")になっていた。
        修正後は本文はリトライせず、rollbackした上でそのまま再送出されること。
        """
        with db.get_db_cursor(commit=True) as cur:
            _seed_table(cur)

        with pytest.raises(sqlite3.OperationalError):
            with db.get_db_cursor(commit=True) as cur:
                cur.execute("SELECT 1")
                raise sqlite3.OperationalError("database is locked")

        # rollbackされているため元のレコードは変化していないこと
        with db.get_db_cursor() as cur:
            row = cur.execute("SELECT name FROM quest_users WHERE user_id='dad'").fetchone()
        assert row["name"] == "Dad"

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

    def test_rejects_sql_injection_in_table_name(self, isolated_db):
        """B3: table名はSQL文字列へ直接展開されるため、不正な識別子は実行前に拒否する。"""
        result = db.save_log_generic(
            f"{config.SQLITE_TABLE_SENSOR}; DROP TABLE {config.SQLITE_TABLE_SENSOR}--",
            ["timestamp"],
            ("2026-01-01T00:00:00",),
        )
        assert result is False
        with db.get_db_cursor() as cur:
            # テーブル自体が破壊されていないこと
            cur.execute(f"SELECT COUNT(*) as c FROM {config.SQLITE_TABLE_SENSOR}")

    def test_rejects_sql_injection_in_column_name(self, isolated_db):
        """B3: カラム名も同様にSQL文字列へ直接展開されるため、不正な識別子は実行前に拒否する。"""
        result = db.save_log_generic(
            config.SQLITE_TABLE_SENSOR,
            ["timestamp) VALUES ('x'); DROP TABLE " + config.SQLITE_TABLE_SENSOR + "--"],
            ("2026-01-01T00:00:00",),
        )
        assert result is False
        with db.get_db_cursor() as cur:
            cur.execute(f"SELECT COUNT(*) as c FROM {config.SQLITE_TABLE_SENSOR}")


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


class TestSaveLogsBatchGeneric:
    """Issue #231の回帰テスト。

    save_log_generic を複数回呼ぶ実装(handlers/line_logic.py の all_genki等)
    では、各呼び出しがそれぞれ独立にcommitされるため、途中の1件が失敗しても
    既に成功した分はコミット済みのまま残ってしまう。呼び出し元は「1件でも
    失敗すれば全体を失敗扱いとする」と案内し再試行を促す(H-7)が、実際には
    成功済み分が残ったままのため、再試行すると重複して保存されていた。
    save_logs_batch_generic は複数行を単一トランザクションでまとめて保存し、
    1件でも失敗すれば全件をロールバックすることでこれを防ぐ。
    """

    def test_saves_all_rows_successfully(self, isolated_db):
        result = db.save_logs_batch_generic(
            config.SQLITE_TABLE_CHILD,
            ["user_id", "user_name", "child_name", "condition", "timestamp"],
            [
                ("U1", "テスト太郎", "長男", "😊 元気いっぱい", "2026-01-01T00:00:00"),
                ("U1", "テスト太郎", "次男", "😊 元気いっぱい", "2026-01-01T00:00:00"),
            ],
        )
        assert result is True
        with db.get_db_cursor() as cur:
            rows = cur.execute(f"SELECT child_name FROM {config.SQLITE_TABLE_CHILD}").fetchall()
        assert {r["child_name"] for r in rows} == {"長男", "次男"}

    def test_partial_failure_rolls_back_entire_batch(self, isolated_db):
        """1件目は正常な行、2件目は列数不一致でINSERTが失敗する行を与えた場合、
        1件目分も含めて全件がロールバックされテーブルに一切残らないこと
        (真のall-or-nothing)。以前の実装(save_log_genericの複数回呼び出し)
        であれば1件目だけがコミット済みのまま残っていたはずのケース。"""
        result = db.save_logs_batch_generic(
            config.SQLITE_TABLE_CHILD,
            ["user_id", "user_name", "child_name", "condition", "timestamp"],
            [
                ("U1", "テスト太郎", "長男", "😊 元気いっぱい", "2026-01-01T00:00:00"),
                ("U1", "テスト太郎", "次男"),  # 列数不一致で2件目のINSERTが失敗する
            ],
        )
        assert result is False
        with db.get_db_cursor() as cur:
            count = cur.execute(f"SELECT COUNT(*) c FROM {config.SQLITE_TABLE_CHILD}").fetchone()["c"]
        assert count == 0

    def test_returns_false_on_invalid_table(self, isolated_db):
        result = db.save_logs_batch_generic("table_that_does_not_exist", ["col"], [("value",)])
        assert result is False


class TestSaveLogsBatchAsync:
    @pytest.mark.asyncio
    async def test_delegates_to_save_logs_batch_generic(self, isolated_db):
        result = await db.save_logs_batch_async(
            config.SQLITE_TABLE_CHILD,
            ["user_id", "user_name", "child_name", "condition", "timestamp"],
            [("U1", "テスト太郎", "長男", "😊 元気いっぱい", "2026-01-01T00:00:00")],
        )
        assert result is True
        with db.get_db_cursor() as cur:
            row = cur.execute(
                f"SELECT * FROM {config.SQLITE_TABLE_CHILD} WHERE child_name='長男'"
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


class TestExecuteReadQueryConnectionCleanup:
    """Issue #178の回帰テスト: execute_read_queryはconn.close()が正常経路にしか
    無くtry/finallyが無かったため、cursor.execute()が例外を送出する
    (不正なSQL等)たびに接続がGC任せで残りリークしていた。"""

    def _spy_on_close(self, monkeypatch):
        # sqlite3.Connectionはインスタンス単位の属性代入(conn.close = ...)や
        # クラスメソッドの直接上書き(sqlite3.Connection.close = ...)を許可
        # しない('immutable type')C拡張型のため、sqlite3.connect()の
        # factory引数でサブクラスを注入しclose()呼び出しを記録する。
        close_calls = []

        class SpyConnection(sqlite3.Connection):
            def close(self):
                close_calls.append(True)
                super().close()

        real_connect = sqlite3.connect

        def spy_connect(*args, **kwargs):
            kwargs["factory"] = SpyConnection
            return real_connect(*args, **kwargs)

        monkeypatch.setattr(db.sqlite3, "connect", spy_connect)
        return close_calls

    def test_connection_is_closed_when_query_raises(self, isolated_db, monkeypatch):
        close_calls = self._spy_on_close(monkeypatch)

        result = db.execute_read_query("SELECT * FROM nonexistent_table_xyz")

        assert result.startswith("検索エラー:")
        assert close_calls == [True], "cursor.execute()が例外を送出した場合も接続はcloseされるべき"

    def test_connection_is_closed_on_success(self, isolated_db, monkeypatch):
        close_calls = self._spy_on_close(monkeypatch)

        db.execute_read_query("SELECT * FROM quest_users WHERE user_id = ?", ("nobody",))

        assert close_calls == [True]
