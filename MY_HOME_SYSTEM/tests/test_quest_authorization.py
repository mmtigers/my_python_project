# MY_HOME_SYSTEM/tests/test_quest_authorization.py
"""
services/quest_service.py の承認・却下フローの権限チェックのテスト。

process_approve_quest / process_reject_quest は approver_id に対応する
quest_users.role が 'role_adult' でない場合は 403 を返す実装になっている。
"""
import os
import sys
from datetime import datetime

import pytest
from fastapi import HTTPException

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
import common
import init_unified_db
from services.quest_service import QuestService


class TestApprovalAuthorization:
    def setup_method(self):
        self.test_db_file = "test_quest_auth_home_system.db"
        self.original_db_path = config.SQLITE_DB_PATH
        config.SQLITE_DB_PATH = self.test_db_file
        init_unified_db.init_db()

        self.quest_service = QuestService()

        with common.get_db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, role) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("dad", "Dad", "Warrior", 1, 0, 0, "role_adult"),
            )
            cur.execute(
                "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, role) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("daughter", "Daughter", "Novice", 1, 0, 0, "role_child"),
            )
            cur.execute(
                "INSERT INTO quest_master (quest_id, title, quest_type, exp_gain, gold_gain) VALUES (?, ?, ?, ?, ?)",
                (301, "お手伝い", "daily", 10, 5),
            )
            cur.execute("""
                INSERT INTO quest_history (user_id, quest_id, quest_title, exp_earned, gold_earned, completed_at, status)
                VALUES (?, ?, ?, ?, ?, ?, 'pending')
            """, ("daughter", 301, "お手伝い", 10, 5, datetime.now().isoformat()))
            self.history_id = cur.lastrowid

    def teardown_method(self):
        config.SQLITE_DB_PATH = self.original_db_path
        if os.path.exists(self.test_db_file):
            try:
                os.remove(self.test_db_file)
            except PermissionError:
                pass

    def test_approve_by_non_parent_is_rejected(self):
        with pytest.raises(HTTPException) as exc_info:
            self.quest_service.process_approve_quest("daughter", self.history_id)
        assert exc_info.value.status_code == 403

    def test_reject_by_non_parent_is_rejected(self):
        with pytest.raises(HTTPException) as exc_info:
            self.quest_service.process_reject_quest("daughter", self.history_id)
        assert exc_info.value.status_code == 403

    def test_approve_by_parent_succeeds(self):
        result = self.quest_service.process_approve_quest("dad", self.history_id)
        assert result["status"] == "success"

        with common.get_db_cursor() as cur:
            hist = cur.execute("SELECT status FROM quest_history WHERE id = ?", (self.history_id,)).fetchone()
        assert hist["status"] == "approved"
