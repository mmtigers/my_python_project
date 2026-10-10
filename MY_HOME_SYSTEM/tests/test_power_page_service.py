# MY_HOME_SYSTEM/tests/test_power_page_service.py
"""services/power_page_service.py(電気ページのHTML)のテスト。レポート(辞書)を直接渡して描画を検証する。"""
import os
import sys
from datetime import timedelta

import pandas as pd
import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from services import power_page_service as ps

NOW = pd.Timestamp(2026, 10, 10, 12, 0, tz="Asia/Tokyo").to_pydatetime()
NAN = float("nan")


def _monthly():
    return [{"month": f"2026-{m:02d}", "kwh": 300.0 + m, "yen": 9000 + m * 100, "days_with_data": 30, "days": 30, "partial": m == 10}
            for m in range(1, 11)]


def _daily(n=14):
    rows = []
    for i in range(n):
        d = NOW.date() - timedelta(days=i)
        rows.append({"date": d, "kwh": 9.0, "yen": 280, "coverage": 1.0 if i != 3 else 0.5,
                     "min_temp": 14.0 if i != 5 else NAN, "max_temp": 24.0, "mean_temp": 19.0})
    rows[1]["coverage"] = 0.0
    return rows


def _tv(**over):
    base = {
        "configured": True, "has_data": True, "threshold": 10.0, "sessions": 40, "longest_minutes": 185.0,
        "longest_at": NOW - timedelta(days=2), "avg_on_watts": 97.4, "standby_watts": 0.8, "kwh": 12.3, "yen": 381,
        "share_percent": 5.9,
        "daily": [{"date": NOW.date() - timedelta(days=i), "on_hours": 2.0, "kwh": 0.4, "yen": 12, "offday": i % 7 in (0, 6)} for i in range(30)],
        "hourly": {"weekday": [0.0] * 24, "offday": [0.0] * 24, "weekday_days": 21, "offday_days": 9},
    }
    base["hourly"]["weekday"][19] = 45.0
    base["hourly"]["offday"][10] = 30.0
    base.update(over)
    return base


def _report(**over):
    bands = [{"label": ps.analysis.band_label(i), "days": 0, "avg_kwh": 0.0, "avg_yen": 0} for i in range(7)]
    bands[3] = {"label": bands[3]["label"], "days": 12, "avg_kwh": 8.0, "avg_yen": 248}
    bands[4] = {"label": bands[4]["label"], "days": 2, "avg_kwh": 10.0, "avg_yen": 310}
    base = {
        "days": 30, "unit_price": 31.0, "generated_at": NOW, "meter_has_data": True, "monthly": _monthly(),
        "comparison": {"span_days": 9, "this": {"kwh": 86.9, "yen": 2694}, "last_month": {"kwh": 88.0, "yen": 2732}, "last_year": None},
        "daily": _daily(), "temp_bands": bands,
        "weather": {"has_data": True, "latest_date": "2026-10-10", "location": "伊丹"}, "tv": _tv(),
    }
    base.update(over)
    return base


def _render(report=None):
    return ps.render_power_page(report or _report(), dashboard_path="/dashboard/")


class TestSvgBars:
    def test_bar_heights_scale_to_the_peak(self):
        svg = ps.svg_bars([10.0, 5.0, 0.0], ["a", "b", "c"], titles=["A", "B", "C"], aria="t")
        heights = [float(h) for h in __import__("re").findall(r'<rect [^>]*height="([\d.]+)"', svg)]
        assert heights[0] == pytest.approx(heights[1] * 2) and heights[2] == 0

    def test_all_zero_values_do_not_crash(self):
        assert "<svg" in ps.svg_bars([0.0, 0.0], ["a", "b"], titles=["A", "B"], aria="t")

    def test_faded_bars_get_the_faded_class(self):
        svg = ps.svg_bars([1.0, 2.0], ["a", "b"], titles=["A", "B"], faded=[True, False], aria="t")
        assert svg.count("bar bar-faded") == 1

    def test_text_is_escaped(self):
        svg = ps.svg_bars([1.0], ["<b>"], titles=['"><script>'], aria='x"y', value_labels=["<i>"])
        assert "<script>" not in svg and "<b>" not in svg and "&lt;b&gt;" in svg

    def test_value_labels_are_omitted_for_many_bars(self):
        n = 20
        svg = ps.svg_bars([1.0] * n, [str(i) for i in range(n)], titles=["t"] * n, aria="t", value_labels=["v"] * n)
        assert ">v<" not in svg

    def test_grouped_bars_have_two_series_and_label_every_n(self):
        svg = ps.svg_grouped_bars([[1.0] * 24, [2.0] * 24], [str(h) for h in range(24)], label_every=3,
                                  titles=[["a"] * 24, ["b"] * 24], aria="t")
        assert svg.count("<rect") == 48 and svg.count("bar bar-alt") == 24
        assert svg.count("bar-label") == 8


