# MY_HOME_SYSTEM/tests/test_weather_history_service.py
"""services/weather_history_service.py と monitors/weather_monitor.py のテスト。

Open-Meteo へは実際に接続しない(`requests.get` を差し替える)。DBは isolated_db を使う。
"""
import os
import sys
from datetime import date
from typing import ClassVar

import pytest
import requests

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from core.database import get_db_cursor
from monitors import weather_monitor
from services import weather_history_service as svc


def _payload(days):
    """[(日付, 最低, 最高, コード)] から Open-Meteo の `daily` 応答を作る。"""
    return {"daily": {
        "time": [d[0] for d in days],
        "temperature_2m_min": [d[1] for d in days],
        "temperature_2m_max": [d[2] for d in days],
        "weather_code": [d[3] for d in days],
    }}


class _Resp:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(str(self.status_code))

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _rows():
    with get_db_cursor() as cur:
        cur.execute("SELECT date, location, min_temp, max_temp, weather_desc, max_pop, umbrella_level "
                    "FROM weather_history ORDER BY date")
        return [dict(r) for r in cur.fetchall()]


class TestDescribeWeatherCode:
    @pytest.mark.parametrize("code,label", [(0, "快晴"), (1, "晴れ"), (3, "くもり"), (61, "雨"), (75, "雪"), (95, "雷雨"), ("63", "雨"), (4, "その他")])
    def test_labels(self, code, label):
        assert svc.describe_weather_code(code) == label

    @pytest.mark.parametrize("code", [None, "", "x", [1]])
    def test_missing_or_invalid_is_none(self, code):
        assert svc.describe_weather_code(code) is None


class TestParseDaily:
    def test_parses_rows(self):
        rows = svc.parse_daily(_payload([("2026-10-01", 14.2, 24.8, 1), ("2026-10-02", 15.0, 21.0, 61)]))
        assert rows == [
            {"date": "2026-10-01", "min_temp": 14.2, "max_temp": 24.8, "weather_desc": "晴れ"},
            {"date": "2026-10-02", "min_temp": 15.0, "max_temp": 21.0, "weather_desc": "雨"},
        ]

    @pytest.mark.parametrize("payload", [None, [], {}, {"daily": None}, {"daily": {}}, {"daily": {"time": "x"}}])
    def test_unexpected_shapes_yield_nothing(self, payload):
        assert svc.parse_daily(payload) == []

    def test_bad_dates_and_all_missing_temps_are_dropped(self):
        rows = svc.parse_daily(_payload([
            ("not-a-date", 10, 20, 1), ("2026-10-02", None, None, 1), ("2026-10-03", 11, 19, 1),
        ]))
        assert [r["date"] for r in rows] == ["2026-10-03"]

    def test_one_missing_temperature_is_kept_as_none(self):
        rows = svc.parse_daily(_payload([("2026-10-01", None, 20.0, None)]))
        assert rows == [{"date": "2026-10-01", "min_temp": None, "max_temp": 20.0, "weather_desc": None}]

    @pytest.mark.parametrize("bad", ["abc", float("nan"), float("inf"), 999, -999])
    def test_implausible_temperatures_are_dropped(self, bad):
        rows = svc.parse_daily(_payload([("2026-10-01", bad, 20.0, 1)]))
        assert rows[0]["min_temp"] is None and rows[0]["max_temp"] == 20.0

    def test_short_value_lists_do_not_crash(self):
        payload = {"daily": {"time": ["2026-10-01", "2026-10-02"], "temperature_2m_min": [10.0],
                             "temperature_2m_max": [20.0, 21.0], "weather_code": []}}
        rows = svc.parse_daily(payload)
        assert [r["date"] for r in rows] == ["2026-10-01", "2026-10-02"]
        assert rows[1]["min_temp"] is None and rows[1]["max_temp"] == 21.0


