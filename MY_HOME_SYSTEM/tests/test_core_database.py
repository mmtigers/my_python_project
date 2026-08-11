# MY_HOME_SYSTEM/tests/test_core_database.py
"""
core/database.py の PRAGMA foreign_keys=ON 有効化のテスト。
"""
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from core.database import get_db_cursor


def test_foreign_keys_pragma_is_enabled():
    original_db_path = config.SQLITE_DB_PATH
    config.SQLITE_DB_PATH = ":memory:"
    try:
        with get_db_cursor() as cur:
            cur.execute("PRAGMA foreign_keys")
            value = cur.fetchone()[0]
            assert value == 1
    finally:
        config.SQLITE_DB_PATH = original_db_path
