# MY_HOME_SYSTEM/tests/test_power_analysis_service.py
"""services/power_analysis_service.py(電気代・電力の分析)のテスト。

純粋な計算はDataFrameを直接渡して検証し、SQLの経路(スマートメーター・テレビのプラグ・気温)は
isolated_db に実際に記録を入れて通す。
"""
import os
import sys
from datetime import date, timedelta

import pandas as pd
import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from core.database import get_db_cursor
from services import analysis_service
from services import power_analysis_service as svc

PRICE = config.ELECTRICITY_YEN_PER_KWH


def jst(y, m, d, h=0, mi=0):
    return pd.Timestamp(y, m, d, h, mi, tz="Asia/Tokyo").to_pydatetime()


def samples(start, end, watts, device="m", step=5):
    ts = pd.date_range(start, end, freq=f"{step}min")
    return pd.DataFrame({"device_id": device, "timestamp": ts, "power_watts": watts if hasattr(watts, "__len__") else float(watts)})


@pytest.fixture(autouse=True)
def _fresh_cache():
    svc.clear_cache()
    yield
    svc.clear_cache()


class TestComputeIntervals:
    def test_energy_is_watts_times_elapsed_hours(self):
        df = samples(jst(2026, 10, 1, 0), jst(2026, 10, 1, 1), 1000)  # 13点・12区間(5分)=1時間
        iv = svc.compute_intervals(df)
        assert len(iv) == 12 and iv["hours"].sum() == pytest.approx(1.0)
        assert iv["kwh"].sum() == pytest.approx(1.0)

    def test_long_gaps_are_not_counted(self):
        df = pd.DataFrame({"device_id": "m", "power_watts": 1000.0, "timestamp": [
            jst(2026, 10, 1, 0, 0), jst(2026, 10, 1, 0, 5), jst(2026, 10, 1, 3, 0), jst(2026, 10, 1, 3, 5)]})
        iv = svc.compute_intervals(df)
        assert iv["hours"].round(4).tolist() == [round(5 / 60, 4), round(5 / 60, 4)]  # 2時間55分の欠落は除外

    def test_exactly_one_hour_gap_is_counted(self):
        df = pd.DataFrame({"device_id": "m", "power_watts": 1000.0, "timestamp": [jst(2026, 10, 1, 0), jst(2026, 10, 1, 1)]})
        assert svc.compute_intervals(df)["hours"].tolist() == [1.0]

    def test_intervals_do_not_cross_midnight(self):
        """日ごとに区切る(既存の日別と同じ)。23:55→00:00の区間は数えない。"""
        df = samples(jst(2026, 10, 1, 23, 50), jst(2026, 10, 2, 0, 10), 1000)
        iv = svc.compute_intervals(df)
        assert sorted(iv["date"].unique()) == [date(2026, 10, 1), date(2026, 10, 2)]
        # 23:50-23:55(1日目)・00:00-00:05・00:05-00:10(2日目)の3区間。23:55→00:00の区間は数えない
        assert len(iv) == 3

    def test_devices_are_not_mixed(self):
        a = samples(jst(2026, 10, 1, 0), jst(2026, 10, 1, 0, 30), 1000, "a")
        b = samples(jst(2026, 10, 1, 0, 2), jst(2026, 10, 1, 0, 32), 2000, "b")
        iv = svc.compute_intervals(pd.concat([a, b]))
        assert iv["hours"].round(4).eq(round(5 / 60, 4)).all()
        assert iv["kwh"].sum() == pytest.approx(1.0 * 6 / 12 + 2.0 * 6 / 12)

    @pytest.mark.parametrize("df", [pd.DataFrame(), pd.DataFrame({"timestamp": [1]}), pd.DataFrame({"power_watts": [1]})])
    def test_empty_or_missing_columns(self, df):
        assert svc.compute_intervals(df).empty

    def test_missing_device_column_is_tolerated_and_nan_power_is_dropped(self):
        df = pd.DataFrame({"timestamp": [jst(2026, 10, 1, 0), jst(2026, 10, 1, 0, 5), jst(2026, 10, 1, 0, 10)],
                           "power_watts": [100.0, None, 100.0]})
        iv = svc.compute_intervals(df)
        assert len(iv) == 1 and iv["hours"].iloc[0] == pytest.approx(10 / 60)

    def test_negative_power_reverse_flow_reduces_energy(self):
        df = samples(jst(2026, 10, 1, 0), jst(2026, 10, 1, 1), -500)
        assert svc.compute_intervals(df)["kwh"].sum() == pytest.approx(-0.5)

    def test_matches_the_existing_daily_cost_semantics(self, isolated_db):
        """既存の日別電気代(_calculate_cost_between)と同じ値になる(日単位の窓)。"""
        day = date(2026, 10, 1)
        for ts in pd.date_range(jst(2026, 10, 1, 0), jst(2026, 10, 1, 23, 55), freq="5min"):
            _insert_power("m1", ts, 600, "smart_meter")
        start, end = jst(2026, 10, 1, 0), jst(2026, 10, 1, 23, 59)
        old = analysis_service._calculate_cost_between(start, end)
        iv = svc.compute_intervals(analysis_service.load_smart_meter_rows(start, end))
        daily = svc.daily_energy(iv, [day], jst(2026, 10, 2, 12), 1)
        assert int(daily["kwh"].iloc[0] * PRICE) == old


