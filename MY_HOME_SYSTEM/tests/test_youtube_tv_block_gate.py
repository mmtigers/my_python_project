# MY_HOME_SYSTEM/tests/test_youtube_tv_block_gate.py
"""
休日のテレビ禁止時間帯(12:00〜14:00 / 20:00以降)とYouTube系ごほうび券の整合の回帰テスト。

券を使った後にプラグを切られて怒られるのを防ぐため、禁止中・禁止開始を跨ぐ券は
使用時点で429にする。ちょうど禁止開始に終わる券は許可。
2026-10-10は土曜(休日)、2026-10-09は金曜(平日)。JSTの時刻は UTC+9 で凍結する。
自宅保育の 'daughter' はルーティン判定の対象外なので、ここでは常に使用可能側として使う。
"""
import os
import sys

import pytest
from fastapi import HTTPException
from freezegun import freeze_time

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.database import get_db_cursor
from core.utils import get_now_iso
from monitors import tv_lock_monitor
from services import quest_service as qs_module
from services import switchbot_service
from services.quest_service import ROLE_CHILD

REWARD_ID = 801
USER = 'daughter'


def _seed_ticket() -> int:
    with get_db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, role) "
            "VALUES (?, ?, 'Novice', 1, 0, 0, ?)",
            (USER, USER, ROLE_CHILD),
        )
        cur.execute(
            "INSERT INTO reward_master (reward_id, title, cost_gold, target) "
            "VALUES (?, 'Youtube', 50, 'children')",
            (REWARD_ID,),
        )
        cur.execute(
            "INSERT INTO user_inventory (user_id, reward_id, status, purchased_at) "
            "VALUES (?, ?, 'owned', ?)",
            (USER, REWARD_ID, get_now_iso()),
        )
        return cur.execute("SELECT id FROM user_inventory ORDER BY id DESC LIMIT 1").fetchone()["id"]


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    monkeypatch.setattr(qs_module.config, "YOUTUBE_REWARD_IDS", [REWARD_ID])
    monkeypatch.setattr(qs_module.config, "YOUTUBE_REWARD_DURATION_MINUTES", {REWARD_ID: 30})
    monkeypatch.setattr(qs_module.config, "TV_PLUG_DEVICE_ID", "tv-plug")
    monkeypatch.setattr(qs_module.config, "YOUTUBE_HOME_CARE_USER_IDS", [USER], raising=False)
    monkeypatch.setattr(qs_module.notification_service, "send_push", lambda *a, **k: None)
    monkeypatch.setattr(qs_module.sound_manager, "play", lambda *a, **k: None)


def _use(inv_id):
    return qs_module.InventoryService().use_item(USER, inv_id)


def _assert_rejected(inv_id, fragment):
    with pytest.raises(HTTPException) as exc:
        _use(inv_id)
    assert exc.value.status_code == 429
    assert fragment in exc.value.detail
    with get_db_cursor() as cur:
        status = cur.execute("SELECT status FROM user_inventory WHERE id = ?", (inv_id,)).fetchone()["status"]
    assert status == "owned"


# JST = UTC+9。2026-10-10(土) JST 11:30 = UTC 02:30
@freeze_time("2026-10-10 02:30:00")
def test_ticket_ending_exactly_at_block_start_is_allowed(isolated_db):
    assert _use(_seed_ticket())["status"] == "consumed"


@freeze_time("2026-10-10 02:31:00")  # JST 11:31 → 12:01 終了
def test_ticket_crossing_noon_block_is_rejected(isolated_db):
    _assert_rejected(_seed_ticket(), "12:00からテレビがおやすみ")


@freeze_time("2026-10-10 02:45:00")  # JST 11:45(相談のあったケース)
def test_reported_case_1145_with_30min_ticket(isolated_db):
    _assert_rejected(_seed_ticket(), "30分の券")


@freeze_time("2026-10-10 03:10:00")  # JST 12:10 禁止中
def test_ticket_during_noon_block_is_rejected(isolated_db):
    _assert_rejected(_seed_ticket(), "14:00からつかえる")


@freeze_time("2026-10-10 10:30:00")  # JST 19:30 → 20:00 ちょうど
def test_ticket_ending_exactly_at_evening_block_is_allowed(isolated_db):
    assert _use(_seed_ticket())["status"] == "consumed"


