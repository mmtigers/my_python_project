# MY_HOME_SYSTEM/tests/test_youtube_free_time_gate.py
"""
YouTube視聴(等の時間消費型ごほうびの使用)は自由時間中のみ許可する制限の回帰テスト
(要件確認済み、2026-09-23)。

判定は services/routine_service.py の is_user_currently_in_free_time() に委ねており、
ここでは InventoryService 側の配線(使用時のガード・get_user_inventory の
is_in_free_time フラグ)だけを、routine_service をモックして検証する。
「自由時間の判定そのもの」の回帰は tests/test_routine_service.py の
TestIsUserCurrentlyInFreeTime が別途固定している。

このゲートの配線テストは既定ユーザーを 'son'(智矢)にしている。'daughter'(涼花)は
今年度自宅保育(登校しない)のため、2026-09-24よりこのゲート自体の対象外になった
(config.YOUTUBE_HOME_CARE_USER_IDS。services/quest/inventory_service.py の
_is_user_in_youtube_free_time参照)。涼花のこの適用除外と、お昼寝の時間帯
(config.YOUTUBE_NAP_BLOCK_START〜END)がどちらにも常に適用されることは、
このファイル末尾の TestNapBlockAndHomeCareExemption が別途固定する。
"""
import os
import sys

import pytest
from fastapi import HTTPException
from freezegun import freeze_time

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.database import get_db_cursor
from core.utils import get_now_iso
from services import quest_service as qs_module
from services.quest.inventory_service import _is_user_in_youtube_free_time
from services.quest_service import ROLE_CHILD
from services.routine_service import routine_service

YOUTUBE_REWARD_ID = 701


def _seed_owned_youtube_ticket(user_id: str = 'son') -> int:
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


@freeze_time("2026-09-24 01:00:00")  # = JST 2026-09-24 10:00(お昼寝の時間帯の外)
def test_use_item_is_blocked_outside_free_time(isolated_db, monkeypatch):
    monkeypatch.setattr(routine_service, "is_user_currently_in_free_time", lambda *a, **k: False)
    inv_id = _seed_owned_youtube_ticket()
    service = qs_module.InventoryService()

    with pytest.raises(HTTPException) as exc_info:
        service.use_item("son", inv_id)
    assert exc_info.value.status_code == 429
    assert "自由時間" in exc_info.value.detail

    # 拒否された券はownedのままで、消費履歴も作られない。
    with get_db_cursor() as cur:
        status = cur.execute(
            "SELECT status FROM user_inventory WHERE id = ?", (inv_id,)
        ).fetchone()["status"]
        history_count = cur.execute(
            "SELECT COUNT(*) AS c FROM quest_history WHERE user_id = 'son' AND quest_id = 0"
        ).fetchone()["c"]
    assert status == "owned"
    assert history_count == 0


@freeze_time("2026-09-24 01:00:00")  # = JST 2026-09-24 10:00(お昼寝の時間帯の外)
def test_use_item_succeeds_during_free_time(isolated_db, monkeypatch):
    monkeypatch.setattr(routine_service, "is_user_currently_in_free_time", lambda *a, **k: True)
    inv_id = _seed_owned_youtube_ticket()
    service = qs_module.InventoryService()

    result = service.use_item("son", inv_id)
    assert result["status"] == "consumed"


@freeze_time("2026-09-24 01:00:00")  # = JST 2026-09-24 10:00(お昼寝の時間帯の外)
def test_get_user_inventory_reports_is_in_free_time_flag(isolated_db, monkeypatch):
    _seed_owned_youtube_ticket()
    service = qs_module.InventoryService()

    monkeypatch.setattr(routine_service, "is_user_currently_in_free_time", lambda *a, **k: False)
    assert service.get_user_inventory("son")["is_in_free_time"] is False

    monkeypatch.setattr(routine_service, "is_user_currently_in_free_time", lambda *a, **k: True)
    assert service.get_user_inventory("son")["is_in_free_time"] is True


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


