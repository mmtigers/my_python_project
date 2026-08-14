# MY_HOME_SYSTEM/tests/test_quest_router_endpoints.py
"""
routers/quest_router.py の残りのエンドポイント(TestClient経由)。

test_quest_router_api.py は complete/approve/reject/purchase/upload を
カバーしているが、本ファイルは以下を補う:
sync_master, data, family/chronicle, seed, user/update, test_sound, inventory系
"""
import os
import sys

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import common
import config


def _seed_basic_data():
    with common.get_db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, role) VALUES "
            "('dad', 'Dad', 'Warrior', 1, 0, 100, 'role_adult'), "
            "('daughter', 'Daughter', 'Novice', 1, 0, 10, 'role_child')"
        )
        cur.execute(
            "INSERT INTO quest_master (quest_id, title, quest_type, exp_gain, gold_gain) VALUES "
            "(101, 'TestQuest', 'daily', 10, 5)"
        )
        cur.execute(
            "INSERT INTO reward_master (reward_id, title, cost_gold) VALUES (201, 'TestReward', 50)"
        )


@pytest.fixture
def seeded_client(isolated_db, api_client):
    _seed_basic_data()
    return api_client


class TestSyncMasterAndSeed:
    def test_sync_master_populates_real_quest_data(self, seeded_client):
        res = seeded_client.post("/api/quest/sync_master")
        assert res.status_code == 200
        assert res.json()["status"] == "synced"

        with common.get_db_cursor() as cur:
            user_count = cur.execute("SELECT COUNT(*) as c FROM quest_users").fetchone()["c"]
            quest_count = cur.execute("SELECT COUNT(*) as c FROM quest_master").fetchone()["c"]
        # quest_dataの実データが投入され、既存のquest_users/quest_masterへupsertされていること
        assert user_count >= 4
        assert quest_count > 0

    def test_seed_endpoint_is_an_alias_for_sync_master(self, seeded_client):
        res = seeded_client.post("/api/quest/seed")
        assert res.status_code == 200
        assert res.json()["status"] == "synced"


class TestGetAllData:
    def test_returns_full_view_data_shape(self, seeded_client):
        res = seeded_client.get("/api/quest/data")
        assert res.status_code == 200
        body = res.json()
        for key in ["users", "quests", "rewards"]:
            assert key in body
        assert any(u["user_id"] == "dad" for u in body["users"])


class TestFamilyChronicle:
    def test_returns_stats_and_chronicle(self, seeded_client):
        seeded_client.post("/api/quest/complete", json={"user_id": "dad", "quest_id": 101})
        res = seeded_client.get("/api/quest/family/chronicle")
        assert res.status_code == 200
        body = res.json()
        assert "stats" in body
        assert body["stats"]["totalQuests"] >= 1
        assert len(body["chronicle"]) >= 1


class TestUpdateUserAvatar:
    def test_updates_avatar_for_existing_user(self, seeded_client):
        res = seeded_client.post(
            "/api/quest/user/update", json={"user_id": "dad", "avatar_url": "/uploads/new.jpg"}
        )
        assert res.status_code == 200
        assert res.json()["avatar"] == "/uploads/new.jpg"

        with common.get_db_cursor() as cur:
            row = cur.execute("SELECT avatar FROM quest_users WHERE user_id='dad'").fetchone()
        assert row["avatar"] == "/uploads/new.jpg"

    def test_returns_404_for_unknown_user(self, seeded_client):
        res = seeded_client.post(
            "/api/quest/user/update", json={"user_id": "nobody", "avatar_url": "/x.jpg"}
        )
        assert res.status_code == 404


class TestSound:
    def test_valid_sound_key_returns_200(self, seeded_client, monkeypatch):
        import sound_manager
        monkeypatch.setattr(sound_manager, "play", lambda key: None)
        res = seeded_client.post("/api/quest/test_sound", json={"sound_key": "level_up"})
        assert res.status_code == 200
        assert res.json()["key"] == "level_up"

    def test_invalid_sound_key_returns_400(self, seeded_client):
        res = seeded_client.post("/api/quest/test_sound", json={"sound_key": "not_a_real_sound"})
        assert res.status_code == 400


class TestInventoryEndpoints:
    def _purchase_reward(self, client, user_id="dad"):
        res = client.post("/api/quest/reward/purchase", json={"user_id": user_id, "reward_id": 201})
        assert res.status_code == 200
        with common.get_db_cursor() as cur:
            row = cur.execute(
                "SELECT id FROM user_inventory WHERE user_id=? ORDER BY id DESC LIMIT 1", (user_id,)
            ).fetchone()
        return row["id"]

    def test_get_inventory_lists_owned_items(self, seeded_client):
        self._purchase_reward(seeded_client)
        res = seeded_client.get("/api/quest/inventory/dad")
        assert res.status_code == 200
        items = res.json()
        assert len(items) == 1
        assert items[0]["status"] == "owned"

    def test_use_item_by_owner_succeeds(self, seeded_client, monkeypatch):
        import sound_manager
        monkeypatch.setattr(sound_manager, "play", lambda key: None)
        inventory_id = self._purchase_reward(seeded_client)

        res = seeded_client.post("/api/quest/inventory/use", json={"user_id": "dad", "inventory_id": inventory_id})
        assert res.status_code == 200
        assert res.json()["status"] == "consumed"

    def test_use_item_by_non_owner_returns_403(self, seeded_client):
        inventory_id = self._purchase_reward(seeded_client, user_id="dad")
        res = seeded_client.post(
            "/api/quest/inventory/use", json={"user_id": "daughter", "inventory_id": inventory_id}
        )
        assert res.status_code == 403

    def test_consume_item_requires_parent_approver(self, seeded_client, monkeypatch):
        import sound_manager
        monkeypatch.setattr(sound_manager, "play", lambda key: None)
        inventory_id = self._purchase_reward(seeded_client)

        res_denied = seeded_client.post(
            "/api/quest/inventory/consume", json={"approver_id": "daughter", "inventory_id": inventory_id}
        )
        assert res_denied.status_code == 403

        res_ok = seeded_client.post(
            "/api/quest/inventory/consume", json={"approver_id": "dad", "inventory_id": inventory_id}
        )
        assert res_ok.status_code == 200

    def test_cancel_usage_returns_item_to_owned_status(self, seeded_client):
        inventory_id = self._purchase_reward(seeded_client)
        with common.get_db_cursor(commit=True) as cur:
            cur.execute("UPDATE user_inventory SET status='pending' WHERE id=?", (inventory_id,))

        res = seeded_client.post(
            "/api/quest/inventory/cancel", json={"user_id": "dad", "inventory_id": inventory_id}
        )
        assert res.status_code == 200
        assert res.json()["status"] == "owned"

    def test_admin_pending_inventory_lists_pending_items(self, seeded_client):
        inventory_id = self._purchase_reward(seeded_client)
        with common.get_db_cursor(commit=True) as cur:
            cur.execute("UPDATE user_inventory SET status='pending' WHERE id=?", (inventory_id,))

        res = seeded_client.get("/api/quest/inventory/admin/pending")
        assert res.status_code == 200
        assert len(res.json()) == 1
