# MY_HOME_SYSTEM/tests/test_analysis_service.py
"""
services/analysis_service.py (ダッシュボード向けDB読み取り専用クエリ群)のテスト。

これらは全て「読み取り専用モードでDBに接続し、Pandas DataFrameとして返す」という
DB操作そのもの。テーブルが存在しない・カラムが一致しない場合でも例外を投げず
空データを返すFail-Soft設計になっているため、その両方を検証する。
"""
import os
import sys
from unittest.mock import MagicMock

import pandas as pd
import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import common
import config
import services.analysis_service as analysis_service


class TestProcessDataframe:
    def test_empty_dataframe_passes_through_unchanged(self):
        df = pd.DataFrame()
        result = analysis_service.process_dataframe(df)
        assert result.empty

    def test_converts_timestamp_to_jst(self):
        df = pd.DataFrame({"timestamp": ["2026-01-01T00:00:00Z"], "value": [1]})
        result = analysis_service.process_dataframe(df)
        assert str(result["timestamp"].dt.tz) == "Asia/Tokyo"

    def test_naive_timestamp_is_interpreted_as_jst_not_utc(self):
        """M-1-4回帰防止: tzinfoの無いレガシーレコードは、保存規約
        (core.utils.get_now_iso)に合わせてJSTとして記録されているとみなす。
        以前はpd.to_datetime(..., utc=True)で一律UTCとみなしていたため、
        9時間先の時刻にズレていた。"""
        df = pd.DataFrame({"timestamp": ["2026-01-01 09:00:00"], "value": [1]})
        result = analysis_service.process_dataframe(df)
        ts = result["timestamp"].iloc[0]
        assert (ts.hour, ts.minute) == (9, 0)

    def test_aware_and_naive_timestamps_can_coexist_in_the_same_column(self):
        """実データはget_now_iso導入前後で naive/aware が混在しうるため、
        混在カラムでも例外にならず両方とも正しくJSTへ変換されること。"""
        df = pd.DataFrame({
            "timestamp": ["2026-01-01 09:00:00", "2026-01-01T00:00:00Z"],
            "value": [1, 2],
        })
        result = analysis_service.process_dataframe(df)
        assert str(result["timestamp"].dt.tz) == "Asia/Tokyo"
        assert result["timestamp"].iloc[0].hour == 9
        assert result["timestamp"].iloc[1].hour == 9


class TestApplyFriendlyNames:
    def test_empty_dataframe_passes_through(self):
        assert analysis_service.apply_friendly_names(pd.DataFrame()).empty

    def test_missing_device_id_column_defaults_to_unknown(self):
        df = pd.DataFrame({"other_col": [1, 2]})
        result = analysis_service.apply_friendly_names(df)
        assert (result["friendly_name"] == "Unknown").all()
        assert (result["location"] == "その他").all()

    def test_camera_id_is_treated_as_device_id(self):
        df = pd.DataFrame({"camera_id": ["cam1"], "timestamp": ["2026-01-01T00:00:00"]})
        result = analysis_service.apply_friendly_names(df)
        assert result["device_id"].iloc[0] == "cam1"

    def test_unmapped_device_falls_back_to_device_id(self, monkeypatch):
        monkeypatch.setattr(config, "MONITOR_DEVICES", [])
        df = pd.DataFrame({"device_id": ["unknown_device"]})
        result = analysis_service.apply_friendly_names(df)
        assert result["friendly_name"].iloc[0] == "unknown_device"
        assert result["location"].iloc[0] == "その他"

    def test_known_device_uses_configured_name_and_location(self, monkeypatch):
        monkeypatch.setattr(
            config, "MONITOR_DEVICES", [{"id": "dev1", "name": "リビングセンサー", "location": "1階"}]
        )
        df = pd.DataFrame({"device_id": ["dev1"]})
        result = analysis_service.apply_friendly_names(df)
        assert result["friendly_name"].iloc[0] == "リビングセンサー"
        assert result["location"].iloc[0] == "1階"


class TestLoadDataFromDb:
    def test_returns_empty_dataframe_on_malformed_query(self, isolated_db):
        result = analysis_service.load_data_from_db("SELECT * FROM nonexistent_table_xyz")
        assert isinstance(result, pd.DataFrame)
        assert result.empty

    def test_returns_rows_for_valid_query(self, isolated_db):
        with common.get_db_cursor(commit=True) as cur:
            cur.execute(
                f"INSERT INTO {config.SQLITE_TABLE_NAS} "
                "(timestamp, device_name, ip_address, status_ping, status_mount, total_gb, used_gb, free_gb, percent) "
                "VALUES ('2026-01-01T00:00:00', 'NAS1', '192.168.1.1', 'ok', 'ok', 100, 50, 50, 50.0)"
            )
        result = analysis_service.load_data_from_db(f"SELECT * FROM {config.SQLITE_TABLE_NAS}")
        assert len(result) == 1
        assert result.iloc[0]["device_name"] == "NAS1"


class TestLoadNasStatus:
    def test_returns_none_when_no_data(self, isolated_db):
        assert analysis_service.load_nas_status() is None

    def test_returns_latest_row_when_data_exists(self, isolated_db):
        with common.get_db_cursor(commit=True) as cur:
            for ts in ["2026-01-01T00:00:00", "2026-01-02T00:00:00"]:
                cur.execute(
                    f"INSERT INTO {config.SQLITE_TABLE_NAS} "
                    "(timestamp, device_name, ip_address, status_ping, status_mount, total_gb, used_gb, free_gb, percent) "
                    f"VALUES ('{ts}', 'NAS1', '192.168.1.1', 'ok', 'ok', 100, 50, 50, 50.0)"
                )
        row = analysis_service.load_nas_status()
        assert row is not None
        assert "2026-01-02" in str(row["timestamp"])


