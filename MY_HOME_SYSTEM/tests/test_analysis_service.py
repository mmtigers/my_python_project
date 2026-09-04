# MY_HOME_SYSTEM/tests/test_analysis_service.py
"""
services/analysis_service.py (ダッシュボード向けDB読み取り専用クエリ群)のテスト。

これらは全て「読み取り専用モードでDBに接続し、Pandas DataFrameとして返す」という
DB操作そのもの。テーブルが存在しない・カラムが一致しない場合でも例外を投げず
空データを返すFail-Soft設計になっているため、その両方を検証する。
"""
import os
import sqlite3
import sys
from datetime import datetime

import pandas as pd
import pytz
from freezegun import freeze_time

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


class TestLoadSensorDataPowerDeviceTypeClassification:
    """Issue #169の回帰テスト: device_nameに"Remo"を含むかで"Nature Remo E Lite"/"Plug"に
    正しく振り分けた直後、`.replace("Plug", "Nature Remo E Lite")`で全行を
    "Nature Remo E Lite"に一括置換してしまっていた不具合。この結果、個別家電(Plug)の
    グラフが常に空になり(views/dashboard/sensor_tab.pyのstr.contains("Plug")フィルタが
    一致しなくなる)、全プラグの消費電力がスマートメーター全体消費のグラフへ混入していた。"""

    def test_plug_device_keeps_plug_device_type(self, isolated_db):
        with common.get_db_cursor(commit=True) as cur:
            cur.execute(
                f"INSERT INTO {config.SQLITE_TABLE_POWER_USAGE} (device_id, device_name, wattage, timestamp) "
                "VALUES ('dev1', 'Plug1', 50, '2026-01-01T00:00:00')"
            )
        result = analysis_service.load_sensor_data()
        row = result[result["device_id"] == "dev1"].iloc[0]
        assert row["device_type"] == "Plug", (
            f"Plug由来のdevice_typeがNature Remo E Liteへ一括置換されている: {row['device_type']!r}"
        )

    def test_nature_remo_device_keeps_nature_remo_device_type(self, isolated_db):
        with common.get_db_cursor(commit=True) as cur:
            cur.execute(
                f"INSERT INTO {config.SQLITE_TABLE_POWER_USAGE} (device_id, device_name, wattage, timestamp) "
                "VALUES ('dev2', 'Nature Remo E Lite (Living)', 300, '2026-01-01T00:00:00')"
            )
        result = analysis_service.load_sensor_data()
        row = result[result["device_id"] == "dev2"].iloc[0]
        assert row["device_type"] == "Nature Remo E Lite"


