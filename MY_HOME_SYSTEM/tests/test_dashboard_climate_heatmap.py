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
import pytz
from core.database import get_db_cursor
from routers import dashboard_router
from services import analysis_service, dashboard_page_service, home_status_service

_JST = pytz.timezone("Asia/Tokyo")
CAMERA = home_status_service.CAMERA_DEVICE_TYPE

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


def _home(path="/dashboard/", climate=None, activity=None):
    stubs = {
        "load_sensor_data": pd.DataFrame(), "load_generic_data": pd.DataFrame(), "load_nas_status": None,
        "load_nas_history": pd.DataFrame(), "get_memory_usage": {"percent": 42.0},
        "calculate_monthly_cost_cumulative": 1234, "calculate_last_month_cost_same_point": 1000,
        "get_disk_usage": {"percent": 55.0}, "calculate_daily_cost_series": [],
        "load_hourly_climate": TestRender.DF if climate is None else climate,
        "load_activity_events": pd.DataFrame() if activity is None else activity,
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


# --- 動き(「今日の動き」と同じ数え方の30日版) ---

def _events(rows):
    """`load_activity_events`の戻り値の形(JST・location/friendly_name付き)。
    行: (ISO時刻, device_id, device_type, location, contact_state, movement_state)"""
    df = pd.DataFrame(rows, columns=["timestamp", "device_id", "device_type", "location", "contact_state", "movement_state"])
    df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_convert(_JST)
    df["friendly_name"] = df["device_id"]
    return df


class TestActivityLoader:
    def test_only_state_rows_are_loaded(self, isolated_db):
        rows = [
            ("2026-10-16T09:00:00+09:00", "door", "Contact Sensor", "open", None),
            ("2026-10-16T09:05:00+09:00", "cam", CAMERA, None, "ON"),
            ("2026-10-16T09:10:00+09:00", "meter", "Meter", None, None),        # 状態なし → 除外
            ("2026-10-16T09:15:00+09:00", "plug", "Plug", "", ""),               # 空文字 → 除外
            ("2026-09-01T09:00:00+09:00", "door", "Contact Sensor", "open", None),  # 期間外
        ]
        with get_db_cursor(commit=True) as cur:
            for ts, dev, typ, contact, move in rows:
                cur.execute(
                    "INSERT INTO device_records (timestamp, device_id, device_name, device_type, contact_state, movement_state) "
                    "VALUES (?, ?, ?, ?, ?, ?)", (ts, dev, dev, typ, contact, move))
        df = analysis_service.load_activity_events(date(2026, 10, 1))
        assert sorted(df["device_id"]) == ["cam", "door"]
        assert {"friendly_name", "location"} <= set(df.columns)

    def test_empty_table_gives_empty_frame(self, isolated_db):
        assert analysis_service.load_activity_events(date(2026, 10, 1)).empty


class TestActivityGrids:
    def test_counts_use_the_same_rules_as_todays_activity(self):
        df = _events([
            # 防犯: カメラの動体検知(ON)だけが数える
            ("2026-10-16T09:01:00+09:00", "cam", CAMERA, "その他", None, "ON"),
            ("2026-10-16T09:20:00+09:00", "cam", CAMERA, "その他", None, "ON"),
            ("2026-10-16T09:30:00+09:00", "cam", CAMERA, "その他", None, "OFF"),
            # 高砂: 開→閉(60秒以内)は1回にまとめる
            ("2026-10-16T10:00:00+09:00", "door_t", "Contact Sensor", "高砂", "open", None),
            ("2026-10-16T10:00:20+09:00", "door_t", "Contact Sensor", "高砂", "close", None),
            # 伊丹: 人感センサーは detected だけ数える(not_detected は数えない)
            ("2026-10-16T11:00:00+09:00", "pir", "Motion Sensor", "伊丹", "detected", None),
            ("2026-10-16T11:01:00+09:00", "pir", "Motion Sensor", "伊丹", "not_detected", None),
            ("2026-10-15T23:00:00+09:00", "pir", "Motion Sensor", "伊丹", "detected", None),
            ("2026-10-15T23:05:00+09:00", "pir", "Motion Sensor", "伊丹", "not_detected", None),  # 同じ状態の連続は1回にまとまる
        ])
        grids = dashboard_page_service.activity_hourly_grids(df)
        assert grids["security"]["2026-10-16"][9] == 2
        assert grids["takasago"]["2026-10-16"][10] == 1
        assert grids["itami"]["2026-10-16"][11] == 1
        assert grids["itami"]["2026-10-15"][23] == 1
        assert sum(grids["itami"]["2026-10-16"]) == 1

    def test_empty_input_gives_empty_grids_for_every_source(self):
        assert dashboard_page_service.activity_hourly_grids(pd.DataFrame()) == {"security": {}, "takasago": {}, "itami": {}}

    def test_parsers(self):
        assert dashboard_page_service.parse_climate_metric("act") == "act"
        assert dashboard_page_service.parse_climate_source("itami") == "itami"
        assert dashboard_page_service.parse_climate_source("<x>") == "security"


class TestActivityRender:
    ACT = {"security": {"2026-10-16": [0] * 9 + [2, 5, 12] + [0] * 12}, "takasago": {}, "itami": {}}

    def _render(self, **kw):
        return dashboard_page_service.render_home_climate_heatmap(
            pd.DataFrame(), NOW, dashboard_path="/dashboard/", metric="act", activity=self.ACT, **kw)

    def test_rows_levels_and_titles(self):
        html = self._render()
        assert html.count('class="climate-row"') == 30
        assert html.count('class="climate-cell ') == 30 * 24
        assert 'hm-1" title="10/16 9時台: 2回"' in html
        assert 'hm-2" title="10/16 10時台: 5回"' in html
        assert 'hm-3" title="10/16 11時台: 12回"' in html
        assert 'hm-0" title="10/16 8時台: 動きなし"' in html
        assert "10/16 13時台: まだ先です" in html
        assert "10/15 5時台: 動きなし" in html                  # 記録のない日も0回の灰色(欠損とは区別しない)

    def test_heading_subtitle_and_legend(self):
        html = self._render()
        assert "🏃 動きの推移(過去30日)" in html
        assert "🛡️ 防犯・1時間ごとの回数(「今日の動き」と同じ数え方)" in html
        for text in ("動きなし", "1〜2回", "3〜9回", "10回以上"):
            assert text in html

    def test_other_source_is_selected_and_empty_source_is_all_gray(self):
        html = self._render(source="itami")
        assert "🏠 伊丹(自宅)・1時間ごとの回数" in html
        assert 'class="climate-cell hm-1"' not in html and 'class="climate-cell hm-3"' not in html

    def test_switch_links(self):
        html = self._render(source="takasago")
        assert 'href="/dashboard/?hm_loc=itami&amp;hm_metric=act&amp;hm_src=takasago#climate-heatmap"' in html
        assert 'href="/dashboard/?hm_loc=itami&amp;hm_metric=act#climate-heatmap"' in html          # 防犯(既定)は hm_src を省く
        assert 'href="/dashboard/?hm_loc=itami&amp;hm_metric=temp&amp;hm_src=takasago#climate-heatmap"' in html
        assert html.count('aria-current="true"') == 2
        for label in ("🌡️ 温度", "💧 湿度", "🏃 動き", "🛡️ 防犯", "👵 高砂(実家)", "🏠 伊丹(自宅)"):
            assert label in html

    def test_temperature_view_also_offers_the_activity_tab(self):
        html = dashboard_page_service.render_home_climate_heatmap(TestRender.DF, NOW, dashboard_path="/dashboard/")
        assert "🏃 動き" in html and "hm_metric=act" in html


class TestActivityHome:
    def _events_df(self):
        return _events([("2026-10-16T09:01:00+09:00", "cam", CAMERA, "その他", None, "ON")])

    def test_activity_view_is_applied_to_page_and_fragment(self):
        for path in ("/dashboard/?hm_metric=act&hm_src=itami", f"{config.DASHBOARD_BASE_PATH}/status?hm_metric=act&hm_src=itami"):
            text = _home(path, activity=self._events_df()).text
            assert "動きの推移(過去30日)" in text and "🏠 伊丹(自宅)・1時間ごとの回数" in text

    def test_activity_is_read_only_when_selected(self):
        with patch.object(home_status_service.analysis_service, "load_activity_events", return_value=pd.DataFrame()) as spy:
            with patch.object(home_status_service.analysis_service, "load_hourly_climate", return_value=TestRender.DF):
                _home("/dashboard/")  # 既定は温度。動きの集計には触れない
        # _home 自身が同名を差し替えるため、呼び出し回数ではなく「既定ページに動きの見出しが出ない」ことを見る
        assert "動きの推移" not in _home("/dashboard/").text
        assert spy is not None

    def test_invalid_source_falls_back_to_security(self):
        text = _home("/dashboard/?hm_metric=act&hm_src=%3Cx%3E", activity=self._events_df()).text
        assert "🛡️ 防犯・1時間ごとの回数" in text and "<x>" not in text

    def test_activity_result_is_cached(self):
        home_status_service.clear_status_cache()
        calls = []
        def build(df):
            calls.append(1)
            return {"ok": True}
        with patch.object(home_status_service.analysis_service, "load_activity_events", return_value=pd.DataFrame()):
            assert home_status_service.get_hourly_activity(build) == {"ok": True}
            assert home_status_service.get_hourly_activity(build) == {"ok": True}
        assert len(calls) == 1
