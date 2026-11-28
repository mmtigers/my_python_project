# MY_HOME_SYSTEM/tests/test_recording_gaps_and_db_size.py
"""
Issue #1169(録画の欠けの記録)・Issue #1170(DBサイズの前週比)を週次レポートに出す処理のテスト。
"""
import datetime
import os
import sys
from zoneinfo import ZoneInfo

import pytest
from freezegun import freeze_time

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from services import backup_service, recording_status_service as rs
import weekly_analyze_report as report

JST = ZoneInfo("Asia/Tokyo")


def _t(s: str) -> datetime.datetime:
    return datetime.datetime.strptime(s, "%Y%m%d_%H%M%S").replace(tzinfo=JST)


class TestFindRecordingGaps:
    def test_regular_600s_segments_have_no_gap(self):
        times = [_t("20261125_060000") + datetime.timedelta(seconds=600 * i) for i in range(12)]
        assert rs.find_recording_gaps(times) == []

    def test_three_minute_hole_is_one_gap_of_three_minutes(self):
        times = [_t("20261125_064000"), _t("20261125_065000"), _t("20261125_070300"), _t("20261125_071300")]
        assert rs.find_recording_gaps(times) == [(_t("20261125_070000"), 180)]

    def test_short_restart_segments_are_not_gaps(self):
        # ffmpeg の再起動で40秒・10秒の短いセグメントが挟まっても、間隔が縮むだけで欠けではない
        times = [_t("20261125_064000"), _t("20261125_064040"), _t("20261125_064050"), _t("20261125_065050")]
        assert rs.find_recording_gaps(times) == []

    def test_small_jitter_is_tolerated(self):
        times = [_t("20261125_064000"), _t("20261125_065045")]  # 645秒
        assert rs.find_recording_gaps(times) == []


class TestCameraRecordingGaps:
    def _setup(self, tmp_path, monkeypatch, names):
        monkeypatch.setattr(config, "NVR_RECORD_DIR", str(tmp_path))
        monkeypatch.setattr(config, "CAMERAS", [
            {"id": "p", "name": "駐車場", "nas_folder": "parking"},
            {"id": "x", "name": "旧カメラ", "enabled": False},
        ])
        monkeypatch.setattr(rs.os.path, "ismount", lambda p: True)
        (tmp_path / "parking").mkdir()
        for n in names:
            (tmp_path / "parking" / n).write_bytes(b"")

    def test_counts_gaps_in_window_including_across_midnight(self, tmp_path, monkeypatch):
        names = ["20261124_235000.mp4", "20261125_000000.mp4", "20261125_001300.mp4",  # 0:10〜 3分
                 "20261125_002300.mp4", "20261125_003800.mp4", "20261125_004800.mp4"]  # 0:33〜 5分
        self._setup(tmp_path, monkeypatch, names)
        result = rs.camera_recording_gaps(_t("20261125_000000"), _t("20261125_010000"))
        assert result == [{"name": "駐車場", "count": 2, "total_sec": 480}]

    def test_previous_segment_before_window_counts_leading_gap(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch, ["20261124_235000.mp4", "20261125_000500.mp4"])
        result = rs.camera_recording_gaps(_t("20261125_000000"), _t("20261125_010000"))
        assert result[0]["count"] == 1 and result[0]["total_sec"] == 300

    def test_none_when_nas_not_mounted(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch, [])
        monkeypatch.setattr(rs.os.path, "ismount", lambda p: False)
        assert rs.camera_recording_gaps(_t("20261125_000000"), _t("20261125_010000")) is None


class TestBackupSizeTrend:
    @pytest.fixture
    def backup_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "NAS_PROJECT_ROOT", str(tmp_path), raising=False)
        d = tmp_path / "db_backups"
        d.mkdir()
        return d

    @staticmethod
    def _backup(d, day, mb):
        p = d / f"home_system_202611{day:02d}_040000.db"
        p.write_bytes(b"\0" * int(mb * 1024 * 1024))
        ts = _t(f"202611{day:02d}_040012").timestamp()
        os.utime(p, (ts, ts))
        (d / f"devices_202611{day:02d}_040000.json").write_text("{}")  # 種類が違うファイルは無視

    def test_week_over_week_diff(self, backup_dir):
        for day, mb in [(17, 1.0), (18, 1.2), (24, 2.5), (25, 3.0)]:
            self._backup(backup_dir, day, mb)
        trend = backup_service.get_backup_size_trend(days=7)
        assert trend == {"size_mb": 3.0, "diff_mb": 1.8}  # 11/25 と 11/18 を比べる

    def test_no_previous_week(self, backup_dir):
        self._backup(backup_dir, 25, 1.0)
        assert backup_service.get_backup_size_trend(days=7) == {"size_mb": 1.0, "diff_mb": None}

    def test_no_backups(self, backup_dir):
        assert backup_service.get_backup_size_trend() is None


class TestWeeklyReportSystemSection:
    def test_lines_for_gaps_and_db_size(self, monkeypatch):
        monkeypatch.setattr(rs, "camera_recording_gaps", lambda s, e: [
            {"name": "玄関", "count": 0, "total_sec": 0},
            {"name": "駐車場", "count": 2, "total_sec": 250},
        ])
        monkeypatch.setattr(backup_service, "get_backup_size_trend", lambda days=7: {"size_mb": 253.2, "diff_mb": 18.9})
        text = report.format_system_section(_t("20261117_000000"), _t("20261124_080000"))
        assert "🎥 録画の欠け: 玄関 なし / 駐車場 2件・計5分" in text
        assert "💾 DBサイズ: 253.2MB (前週比 +18.9MB)" in text

    def test_omitted_when_unavailable_or_failing(self, monkeypatch):
        monkeypatch.setattr(rs, "camera_recording_gaps", lambda s, e: None)

        def _boom(days=7):
            raise OSError("nas")

        monkeypatch.setattr(backup_service, "get_backup_size_trend", _boom)
        assert report.format_system_section(_t("20261117_000000"), _t("20261124_080000")) == ""

    @freeze_time("2026-11-24 08:05:00+09:00")
    def test_run_report_includes_system_section(self, isolated_db, monkeypatch):
        sent = []
        monkeypatch.setattr(report, "send_push", lambda msgs, **k: sent.append(msgs[0]["text"]) or True)
        monkeypatch.setattr(report, "format_system_section", lambda s, n: "🛠️ システム\n💾 DBサイズ: 1.0MB\n")
        monkeypatch.setattr(report.sys, "argv", ["weekly_analyze_report.py", "--force"])
        report.run_report()
        assert sent and "💾 DBサイズ: 1.0MB" in sent[0]