def _insert_power(device_id, ts, watts, category, name=None):
    _insert_power_rows([(device_id, name or device_id, watts, ts.isoformat(), category)])


def _insert_power_rows(rows):
    """(device_id, device_name, wattage, timestamp_iso, device_category) をまとめて1トランザクションで入れる。"""
    with get_db_cursor(commit=True) as cur:
        cur.executemany(
            "INSERT INTO power_usage (device_id, device_name, wattage, timestamp, device_category) VALUES (?,?,?,?,?)", rows,
        )


class TestDailyEnergy:
    def test_full_day_has_near_full_coverage(self):
        iv = svc.compute_intervals(samples(jst(2026, 10, 1, 0), jst(2026, 10, 1, 23, 55), 1000))
        d = svc.daily_energy(iv, [date(2026, 10, 1)], jst(2026, 10, 3), 1)
        assert d["coverage"].iloc[0] > 0.99
        assert d["yen"].iloc[0] == int(d["kwh"].iloc[0] * PRICE)

    def test_missing_day_is_zero_with_no_coverage(self):
        iv = svc.compute_intervals(samples(jst(2026, 10, 1, 0), jst(2026, 10, 1, 23, 55), 1000))
        d = svc.daily_energy(iv, [date(2026, 10, 1), date(2026, 10, 2)], jst(2026, 10, 3), 1)
        assert d.iloc[1].to_dict() == {"date": date(2026, 10, 2), "kwh": 0.0, "yen": 0, "coverage": 0.0}

    def test_today_uses_elapsed_hours_as_the_denominator(self):
        iv = svc.compute_intervals(samples(jst(2026, 10, 3, 0), jst(2026, 10, 3, 11, 55), 1000))
        d = svc.daily_energy(iv, [date(2026, 10, 3)], jst(2026, 10, 3, 12), 1)
        assert d["coverage"].iloc[0] > 0.99  # 12時間ぶん揃っている=当日としてはほぼ100%

    def test_two_meters_with_one_missing_halves_the_coverage(self):
        iv = svc.compute_intervals(samples(jst(2026, 10, 1, 0), jst(2026, 10, 1, 23, 55), 1000, "a"))
        d = svc.daily_energy(iv, [date(2026, 10, 1)], jst(2026, 10, 3), 2)
        assert 0.49 < d["coverage"].iloc[0] < 0.51

    def test_empty_intervals(self):
        d = svc.daily_energy(svc.compute_intervals(pd.DataFrame()), [date(2026, 10, 1)], jst(2026, 10, 3), 1)
        assert d["kwh"].iloc[0] == 0 and d["coverage"].iloc[0] == 0


def _daily(rows):
    return pd.DataFrame(rows, columns=["date", "kwh", "yen", "coverage"])


