# MY_HOME_SYSTEM/tests/test_weekly_analyze_report.py
"""
weekly_analyze_report.py (週次/月次/年次のDB集計・LINE/Discordレポート送信)のテスト。
"""
import datetime
import os
import sys
from unittest.mock import MagicMock

import pytz
from freezegun import freeze_time

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import common
import config
import weekly_analyze_report as report


JST = pytz.timezone("Asia/Tokyo")


class TestGetStartDate:
    def test_month_returns_first_day_of_current_month(self):
        # UTC 2026-08-15 01:00 = JST 2026-08-15 10:00 (日付境界を避けるため日中の時刻を使う)
        with freeze_time("2026-08-15 01:00:00"):
            result = report.get_start_date("month")
        assert result.day == 1
        assert result.month == 8

    def test_year_returns_january_first(self):
        with freeze_time("2026-08-15 01:00:00"):
            result = report.get_start_date("year")
        assert (result.month, result.day) == (1, 1)

    def test_unknown_period_type_returns_none(self):
        assert report.get_start_date("decade") is None

    def test_week_on_monday_goes_back_a_full_week(self):
        """月曜実行時は『先週の月曜』を取得する(7日戻る)仕様"""
        with freeze_time("2026-08-16 23:00:00"):  # UTC 23:00 = JST 2026-08-17 08:00 (月曜)
            result = report.get_start_date("week")
        assert result.strftime("%Y-%m-%d") == "2026-08-10"

    def test_week_on_wednesday_goes_back_to_this_weeks_monday(self):
        with freeze_time("2026-08-18 23:00:00"):  # UTC 23:00 = JST 2026-08-19 08:00 (水曜)
            result = report.get_start_date("week")
        assert result.strftime("%Y-%m-%d") == "2026-08-17"


class TestIsMonthEndReport:
    def test_true_when_next_week_crosses_into_new_month(self):
        with freeze_time("2026-08-27 23:00:00"):  # JST 2026-08-28
            assert report.is_month_end_report() is True

    def test_false_when_mid_month(self):
        with freeze_time("2026-08-09 23:00:00"):  # JST 2026-08-10
            assert report.is_month_end_report() is False


class TestGetAnalysisData:
    def test_aggregates_correctly_when_all_tables_present(self, isolated_db):
        start = datetime.datetime.now(JST) - datetime.timedelta(days=7)

        with common.get_db_cursor(commit=True) as cur:
            cur.execute(
                f"INSERT INTO {config.SQLITE_TABLE_FOOD} (menu_category, timestamp) VALUES "
                "('自炊: カレー', datetime('now')), ('外食: マック', datetime('now')), "
                "('自炊: うどん', datetime('now'))"
            )
            cur.execute("INSERT INTO car_records (action, timestamp) VALUES ('LEAVE', datetime('now'))")
            cur.execute(
                f"INSERT INTO {config.SQLITE_TABLE_POWER_USAGE} (device_id, wattage, timestamp) VALUES "
                "('d1', 500, datetime('now'))"
            )
            cur.execute(
                f"INSERT INTO {config.SQLITE_TABLE_CHILD} (child_name, condition, timestamp) VALUES "
                "('daughter', '発熱', datetime('now'))"
            )

        result = report.get_analysis_data(start)

        assert result is not None
        assert result["total_meals"] == 3
        assert result["food_counts"]["自炊"] == 2
        assert result["food_counts"]["外食"] == 1
        assert result["car_count"] == 1
        assert result["sick_count"] == 1
        assert result["elec_bill"] >= 0


class TestGetAnalysisDataElectricityCostExcludesPlugs:
    """Issue #170の回帰テスト: get_analysis_dataの電気代計算(sql_power)が
    デバイス無差別にSELECT AVG(wattage)しており、プラグ(個別家電。既に
    スマートメーターの計測値に含まれる部分集合)のアイドル値がスマートメーターの
    平均値を希釈していた不具合。"""

    def test_plug_readings_do_not_affect_elec_bill(self, isolated_db):
        start = datetime.datetime.now(JST) - datetime.timedelta(days=7)

        with common.get_db_cursor(commit=True) as cur:
            cur.execute(
                f"INSERT INTO {config.SQLITE_TABLE_POWER_USAGE} (device_id, device_name, wattage, timestamp) VALUES "
                "('remo1', '伊丹_Nature Remo E Lite', 1000, datetime('now'))"
            )

        meter_only = report.get_analysis_data(start)

        with common.get_db_cursor(commit=True) as cur:
            # アイドル時の小さいwattage(1W)を持つプラグを大量に追加しても、
            # スマートメーター単独の場合と結果が変わらないべき
            for _ in range(5):
                cur.execute(
                    f"INSERT INTO {config.SQLITE_TABLE_POWER_USAGE} (device_id, device_name, wattage, timestamp) VALUES "
                    "('plug1', 'Plug_TV', 1, datetime('now'))"
                )

        with_plugs = report.get_analysis_data(start)

        assert meter_only is not None and with_plugs is not None
        assert with_plugs["elec_bill"] == meter_only["elec_bill"], (
            "プラグのアイドル値が電気代計算(AVG(wattage))を希釈している: "
            f"meter_only={meter_only['elec_bill']}, with_plugs={with_plugs['elec_bill']}"
        )