class TestLoadSensorData:
    def test_merges_legacy_meter_and_power_sources(self, isolated_db):
        with common.get_db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO device_records (timestamp, device_name, device_id, device_type) "
                "VALUES ('2026-01-01T00:00:00', 'LegacySensor', 'dev1', 'Contact Sensor')"
            )
            cur.execute(
                f"INSERT INTO {config.SQLITE_TABLE_SWITCHBOT_LOGS} (device_id, device_name, temperature, humidity, timestamp) "
                "VALUES ('dev2', 'Meter1', 25.0, 50.0, '2026-01-01T00:00:00')"
            )
            cur.execute(
                f"INSERT INTO {config.SQLITE_TABLE_POWER_USAGE} (device_id, device_name, wattage, timestamp) "
                "VALUES ('dev3', 'Plug1', 100, '2026-01-01T00:00:00')"
            )
        result = analysis_service.load_sensor_data()
        assert len(result) == 3

    def test_returns_empty_dataframe_when_no_sensor_data(self, isolated_db):
        result = analysis_service.load_sensor_data()
        assert result.empty


class TestCalculateMonthlyCostCumulative:
    def test_returns_zero_when_no_power_data(self, isolated_db):
        assert analysis_service.calculate_monthly_cost_cumulative() == 0


class TestLoadBicycleData:
    def test_returns_empty_when_no_data(self, isolated_db):
        assert analysis_service.load_bicycle_data().empty

    def test_returns_rows_when_seeded(self, isolated_db):
        with common.get_db_cursor(commit=True) as cur:
            cur.execute(
                f"INSERT INTO {config.SQLITE_TABLE_BICYCLE} (area_name, status_text, waiting_count, timestamp) "
                "VALUES ('駐輪場A', '空きあり', 0, '2026-01-01T00:00:00')"
            )
        result = analysis_service.load_bicycle_data()
        assert len(result) == 1


class TestLoadAiReport:
    def test_returns_none_when_no_report(self, isolated_db):
        assert analysis_service.load_ai_report() is None

    def test_returns_latest_report(self, isolated_db):
        with common.get_db_cursor(commit=True) as cur:
            cur.execute(
                f"INSERT INTO {config.SQLITE_TABLE_AI_REPORT} (message, timestamp) "
                "VALUES ('週次レポート', '2026-01-01T00:00:00')"
            )
        row = analysis_service.load_ai_report()
        assert row["message"] == "週次レポート"


class TestLoadRankingData:
    def test_load_ranking_dates_returns_empty_when_table_missing(self, isolated_db):
        assert analysis_service.load_ranking_dates() == []

    def test_load_ranking_data_returns_empty_when_table_missing(self, isolated_db):
        result = analysis_service.load_ranking_data("2026-01-01", "free")
        assert result.empty


class TestWeatherFunctionsFailSoftOnSchemaMismatch:
    """
    weather_history テーブルの実カラムと load_weather_history/load_yearly_temperature_stats が
    要求するカラム(location, umbrella_level等)が一致していない既知の問題があるが、
    いずれも例外を外に投げず空のDataFrameを返すFail-Soft設計になっていることを確認する。
    """

    def test_load_weather_history_does_not_raise_and_returns_dataframe(self, isolated_db):
        result = analysis_service.load_weather_history()
        assert isinstance(result, pd.DataFrame)

    def test_load_yearly_temperature_stats_does_not_raise(self, isolated_db):
        result = analysis_service.load_yearly_temperature_stats(2026)
        assert isinstance(result, pd.DataFrame)


class TestSystemStats:
    def test_get_disk_usage_returns_percent_between_0_and_100(self):
        usage = analysis_service.get_disk_usage()
        assert usage is not None
        assert 0 <= usage["percent"] <= 100

    def test_get_memory_usage_returns_expected_keys(self):
        usage = analysis_service.get_memory_usage()
        if usage is not None:  # 環境によっては `free` コマンドが無い場合もある
            assert set(usage.keys()) == {"total_mb", "used_mb", "available_mb", "percent"}

    def test_get_system_logs_does_not_raise(self):
        result = analysis_service.get_system_logs(lines=10)
        assert isinstance(result, str)

    def test_get_ngrok_url_returns_empty_dict_when_ngrok_not_running(self, monkeypatch):
        def _raise(*a, **kw):
            raise ConnectionError("no ngrok running")
        monkeypatch.setattr(analysis_service.requests, "get", _raise)
        assert analysis_service.get_ngrok_url() == {}

    def test_get_ngrok_url_parses_tunnels_response(self, monkeypatch):
        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = {
            "tunnels": [
                {"config": {"addr": "http://localhost:8000"}, "public_url": "https://server.ngrok.io"},
                {"config": {"addr": "http://localhost:8501"}, "public_url": "https://dashboard.ngrok.io"},
            ]
        }
        monkeypatch.setattr(analysis_service.requests, "get", lambda *a, **kw: fake_response)
        urls = analysis_service.get_ngrok_url()
        assert urls["server"] == "https://server.ngrok.io"
        assert urls["dashboard"] == "https://dashboard.ngrok.io"
