# MY_HOME_SYSTEM/tests/test_tv_lock_monitor.py
"""
monitors/tv_lock_monitor.py のテスト。

Issue #490: このスクリプトは scheduler_boot.py により5分間隔(1日288回)で
実行される高頻度ジョブだが、本番コードカバレッジが0%だった。
毎日深夜2:00〜2:05の時間帯にのみSwitchBotプラグをOFFにし、当日分の
重複実行をLAST_RUN_FILEで防止するロジックをカバーする。
"""
import os
import sys
from datetime import datetime
from unittest.mock import patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from monitors import tv_lock_monitor


class TestTvLockMonitor:
    def test_skips_when_device_id_not_configured(self, monkeypatch):
        monkeypatch.setattr(config, "TV_PLUG_DEVICE_ID", "", raising=False)

        with patch.object(tv_lock_monitor.switchbot_service, "send_device_command") as mock_cmd:
            tv_lock_monitor.main()

        mock_cmd.assert_not_called()

    def test_skips_outside_the_midnight_window(self, monkeypatch):
        monkeypatch.setattr(config, "TV_PLUG_DEVICE_ID", "tv-plug-1", raising=False)
        outside_window = datetime(2026, 9, 5, 2, 6, 0)

        class _FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return outside_window

        with patch.object(tv_lock_monitor, "datetime", _FixedDatetime), \
             patch.object(tv_lock_monitor.switchbot_service, "send_device_command") as mock_cmd:
            tv_lock_monitor.main()

        mock_cmd.assert_not_called()

    def test_turns_off_plug_and_records_run_within_window(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "TV_PLUG_DEVICE_ID", "tv-plug-1", raising=False)
        last_run_file = str(tmp_path / "last_tv_lock.txt")
        monkeypatch.setattr(tv_lock_monitor, "LAST_RUN_FILE", last_run_file)

        in_window = datetime(2026, 9, 5, 2, 3, 0)

        class _FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return in_window

        with patch.object(tv_lock_monitor, "datetime", _FixedDatetime), \
             patch.object(
                 tv_lock_monitor.switchbot_service,
                 "send_device_command",
                 return_value={"statusCode": 100},
             ) as mock_cmd:
            tv_lock_monitor.main()

        mock_cmd.assert_called_once_with("tv-plug-1", "turnOff")
        assert os.path.exists(last_run_file)
        with open(last_run_file, "r") as f:
            assert f.read().strip() == "2026-09-05"

    def test_does_not_run_twice_on_the_same_day(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "TV_PLUG_DEVICE_ID", "tv-plug-1", raising=False)
        last_run_file = str(tmp_path / "last_tv_lock.txt")
        monkeypatch.setattr(tv_lock_monitor, "LAST_RUN_FILE", last_run_file)
        with open(last_run_file, "w") as f:
            f.write("2026-09-05")

        in_window = datetime(2026, 9, 5, 2, 1, 0)

        class _FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return in_window

        with patch.object(tv_lock_monitor, "datetime", _FixedDatetime), \
             patch.object(tv_lock_monitor.switchbot_service, "send_device_command") as mock_cmd:
            tv_lock_monitor.main()

        mock_cmd.assert_not_called()

    def test_runs_again_the_next_day_after_a_previous_run(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "TV_PLUG_DEVICE_ID", "tv-plug-1", raising=False)
        last_run_file = str(tmp_path / "last_tv_lock.txt")
        monkeypatch.setattr(tv_lock_monitor, "LAST_RUN_FILE", last_run_file)
        with open(last_run_file, "w") as f:
            f.write("2026-09-04")

        in_window = datetime(2026, 9, 5, 2, 1, 0)

        class _FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return in_window

        with patch.object(tv_lock_monitor, "datetime", _FixedDatetime), \
             patch.object(
                 tv_lock_monitor.switchbot_service,
                 "send_device_command",
                 return_value={"statusCode": 100},
             ) as mock_cmd:
            tv_lock_monitor.main()

        mock_cmd.assert_called_once_with("tv-plug-1", "turnOff")
        with open(last_run_file, "r") as f:
            assert f.read().strip() == "2026-09-05"

    def test_does_not_record_run_when_api_reports_failure(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "TV_PLUG_DEVICE_ID", "tv-plug-1", raising=False)
        last_run_file = str(tmp_path / "last_tv_lock.txt")
        monkeypatch.setattr(tv_lock_monitor, "LAST_RUN_FILE", last_run_file)

        in_window = datetime(2026, 9, 5, 2, 1, 0)

        class _FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return in_window

        with patch.object(tv_lock_monitor, "datetime", _FixedDatetime), \
             patch.object(
                 tv_lock_monitor.switchbot_service,
                 "send_device_command",
                 return_value={"statusCode": 190, "message": "error"},
             ):
            tv_lock_monitor.main()

        assert not os.path.exists(last_run_file)

    def test_does_not_record_run_when_command_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "TV_PLUG_DEVICE_ID", "tv-plug-1", raising=False)
        last_run_file = str(tmp_path / "last_tv_lock.txt")
        monkeypatch.setattr(tv_lock_monitor, "LAST_RUN_FILE", last_run_file)

        in_window = datetime(2026, 9, 5, 2, 1, 0)

        class _FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return in_window

        with patch.object(tv_lock_monitor, "datetime", _FixedDatetime), \
             patch.object(
                 tv_lock_monitor.switchbot_service,
                 "send_device_command",
                 side_effect=RuntimeError("network error"),
             ):
            # 例外はmain()内でキャッチされ、外へは伝播しない。
            tv_lock_monitor.main()

        assert not os.path.exists(last_run_file)