class TestNapBlockAndHomeCareExemption:
    """涼花(自宅保育、config.YOUTUBE_HOME_CARE_USER_IDS)の自由時間ゲート適用除外と、
    お昼寝の時間帯(config.YOUTUBE_NAP_BLOCK_START〜END)の回帰(Issue報告、2026-09-24:
    涼花が平日8:50にYouTube券を使えなかった件の修正)。

    _is_user_in_youtube_free_time を直接呼ぶユニットテストと、実際のuse_item経由の
    統合テストの両方を置く。
    """

    # --- 涼花(自宅保育): 登校/下校前提のルーティンフロー判定を適用しない ---

    @freeze_time("2026-09-23 23:50:00")  # = JST 2026-09-24(木) 08:50。報告された症状そのもの。
    def test_daughter_is_exempt_from_routine_flow_gate_at_reported_time(self, monkeypatch):
        # 平日07:50〜14:00は本来ルーティンフロー上「自由時間ではない」時間帯だが、
        # 涼花はこのゲート自体の対象外なので、routine_service側がFalseを返しても使える。
        monkeypatch.setattr(routine_service, "is_user_currently_in_free_time", lambda *a, **k: False)
        assert _is_user_in_youtube_free_time("daughter") is True

    @freeze_time("2026-09-24 01:00:00")  # = JST 10:00(お昼寝の時間帯の外)
    def test_daughter_use_item_succeeds_even_when_routine_flow_says_not_free(self, isolated_db, monkeypatch):
        monkeypatch.setattr(routine_service, "is_user_currently_in_free_time", lambda *a, **k: False)
        inv_id = _seed_owned_youtube_ticket("daughter")
        service = qs_module.InventoryService()

        result = service.use_item("daughter", inv_id)
        assert result["status"] == "consumed"

    # --- お昼寝の時間帯: 涼花・智矢とも常にブロック(フローの状態は無視) ---

    @freeze_time("2026-09-24 04:30:00")  # = JST 13:30(お昼寝ブロックの開始、境界含む)
    def test_daughter_is_blocked_during_nap_even_without_routine_flow_gate(self, monkeypatch):
        monkeypatch.setattr(routine_service, "is_user_currently_in_free_time", lambda *a, **k: True)
        assert _is_user_in_youtube_free_time("daughter") is False

    @freeze_time("2026-09-24 05:59:00")  # = JST 14:59(お昼寝ブロックの範囲内)
    def test_son_is_blocked_during_nap_even_during_routine_free_time(self, isolated_db, monkeypatch):
        # 智矢は普段どおりルーティンフローの自由時間中(True)でも、お昼寝の時間帯は
        # 別軸の制限として優先してブロックする(締切通過が早くフローが先に
        # 自由時間へ入ってしまうケースの抜け道を塞ぐ)。
        monkeypatch.setattr(routine_service, "is_user_currently_in_free_time", lambda *a, **k: True)
        inv_id = _seed_owned_youtube_ticket("son")
        service = qs_module.InventoryService()

        with pytest.raises(HTTPException) as exc_info:
            service.use_item("son", inv_id)
        assert exc_info.value.status_code == 429
        assert "お昼寝" in exc_info.value.detail

    @freeze_time("2026-09-24 06:00:00")  # = JST 15:00(お昼寝ブロックの終了、境界は含まない)
    def test_block_ends_exactly_at_nap_block_end(self, monkeypatch):
        monkeypatch.setattr(routine_service, "is_user_currently_in_free_time", lambda *a, **k: True)
        assert _is_user_in_youtube_free_time("son") is True
        assert _is_user_in_youtube_free_time("daughter") is True

    @freeze_time("2026-09-24 04:29:00")  # = JST 13:29(お昼寝ブロックの1分前)
    def test_block_has_not_started_one_minute_before(self, monkeypatch):
        monkeypatch.setattr(routine_service, "is_user_currently_in_free_time", lambda *a, **k: True)
        assert _is_user_in_youtube_free_time("son") is True
        assert _is_user_in_youtube_free_time("daughter") is True

    @freeze_time("2026-09-24 04:30:00")  # = JST 13:30
    def test_get_user_inventory_reflects_nap_block_for_both_children(self, isolated_db, monkeypatch):
        monkeypatch.setattr(routine_service, "is_user_currently_in_free_time", lambda *a, **k: True)
        _seed_owned_youtube_ticket("son")
        with get_db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, role) "
                "VALUES ('daughter', 'daughter', 'Novice', 1, 0, 0, ?)",
                (ROLE_CHILD,),
            )
        service = qs_module.InventoryService()

        assert service.get_user_inventory("son")["is_in_free_time"] is False
        assert service.get_user_inventory("daughter")["is_in_free_time"] is False
