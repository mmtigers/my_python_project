"""画面タップログ(POST /api/ui-log/events → ui_tap_events)のテスト。"""
import datetime

import config
import pytest
from core.database import get_db_cursor
from freezegun import freeze_time
from models.ui_log import UiTapBatch
from services.ui_event_service import ui_event_service

JST = datetime.timezone(datetime.timedelta(hours=9), "JST")
NOW = datetime.datetime(2026, 10, 5, 10, 0, 0, tzinfo=JST)
NOW_MS = int(NOW.timestamp() * 1000)


def _event(i=0, **over):
    ev = {
        "event_id": f"evt-0000000{i}",
        "occurred_at_ms": NOW_MS - i * 1000,
        "user_id": "son",
        "screen": "quest",
        "element_id": "宿題と明日の準備",
        "element_tag": "button",
        "is_interactive": True,
        "x_pct": 42.5,
        "y_pct": 60.0,
        "layout_mode": "portrait",
    }
    ev.update(over)
    return ev


def _batch(events, session="sess-0000001"):
    return {"session_id": session, "events": events}


def _rows():
    with get_db_cursor() as cur:
        return [dict(r) for r in cur.execute("SELECT * FROM ui_tap_events ORDER BY id")]


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setattr(config, "UI_TAP_LOG_ENABLED", True)


class TestRecordBatch:
    def test_saves_events_with_jst_timestamp(self, isolated_db):
        batch = UiTapBatch(**_batch([_event(0), _event(1, is_interactive=False, element_id=None)]))
        with freeze_time(NOW):
            result = ui_event_service.record_batch(batch, now=NOW)
        assert result == {"accepted": 2, "duplicated": 0, "rejected": 0}
        rows = _rows()
        assert [r["event_id"] for r in rows] == ["evt-00000000", "evt-00000001"]
        assert rows[0]["occurred_at"] == "2026-10-05T10:00:00+09:00"
        assert rows[0]["user_id"] == "son" and rows[0]["screen"] == "quest"
        assert rows[0]["is_interactive"] == 1 and rows[1]["is_interactive"] == 0
        assert rows[1]["element_id"] is None
        assert rows[0]["session_id"] == "sess-0000001"
        assert rows[0]["received_at"]

    def test_resending_the_same_batch_does_not_duplicate(self, isolated_db):
        batch = UiTapBatch(**_batch([_event(0), _event(1)]))
        ui_event_service.record_batch(batch, now=NOW)
        result = ui_event_service.record_batch(batch, now=NOW)
        assert result == {"accepted": 0, "duplicated": 2, "rejected": 0}
        assert len(_rows()) == 2

    def test_partial_overlap_counts_only_new_rows(self, isolated_db):
        ui_event_service.record_batch(UiTapBatch(**_batch([_event(0)])), now=NOW)
        result = ui_event_service.record_batch(UiTapBatch(**_batch([_event(0), _event(1)])), now=NOW)
        assert result == {"accepted": 1, "duplicated": 1, "rejected": 0}

    def test_rejects_events_outside_the_time_window(self, isolated_db):
        too_old = int((NOW - datetime.timedelta(days=31)).timestamp() * 1000)
        too_new = int((NOW + datetime.timedelta(minutes=11)).timestamp() * 1000)
        offline_ok = int((NOW - datetime.timedelta(days=2)).timestamp() * 1000)
        batch = UiTapBatch(**_batch([
            _event(0, occurred_at_ms=too_old),
            _event(1, occurred_at_ms=too_new),
            _event(2, occurred_at_ms=offline_ok),
        ]))
        result = ui_event_service.record_batch(batch, now=NOW)
        assert result == {"accepted": 1, "duplicated": 0, "rejected": 2}
        assert [r["event_id"] for r in _rows()] == ["evt-00000002"]

    @pytest.mark.parametrize("ms", [10**18, 10**30, 2**63])
    def test_unconvertible_timestamp_is_rejected_without_failing_the_batch(self, isolated_db, ms):
        """桁外れの occurred_at_ms は当該イベントだけを破棄し、同じバッチの正常分は保存する。"""
        batch = UiTapBatch(**_batch([_event(0, occurred_at_ms=ms), _event(1)]))
        result = ui_event_service.record_batch(batch, now=NOW)
        assert result == {"accepted": 1, "duplicated": 0, "rejected": 1}
        assert [r["event_id"] for r in _rows()] == ["evt-00000001"]

    def test_disabled_saves_nothing_but_reports_accepted(self, isolated_db, monkeypatch):
        monkeypatch.setattr(config, "UI_TAP_LOG_ENABLED", False)
        batch = UiTapBatch(**_batch([_event(0), _event(1)]))
        result = ui_event_service.record_batch(batch, now=NOW)
        assert result == {"accepted": 2, "duplicated": 0, "rejected": 0}
        assert _rows() == []


