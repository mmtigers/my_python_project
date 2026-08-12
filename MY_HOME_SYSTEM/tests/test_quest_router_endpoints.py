# MY_HOME_SYSTEM/tests/test_quest_router_endpoints.py
"""
routers/quest_router.py の残りのエンドポイント(TestClient経由)。

test_quest_router_api.py は complete/approve/reject/purchase/upload を
カバーしているが、本ファイルは以下を補う:
sync_master, data, family/chronicle, seed, user/update, test_sound,
admin/boss/update, family-mileage(GET/PUT), inventory系, analytics/weekly,
equip/purchase, equip/change
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
            "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold) VALUES "
            "('dad', 'Dad', 'Warrior', 1, 0, 100), ('daughter', 'Daughter', 'Novice', 1, 0, 10)"
        )
        cur.execute(
            "INSERT INTO quest_master (quest_id, title, quest_type, exp_gain, gold_gain) VALUES "
            "(101, 'TestQuest', 'daily', 10, 5)"
        )
        cur.execute(
            "INSERT INTO reward_master (reward_id, title, cost_gold) VALUES (201, 'TestReward', 50)"
        )
        cur.execute(
            "INSERT INTO equipment_master (equipment_id, name, type, power, cost_gold, icon_key) VALUES "
            "(301, 'つよいけん', 'weapon', 10, 30, '⚔️'), (302, 'よわいけん', 'weapon', 5, 20, '🗡️')"
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
        for key in ["users", "quests", "rewards", "boss"]:
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


class TestAdminBossUpdate:
    def test_no_fields_returns_no_change(self, seeded_client):
        res = seeded_client.post("/api/quest/admin/boss/update", json={})
        assert res.status_code == 200
        assert res.json()["status"] == "no_change"

    def test_updates_boss_hp_directly(self, seeded_client):
        with common.get_db_cursor(commit=True) as cur:
            cur.execute("""
                INSERT INTO party_state (id, current_boss_id, current_hp, max_hp, week_start_date, is_defeated, total_damage, charge_gauge, updated_at)
                VALUES (1, 1, 1000, 1000, '2026-01-01', 0, 0, 0, '2026-01-01T00:00:00')
            """)
        res = seeded_client.post(
            "/api/quest/admin/boss/update", json={"current_hp": 500, "is_defeated": False}
        )
        assert res.status_code == 200
        assert res.json()["status"] == "updated"

        with common.get_db_cursor() as cur:
            row = cur.execute("SELECT current_hp FROM party_state WHERE id=1").fetchone()
        assert row["current_hp"] == 500


class TestFamilyMileage:
    def test_get_before_any_target_set_returns_is_set_false(self, seeded_client):
        res = seeded_client.get("/api/quest/family-mileage")
        assert res.status_code == 200
        assert res.json()["is_set"] is False

    def test_put_sets_target_then_get_reflects_it(self, seeded_client):
        res = seeded_client.put(
            "/api/quest/family-mileage", json={"target_name": "沖縄旅行", "target_exp": 5000}
        )
        assert res.status_code == 200
        assert res.json()["status"] == "updated"

        res2 = seeded_client.get("/api/quest/family-mileage")
        body = res2.json()
        assert body["is_set"] is True
        assert body["target_name"] == "沖縄旅行"
        assert body["target_exp"] == 5000
        assert body["current_exp"] == 0

    def test_put_again_archives_previous_target_to_history(self, seeded_client):
        seeded_client.put("/api/quest/family-mileage", json={"target_name": "旅行A", "target_exp": 1000})
        seeded_client.put("/api/quest/family-mileage", json={"target_name": "旅行B", "target_exp": 2000})

        with common.get_db_cursor() as cur:
            history_count = cur.execute("SELECT COUNT(*) as c FROM family_mileage_history").fetchone()["c"]
        assert history_count == 1


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


class TestEquipmentPurchaseAndChange:
    def test_purchase_equipment_success(self, seeded_client):
        res = seeded_client.post("/api/quest/equip/purchase", json={"user_id": "dad", "equipment_id": 301})
        assert res.status_code == 200
        assert res.json()["newGold"] == 70

    def test_purchase_same_equipment_twice_returns_400(self, seeded_client):
        seeded_client.post("/api/quest/equip/purchase", json={"user_id": "dad", "equipment_id": 301})
        res = seeded_client.post("/api/quest/equip/purchase", json={"user_id": "dad", "equipment_id": 301})
        assert res.status_code == 400

    def test_change_equipment_equips_and_unequips_same_type(self, seeded_client):
        seeded_client.post("/api/quest/equip/purchase", json={"user_id": "dad", "equipment_id": 301})
        seeded_client.post("/api/quest/equip/purchase", json={"user_id": "dad", "equipment_id": 302})

        res1 = seeded_client.post("/api/quest/equip/change", json={"user_id": "dad", "equipment_id": 301})
        assert res1.status_code == 200

        res2 = seeded_client.post("/api/quest/equip/change", json={"user_id": "dad", "equipment_id": 302})
        assert res2.status_code == 200

        with common.get_db_cursor() as cur:
            equipped = cur.execute(
                "SELECT equipment_id FROM user_equipments WHERE user_id='dad' AND is_equipped=1"
            ).fetchall()
        # 同じtype(weapon)なので、最後に装備したものだけがis_equipped=1のこと
        assert len(equipped) == 1
        assert equipped[0]["equipment_id"] == 302


class TestWeeklyAnalytics:
    def test_returns_analytics_shape_with_no_data(self, seeded_client):
        res = seeded_client.get("/api/quest/analytics/weekly")
        assert res.status_code == 200
        body = res.json()
        assert "rankings" in body
        assert "dailyStats" in body

    def test_reflects_completed_quest_in_rankings(self, seeded_client):
        seeded_client.post("/api/quest/complete", json={"user_id": "dad", "quest_id": 101})
        res = seeded_client.get("/api/quest/analytics/weekly")
        body = res.json()
        assert any(r["user_id"] == "dad" for r in body["rankings"]["count"])