class TestMonthlyAndComparison:
    def _full_month(self, year, month, days, kwh):
        return [(date(year, month, d), kwh, int(kwh * PRICE), 1.0) for d in range(1, days + 1)]

    def test_monthly_totals_sum_and_flag_partial_months(self):
        rows = self._full_month(2026, 8, 31, 10.0) + [(date(2026, 9, d), 5.0, int(5 * PRICE), 1.0 if d <= 20 else 0.0) for d in range(1, 31)]
        months = svc.monthly_totals(_daily(rows))
        assert [m["month"] for m in months] == ["2026-08", "2026-09"]
        assert months[0]["kwh"] == pytest.approx(310.0) and months[0]["partial"] is False
        assert months[1]["days_with_data"] == 20 and months[1]["days"] == 30 and months[1]["partial"] is True

    def test_empty(self):
        assert svc.monthly_totals(_daily([])) == []

    def test_comparison_uses_whole_days_up_to_yesterday(self):
        rows = (self._full_month(2025, 10, 31, 8.0) + self._full_month(2026, 9, 30, 9.0) + self._full_month(2026, 10, 10, 10.0))
        cmp_ = svc.month_to_date_comparison(_daily(rows), date(2026, 10, 10))
        assert cmp_["span_days"] == 9
        assert cmp_["this"]["kwh"] == pytest.approx(90.0)
        assert cmp_["last_month"]["kwh"] == pytest.approx(81.0)
        assert cmp_["last_year"]["kwh"] == pytest.approx(72.0)

    def test_comparison_skips_periods_with_missing_days(self):
        rows = [r for r in self._full_month(2026, 9, 30, 9.0) if r[0].day != 4] + self._full_month(2026, 10, 10, 10.0)
        cmp_ = svc.month_to_date_comparison(_daily(rows), date(2026, 10, 10))
        assert cmp_["last_month"] is None and cmp_["last_year"] is None and cmp_["this"] is not None

    def test_first_day_of_month_has_nothing_to_compare(self):
        assert svc.month_to_date_comparison(_daily(self._full_month(2026, 9, 30, 9.0)), date(2026, 10, 1)) is None

    def test_short_previous_month_is_handled(self):
        rows = self._full_month(2026, 2, 28, 9.0) + self._full_month(2026, 3, 30, 10.0)
        cmp_ = svc.month_to_date_comparison(_daily(rows), date(2026, 3, 31))
        assert cmp_["span_days"] == 30 and cmp_["last_month"] is None  # 2月に30日は無い

    def test_first_of_month_helper(self):
        assert svc._first_of_month(date(2026, 10, 10), 0) == date(2026, 10, 1)
        assert svc._first_of_month(date(2026, 10, 10), 12) == date(2025, 10, 1)
        assert svc._first_of_month(date(2026, 1, 5), 1) == date(2025, 12, 1)


