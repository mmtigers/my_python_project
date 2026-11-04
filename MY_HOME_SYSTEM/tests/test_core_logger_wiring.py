# MY_HOME_SYSTEM/tests/test_core_logger_wiring.py
"""
Issue #1089: core モジュールの失敗ログが home_system.log に届くことの回帰テスト。

以前の core/database.py・core/utils.py・core/alexa_verifier.py は素の
logging.getLogger() を使っており、core.logger.setup_logging() が付ける
ファイル/Discord ハンドラにつながっていなかった。save_log_generic() の
「データ保存失敗」は lastResort(stderr)にしか出ず、子プロセスでは正常終了時に
捨てられるため、2026-11-02 のセンサー保存失敗では SQLite のエラー内容が
どこにも残らなかった。

core.logger のロガーは propagate=False で caplog には届かないため、対象ロガーに
記録用ハンドラを直接付けて検証する。Discord 系の環境変数は conftest が空にして
いるので、ERROR を出しても実際の通知は飛ばない。
"""
import logging
import os
import sqlite3
import subprocess
import sys
from contextlib import contextmanager
from logging.handlers import WatchedFileHandler

import pytest

MY_HOME_SYSTEM_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(MY_HOME_SYSTEM_DIR)

from core import alexa_verifier, database, utils  # noqa: E402


class _ListHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def captured_db_log():
    handler = _ListHandler()
    database.logger.addHandler(handler)
    try:
        yield handler.records
    finally:
        database.logger.removeHandler(handler)


def _writes_home_system_log(logger: logging.Logger) -> bool:
    return any(
        isinstance(h, WatchedFileHandler) and os.path.basename(h.baseFilename) == "home_system.log"
        for h in logger.handlers
    )


class TestLoggersAreWiredToHomeSystemLog:
    @pytest.mark.parametrize(
        "get_target",
        [
            lambda: database.logger,
            lambda: alexa_verifier.logger,
            lambda: utils._get_logger(),
        ],
        ids=["core.database", "core.alexa_verifier", "core.utils"],
    )
    def test_logger_has_home_system_log_handler(self, get_target):
        assert _writes_home_system_log(get_target())

    def test_core_utils_logger_is_not_the_core_parent_logger(self):
        # "core" にハンドラを付けると core.discord(core.logger を import できない)等の
        # 子ロガーまで Discord ハンドラへ伝播し、Webhook 失敗ログが再帰しうる。
        assert utils._get_logger().name == "core.utils"
        assert logging.getLogger("core").handlers == []


class TestSaveFailureLogsErrorTypeAndMessage:
    def test_save_log_generic_logs_sqlite_error_type_and_message(self, isolated_db, captured_db_log):
        assert database.save_log_generic("no_such_table_1089", ["a"], (1,)) is False

        messages = [r.getMessage() for r in captured_db_log if r.levelno == logging.ERROR]
        assert any(
            "データ保存失敗 (no_such_table_1089)" in m and "OperationalError" in m and "no such table" in m
            for m in messages
        ), messages

    def test_save_logs_batch_generic_logs_sqlite_error_type_and_message(self, isolated_db, captured_db_log):
        assert database.save_logs_batch_generic("no_such_table_1089", ["a"], [(1,), (2,)]) is False

        messages = [r.getMessage() for r in captured_db_log if r.levelno == logging.ERROR]
        assert any(
            "バッチデータ保存失敗 (no_such_table_1089)" in m and "OperationalError" in m and "no such table" in m
            for m in messages
        ), messages

    def test_locked_and_disk_io_errors_are_distinguishable(self, monkeypatch, captured_db_log):
        """ロック競合と SD カード劣化の前兆(disk I/O error)をログだけで区別できること。"""
        for detail in ("database is locked", "disk I/O error"):
            @contextmanager
            def _failing_cursor(commit=False, _detail=detail):
                raise sqlite3.OperationalError(_detail)
                yield  # pragma: no cover

            monkeypatch.setattr(database, "get_db_cursor", _failing_cursor)
            assert database.save_log_generic("power_usage", ["wattage"], (1.0,)) is False

        messages = [r.getMessage() for r in captured_db_log if r.levelno == logging.ERROR]
        assert "データ保存失敗 (power_usage): OperationalError: database is locked" in messages
        assert "データ保存失敗 (power_usage): OperationalError: disk I/O error" in messages


class TestNoImportCycle:
    """config は import 時に core.utils を、core.logger は config を import する。
    core.utils はロガーを遅延取得しているため、どの順で import しても循環しないこと。"""

    @pytest.mark.parametrize(
        "statement",
        [
            "import core.utils; import config; import core.database",
            "import config; import core.database; import core.alexa_verifier",
            "import core.database",
            "import core.alexa_verifier",
            "import core.nas_utils",
        ],
    )
    def test_fresh_interpreter_import(self, statement, tmp_path):
        env = dict(os.environ)
        env.setdefault("NAS_MOUNT_POINT", str(tmp_path / "nas"))
        result = subprocess.run(
            [sys.executable, "-c", statement + "; import core.utils as u; assert u._get_logger().name == 'core.utils'"],
            cwd=MY_HOME_SYSTEM_DIR,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
