# MY_HOME_SYSTEM/tests/test_dashboard_climate_heatmap.py
"""ホームの「過去30日の温度/湿度」ヒートマップ(`render_home_climate_heatmap`)のテスト。

集計(`analysis_service.load_hourly_climate`)・拠点の判定・色段階・表示・ホームへの差し込み位置を確認する。
"""
import os
import sys
from datetime import date, datetime
from unittest.mock import patch

import pandas as pd
import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from core.database import get_db_cursor
from routers import dashboard_router
from services import analysis_service, dashboard_page_service, home_status_service

NOW = datetime.fromisoformat("2026-10-16T12:30:00+09:00")


def _df(rows):
    """`load_hourly_climate`の戻り値の形(date/hour/device_id/device_name/temp_avg/hum_avg/n)。"""
    return pd.DataFrame(rows, columns=["date", "hour", "device_id", "device_name", "temp_avg", "hum_avg", "n"])


@pytest.fixture(autouse=True)
def _no_monitor_devices(monkeypatch):
    monkeypatch.setattr(config, "MONITOR_DEVICES", [])


class TestLoadHourlyClimate:
    def test_hourly_average_per_device_and_zero_humidity_is_excluded(self, isolated_db):
        rows = [
            ("d1", "伊丹_Remo", 20.0, 50.0, "2026-10-16T09:05:00+09:00"),
            ("d1", "伊丹_Remo", 22.0, 0.0, "2026-10-16T09:35:00+09:00"),    # 湿度0.0=欠損の代用値
            ("d1", "伊丹_Remo", 30.0, 60.0, "2026-10-16T10:05:00+09:00"),
            ("d1", "伊丹_Remo", 10.0, 40.0, "2026-10-01T09:05:00+09:00"),    # 期間外
        ]
        with get_db_cursor(commit=True) as cur:
            for dev, name, t, h, ts in rows:
                cur.execute(
                    "INSERT INTO switchbot_meter_logs (device_id, device_name, temperature, humidity, timestamp) "
                    "VALUES (?, ?, ?, ?, ?)", (dev, name, t, h, ts),
                )
        df = analysis_service.load_hourly_climate(date(2026, 10, 15))
        nine = df[(df["date"] == "2026-10-16") & (df["hour"] == 9)].iloc[0]
        assert nine["temp_avg"] == pytest.approx(21.0)
        assert nine["hum_avg"] == pytest.approx(50.0)   # 0.0は平均に入れない
        assert nine["n"] == 2
        assert len(df) == 2                              # 期間外(10/01)は含まない

    def test_empty_table_returns_empty_frame(self, isolated_db):
        assert analysis_service.load_hourly_climate(date(2026, 10, 1)).empty


class TestGridAndLocation:
    def test_location_from_device_name_prefix(self):
        df = _df([
            ("2026-10-16", 9, "a", "伊丹_Remo", 20.0, 50.0, 1),
            ("2026-10-16", 9, "b", "高砂_Remo", 10.0, 40.0, 1),
            ("2026-10-16", 9, "c", "ガレージ", 99.0, 99.0, 1),     # どちらでもない → 含めない
        ])
        assert dashboard_page_service.climate_hourly_grid(df, "伊丹", "temp")["2026-10-16"][9] == 20.0
        assert dashboard_page_service.climate_hourly_grid(df, "高砂", "temp")["2026-10-16"][9] == 10.0

    def test_location_from_devices_json_wins(self, monkeypatch):
        monkeypatch.setattr(config, "MONITOR_DEVICES", [{"id": "x", "location": "高砂"}])
        df = _df([("2026-10-16", 9, "x", "伊丹_名前が紛らわしい", 10.0, 40.0, 1)])
        assert dashboard_page_service.climate_hourly_grid(df, "高砂", "temp")
        assert not dashboard_page_service.climate_hourly_grid(df, "伊丹", "temp")

    def test_devices_in_one_location_are_weighted_by_record_count(self):
        df = _df([
            ("2026-10-16", 9, "a", "伊丹_A", 20.0, 50.0, 3),
            ("2026-10-16", 9, "b", "伊丹_B", 30.0, 50.0, 1),
        ])
        assert dashboard_page_service.climate_hourly_grid(df, "伊丹", "temp")["2026-10-16"][9] == pytest.approx(22.5)

    def test_missing_humidity_is_left_empty(self):
        df = _df([("2026-10-16", 9, "a", "伊丹_A", 20.0, None, 2)])
        assert dashboard_page_service.climate_hourly_grid(df, "伊丹", "hum") == {}

    def test_empty_or_broken_frames_give_empty_grid(self):
        assert dashboard_page_service.climate_hourly_grid(pd.DataFrame(), "伊丹", "temp") == {}
        assert dashboard_page_service.climate_hourly_grid(pd.DataFrame({"x": [1]}), "伊丹", "temp") == {}

    @pytest.mark.parametrize("metric,value,level", [
        ("temp", 14.9, 0), ("temp", 15.0, 1), ("temp", 24.9, 2), ("temp", 25.0, 3), ("temp", 30.0, 4),
        ("hum", 29.9, 0), ("hum", 40.0, 2), ("hum", 69.9, 3), ("hum", 70.0, 4),
    ])
    def test_levels(self, metric, value, level):
        assert dashboard_page_service._climate_level(value, metric) == level

    def test_param_parsers_fall_back_to_default(self):
        assert dashboard_page_service.parse_climate_location("takasago") == "高砂"
        assert dashboard_page_service.parse_climate_location("<x>") == "伊丹"
        assert dashboard_page_service.parse_climate_metric("hum") == "hum"
        assert dashboard_page_service.parse_climate_metric("nope") == "temp"