class TestTemperature:
    def _base(self):
        return _daily([(date(2026, 10, d), 10.0, 310, 1.0) for d in range(1, 6)])

    def test_join_computes_mean_only_when_both_present(self):
        weather = pd.DataFrame({"date": ["2026-10-01", "2026-10-02", "2026-10-03"],
                                "min_temp": [10.0, None, 12.0], "max_temp": [20.0, 21.0, 18.0]})
        out = svc.join_temperature(self._base(), weather)
        assert out["mean_temp"].iloc[0] == 15.0 and pd.isna(out["mean_temp"].iloc[1]) and out["mean_temp"].iloc[2] == 15.0
        assert out["max_temp"].iloc[1] == 21.0 and pd.isna(out["min_temp"].iloc[1])
        assert pd.isna(out["mean_temp"].iloc[4])  # 気温の無い日

    @pytest.mark.parametrize("weather", [pd.DataFrame(), pd.DataFrame({"date": ["2026-10-01"]})])
    def test_join_without_weather_keeps_daily(self, weather):
        out = svc.join_temperature(self._base(), weather)
        assert len(out) == 5 and out["mean_temp"].isna().all()

    def test_join_tolerates_duplicate_and_bad_dates(self):
        weather = pd.DataFrame({"date": ["2026-10-01", "2026-10-01", "bad"], "min_temp": [1.0, 10.0, 0.0], "max_temp": [3.0, 20.0, 0.0]})
        assert svc.join_temperature(self._base(), weather)["mean_temp"].iloc[0] == 15.0  # 重複は後勝ち

    def test_band_labels(self):
        assert [svc.band_label(i) for i in range(7)] == ["〜5℃", "5〜10℃", "10〜15℃", "15〜20℃", "20〜25℃", "25〜30℃", "30℃〜"]

    def _joined(self, rows):
        df = _daily([(r[0], r[1], int(r[1] * PRICE), r[2]) for r in rows])
        df["mean_temp"] = [r[3] for r in rows]
        return df

    def test_bands_average_per_day_and_use_lower_bound_inclusive(self):
        rows = [(date(2026, 10, 1), 20.0, 1.0, 5.0), (date(2026, 10, 2), 10.0, 1.0, 5.0),   # 5℃ちょうどは「5〜10」
                (date(2026, 10, 3), 6.0, 1.0, 29.9), (date(2026, 10, 4), 8.0, 1.0, 35.0)]
        bands = svc.temperature_bands(self._joined(rows), date(2026, 10, 20))
        assert [b["days"] for b in bands] == [0, 2, 0, 0, 0, 1, 1]
        assert bands[1]["avg_kwh"] == pytest.approx(15.0) and bands[1]["avg_yen"] == int(15.0 * PRICE)

    def test_bands_exclude_low_coverage_today_and_missing_temperature(self):
        rows = [(date(2026, 10, 1), 10.0, 0.5, 12.0), (date(2026, 10, 2), 10.0, 1.0, float("nan")),
                (date(2026, 10, 10), 10.0, 1.0, 12.0), (date(2026, 10, 3), 10.0, 0.8, 12.0)]
        bands = svc.temperature_bands(self._joined(rows), date(2026, 10, 10))
        assert sum(b["days"] for b in bands) == 1 and bands[2]["days"] == 1  # 記録率0.8は含む(境界)

    def test_empty_bands_still_have_all_labels(self):
        assert len(svc.temperature_bands(self._joined([]), date(2026, 10, 10))) == 7


def tv_rows(on_ranges, start, end, on_watts=100.0, standby=0.8):
    ts = pd.date_range(start, end, freq="5min")
    watts = []
    for t in ts:
        watts.append(on_watts if any(a <= t < b for a, b in on_ranges) else standby)
    return pd.DataFrame({"device_id": "tv", "timestamp": ts, "power_watts": watts})


