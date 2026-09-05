# MY_HOME_SYSTEM/tests/test_youtube_reward_cooldown.py
"""
YouTube系ごほうび券の連続使用防止クールダウン(15分)の回帰テスト。

子供の目の負担を防ぐため、config.YOUTUBE_REWARD_IDSに含まれるreward_idの
ごほうび券を使用すると、同じ系統の券を再度使用できるようになるまで
YOUTUBE_REWARD_COOLDOWN_SECONDS(15分)待つ必要がある。
"""
import datetime
import os
import sys

import pytest
from fastapi import HTTPException

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import common
from services import quest_service as qs_module
from services.quest_service import JST, ROLE_CHILD

YOUTUBE_REWARD_IDS = [701, 702]
OTHER_REWARD_ID = 703


def _seed_user(user_id: str) -> None:
    with common.get_db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, role) "
            "VALUES (?, ?, 'Warrior', 1, 0, 0, ?)",
            (user_id, user_id, ROLE_CHILD),
        )


def _seed_reward_master() -> None:
    with common.get_db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO reward_master (reward_id, title, cost_gold, target) "
            "VALUES (701, 'Youtube (10:00)', 50, 'children')"
        )
        cur.execute(
            "INSERT INTO reward_master (reward_id, title, cost_gold, target) "
            "VALUES (702, 'Youtube (30:00)', 150, 'children')"
        )
        cur.execute(
            "INSERT INTO reward_master (reward_id, title, cost_gold, target) "
            "VALUES (703, '好きなおやつ', 100, 'children')"
        )


def _seed(user_id: str = "son") -> None:
    _seed_user(user_id)
    _seed_reward_master()


def _grant_item(user_id: str, reward_id: int, used_at: str = None, status: str = 'owned') -> int:
    with common.get_db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO user_inventory (user_id, reward_id, status, purchased_at, used_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, reward_id, status, common.get_now_iso(), used_at),
        )
        return cur.execute("SELECT id FROM user_inventory ORDER BY id DESC LIMIT 1").fetchone()["id"]


def _iso_seconds_ago(seconds: int) -> str:
    return (datetime.datetime.now(JST) - datetime.timedelta(seconds=seconds)).isoformat()


@pytest.fixture(autouse=True)
def _youtube_reward_ids(monkeypatch):
    monkeypatch.setattr(qs_module.config, "YOUTUBE_REWARD_IDS", YOUTUBE_REWARD_IDS)
    monkeypatch.setattr(qs_module.notification_service, "send_push", lambda *a, **k: None)
    monkeypatch.setattr(qs_module.sound_manager, "play", lambda *a, **k: None)


def test_second_youtube_ticket_is_blocked_within_cooldown(isolated_db):
    _seed()
    service = qs_module.InventoryService()
    first_id = _grant_item("son", 701)
    second_id = _grant_item("son", 702)  # 別の尺(30:00)でも同じYouTube系として扱う

    assert service.use_item("son", first_id)["status"] == "consumed"

    with pytest.raises(HTTPException) as exc:
        service.use_item("son", second_id)
    assert exc.value.status_code == 429
    assert "分" in str(exc.value.detail)

    with common.get_db_cursor() as cur:
        assert cur.execute(
            "SELECT status FROM user_inventory WHERE id=?", (second_id,)
        ).fetchone()["status"] == "owned"


def test_non_youtube_ticket_is_not_affected_by_cooldown(isolated_db):
    _seed()
    service = qs_module.InventoryService()
    youtube_id = _grant_item("son", 701)
    other_id = _grant_item("son", OTHER_REWARD_ID)

    assert service.use_item("son", youtube_id)["status"] == "consumed"
    # YouTube系を使った直後でも、無関係な報酬は即座に使える
    assert service.use_item("son", other_id)["status"] == "consumed"


def test_cooldown_expires_after_configured_duration(isolated_db):
    _seed()
    service = qs_module.InventoryService()
    # 16分前に使用済みのYouTube券(クールダウンは既に終了している)
    _grant_item("son", 701, used_at=_iso_seconds_ago(16 * 60), status='consumed')
    second_id = _grant_item("son", 702)

    assert service.use_item("son", second_id)["status"] == "consumed"


def test_cooldown_is_scoped_per_user(isolated_db):
    _seed("son")
    _seed_user("daughter")
    service = qs_module.InventoryService()
    son_item = _grant_item("son", 701)
    daughter_item = _grant_item("daughter", 701)

    assert service.use_item("son", son_item)["status"] == "consumed"
    # 兄がクールダウン中でも、妹は別ユーザーなので影響を受けない
    assert service.use_item("daughter", daughter_item)["status"] == "consumed"


def test_get_user_inventory_reports_cooldown_and_youtube_flag(isolated_db):
    _seed()
    service = qs_module.InventoryService()
    used_id = _grant_item("son", 701)
    still_owned_youtube_id = _grant_item("son", 702)
    still_owned_other_id = _grant_item("son", OTHER_REWARD_ID)

    service.use_item("son", used_id)

    result = service.get_user_inventory("son")
    assert set(result.keys()) == {"items", "youtube_cooldown_remaining_seconds"}
    assert 0 < result["youtube_cooldown_remaining_seconds"] <= 15 * 60

    items_by_id = {item["id"]: item for item in result["items"]}
    # 消費済みのアイテムは(既存仕様どおり)一覧に含まれない
    assert used_id not in items_by_id
    assert items_by_id[still_owned_youtube_id]["is_youtube_reward"] is True
    assert items_by_id[still_owned_other_id]["is_youtube_reward"] is False


def test_get_user_inventory_reports_zero_cooldown_when_never_used(isolated_db):
    _seed()
    service = qs_module.InventoryService()
    _grant_item("son", 701)

    result = service.get_user_inventory("son")
    assert result["youtube_cooldown_remaining_seconds"] == 0