# C-L3 (Issue #414): 月初 00:00:00〜00:00:01 JST の1秒窓で境界判定が揺れるため、
# 現在時刻を月の半ば(2026-09-15 12:00 JST = 03:00 UTC)に固定する。
@freeze_time("2026-09-15 03:00:00")
class TestCalculateMonthlyCostCumulative:
    def test_returns_zero_when_no_power_data(self, isolated_db):
        assert analysis_service.calculate_monthly_cost_cumulative() == 0

    def test_plug_readings_are_excluded_from_smart_meter_cost(self, isolated_db):
        """Issue #170の回帰テスト: power_usageにはスマートメーター(全体消費)と
        各プラグ(個別家電)が同居しているが、以前はデバイスを絞らず全行を
        diff()ベースで合算していたため、プラグの消費電力がスマートメーターの
        計測値(既にプラグ分を含む)へ二重計上されていた。プラグの読み取り値が
        時系列上にどれだけ混在していても、計算結果はスマートメーター単独の
        場合と一致するべき。"""
        now = datetime.now(pytz.timezone("Asia/Tokyo"))
        # start_of_monthはマイクロ秒+タイムゾーンオフセット付きのisoformat()文字列
        # ("...T00:00:00.xxxxxx+09:00")で、SQL側は単純な文字列比較(>=)を行うため、
        # ちょうど月初(second=0)ちょうどのタイムスタンプは境界で除外されうる。
        # 1秒後を基準にして安全にstart_of_month以降になるようにする。
        base = now.replace(day=1, hour=0, minute=0, second=1)

        with common.get_db_cursor(commit=True) as cur:
            # スマートメーター: 1時間おきに1000Wで2点(1.0kWh分)
            cur.execute(
                f"INSERT INTO {config.SQLITE_TABLE_POWER_USAGE} (device_id, device_name, wattage, timestamp) VALUES "
                f"('remo1', '伊丹_Nature Remo E Lite', 1000, '{base.strftime('%Y-%m-%dT%H:%M:%S')}')"
            )
            ts2 = base.replace(hour=1).strftime("%Y-%m-%dT%H:%M:%S")
            cur.execute(
                f"INSERT INTO {config.SQLITE_TABLE_POWER_USAGE} (device_id, device_name, wattage, timestamp) VALUES "
                f"('remo1', '伊丹_Nature Remo E Lite', 1000, '{ts2}')"
            )
            # プラグ: スマートメーターの間に挟まる形で大電力(5000W)を1点記録
            ts_plug = base.replace(minute=30).strftime("%Y-%m-%dT%H:%M:%S")
            cur.execute(
                f"INSERT INTO {config.SQLITE_TABLE_POWER_USAGE} (device_id, device_name, wattage, timestamp) VALUES "
                f"('plug1', 'Plug_TV', 5000, '{ts_plug}')"
            )

        with_plug_result = analysis_service.calculate_monthly_cost_cumulative()

        with common.get_db_cursor(commit=True) as cur:
            cur.execute(f"DELETE FROM {config.SQLITE_TABLE_POWER_USAGE} WHERE device_id = 'plug1'")
        meter_only_result = analysis_service.calculate_monthly_cost_cumulative()

        assert with_plug_result == meter_only_result, (
            "プラグの読み取り値がスマートメーターの電気代計算に混入している: "
            f"with_plug={with_plug_result}, meter_only={meter_only_result}"
        )

    def test_diff_is_computed_per_device_not_across_interleaved_devices(self, isolated_db):
        """Issue #170の回帰テスト: 複数device_id(例: 複数拠点のスマートメーター)の行が
        時系列で混在したままdiff()を取ると、直前行が別デバイスの場合に誤った時間幅が
        計算に使われる。device_idごとにグループ化してdiff()を取ることで、各デバイスの
        経過時間はそのデバイス自身の直前レコードとの差分になるべき。"""
        now = datetime.now(pytz.timezone("Asia/Tokyo"))
        # (前テストと同じ理由で)月初ちょうどの境界を避けて1秒後を基準にする
        base = now.replace(day=1, hour=0, minute=0, second=1)

        with common.get_db_cursor(commit=True) as cur:
            # デバイスA(伊丹)とデバイスB(高砂)が10分ずれで交互に記録される
            rows = [
                ("remo_itami", "伊丹_Nature Remo E Lite", 1000, base),
                ("remo_takasago", "高砂_Nature Remo E Lite", 2000, base.replace(minute=10)),
                ("remo_itami", "伊丹_Nature Remo E Lite", 1000, base.replace(hour=1)),
                ("remo_takasago", "高砂_Nature Remo E Lite", 2000, base.replace(hour=1, minute=10)),
            ]
            for device_id, device_name, watts, ts in rows:
                cur.execute(
                    f"INSERT INTO {config.SQLITE_TABLE_POWER_USAGE} (device_id, device_name, wattage, timestamp) VALUES "
                    f"('{device_id}', '{device_name}', {watts}, '{ts.strftime('%Y-%m-%dT%H:%M:%S')}')"
                )

        result = analysis_service.calculate_monthly_cost_cumulative()

        # 正しい計算: 伊丹=1000W×1h=1.0kWh, 高砂=2000W×1h=2.0kWh, 合計3.0kWh -> 31倍で93
        # (device_idでグループ化せず時系列のまま混在diff()を取ると、10分/50分単位の
        # 誤った時間幅が使われ、異なる(98)結果になっていた)
        assert result == int(3.0 * 31)


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
    Issue #114で修正済み: 以前はweather_historyテーブルの実カラムと
    load_weather_history/load_yearly_temperature_stats が要求するカラム
    (location, umbrella_level等)が一致しておらず、新規DB(init_db)では
    "no such column: location" のOperationalErrorがexceptで握りつぶされ、
    常に空のDataFrameが返っていた(天気関連の表示・年間気温統計が無言で空になるバグ)。
    例外を投げないこと自体は引き続き保証しつつ、修正後は実際に投入したデータが
    正しく返ってくることも検証する。
    """

    @staticmethod
    def _insert_weather_row(db_path, date, location, min_temp=1.0, max_temp=10.0,
                             weather_desc="晴れ", max_pop=10, umbrella_level="不要"):
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "INSERT INTO weather_history "
                "(date, location, min_temp, max_temp, weather_desc, max_pop, umbrella_level, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (date, location, min_temp, max_temp, weather_desc, max_pop, umbrella_level, date),
            )
            conn.commit()
        finally:
            conn.close()

    def test_load_weather_history_does_not_raise_and_returns_dataframe(self, isolated_db):
        result = analysis_service.load_weather_history()
        assert isinstance(result, pd.DataFrame)

    @freeze_time("2026-09-15 03:00:00")  # C-L3 (Issue #414): naive な datetime.now() の日付を固定
    def test_load_weather_history_returns_rows_matching_location(self, isolated_db):
        today = datetime.now().strftime("%Y-%m-%d")
        self._insert_weather_row(isolated_db, today, location="伊丹")
        self._insert_weather_row(isolated_db, today, location="東京")

        result = analysis_service.load_weather_history(location="伊丹")

        assert len(result) == 1
        assert result.iloc[0]["date"] == today

    def test_load_yearly_temperature_stats_does_not_raise(self, isolated_db):
        result = analysis_service.load_yearly_temperature_stats(2026)
        assert isinstance(result, pd.DataFrame)

    def test_load_yearly_temperature_stats_returns_weather_data_for_matching_location(self, isolated_db):
        self._insert_weather_row(isolated_db, "2026-03-15", location="伊丹", max_temp=15.0, min_temp=5.0)

        result = analysis_service.load_yearly_temperature_stats(2026, location="伊丹")

        assert len(result) == 1
        assert result.iloc[0]["out_max"] == 15.0
        assert result.iloc[0]["out_min"] == 5.0


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
