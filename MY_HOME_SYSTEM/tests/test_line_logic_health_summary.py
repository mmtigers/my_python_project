# MY_HOME_SYSTEM/tests/test_line_logic_health_summary.py
"""
#571回帰防止: handlers/line_logic.py の get_daily_health_summary() が、
Issue #375で否定表現用に正規化される固定文字列 CONDITION_NOT_GENKI = "元気なし"
(handlers/line_handler.py)を、部分文字列マッチ("元気" in status)により
誤って✅(元気)と表示していた問題の回帰テスト。
"""
import os
import sys
import sqlite3

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from core.utils import get_now_iso
from handlers import line_logic


def _insert_health_record(child_name: str, condition: str) -> None:
    with sqlite3.connect(config.SQLITE_DB_PATH) as conn:
        conn.execute(
            f"INSERT INTO {config.SQLITE_TABLE_CHILD} "
            "(user_id, user_name, child_name, condition, timestamp) VALUES (?, ?, ?, ?, ?)",
            ("U1", "テストユーザー", child_name, condition, get_now_iso()),
        )
        conn.commit()


class TestGetDailyHealthSummaryIcon:
    def test_negative_condition_shows_warning_icon(self, isolated_db):
        child_name = line_logic.TARGET_MEMBERS[0]
        _insert_health_record(child_name, "元気なし")

        summary = line_logic.get_daily_health_summary()

        line = next(ln for ln in summary.splitlines() if child_name in ln)
        assert line.startswith("⚠️"), f"「元気なし」は警告アイコンで表示されるべき: {line!r}"
        assert not line.startswith("✅")

    def test_positive_condition_still_shows_ok_icon(self, isolated_db):
        child_name = line_logic.TARGET_MEMBERS[0]
        _insert_health_record(child_name, "😊 元気いっぱい")

        summary = line_logic.get_daily_health_summary()

        line = next(ln for ln in summary.splitlines() if child_name in ln)
        assert line.startswith("✅"), f"通常の「元気」表現は従来どおり✅で表示されるべき: {line!r}"
