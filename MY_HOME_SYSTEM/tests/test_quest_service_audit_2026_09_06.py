# MY_HOME_SYSTEM/tests/test_quest_service_audit_2026_09_06.py
"""
2026-09-06 のリポジトリ品質監査で見つかった Family Quest バックエンドの不具合の回帰テスト。

- reset_period='monthly' がモデルでは許容されるのに周期判定が未実装で、周期内の多重完了・
  多重報酬が可能だった
- pre_requisite_quest_id がフロントエンドのロック表示のみで、API直叩きで素通りできた
- quest_history.gold_earned/exp_earned が NULL の行を承認/取消すると TypeError → 500
- UseItemAction だけ Q-L4 の整数上限が漏れていて inventory_id=2**64 で 500
- 「アイテム使用」行(quest_id=0)が年代記/最近のログにクエスト達成として混入していた
- TV解錠の副作用がトランザクションのコミット前に起動されていた
"""
import datetime
import os
import sys

import pytest
from fastapi import HTTPException
from freezegun import freeze_time

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import common
import config
from services import quest_service as quest_service_module
from services.quest_service import QuestService, UserService, GameSystem, JST


def _seed_adult(gold=100):
    with common.get_db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, role) VALUES "
            "('dad', 'Dad', 'Warrior', 5, 0, ?, 'role_adult')", (gold,)
        )


def _seed_quest(quest_id, reset_period='daily', quest_type='daily', prereq=None, gold=10, exp=20):
    with common.get_db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO quest_master (quest_id, title, quest_type, exp_gain, gold_gain, reset_period, pre_requisite_quest_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (quest_id, f"Quest{quest_id}", quest_type, exp, gold, reset_period, prereq),
        )


class TestMonthlyResetPeriod:
    @freeze_time("2026-09-15 12:00:00+09:00")
    def test_same_month_is_within_period(self):
        qs = QuestService()
        assert qs.is_within_reset_period("2026-09-01T00:30:00+09:00", "monthly") is True
        assert qs.is_within_reset_period("2026-08-31T23:59:59+09:00", "monthly") is False
        assert qs.is_within_reset_period("2025-09-15T12:00:00+09:00", "monthly") is False

    def test_second_completion_in_same_month_is_rejected(self, isolated_db):
        _seed_adult()
        _seed_quest(201, reset_period='monthly')
        qs = QuestService()
        first = qs.process_complete_quest("dad", 201)
        assert first["status"] == "success"
        # スパムチェック(10秒)を越えた後でも、同じ月内なら周期チェックで拒否される
        with common.get_db_cursor(commit=True) as cur:
            earlier = (datetime.datetime.now(JST) - datetime.timedelta(hours=3)).isoformat()
            cur.execute("UPDATE quest_history SET completed_at = ? WHERE user_id='dad' AND quest_id=201", (earlier,))
        with pytest.raises(HTTPException) as exc_info:
            qs.process_complete_quest("dad", 201)
        assert exc_info.value.status_code == 400
        assert "今月" in exc_info.value.detail

    def test_monthly_completion_is_listed_in_completed_quests(self, isolated_db):
        _seed_adult()
        _seed_quest(201, reset_period='monthly')
        QuestService().process_complete_quest("dad", 201)
        data = GameSystem().get_all_view_data()
        assert any(c["quest_id"] == 201 and c["user_id"] == "dad" for c in data["completedQuests"])


class TestPrerequisiteEnforcedServerSide:
    def test_completing_without_prerequisite_is_forbidden(self, isolated_db):
        _seed_adult()
        _seed_quest(1)
        _seed_quest(2, prereq=1)
        qs = QuestService()
        with pytest.raises(HTTPException) as exc_info:
            qs.process_complete_quest("dad", 2)
        assert exc_info.value.status_code == 403
        with common.get_db_cursor() as cur:
            assert cur.execute("SELECT gold FROM quest_users WHERE user_id='dad'").fetchone()["gold"] == 100

    def test_completing_after_prerequisite_approved_succeeds(self, isolated_db):
        _seed_adult()
        _seed_quest(1)
        _seed_quest(2, prereq=1)
        qs = QuestService()
        assert qs.process_complete_quest("dad", 1)["status"] == "success"
        assert qs.process_complete_quest("dad", 2)["status"] == "success"

    def test_pending_prerequisite_does_not_unlock(self, isolated_db):
        """フロント(getQuestLockState)と同じく、前提クエストは承認済み(approved)でなければならない"""
        _seed_adult()
        _seed_quest(1)
        _seed_quest(2, prereq=1)
        with common.get_db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO quest_history (user_id, quest_id, quest_title, exp_earned, gold_earned, completed_at, status) "
                "VALUES ('dad', 1, 'Quest1', 20, 10, ?, 'pending')", (datetime.datetime.now(JST).isoformat(),)
            )
        with pytest.raises(HTTPException) as exc_info:
            QuestService().process_complete_quest("dad", 2)
        assert exc_info.value.status_code == 403

    def test_prerequisite_completed_in_previous_period_does_not_unlock(self, isolated_db):
        _seed_adult()
        _seed_quest(1)
        _seed_quest(2, prereq=1)
        with common.get_db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO quest_history (user_id, quest_id, quest_title, exp_earned, gold_earned, completed_at, status) "
                "VALUES ('dad', 1, 'Quest1', 20, 10, ?, 'approved')",
                ((datetime.datetime.now(JST) - datetime.timedelta(days=2)).isoformat(),)
            )
        with pytest.raises(HTTPException) as exc_info:
            QuestService().process_complete_quest("dad", 2)
        assert exc_info.value.status_code == 403


