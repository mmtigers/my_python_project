# MY_HOME_SYSTEM/tests/test_bounty_router.py
"""
routers/bounty_router.py のテスト。

- is_target_match の対象判定ロジック
- accept_bounty の排他制御（同時受注リクエストが来ても1人だけ成功すること）
"""
import os
import sys
import threading

import pytest
from fastapi import HTTPException

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
import common
import init_unified_db
from routers.bounty_router import is_target_match, create_bounty, accept_bounty, BountyCreate, BountyAction


class TestIsTargetMatch:
    def test_all_matches_everyone(self):
        assert is_target_match("dad", "ALL", None) is True
        assert is_target_match("daughter", "ALL", None) is True

    def test_user_matches_only_exact_user(self):
        assert is_target_match("dad", "USER", "dad") is True
        assert is_target_match("mom", "USER", "dad") is False

    def test_adults_matches_only_parents(self):
        assert is_target_match("dad", "ADULTS", None) is True
        assert is_target_match("mom", "ADULTS", None) is True
        assert is_target_match("daughter", "ADULTS", None) is False

    def test_children_matches_only_children(self):
        assert is_target_match("daughter", "CHILDREN", None) is True
        assert is_target_match("dad", "CHILDREN", None) is False

    def test_unknown_target_type_matches_nobody(self):
        assert is_target_match("dad", "UNKNOWN_TYPE", None) is False


class TestAcceptBountyConcurrency:
    def setup_method(self):
        self.test_db_file = "test_bounty_home_system.db"
        self.original_db_path = config.SQLITE_DB_PATH
        config.SQLITE_DB_PATH = self.test_db_file
        init_unified_db.init_db()

        create_bounty(BountyCreate(
            title="お風呂掃除", reward_gold=100, target_type="ALL", created_by="dad"
        ))
        with common.get_db_cursor() as cur:
            row = cur.execute("SELECT id FROM bounties ORDER BY id DESC LIMIT 1").fetchone()
            self.bounty_id = row["id"]

    def teardown_method(self):
        config.SQLITE_DB_PATH = self.original_db_path
        if os.path.exists(self.test_db_file):
            try:
                os.remove(self.test_db_file)
            except PermissionError:
                pass

    def test_only_one_of_two_concurrent_accepts_succeeds(self):
        results = {}

        def try_accept(user_id: str):
            try:
                accept_bounty(self.bounty_id, BountyAction(user_id=user_id))
                results[user_id] = "success"
            except HTTPException as e:
                results[user_id] = e.status_code

        threads = [
            threading.Thread(target=try_accept, args=("daughter",)),
            threading.Thread(target=try_accept, args=("son",)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        outcomes = list(results.values())
        assert outcomes.count("success") == 1
        assert outcomes.count(409) == 1

        with common.get_db_cursor() as cur:
            row = cur.execute("SELECT status, assignee_id FROM bounties WHERE id = ?", (self.bounty_id,)).fetchone()
        assert row["status"] == "TAKEN"
        assert row["assignee_id"] in ("daughter", "son")

    def test_creator_cannot_accept_own_bounty(self):
        with pytest.raises(HTTPException) as exc_info:
            accept_bounty(self.bounty_id, BountyAction(user_id="dad"))
        assert exc_info.value.status_code == 400