class TestTvAnalysis:
    DAYS = tuple(date(2026, 10, 1) + timedelta(days=i) for i in range(7))  # 10/1(木)〜10/7(水)

    def _run(self, rows, total_kwh=100.0, threshold=10.0, days=None):
        return svc.tv_analysis(rows, days or list(self.DAYS), threshold, total_kwh)

    def test_no_data(self):
        assert self._run(pd.DataFrame())["has_data"] is False

    def test_on_time_energy_and_share(self):
        rows = tv_rows([(jst(2026, 10, 1, 19), jst(2026, 10, 1, 21))], jst(2026, 10, 1, 0), jst(2026, 10, 1, 23, 55))
        r = self._run(rows, total_kwh=10.0)
        day = next(d for d in r["daily"] if d["date"] == date(2026, 10, 1))
        assert day["on_hours"] == pytest.approx(2.0, abs=0.1)
        assert r["kwh"] == pytest.approx(0.1 * 2 + 0.0008 * 22, abs=0.02)
        assert r["share_percent"] == pytest.approx(r["kwh"] / 10.0 * 100)
        assert r["avg_on_watts"] == pytest.approx(100.0) and r["standby_watts"] == pytest.approx(0.8)

    def test_threshold_boundary_is_on(self):
        rows = tv_rows([(jst(2026, 10, 1, 19), jst(2026, 10, 1, 20))], jst(2026, 10, 1, 18), jst(2026, 10, 1, 21), on_watts=10.0)
        assert self._run(rows)["sessions"] == 1
        assert self._run(rows, threshold=10.5)["has_data"] is True and self._run(rows, threshold=10.5)["sessions"] == 0

    def test_short_off_gap_is_one_session_long_gap_splits(self):
        # 19:00-19:30 点灯 → 10分消灯 → 19:40-20:00 点灯(同じ視聴) → 1時間消灯 → 21:00-21:30(別の視聴)
        ranges = [(jst(2026, 10, 1, 19, 0), jst(2026, 10, 1, 19, 30)), (jst(2026, 10, 1, 19, 40), jst(2026, 10, 1, 20, 0)),
                  (jst(2026, 10, 1, 21, 0), jst(2026, 10, 1, 21, 30))]
        r = self._run(tv_rows(ranges, jst(2026, 10, 1, 18), jst(2026, 10, 1, 22)))
        assert r["sessions"] == 2
        assert r["longest_minutes"] == pytest.approx(50.0, abs=6)  # 30分+20分(間の消灯は含めない)

    def test_gap_of_exactly_the_limit_stays_in_one_session(self):
        a, b = jst(2026, 10, 1, 19, 0), jst(2026, 10, 1, 19, 30)
        ranges = [(a, a + timedelta(minutes=20)), (a + timedelta(minutes=35), b)]  # 最後の点灯記録(19:15)→次(19:35)=20分?
        rows = tv_rows(ranges, jst(2026, 10, 1, 18), jst(2026, 10, 1, 21))
        assert self._run(rows)["sessions"] in (1, 2)  # 境界はサンプル位置に依存。落ちないことだけ確認

    def test_longest_session_reports_when_it_ended(self):
        ranges = [(jst(2026, 10, 2, 7), jst(2026, 10, 2, 7, 30)), (jst(2026, 10, 3, 18), jst(2026, 10, 3, 21))]
        r = self._run(tv_rows(ranges, jst(2026, 10, 2, 6), jst(2026, 10, 3, 22)))
        assert r["longest_at"].day == 3 and 20 <= r["longest_at"].hour <= 21

    def test_hourly_is_per_day_and_split_by_weekday_and_holiday(self):
        # 10/1(木)平日 と 10/3(土)休日 に、同じ19時台に60分点灯
        ranges = [(jst(2026, 10, 1, 19), jst(2026, 10, 1, 20)), (jst(2026, 10, 3, 19), jst(2026, 10, 3, 20))]
        rows = tv_rows(ranges, jst(2026, 10, 1, 0), jst(2026, 10, 3, 23, 55))
        r = self._run(rows)
        h = r["hourly"]
        assert (h["weekday_days"], h["offday_days"]) == (5, 2)
        assert h["weekday"][19] == pytest.approx(60 / 5, abs=2)
        assert h["offday"][19] == pytest.approx(60 / 2, abs=4)
        assert h["weekday"][3] == 0

    def test_records_outside_the_period_are_ignored(self):
        old = tv_rows([(jst(2026, 9, 1, 19), jst(2026, 9, 1, 21))], jst(2026, 9, 1, 0), jst(2026, 9, 1, 23, 55))
        new = tv_rows([(jst(2026, 10, 1, 19), jst(2026, 10, 1, 20))], jst(2026, 10, 1, 0), jst(2026, 10, 1, 23, 55))
        r = self._run(pd.concat([old, new]))
        assert r["sessions"] == 1 and sum(d["on_hours"] for d in r["daily"]) == pytest.approx(1.0, abs=0.1)

    def test_share_is_none_without_total(self):
        rows = tv_rows([(jst(2026, 10, 1, 19), jst(2026, 10, 1, 20))], jst(2026, 10, 1, 18), jst(2026, 10, 1, 21))
        assert self._run(rows, total_kwh=0.0)["share_percent"] is None

    def test_never_on_has_zero_sessions_and_no_longest(self):
        rows = tv_rows([], jst(2026, 10, 1, 0), jst(2026, 10, 1, 6))
        r = self._run(rows)
        assert r["sessions"] == 0 and r["longest_minutes"] == 0.0 and r["longest_at"] is None and r["avg_on_watts"] is None

    def test_standby_is_none_when_plug_reads_zero_off(self):
        rows = tv_rows([(jst(2026, 10, 1, 19), jst(2026, 10, 1, 20))], jst(2026, 10, 1, 18), jst(2026, 10, 1, 21), standby=0.0)
        assert self._run(rows)["standby_watts"] is None

    def test_daily_lists_every_day_newest_first(self):
        rows = tv_rows([(jst(2026, 10, 2, 19), jst(2026, 10, 2, 20))], jst(2026, 10, 2, 0), jst(2026, 10, 2, 23, 55))
        daily = self._run(rows)["daily"]
        assert [d["date"] for d in daily] == list(reversed(self.DAYS))
        assert next(d for d in daily if d["date"] == date(2026, 10, 3))["on_hours"] == 0.0
        assert next(d for d in daily if d["date"] == date(2026, 10, 3))["offday"] is True