class TestGenerateTextSection:
    def test_returns_empty_string_when_no_data(self):
        assert report.generate_text_section("test", None) == ""

    def test_simple_mode_shows_cook_rate_and_bill(self):
        data = {"total_meals": 4, "food_counts": {"自炊": 2, "外食": 1, "その他": 1}, "elec_bill": 3000}
        text = report.generate_text_section("月次", data, is_simple=True)
        assert "50%" in text
        assert "3,000円" in text

    def test_detailed_mode_includes_all_sections(self):
        data = {
            "total_meals": 2, "food_counts": {"自炊": 1, "外食": 1, "その他": 0},
            "elec_bill": 1500, "car_count": 3, "sick_count": 0,
        }
        text = report.generate_text_section("先週のまとめ", data)
        assert "自炊率: 50%" in text
        assert "3回" in text
        assert "みんな元気でした" in text

    def test_zero_car_count_shows_none_message(self):
        data = {
            "total_meals": 1, "food_counts": {"自炊": 1, "外食": 0, "その他": 0},
            "elec_bill": 0, "car_count": 0, "sick_count": 2,
        }
        text = report.generate_text_section("test", data)
        assert "車利用: なし" in text
        assert "不調が2回" in text

    def test_zero_total_meals_does_not_divide_by_zero(self):
        data = {"total_meals": 0, "food_counts": {"自炊": 0, "外食": 0, "その他": 0}, "elec_bill": 0}
        text = report.generate_text_section("test", data, is_simple=True)
        assert "0%" in text


class TestRunReport:
    def test_skips_when_not_monday_morning_and_not_forced(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["weekly_analyze_report.py"])
        mock_send = MagicMock()
        monkeypatch.setattr(report.common, "send_push", mock_send)

        with freeze_time("2026-08-19 08:00:00", tz_offset=9):  # 水曜日
            report.run_report()

        mock_send.assert_not_called()

    def test_runs_when_forced_even_if_not_monday(self, isolated_db, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["weekly_analyze_report.py", "--force"])
        mock_send = MagicMock(return_value=True)
        monkeypatch.setattr(report.common, "send_push", mock_send)

        with freeze_time("2026-08-19 08:00:00", tz_offset=9):
            report.run_report()

        mock_send.assert_called_once()
        sent_text = mock_send.call_args[0][1][0]["text"]
        assert "今週の我が家レポート" in sent_text


class TestRunReportDuplicateSendPrevention:
    """Issue #234の回帰テスト: 月曜8時台の判定のみで送信済みフラグが無かったため、
    外部cronが同一時間枠内で本スクリプトを複数回起動すると重複送信されていた不具合。"""

    def test_second_invocation_in_same_monday_morning_is_skipped(self, isolated_db, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "argv", ["weekly_analyze_report.py"])
        monkeypatch.setattr(report, "LAST_RUN_FILE", str(tmp_path / "last_weekly_report.txt"))
        mock_send = MagicMock(return_value=True)
        monkeypatch.setattr(report.common, "send_push", mock_send)

        with freeze_time("2026-08-16 23:00:00"):  # JST 2026-08-17 08:00 (月曜)
            report.run_report()  # 1回目: 外部cronによる正規の起動
            report.run_report()  # 2回目: 同一時間枠内での多重起動(重複)

        mock_send.assert_called_once(), "同一時間枠内の多重起動で重複送信された(実行済みフラグが機能していない)"

    def test_next_mondays_run_is_not_blocked_by_previous_weeks_flag(
        self, isolated_db, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(sys, "argv", ["weekly_analyze_report.py"])
        monkeypatch.setattr(report, "LAST_RUN_FILE", str(tmp_path / "last_weekly_report.txt"))
        mock_send = MagicMock(return_value=True)
        monkeypatch.setattr(report.common, "send_push", mock_send)

        with freeze_time("2026-08-16 23:00:00"):  # 2026-08-17(月)
            report.run_report()
        with freeze_time("2026-08-23 23:00:00"):  # 翌週2026-08-24(月)
            report.run_report()

        assert mock_send.call_count == 2, "前週の実行済みフラグにより翌週の正規実行までブロックされてはならない"

    def test_send_failure_does_not_write_flag_so_retry_can_still_send(
        self, isolated_db, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(sys, "argv", ["weekly_analyze_report.py"])
        monkeypatch.setattr(report, "LAST_RUN_FILE", str(tmp_path / "last_weekly_report.txt"))
        mock_send = MagicMock(side_effect=[False, True])
        monkeypatch.setattr(report.common, "send_push", mock_send)

        with freeze_time("2026-08-16 23:00:00"):
            report.run_report()  # 1回目: 送信失敗
            report.run_report()  # 2回目: 再試行(同一時間枠内でも成功させたい)

        assert mock_send.call_count == 2, "送信失敗時にもフラグを書いてしまうと再試行が永久にブロックされる"

    def test_forced_run_bypasses_flag_and_does_not_write_it(
        self, isolated_db, monkeypatch, tmp_path
    ):
        flag_file = tmp_path / "last_weekly_report.txt"
        monkeypatch.setattr(report, "LAST_RUN_FILE", str(flag_file))
        mock_send = MagicMock(return_value=True)
        monkeypatch.setattr(report.common, "send_push", mock_send)

        with freeze_time("2026-08-19 08:00:00", tz_offset=9):  # 水曜日、--force
            monkeypatch.setattr(sys, "argv", ["weekly_analyze_report.py", "--force"])
            report.run_report()
            report.run_report()

        assert mock_send.call_count == 2, "--force は手動テスト用途のためフラグの影響を受けてはならない"
        assert not flag_file.exists(), "--force 実行時はフラグを記録してはならない(通常実行をブロックしないため)"