class TestNullRewardColumnsDoNotCrash:
    def _seed_child_and_adult(self):
        with common.get_db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, role) VALUES "
                "('dad', 'Dad', 'Warrior', 1, 0, 0, 'role_adult'), "
                "('son', 'Son', 'Novice', 1, 0, 50, 'role_child')"
            )
            cur.execute("INSERT INTO quest_master (quest_id, title, quest_type, exp_gain, gold_gain) VALUES (1, 'Q', 'daily', 10, 5)")

    def test_approve_history_with_null_rewards(self, isolated_db):
        self._seed_child_and_adult()
        with common.get_db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO quest_history (user_id, quest_id, quest_title, exp_earned, gold_earned, completed_at, status) "
                "VALUES ('son', 1, 'Q', NULL, NULL, ?, 'pending')", (datetime.datetime.now(JST).isoformat(),)
            )
            history_id = cur.lastrowid
        result = QuestService().process_approve_quest("dad", history_id)
        assert result["status"] == "success"
        assert result["earnedGold"] >= 0

    def test_cancel_approved_history_with_null_exp(self, isolated_db):
        self._seed_child_and_adult()
        with common.get_db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO quest_history (user_id, quest_id, quest_title, exp_earned, gold_earned, completed_at, status) "
                "VALUES ('son', 1, 'Q', NULL, 0, ?, 'approved')", (datetime.datetime.now(JST).isoformat(),)
            )
            history_id = cur.lastrowid
        assert QuestService().process_cancel_quest("son", history_id)["status"] == "cancelled"


class TestUseItemActionBounds:
    def test_inventory_id_over_sqlite_max_is_422_not_500(self, api_client):
        res = api_client.post("/api/quest/inventory/use", json={"user_id": "dad", "inventory_id": 2 ** 64})
        assert res.status_code == 422

    def test_empty_user_id_is_422(self, api_client):
        res = api_client.post("/api/quest/inventory/use", json={"user_id": "", "inventory_id": 1})
        assert res.status_code == 422
        res = api_client.post("/api/quest/quest/cancel", json={"user_id": "", "history_id": 1})
        assert res.status_code == 422


class TestItemUseRowsExcludedFromLogs:
    def test_item_use_rows_are_not_quest_achievements(self, isolated_db):
        _seed_adult()
        with common.get_db_cursor(commit=True) as cur:
            now = datetime.datetime.now(JST).isoformat()
            cur.execute(
                "INSERT INTO quest_history (user_id, quest_id, quest_title, exp_earned, gold_earned, completed_at, status) "
                "VALUES ('dad', 0, 'アイテム使用: Youtube', 0, 0, ?, 'approved')", (now,)
            )
            cur.execute(
                "INSERT INTO quest_history (user_id, quest_id, quest_title, exp_earned, gold_earned, completed_at, status) "
                "VALUES ('dad', 1, 'RealQuest', 10, 5, ?, 'approved')", (now,)
            )
        chronicle = UserService().get_family_chronicle()["chronicle"]
        assert not any("アイテム使用" in ev["text"] for ev in chronicle)
        assert any("RealQuest" in ev["text"] for ev in chronicle)

        logs = GameSystem().get_all_view_data()["logs"]
        assert not any("アイテム使用" in (log.get("title") or "") for log in logs)


