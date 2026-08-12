# MY_HOME_SYSTEM/tests/test_bounty_router_list_and_resign.py
"""
routers/bounty_router.py の GET /list (get_bounties) と /resign (resign_bounty) のテスト。
既存の test_bounty_router.py / test_bounty_router_api.py は accept/complete/approve/delete を
カバーしているが、一覧取得のフィルタリングロジックと辞退フローは未テストだった。
"""
import os
import sys

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import common


def _create_bounty(client, title="お手伝い", target_type="ALL", target_user_id=None, created_by="dad"):
    payload = {
        "title": title, "reward_gold": 50, "target_type": target_type, "created_by": created_by,
    }
    if target_user_id:
        payload["target_user_id"] = target_user_id
    client.post("/api/bounty/create", json=payload)
    with common.get_db_cursor() as cur:
        row = cur.execute("SELECT id FROM bounties ORDER BY id DESC LIMIT 1").fetchone()
    return row["id"]


class TestGetBountiesList:
    def test_creator_sees_own_open_bounty(self, isolated_db, api_client):
        _create_bounty(api_client, created_by="dad")
        res = api_client.get("/api/bounty/list", params={"user_id": "dad"})
        assert res.status_code == 200
        body = res.json()
        assert len(body) == 1
        assert body[0]["is_mine"] is True
        assert body[0]["can_accept"] is False  # 自分の依頼は受注不可

    def test_target_user_sees_open_bounty_as_acceptable(self, isolated_db, api_client):
        _create_bounty(api_client, created_by="dad")
        res = api_client.get("/api/bounty/list", params={"user_id": "daughter"})
        body = res.json()
        assert len(body) == 1
        assert body[0]["is_mine"] is False
        assert body[0]["can_accept"] is True

    def test_user_targeted_bounty_hidden_from_other_users(self, isolated_db, api_client):
        _create_bounty(api_client, target_type="USER", target_user_id="daughter", created_by="dad")
        res = api_client.get("/api/bounty/list", params={"user_id": "son"})
        assert res.json() == []

    def test_adults_only_bounty_hidden_from_children(self, isolated_db, api_client):
        _create_bounty(api_client, target_type="ADULTS", created_by="dad")
        res = api_client.get("/api/bounty/list", params={"user_id": "daughter"})
        assert res.json() == []

    def test_assignee_sees_taken_bounty_even_if_not_target_anymore(self, isolated_db, api_client):
        bounty_id = _create_bounty(api_client, created_by="dad")
        api_client.post(f"/api/bounty/{bounty_id}/accept", json={"user_id": "daughter"})

        res = api_client.get("/api/bounty/list", params={"user_id": "daughter"})
        body = res.json()
        assert len(body) == 1
        assert body[0]["is_assigned_to_me"] is True
        assert body[0]["status"] == "TAKEN"

    def test_missing_user_id_query_param_returns_422(self, isolated_db, api_client):
        res = api_client.get("/api/bounty/list")
        assert res.status_code == 422


class TestResignBounty:
    def test_assignee_can_resign_and_bounty_returns_to_open(self, isolated_db, api_client):
        bounty_id = _create_bounty(api_client, created_by="dad")
        api_client.post(f"/api/bounty/{bounty_id}/accept", json={"user_id": "daughter"})

        res = api_client.post(f"/api/bounty/{bounty_id}/resign", json={"user_id": "daughter"})
        assert res.status_code == 200
        assert res.json()["status"] == "resigned"

        with common.get_db_cursor() as cur:
            row = cur.execute("SELECT status, assignee_id FROM bounties WHERE id=?", (bounty_id,)).fetchone()
        assert row["status"] == "OPEN"
        assert row["assignee_id"] is None

    def test_non_assignee_cannot_resign(self, isolated_db, api_client):
        bounty_id = _create_bounty(api_client, created_by="dad")
        api_client.post(f"/api/bounty/{bounty_id}/accept", json={"user_id": "daughter"})

        res = api_client.post(f"/api/bounty/{bounty_id}/resign", json={"user_id": "son"})
        assert res.status_code == 400

    def test_resign_nonexistent_bounty_returns_404(self, isolated_db, api_client):
        res = api_client.post("/api/bounty/999999/resign", json={"user_id": "daughter"})
        assert res.status_code == 404

    def test_cannot_resign_a_bounty_that_is_still_open(self, isolated_db, api_client):
        bounty_id = _create_bounty(api_client, created_by="dad")
        res = api_client.post(f"/api/bounty/{bounty_id}/resign", json={"user_id": "daughter"})
        assert res.status_code == 400