class TestEndpoint:
    def test_post_returns_202_and_saves(self, api_client):
        with freeze_time(NOW):
            res = api_client.post("/api/ui-log/events", json=_batch([_event(0)]))
        assert res.status_code == 202
        assert res.json() == {"accepted": 1, "duplicated": 0, "rejected": 0}
        assert len(_rows()) == 1

    def test_unknown_user_id_is_stored_as_is(self, api_client):
        """user_id はクライアント入力のまま保存する(認可は行わない設計)。"""
        with freeze_time(NOW):
            res = api_client.post("/api/ui-log/events", json=_batch([_event(0, user_id="nobody")]))
        assert res.status_code == 202
        assert _rows()[0]["user_id"] == "nobody"

    def test_null_user_id_is_allowed(self, api_client):
        with freeze_time(NOW):
            res = api_client.post("/api/ui-log/events", json=_batch([_event(0, user_id=None)]))
        assert res.status_code == 202
        assert _rows()[0]["user_id"] is None

    @pytest.mark.parametrize("payload", [
        {"session_id": "sess-0000001", "events": []},
        {"session_id": "short", "events": [_event(0)]},
        {"session_id": "sess-0000001", "events": [_event(i) for i in range(101)]},
        {"session_id": "sess-0000001", "events": [_event(0, x_pct=101)]},
        {"session_id": "sess-0000001", "events": [_event(0, element_id="x" * 65)]},
        {"session_id": "sess-0000001", "events": [_event(0, screen="")]},
        {"session_id": "sess-0000001", "events": [{"event_id": "evt-00000000"}]},
    ])
    def test_invalid_payload_is_rejected(self, api_client, payload):
        assert api_client.post("/api/ui-log/events", json=payload).status_code == 422

    def test_endpoint_returns_202_for_an_unconvertible_timestamp(self, api_client):
        """回帰: 以前は 500 になり、クライアントが5xxを再試行扱いにして永久に再送していた。"""
        with freeze_time(NOW):
            res = api_client.post(
                "/api/ui-log/events", json=_batch([_event(0, occurred_at_ms=10**18), _event(1)])
            )
        assert res.status_code == 202
        assert res.json() == {"accepted": 1, "duplicated": 0, "rejected": 1}

    def test_max_batch_of_100_is_accepted(self, api_client):
        with freeze_time(NOW):
            res = api_client.post("/api/ui-log/events", json=_batch([_event(i) for i in range(100)]))
        assert res.status_code == 202
        assert res.json()["accepted"] == 100


class TestRetentionRegistration:
    def test_ui_tap_events_is_a_retention_target(self):
        from services import db_retention_service as retention
        targets = {t.table: t for t in retention.RETENTION_TARGETS}
        assert targets["ui_tap_events"].timestamp_column == "occurred_at"
        assert targets["ui_tap_events"].retention_days_attr == "DB_ROW_RETENTION_EVENT_DAYS"

    def test_old_rows_are_planned_for_deletion(self, isolated_db):
        from services import db_retention_service as retention
        old = datetime.datetime.now(JST) - datetime.timedelta(days=config.DB_ROW_RETENTION_EVENT_DAYS + 5)
        with get_db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO ui_tap_events (event_id, session_id, occurred_at, received_at, screen, is_interactive) "
                "VALUES ('evt-old-0001', 'sess-0000001', ?, ?, 'quest', 1)",
                (old.isoformat(), old.isoformat()),
            )
        plans = {p.target.table: p for p in retention.plan_deletions()}
        assert plans["ui_tap_events"].deletable_rows == 1
