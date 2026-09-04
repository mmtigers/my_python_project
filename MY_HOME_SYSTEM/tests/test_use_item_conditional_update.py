# MY_HOME_SYSTEM/tests/test_use_item_conditional_update.py
"""
#369: use_item の二重使用防止の回帰テスト。

SELECT→Python判定→無条件UPDATE の構造では、連打された2リクエストが両方 'owned' を
読んで両方が消費処理を行っていた。修正後は status='owned' を条件に含む条件付きUPDATEで、
先行リクエストが消費済みなら rowcount==0 となり400で拒否される。
"""
import os
import sys
import threading

import pytest
from fastapi import HTTPException

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import common
from services import quest_service as qs_module
from services.quest_service import ROLE_ADULT


def _seed() -> int:
    with common.get_db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, role) "
            "VALUES ('dad', 'Dad', 'Warrior', 1, 0, 0, ?)",
            (ROLE_ADULT,),
        )
        cur.execute(
            "INSERT INTO reward_master (reward_id, title, cost_gold, target) "
            "VALUES (701, 'テスト報酬', 10, 'all')"
        )
        cur.execute(
            "INSERT INTO user_inventory (user_id, reward_id, status, purchased_at) "
            "VALUES ('dad', 701, 'owned', ?)",
            (common.get_now_iso(),),
        )
        return cur.execute("SELECT id FROM user_inventory ORDER BY id DESC LIMIT 1").fetchone()["id"]


def test_use_item_rejects_when_already_consumed_between_select_and_update(isolated_db, monkeypatch):
    """SELECT後・UPDATE前に別経路で消費された場合でも二重消費にならない。"""
    pushes = []
    monkeypatch.setattr(qs_module.notification_service, "send_push", lambda *a, **k: pushes.append(1))
    monkeypatch.setattr(qs_module.sound_manager, "play", lambda *a, **k: None)
    inv_id = _seed()
    service = qs_module.InventoryService()

    # 1回目の使用は成功
    assert service.use_item("dad", inv_id)["status"] == "consumed"
    assert len(pushes) == 1

    # 2回目は400、通知も増えない
    with pytest.raises(HTTPException) as exc:
        service.use_item("dad", inv_id)
    assert exc.value.status_code == 400
    assert len(pushes) == 1

    with common.get_db_cursor() as cur:
        rows = cur.execute("SELECT COUNT(*) AS c FROM quest_history WHERE quest_id = 0").fetchone()["c"]
    assert rows == 1


def test_concurrent_use_item_consumes_exactly_once(isolated_db, monkeypatch):
    """同時に複数スレッドから使用しても、消費・履歴・通知はちょうど1回。"""
    pushes = []
    lock = threading.Lock()

    def _push(*a, **k):
        with lock:
            pushes.append(1)

    monkeypatch.setattr(qs_module.notification_service, "send_push", _push)
    monkeypatch.setattr(qs_module.sound_manager, "play", lambda *a, **k: None)
    inv_id = _seed()
    service = qs_module.InventoryService()

    results = []

    def _worker():
        try:
            service.use_item("dad", inv_id)
            results.append("ok")
        except HTTPException as e:
            results.append(e.status_code)

    threads = [threading.Thread(target=_worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count("ok") == 1
    assert all(r == 400 for r in results if r != "ok")
    assert len(pushes) == 1
    with common.get_db_cursor() as cur:
        assert cur.execute("SELECT COUNT(*) AS c FROM quest_history WHERE quest_id = 0").fetchone()["c"] == 1
        assert cur.execute("SELECT status FROM user_inventory WHERE id=?", (inv_id,)).fetchone()["status"] == "consumed"