class TestRender:
    DF = _df([
        ("2026-10-16", 9, "a", "伊丹_Remo", 24.3, 45.0, 6),
        ("2026-10-15", 23, "a", "伊丹_Remo", 31.0, 75.0, 6),
        ("2026-10-16", 9, "b", "高砂_Remo", 12.0, 30.0, 6),
    ])

    def _render(self, df=None, **kw):
        return dashboard_page_service.render_home_climate_heatmap(
            self.DF if df is None else df, NOW, dashboard_path="/dashboard/", **kw)

    def test_has_30_day_rows_newest_first_with_24_cells_each(self):
        html = self._render()
        assert html.count('class="climate-row"') == 30
        assert html.count('class="climate-cell ') == 30 * 24
        assert html.index("10/16(金)") < html.index("10/15(木)")

    def test_cell_values_levels_and_empty_and_future(self):
        html = self._render()
        assert 'cl-2" title="10/16 9時台: 24.3℃"' in html
        assert 'cl-4" title="10/15 23時台: 31.0℃"' in html
        assert "10/16 9時台: 記録なし" not in html
        assert "10/16 8時台: 記録なし" in html          # 記録のない時間は枠線だけ
        assert "10/16 13時台: まだ先です" in html         # 今(12時台)より先

    def test_humidity_and_other_location(self):
        html = self._render(location="高砂", metric="hum")
        assert "高砂・1時間ごとの平均" in html
        assert 'cl-0" title="10/16 9時台: 30.0%"' in html or 'cl-1" title="10/16 9時台: 30.0%"' in html
        assert "湿度の推移" in html

    def test_switch_links_keep_selection_and_anchor(self):
        html = self._render(location="高砂", metric="hum")
        assert 'href="/dashboard/?hm_loc=itami&amp;hm_metric=hum#climate-heatmap"' in html
        assert 'href="/dashboard/?hm_loc=takasago&amp;hm_metric=temp#climate-heatmap"' in html
        assert html.count('aria-current="true"') == 2

    def test_location_without_data_falls_back_to_one_with_data(self):
        df = _df([("2026-10-16", 9, "b", "高砂_Remo", 12.0, 30.0, 6)])
        html = self._render(df, location="伊丹")
        assert "高砂・1時間ごとの平均" in html
        assert "hm_loc=itami" not in html                # データのない拠点は切り替えに出さない

    def test_no_data_shows_placeholder(self):
        html = self._render(pd.DataFrame())
        assert "記録がまだありません" in html and "climate-row" not in html

    def test_legend_lists_all_levels(self):
        html = self._render()
        for text in ("〜15℃", "15〜20℃", "20〜25℃", "25〜30℃", "30℃〜", "記録なし"):
            assert text in html


# --- ホームへの差し込み ---

@pytest.fixture(autouse=True)
def _stubs(monkeypatch):
    home_status_service.clear_status_cache()
    monkeypatch.setattr(home_status_service, "check_quest_app_health", lambda: True)
    monkeypatch.setattr(home_status_service, "check_asa_note_health", lambda url: True)
    yield
    home_status_service.clear_status_cache()


def _home(path="/dashboard/", climate=None):
    stubs = {
        "load_sensor_data": pd.DataFrame(), "load_generic_data": pd.DataFrame(), "load_nas_status": None,
        "load_nas_history": pd.DataFrame(), "get_memory_usage": {"percent": 42.0},
        "calculate_monthly_cost_cumulative": 1234, "calculate_last_month_cost_same_point": 1000,
        "get_disk_usage": {"percent": 55.0}, "calculate_daily_cost_series": [],
        "load_hourly_climate": TestRender.DF if climate is None else climate,
    }
    patches = [patch.object(home_status_service.analysis_service, k, return_value=v) for k, v in stubs.items()]
    for p in patches:
        p.start()
    try:
        app = FastAPI()
        app.include_router(dashboard_router.router)
        with TestClient(app) as client:
            return client.get(path)
    finally:
        for p in patches:
            p.stop()


class TestHomeIntegration:
    def test_heatmap_sits_right_below_the_watch_group_and_above_life(self):
        text = _home().text
        assert text.index("👀 見守り") < text.index('id="climate-heatmap"') < text.index("💡 くらし")

    def test_selection_in_query_is_applied_to_page_and_fragment(self):
        page = _home("/dashboard/?hm_loc=takasago&hm_metric=hum").text
        fragment = _home(f"{config.DASHBOARD_BASE_PATH}/status?hm_loc=takasago&hm_metric=hum").text
        for text in (page, fragment):
            assert "高砂・1時間ごとの平均" in text and "湿度の推移" in text

    def test_invalid_selection_falls_back_to_default(self):
        text = _home("/dashboard/?hm_loc=%3Cscript%3E&hm_metric=zzz").text
        assert "伊丹・1時間ごとの平均" in text and "温度の推移" in text and "<script>" not in text.split("climate")[1][:400]

    def test_auto_refresh_carries_the_query_string(self):
        assert "window.location.search" in _home().text

    def test_page_still_opens_when_climate_query_fails(self):
        res = _home(climate=pd.DataFrame())
        assert res.status_code == 200 and "記録がまだありません" in res.text