class TestTvUnlockRunsAfterCommit:
    def test_trigger_tv_unlock_sees_committed_approval(self, isolated_db, monkeypatch):
        with common.get_db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, role) VALUES "
                "('dad', 'Dad', 'Warrior', 1, 0, 0, 'role_adult'), "
                "('son', 'Son', 'Novice', 1, 0, 0, 'role_child')"
            )
            cur.execute("INSERT INTO quest_master (quest_id, title, quest_type, exp_gain, gold_gain) VALUES (7, 'TV', 'daily', 10, 5)")
            cur.execute(
                "INSERT INTO quest_history (user_id, quest_id, quest_title, exp_earned, gold_earned, completed_at, status) "
                "VALUES ('son', 7, 'TV', 10, 5, ?, 'pending')", (datetime.datetime.now(JST).isoformat(),)
            )
            history_id = cur.lastrowid
        monkeypatch.setattr(config, "TV_UNLOCK_QUEST_IDS", [7])
        monkeypatch.setattr(config, "TV_PLUG_DEVICE_ID", "plug-1")

        observed = {}

        def fake_trigger(self, quest_id):
            # 別接続から見て承認がコミット済みであること(=コミット後に呼ばれていること)を確認する
            with common.get_db_cursor() as cur:
                row = cur.execute("SELECT status FROM quest_history WHERE id = ?", (history_id,)).fetchone()
            observed["status_at_trigger"] = row["status"]
            observed["quest_id"] = quest_id

        monkeypatch.setattr(quest_service_module.QuestService, "_trigger_tv_unlock", fake_trigger)
        QuestService().process_approve_quest("dad", history_id)
        assert observed == {"status_at_trigger": "approved", "quest_id": 7}


class TestUseItemReleasesLockBeforePush:
    """Issue #544: use_item のユーザー単位ロックは DB 更新(コミット)までで解放され、
    LINE push / 効果音の実行中は保持されないこと。以前は _use_item_locked の末尾で同期の
    send_push まで行っていたため、LINE が遅いと同一ユーザーの次の use_item が待たされていた。"""

    def _seed_item(self):
        with common.get_db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, role) "
                "VALUES ('dad', 'Dad', 'Warrior', 1, 0, 0, 'role_adult')"
            )
            cur.execute("INSERT INTO reward_master (reward_id, title, cost_gold) VALUES (1, 'Juice', 10)")
            cur.execute(
                "INSERT INTO user_inventory (user_id, reward_id, status, purchased_at) "
                "VALUES ('dad', 1, 'owned', ?)", (datetime.datetime.now(JST).isoformat(),)
            )
            return cur.lastrowid

    def test_push_runs_after_lock_released_and_after_commit(self, isolated_db, monkeypatch):
        import threading

        observed = {}

        def fake_push(*args, **kwargs):
            # 別スレッドからユーザー単位ロックを取得できれば、送信中はロックが解放されている
            acquired = threading.Event()

            def _try_lock():
                with quest_service_module._get_item_use_lock("dad"):
                    acquired.set()

            t = threading.Thread(target=_try_lock)
            t.start()
            observed["lock_free_during_push"] = acquired.wait(timeout=2.0)
            t.join(timeout=2.0)
            # コミット済み(別接続から消費済みが見える)であることも確認する
            with common.get_db_cursor() as cur:
                observed["status_at_push"] = cur.execute(
                    "SELECT status FROM user_inventory WHERE id = ?", (observed["inv_id"],)
                ).fetchone()["status"]
            observed["message"] = kwargs["messages"][0]["text"]

        monkeypatch.setattr(quest_service_module.notification_service, "send_push", fake_push)
        monkeypatch.setattr(quest_service_module.sound_manager, "play", lambda *a, **k: None)
        inv_id = self._seed_item()
        observed["inv_id"] = inv_id

        result = quest_service_module.InventoryService().use_item("dad", inv_id)

        assert result == {"status": "consumed", "message": "つかいました！"}
        assert observed["lock_free_during_push"] is True
        assert observed["status_at_push"] == "consumed"
        assert "Juice" in observed["message"]

    def test_use_item_locked_returns_response_and_message(self, isolated_db, monkeypatch):
        """_use_item_locked は (レスポンス, 通知文) を返し、自身では送信しない。"""
        calls = []
        monkeypatch.setattr(
            quest_service_module.notification_service, "send_push", lambda *a, **k: calls.append(1)
        )
        monkeypatch.setattr(quest_service_module.sound_manager, "play", lambda *a, **k: None)
        inv_id = self._seed_item()
        result, msg = quest_service_module.InventoryService()._use_item_locked("dad", inv_id)
        assert result["status"] == "consumed"
        assert "Juice" in msg
        assert calls == []
