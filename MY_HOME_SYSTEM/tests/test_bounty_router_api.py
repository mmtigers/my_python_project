# MY_HOME_SYSTEM/tests/test_bounty_router_api.py
"""
routers/bounty_router.py の追加テスト。

既存の test_bounty_router.py は is_target_match と accept_bounty の同時実行制御を
カバーしているが、以下は未カバーだったため本ファイルで補う:

- BountyCreate.reward_gold の field_validator の境界値 (hypothesisで網羅的に)
- IDOR: 作成者以外による削除、受注者以外による完了報告、依頼主以外による承認
"""
import os
import sys

import pytest
from fastapi import HTTPException
from hypothesis import given, settings, strategies as st

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import common
from routers.bounty_router import (
    BountyCreate, BountyAction, create_bounty, accept_bounty,
    complete_bounty, approve_bounty, delete_bounty,
)


class TestRewardGoldBoundaryValidation:
    """
    BountyCreate.parse_reward_gold は「上限を超えたら10000に丸める、負数は0にする、
    数値変換できなければ0にする」という安全側フォールバック方針。
    """

    @pytest.mark.parametrize(
        "raw_value,expected",
        [
            (0, 0),
            (1, 1),
            (10_000, 10_000),
            (10_001, 10_000),
            (-1, 0),
            (-999999, 0),
            (99999999, 10_000),
            ("abc", 0),
            ("5000", 5000),
            (5000.9, 5000),
            (None, 0),
        ],
    )
    def test_reward_gold_boundary_values(self, raw_value, expected):
        bounty = BountyCreate(
            title="t", reward_gold=raw_value, target_type="ALL", created_by="dad"
        )
        assert bounty.reward_gold == expected

    @given(raw_value=st.integers(min_value=-10_000_000, max_value=10_000_000))
    @settings(max_examples=100)
    def test_reward_gold_is_always_clamped_into_valid_range(self, raw_value):
        bounty = BountyCreate(title="t", reward_gold=raw_value, target_type="ALL", created_by="dad")
        assert 0 <= bounty.reward_gold <= 10_000

    @given(raw_value=st.text(max_size=20))
    @settings(max_examples=50)
    def test_arbitrary_text_never_raises_and_stays_in_range(self, raw_value):
        bounty = BountyCreate(title="t", reward_gold=raw_value, target_type="ALL", created_by="dad")
        assert 0 <= bounty.reward_gold <= 10_000


class TestBountyIdor:
    """
    「作成者/受注者/依頼主でなければ操作できない」ことを確認する。
    (アプリ全体としてはuser_idをクライアント申告のまま信頼しているため、
    ここで確認できるのは「文字列として異なるuser_idを渡した場合の分岐」までであり、
    なりすまし自体を防げるかは別問題 = CODE_REVIEW_REPORT.md 2.1 として最終報告書に記録する)
    """

    @pytest.fixture(autouse=True)
    def _setup(self, isolated_db):
        create_bounty(BountyCreate(
            title="お風呂掃除", reward_gold=100, target_type="ALL", created_by="dad"
        ))
        with common.get_db_cursor() as cur:
            row = cur.execute("SELECT id FROM bounties ORDER BY id DESC LIMIT 1").fetchone()
        self.bounty_id = row["id"]

    def test_non_creator_cannot_delete_bounty(self):
        with pytest.raises(HTTPException) as exc_info:
            delete_bounty(self.bounty_id, user_id="mom")
        assert exc_info.value.status_code == 403

    def test_creator_can_delete_own_open_bounty(self):
        result = delete_bounty(self.bounty_id, user_id="dad")
        assert result["status"] == "deleted"

    def test_non_assignee_cannot_complete_bounty(self):
        accept_bounty(self.bounty_id, BountyAction(user_id="daughter"))
        with pytest.raises(HTTPException) as exc_info:
            complete_bounty(self.bounty_id, BountyAction(user_id="son"))
        assert exc_info.value.status_code == 400

        with common.get_db_cursor() as cur:
            row = cur.execute("SELECT status FROM bounties WHERE id = ?", (self.bounty_id,)).fetchone()
        assert row["status"] == "TAKEN"

    def test_assignee_can_complete_own_bounty(self):
        accept_bounty(self.bounty_id, BountyAction(user_id="daughter"))
        result = complete_bounty(self.bounty_id, BountyAction(user_id="daughter"))
        assert result["status"] == "pending_approval"

    def test_non_creator_cannot_approve_bounty(self):
        accept_bounty(self.bounty_id, BountyAction(user_id="daughter"))
        complete_bounty(self.bounty_id, BountyAction(user_id="daughter"))
        with pytest.raises(HTTPException) as exc_info:
            approve_bounty(self.bounty_id, BountyAction(user_id="mom"))
        assert exc_info.value.status_code == 403

    def test_creator_can_approve_and_pays_assignee(self):
        with common.get_db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold) VALUES "
                "('daughter', 'Daughter', 'Novice', 1, 0, 0)"
            )
        accept_bounty(self.bounty_id, BountyAction(user_id="daughter"))
        complete_bounty(self.bounty_id, BountyAction(user_id="daughter"))
        result = approve_bounty(self.bounty_id, BountyAction(user_id="dad"))
        assert result["status"] == "completed"
        assert result["reward_paid"] == 100

        with common.get_db_cursor() as cur:
            user = cur.execute("SELECT gold FROM quest_users WHERE user_id='daughter'").fetchone()
        assert user["gold"] == 100

    def test_delete_of_taken_bounty_is_rejected(self):
        accept_bounty(self.bounty_id, BountyAction(user_id="daughter"))
        with pytest.raises(HTTPException) as exc_info:
            delete_bounty(self.bounty_id, user_id="dad")
        assert exc_info.value.status_code == 400