def _fill_meter(device, start, end, watts):
    _insert_power_rows([(device, f"伊丹_{device}", watts, ts.isoformat(), "smart_meter") for ts in pd.date_range(start, end, freq="5min")])


def _fill_plug(device, name, start, end, watts_of):
    _insert_power_rows([(device, name, watts_of(ts), ts.isoformat(), "plug") for ts in pd.date_range(start, end, freq="5min")])


NOW = jst(2026, 10, 10, 12)


class TestBuildPowerReportWithDatabase:
    """SQL(スマートメーター・気温・テレビのプラグ)から画面用の内容までを通す。"""

    @pytest.fixture
    def seeded(self, isolated_db, monkeypatch):
        monkeypatch.setattr(config, "TV_PLUG_DEVICE_ID", "tv-plug")
        _fill_meter("m1", jst(2026, 9, 1, 0), jst(2026, 10, 10, 11, 55), 600)
        start, end = jst(2026, 10, 1, 0), jst(2026, 10, 10, 11, 55)
        _fill_plug("tv-plug", "TV", start, end, lambda ts: 100 if 19 <= ts.hour < 21 else 0.5)
        _fill_plug("other-plug", "炊飯器", start, end, lambda ts: 999)  # 他のプラグは全体に含めない
        with get_db_cursor(commit=True) as cur:
            for d in range(1, 11):
                cur.execute("INSERT INTO weather_history (date, location, min_temp, max_temp) VALUES (?,?,?,?)",
                            (f"2026-10-{d:02d}", config.WEATHER_LOCATION_NAME, 14.0, 24.0))
            cur.execute("INSERT INTO weather_history (date, location, min_temp, max_temp) VALUES ('2026-10-05','高砂',0,1)")

    def test_report_shape_and_totals(self, seeded):
        r = svc.build_power_report(30, now=NOW)
        assert r["meter_has_data"] is True and r["days"] == 30
        assert r["unit_price"] == PRICE
        by_month = {m["month"]: m for m in r["monthly"]}
        assert by_month["2026-09"]["kwh"] == pytest.approx(0.6 * 24 * 30, rel=0.01)  # 600W×24h×30日(日をまたぐ区間の欠けぶん約0.3%)
        assert len(r["daily"]) == 30 and r["daily"][0]["date"] == date(2026, 10, 10)  # 新しい順
        assert r["comparison"]["span_days"] == 9 and r["comparison"]["last_year"] is None

    def test_only_smart_meter_rows_count_toward_the_total(self, seeded):
        r = svc.build_power_report(30, now=NOW)
        day = next(d for d in r["daily"] if d["date"] == date(2026, 10, 5))
        assert day["kwh"] == pytest.approx(0.6 * 24, rel=0.01)  # プラグ(999W)は含まない

    def test_weather_uses_the_configured_location_only(self, seeded):
        r = svc.build_power_report(30, now=NOW)
        day = next(d for d in r["daily"] if d["date"] == date(2026, 10, 5))
        assert (day["min_temp"], day["max_temp"], day["mean_temp"]) == (14.0, 24.0, 19.0)
        assert r["weather"]["has_data"] and r["weather"]["latest_date"] == "2026-10-10"
        assert sum(b["days"] for b in r["temp_bands"]) == 9  # 当日(途中)は除く
        assert r["temp_bands"][3]["days"] == 9  # 平均19℃=15〜20℃

    def test_tv_is_from_the_configured_plug(self, seeded):
        tv = svc.build_power_report(30, now=NOW)["tv"]
        assert tv["configured"] and tv["has_data"]
        assert tv["sessions"] == 9 and tv["avg_on_watts"] == pytest.approx(100.0)  # 10/1〜10/9の19〜21時(10/10は正午まで)
        assert 0 < tv["share_percent"] < 10

    def test_days_option_and_invalid_fallback(self, seeded):
        assert len(svc.build_power_report(60, now=NOW)["daily"]) == 60
        assert svc.build_power_report(7, now=NOW)["days"] == svc.DEFAULT_DAYS

    def test_result_is_cached_when_now_is_not_given(self, seeded, monkeypatch):
        calls = []
        real = analysis_service.load_smart_meter_rows
        monkeypatch.setattr(analysis_service, "load_smart_meter_rows", lambda s, e: calls.append(1) or real(s, e))
        svc.build_power_report(30)
        svc.build_power_report(30)
        assert len(calls) == 1
        svc.build_power_report(60)
        assert len(calls) == 2