class TestFetch:
    def test_fetch_recent_uses_forecast_api_with_past_days(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(svc.requests, "get", lambda url, params, timeout: seen.update(url=url, params=params, timeout=timeout) or _Resp(_payload([("2026-10-10", 12, 22, 0)])))
        rows = svc.fetch_recent(7)
        assert rows and seen["url"] == svc.FORECAST_URL
        assert seen["params"]["past_days"] == 6 and seen["params"]["forecast_days"] == 1
        assert seen["params"]["latitude"] == config.WEATHER_LATITUDE
        assert seen["params"]["timezone"] == "Asia/Tokyo" and seen["timeout"] == svc.REQUEST_TIMEOUT_SEC

    def test_fetch_recent_clamps_days(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(svc.requests, "get", lambda url, params, timeout: seen.update(params=params) or _Resp({}))
        svc.fetch_recent(10_000)
        assert seen["params"]["past_days"] == 91

    def test_fetch_range_uses_archive_api(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(svc.requests, "get", lambda url, params, timeout: seen.update(url=url, params=params) or _Resp({}))
        svc.fetch_range(date(2026, 1, 1), date(2026, 1, 31))
        assert seen["url"] == svc.ARCHIVE_URL
        assert seen["params"]["start_date"] == "2026-01-01" and seen["params"]["end_date"] == "2026-01-31"

    @pytest.mark.parametrize("failure", [
        requests.exceptions.ConnectionError("down"), requests.exceptions.Timeout("slow"),
    ])
    def test_network_errors_return_none(self, monkeypatch, failure):
        def boom(*a, **k):
            raise failure
        monkeypatch.setattr(svc.requests, "get", boom)
        assert svc.fetch_recent() is None

    def test_http_error_and_bad_json_return_none(self, monkeypatch):
        monkeypatch.setattr(svc.requests, "get", lambda *a, **k: _Resp({}, status=500))
        assert svc.fetch_recent() is None
        monkeypatch.setattr(svc.requests, "get", lambda *a, **k: _Resp(ValueError("not json")))
        assert svc.fetch_recent() is None


class TestSaveRows:
    ROW: ClassVar[dict] = {"date": "2026-10-01", "min_temp": 14.0, "max_temp": 24.0, "weather_desc": "晴れ"}

    def test_inserts_with_the_configured_location(self, isolated_db):
        assert svc.save_rows([self.ROW]) == 1
        row = _rows()[0]
        assert row["location"] == config.WEATHER_LOCATION_NAME
        assert (row["min_temp"], row["max_temp"], row["weather_desc"]) == (14.0, 24.0, "晴れ")

    def test_updates_the_same_day_instead_of_duplicating(self, isolated_db):
        svc.save_rows([self.ROW])
        svc.save_rows([{**self.ROW, "min_temp": 15.5, "max_temp": 25.5}])
        rows = _rows()
        assert len(rows) == 1 and rows[0]["min_temp"] == 15.5 and rows[0]["max_temp"] == 25.5

    def test_keeps_other_columns_and_existing_description(self, isolated_db):
        with get_db_cursor(commit=True) as cur:
            cur.execute("INSERT INTO weather_history (date, location, min_temp, max_temp, weather_desc, max_pop, umbrella_level) "
                        "VALUES ('2026-10-01', ?, 1, 2, '雨', 80, '必要')", (config.WEATHER_LOCATION_NAME,))
        svc.save_rows([{**self.ROW, "weather_desc": None}])
        row = _rows()[0]
        assert (row["weather_desc"], row["max_pop"], row["umbrella_level"]) == ("雨", 80, "必要")
        assert row["min_temp"] == 14.0

    def test_other_locations_are_separate_rows(self, isolated_db):
        svc.save_rows([self.ROW], location="高砂")
        svc.save_rows([self.ROW], location="伊丹")
        assert {r["location"] for r in _rows()} == {"高砂", "伊丹"}

    def test_empty_input_saves_nothing(self, isolated_db):
        assert svc.save_rows([]) == 0 and _rows() == []

    def test_db_failure_returns_zero(self, isolated_db, monkeypatch):
        monkeypatch.setattr(svc, "get_db_cursor", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db")))
        assert svc.save_rows([self.ROW]) == 0


class TestUpdateAndBackfill:
    def test_update_recent_saves_fetched_rows(self, isolated_db, monkeypatch):
        monkeypatch.setattr(svc, "fetch_recent", lambda days=7: [{"date": "2026-10-10", "min_temp": 12.0, "max_temp": 22.0, "weather_desc": "晴れ"}])
        assert svc.update_recent() == 1 and len(_rows()) == 1

    def test_update_recent_returns_zero_when_fetch_fails(self, isolated_db, monkeypatch):
        monkeypatch.setattr(svc, "fetch_recent", lambda days=7: None)
        assert svc.update_recent() == 0

    def test_backfill_walks_back_in_chunks_ending_yesterday(self, isolated_db, monkeypatch):
        ranges = []
        monkeypatch.setattr(svc, "fetch_range", lambda s, e: ranges.append((s, e)) or [
            {"date": s.isoformat(), "min_temp": 1.0, "max_temp": 2.0, "weather_desc": None}])
        total = svc.backfill(200, today=date(2026, 10, 10))
        assert ranges[0][0] == date(2026, 3, 24) and ranges[-1][1] == date(2026, 10, 9)
        assert all((e - s).days + 1 <= svc.BACKFILL_CHUNK_DAYS for s, e in ranges)
        assert [r[0] for r in ranges[1:]] == [r[1] + (date(2026, 1, 2) - date(2026, 1, 1)) for r in ranges[:-1]]
        assert len(ranges) == 3 and total == 3

    def test_backfill_continues_after_a_failed_chunk(self, isolated_db, monkeypatch):
        calls = []

        def fake(s, e):
            calls.append(s)
            return None if len(calls) == 1 else [{"date": s.isoformat(), "min_temp": 1.0, "max_temp": 2.0, "weather_desc": None}]

        monkeypatch.setattr(svc, "fetch_range", fake)
        assert svc.backfill(200, today=date(2026, 10, 10)) == 2 and len(calls) == 3

    def test_backfill_minimum_one_day(self, isolated_db, monkeypatch):
        ranges = []
        monkeypatch.setattr(svc, "fetch_range", lambda s, e: ranges.append((s, e)) or None)
        svc.backfill(0, today=date(2026, 10, 10))
        assert ranges == [(date(2026, 10, 9), date(2026, 10, 9))]


class TestMonitor:
    def test_default_run_updates_recent_only(self, monkeypatch):
        calls = []
        monkeypatch.setattr(weather_monitor.weather_history_service, "update_recent", lambda: calls.append("recent") or 3)
        monkeypatch.setattr(weather_monitor.weather_history_service, "backfill", lambda d: calls.append(("backfill", d)) or 0)
        assert weather_monitor.main([]) == 0
        assert calls == ["recent"]

    def test_backfill_option_runs_backfill_then_recent(self, monkeypatch):
        calls = []
        monkeypatch.setattr(weather_monitor.weather_history_service, "update_recent", lambda: calls.append("recent") or 1)
        monkeypatch.setattr(weather_monitor.weather_history_service, "backfill", lambda d: calls.append(("backfill", d)) or 5)
        assert weather_monitor.main(["--backfill-days", "400"]) == 0
        assert calls == [("backfill", 400), "recent"]

    def test_failure_still_exits_zero(self, monkeypatch):
        """外部APIの一時的な不調で、スケジューラ側のエラー扱いにしない。"""
        monkeypatch.setattr(weather_monitor.weather_history_service, "update_recent", lambda: 0)
        assert weather_monitor.main([]) == 0

    def test_is_registered_in_the_scheduler(self):
        import scheduler_boot
        task = next((t for t in scheduler_boot.TASKS if t["script"] == "monitors/weather_monitor.py"), None)
        assert task is not None and task["interval"] == 21600
