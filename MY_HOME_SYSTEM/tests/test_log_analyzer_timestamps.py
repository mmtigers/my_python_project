# MY_HOME_SYSTEM/tests/test_log_analyzer_timestamps.py
"""monitors/log_analyzer.py の syslog 形式タイムスタンプ(年なし)の年補正テスト。

年明け直後に前年12月の syslog 行を読むと「現在年の12月」(=未来)として解釈され、
start_date のフィルタを素通りして必ずカウントされていた。"""
import datetime
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from monitors.log_analyzer import LogAnalyzer


def _analyzer_at(now: datetime.datetime) -> LogAnalyzer:
    analyzer = LogAnalyzer(days_back=7)
    analyzer.now = now
    analyzer.start_date = now - datetime.timedelta(days=7)
    return analyzer


def test_syslog_line_from_previous_december_is_dated_last_year():
    analyzer = _analyzer_at(datetime.datetime(2027, 1, 3, 10, 0, 0))
    dt = analyzer._parse_timestamp("Dec 30 23:59:00 raspi kernel: ERROR something")
    assert dt == datetime.datetime(2026, 12, 30, 23, 59, 0)
    # 7日以内なので集計対象に含まれる(以前は 2027-12-30 と解釈されフィルタを素通りしていた)
    assert dt >= analyzer.start_date


def test_syslog_line_from_same_year_keeps_current_year():
    analyzer = _analyzer_at(datetime.datetime(2026, 9, 6, 10, 0, 0))
    dt = analyzer._parse_timestamp("Sep  5 08:00:00 raspi systemd[1]: Started")
    assert dt == datetime.datetime(2026, 9, 5, 8, 0, 0)


def test_iso_timestamp_is_unaffected():
    analyzer = _analyzer_at(datetime.datetime(2027, 1, 3, 10, 0, 0))
    dt = analyzer._parse_timestamp("2026-12-30 23:59:00 [ERROR] x")
    assert dt == datetime.datetime(2026, 12, 30, 23, 59, 0)
