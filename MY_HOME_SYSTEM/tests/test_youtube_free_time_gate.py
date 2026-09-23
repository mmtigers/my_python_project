# MY_HOME_SYSTEM/tests/test_youtube_free_time_gate.py
"""
YouTube視聴(等の時間消費型ごほうびの使用)は自由時間中のみ許可する制限の回帰テスト
(要件確認済み、2026-09-23)。

判定は services/routine_service.py の is_user_currently_in_free_time() に委ねており、
ここでは InventoryService 側の配線(使用時のガード・get_user_inventory の
is_in_free_time フラグ)だけを、routine_service をモックして検証する。
「自由時間の判定そのもの」の回帰は tests/test_routine_service.py の
TestIsUserCurrentlyInFreeTime が別途固定している。
"""
import os
import sys

import pytest
from fastapi import HTTPException

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.database import get_db_cursor
from core.utils import get_now_iso
from services import quest_service as qs_module
from services.quest_service import ROLE_CHILD
from services.routine_service import routine_service

YOUTUBE_REWARD_ID = 701


def _seed_owned_youtube_ticket(user_id: str = 'daughter') -> int:
    with get_db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, role) "
            "VALUES (?, ?, 'Novice', 1, 0, 0, ?)",
            (user_id, user_id, ROLE_CHILD),
        )
        cur.execute(
            "INSERT INTO reward_master (reward_id, title, cost_gold, target) "
            "VALUES (?, 'Youtube (10:00)', 50, 'children')",
            (YOUTUBE_REWARD_ID,),
        )
        cur.execute(
            "INSERT INTO user_inventory (user_id, reward_id, status, purchased_at) "
            "VALUES (?, ?, 'owned', ?)",
            (user_id, YOUTUBE_REWARD_ID, get_now_iso()),
        )
        return cur.execute("SELECT id FROM user_inventory ORDER BY id DESC LIMIT 1").fetchone()["id"]


@pytest.fixture(autouse=True)
def _youtube_reward_ids(monkeypatch):
    monkeypatch.setattr(qs_module.config, "YOUTUBE_REWARD_IDS", [YOUTUBE_REWARD_ID])
    monkeypatch.setattr(qs_module.notification_service, "send_push", lambda *a, **k: None)
    monkeypatch.setattr(qs_module.sound_manager, "play", lambda *a, **k: None)


def test_use_item_is_blocked_outside_free_time(isolated_db, monkeypatch):
    monkeypatch.setattr(routine_service, "is_user_currently_in_free_time", lambda *a, **k: False)
    inv_id = _seed_owned_youtube_ticket()
    service = qs_module.InventoryService()

    with pytest.raises(HTTPException) as exc_info:
        service.use_item("daughter", inv_id)
    assert exc_info.value.status_code == 429
    assert "自由時間" in exc_info.value.detail

    # 拒否された券はownedのままで、消費履歴も作られない。
    with get_db_cursor() as cur:
        status = cur.execute(
            "SELECT status FROM user_inventory WHERE id = ?", (inv_id,)
        ).fetchone()["status"]
        history_count = cur.execute(
            "SELECT COUNT(*) AS c FROM quest_history WHERE user_id = 'daughter' AND quest_id = 0"
        ).fetchone()["c"]
    assert status == "owned"
    assert history_count == 0


def test_use_item_succeeds_during_free_time(isolated_db, monkeypatch):
    monkeypatch.setattr(routine_service, "is_user_currently_in_free_time", lambda *a, **k: True)
    inv_id = _seed_owned_youtube_ticket()
    service = qs_module.InventoryService()

    result = service.use_item("daughter", inv_id)
    assert result["status"] == "consumed"


def test_get_user_inventory_reports_is_in_free_time_flag(isolated_db, monkeypatch):
    _seed_owned_youtube_ticket()
    service = qs_module.InventoryService()

    monkeypatch.setattr(routine_service, "is_user_currently_in_free_time", lambda *a, **k: False)
    assert service.get_user_inventory("daughter")["is_in_free_time"] is False

    monkeypatch.setattr(routine_service, "is_user_currently_in_free_time", lambda *a, **k: True)
    assert service.get_user_inventory("daughter")["is_in_free_time"] is True


def test_non_youtube_reward_is_not_gated_by_free_time(isolated_db, monkeypatch):
    """自由時間限定はYouTube等の時間消費型のみが対象(要件確認済み)。それ以外の
    ごほうび(例: おやつ)は自由時間外でも使える。"""
    monkeypatch.setattr(routine_service, "is_user_currently_in_free_time", lambda *a, **k: False)
    with get_db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, role) "
            "VALUES ('daughter', 'daughter', 'Novice', 1, 0, 0, ?)",
            (ROLE_CHILD,),
        )
        cur.execute(
            "INSERT INTO reward_master (reward_id, title, cost_gold, target) "
            "VALUES (21, '好きなおやつ', 100, 'children')"
        )
        cur.execute(
            "INSERT INTO user_inventory (user_id, reward_id, status, purchased_at) "
            "VALUES ('daughter', 21, 'owned', ?)",
            (get_now_iso(),),
        )
        inv_id = cur.execute("SELECT id FROM user_inventory ORDER BY id DESC LIMIT 1").fetchone()["id"]

    service = qs_module.InventoryService()
    result = service.use_item("daughter", inv_id)
    assert result["status"] == "consumed"