@freeze_time("2026-10-10 10:31:00")  # JST 19:31
def test_ticket_crossing_evening_block_is_rejected(isolated_db):
    _assert_rejected(_seed_ticket(), "20:00からテレビがおやすみ")


@freeze_time("2026-10-10 11:30:00")  # JST 20:30 禁止中(終了なし)
def test_ticket_in_evening_block_says_tomorrow(isolated_db):
    _assert_rejected(_seed_ticket(), "また明日")


@freeze_time("2026-10-10 06:00:00")  # JST 15:00 昼の禁止明け、20:00まで余裕
def test_ticket_allowed_in_afternoon(isolated_db):
    assert _use(_seed_ticket())["status"] == "consumed"


@freeze_time("2026-10-09 02:50:00")  # 金曜 JST 11:50(平日は対象外)
def test_weekday_is_not_gated(isolated_db):
    assert _use(_seed_ticket())["status"] == "consumed"


@freeze_time("2026-10-10 02:45:00")
def test_gate_disabled_without_tv_plug(isolated_db, monkeypatch):
    monkeypatch.setattr(qs_module.config, "TV_PLUG_DEVICE_ID", "")
    assert _use(_seed_ticket())["status"] == "consumed"


@freeze_time("2026-10-10 03:10:00")  # 禁止中でもYouTube系以外は影響なし
def test_non_youtube_reward_is_not_gated(isolated_db, monkeypatch):
    monkeypatch.setattr(qs_module.config, "YOUTUBE_REWARD_IDS", [])
    assert _use(_seed_ticket())["status"] == "consumed"


class TestTvBlockState:
    @freeze_time("2026-10-10 02:45:00")  # JST 11:45
    def test_countdown_before_noon(self):
        s = switchbot_service.get_tv_block_state()
        assert s["is_blocked"] is False
        assert s["next_block_starts_at"] == "12:00"
        assert s["seconds_until_next_block"] == 15 * 60
        assert s["windows"] == [{"start": "12:00", "end": "14:00"}, {"start": "20:00", "end": None}]

    @freeze_time("2026-10-10 03:00:00")  # JST 12:00 ちょうどは禁止中
    def test_blocked_at_start_boundary(self):
        s = switchbot_service.get_tv_block_state()
        assert s["is_blocked"] is True
        assert s["blocked_until"] == "14:00"
        assert s["seconds_until_next_block"] is None

    @freeze_time("2026-10-10 05:00:00")  # JST 14:00 終了は含まない
    def test_not_blocked_at_end_boundary(self):
        s = switchbot_service.get_tv_block_state()
        assert s["is_blocked"] is False
        assert s["next_block_starts_at"] == "20:00"

    @freeze_time("2026-10-10 11:30:00")  # JST 20:30
    def test_evening_block_has_no_end(self):
        s = switchbot_service.get_tv_block_state()
        assert s["is_blocked"] is True and s["blocked_until"] is None

    @freeze_time("2026-10-09 02:45:00")
    def test_none_on_weekday(self):
        assert switchbot_service.get_tv_block_state() is None

    @freeze_time("2026-10-10 02:45:00")
    def test_none_without_plug(self, monkeypatch):
        monkeypatch.setattr(switchbot_service.config, "TV_PLUG_DEVICE_ID", None)
        assert switchbot_service.get_tv_block_state() is None

    @freeze_time("2026-10-10 02:45:00")
    def test_get_user_inventory_includes_tv_block(self, isolated_db):
        _seed_ticket()
        inv = qs_module.InventoryService().get_user_inventory(USER)
        assert inv["tv_block"]["seconds_until_next_block"] == 15 * 60


def test_monitor_slots_match_blocked_hours():
    """tv_lock_monitor の休日スロットが TV_OFFDAY_BLOCKED_HOURS と食い違わないこと。"""
    offs = {h for h, cmd, holiday_only in tv_lock_monitor._SLOTS if holiday_only and cmd == "turnOff"}
    ons = {h for h, cmd, holiday_only in tv_lock_monitor._SLOTS if holiday_only and cmd == "turnOn"}
    starts = {s for s, _ in switchbot_service.TV_OFFDAY_BLOCKED_HOURS}
    ends = {e for _, e in switchbot_service.TV_OFFDAY_BLOCKED_HOURS if e < 24}
    assert offs == starts
    assert ons == ends