class TestPage:
    def test_has_back_link_period_links_and_price_note(self):
        html = _render()
        assert "ホームへ戻る" in html and "⚡ 電気" in html
        assert "31円/kWh" in html and "概算" in html
        assert 'href="/dashboard/power?days=60"' in html and 'href="/dashboard/power?days=90"' in html
        assert '<span class="nas-period-current">30日</span>' in html and "13か月分" in html

    def test_monthly_section_summary_and_comparison(self):
        html = _render()
        assert "今月(昨日までの9日間)" in html and "¥2,694" in html and "86.9kWh" in html
        assert "先月の同じ日数: ¥2,732(-1%)" in html
        assert "去年の同じ月の同じ日数: 記録がそろっていないため比べられません" in html
        assert html.count('class="bar"') + html.count("bar bar-faded") >= 10

    def test_monthly_without_comparison_explains_when_it_appears(self):
        assert "月が始まって2日目から" in _render(_report(comparison=None))

    def test_monthly_without_meter_data(self):
        html = _render(_report(meter_has_data=False, monthly=[], comparison=None))
        assert "スマートメーターの記録がまだありません" in html

    def test_daily_table_marks_today_missing_and_low_coverage(self):
        html = _render()
        assert "10/10(土)・途中" in html
        assert "記録なし" in html and "⚠️ 記録 50%" in html
        assert "—" in html or "?〜24℃" in html  # 最低気温が欠けた日
        assert "さらに4日を表示" in html  # 14日中10日だけ最初から開く

    def test_daily_empty(self):
        assert "表示できるデータがありません" in _render(_report(daily=[]))

    def test_temperature_section_with_data(self):
        html = _render()
        assert "気温との関係" in html and "15〜20℃" in html and "12日" in html
        assert "2026-10-10" in html and "伊丹" in html

    def test_temperature_section_tells_how_to_backfill_when_empty(self):
        html = _render(_report(weather={"has_data": False, "latest_date": None, "location": "伊丹"}))
        assert "weather_monitor.py --backfill-days 400" in html

    def test_temperature_section_when_no_overlap(self):
        bands = [{"label": "x", "days": 0, "avg_kwh": 0.0, "avg_yen": 0}] * 7
        assert "そろった日がまだありません" in _render(_report(temp_bands=bands))

    def test_tv_section_stats_and_chart(self):
        html = _render()
        assert "テレビの使い方" in html
        assert "1日あたりの点灯時間(30日平均)" in html and "2.0時間" in html  # 2.0h×30日÷30日
        assert "¥381" in html and "5.9%" in html and "40回" in html
        assert "3時間5分" in html and "97W" in html and "0.8W" in html
        assert "平日(21日)" in html and "休日(9日)" in html and "bar bar-alt" in html
        assert "日別の数字を見る" in html and "・休" in html

    def test_tv_not_configured(self):
        assert "TV_PLUG_DEVICE_ID" in _render(_report(tv={"configured": False, "has_data": False}))

    def test_tv_without_records_points_to_devices_json(self):
        html = _render(_report(tv={"configured": True, "has_data": False, "threshold": 10.0}))
        assert "devices.json" in html

    def test_tv_optional_values_missing_do_not_crash(self):
        html = _render(_report(tv=_tv(share_percent=None, avg_on_watts=None, standby_watts=None, longest_at=None, longest_minutes=20.0)))
        assert "20分" in html and "家全体に占める割合" in html

    def test_page_has_no_horizontal_overflow_styles_and_title(self):
        html = _render()
        assert "<title>電気 - おうちの様子</title>" in html and "viewport" in html

    def test_diff_percent_helper(self):
        assert ps._diff_percent(110, 100) == "+10%" and ps._diff_percent(90, 100) == "-10%" and ps._diff_percent(5, 0) == ""