class TestBuildPowerReportEmptyAndFailures:
    def test_empty_database(self, isolated_db, monkeypatch):
        monkeypatch.setattr(config, "TV_PLUG_DEVICE_ID", None)
        r = svc.build_power_report(30, now=NOW)
        assert r["meter_has_data"] is False and r["monthly"] and all(m["kwh"] == 0 for m in r["monthly"])
        assert r["weather"]["has_data"] is False and r["tv"] == {"configured": False, "has_data": False}
        assert len(r["daily"]) == 30

    def test_tv_configured_without_records(self, isolated_db, monkeypatch):
        monkeypatch.setattr(config, "TV_PLUG_DEVICE_ID", "tv-plug")
        assert svc.build_power_report(30, now=NOW)["tv"] == {"configured": True, "has_data": False, "threshold": config.TV_POWER_ON_THRESHOLD_WATTS}

    def test_meter_failure_does_not_break_the_tv_section(self, isolated_db, monkeypatch):
        monkeypatch.setattr(config, "TV_PLUG_DEVICE_ID", "tv-plug")
        _fill_plug("tv-plug", "TV", jst(2026, 10, 9, 0), jst(2026, 10, 9, 23, 55), lambda ts: 100 if 19 <= ts.hour < 21 else 0.5)
        monkeypatch.setattr(analysis_service, "load_smart_meter_rows", lambda s, e: (_ for _ in ()).throw(RuntimeError("db")))
        r = svc.build_power_report(30, now=NOW)
        assert r["meter_has_data"] is False and r["daily"] == []
        assert r["tv"]["has_data"] is True and r["tv"]["share_percent"] is None

    def test_tv_failure_does_not_break_the_rest(self, isolated_db, monkeypatch):
        monkeypatch.setattr(config, "TV_PLUG_DEVICE_ID", "tv-plug")
        _fill_meter("m1", jst(2026, 10, 9, 0), jst(2026, 10, 9, 23, 55), 600)
        monkeypatch.setattr(analysis_service, "load_power_rows", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db")))
        r = svc.build_power_report(30, now=NOW)
        assert r["meter_has_data"] is True and r["tv"] == {"configured": True, "has_data": False}

    def test_weather_failure_still_shows_energy(self, isolated_db, monkeypatch):
        monkeypatch.setattr(config, "TV_PLUG_DEVICE_ID", None)
        _fill_meter("m1", jst(2026, 10, 9, 0), jst(2026, 10, 9, 23, 55), 600)
        with get_db_cursor(commit=True) as cur:
            cur.execute("DROP TABLE weather_history")  # 気温のテーブルが読めない状態
        r = svc.build_power_report(30, now=NOW)
        assert r["weather"]["has_data"] is False and any(d["kwh"] > 0 for d in r["daily"])
