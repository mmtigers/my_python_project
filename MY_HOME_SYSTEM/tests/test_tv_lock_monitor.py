# MY_HOME_SYSTEM/tests/test_tv_lock_monitor.py
"""
monitors/tv_lock_monitor.py のテスト。

Issue #490: このスクリプトは scheduler_boot.py により5分間隔(1日288回)で
実行される高頻度ジョブだが、本番コードカバレッジが0%だった。
毎日深夜2:00〜2:05の時間帯にのみSwitchBotプラグをOFFにし、当日分の
重複実行をLAST_RUN_FILEで防止するロジックをカバーする。

Issue #592: 「深夜2時」判定はホストOSのタイムゾーン設定に依存しないよう
core.utils.get_now_jst()経由でJSTの現在時刻を取得するよう修正した。
テストは`datetime`モジュールをパッチする代わりに、tv_lock_monitorが
importしている`get_now_jst`関数そのものを固定値を返す関数に差し替える。
"""
import os
import sys
from datetime import datetime, timedelta, timezone
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

        with patch.object(tv_lock_monitor, "get_now_jst", lambda: outside_window), \
             patch.object(tv_lock_monitor.switchbot_service, "send_device_command") as mock_cmd:
            tv_lock_monitor.main()

        mock_cmd.assert_not_called()

    def test_turns_off_plug_and_records_run_within_window(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "TV_PLUG_DEVICE_ID", "tv-plug-1", raising=False)
        last_run_file = str(tmp_path / "last_tv_lock.txt")
        monkeypatch.setattr(tv_lock_monitor, "LAST_RUN_FILE", last_run_file)

        in_window = datetime(2026, 9, 5, 2, 3, 0)

        with patch.object(tv_lock_monitor, "get_now_jst", lambda: in_window), \
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

        with patch.object(tv_lock_monitor, "get_now_jst", lambda: in_window), \
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

        with patch.object(tv_lock_monitor, "get_now_jst", lambda: in_window), \
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

        with patch.object(tv_lock_monitor, "get_now_jst", lambda: in_window), \
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

        with patch.object(tv_lock_monitor, "get_now_jst", lambda: in_window), \
             patch.object(
                 tv_lock_monitor.switchbot_service,
                 "send_device_command",
                 side_effect=RuntimeError("network error"),
             ):
            # 例外はmain()内でキャッチされ、外へは伝播しない。
            tv_lock_monitor.main()

        assert not os.path.exists(last_run_file)

    def test_hour_check_uses_jst_regardless_of_host_timezone(self, tmp_path, monkeypatch):
        """Issue #592: get_now_jst()由来のawareなJST時刻でも、naiveなdatetimeを
        使っていた以前のテストと同じ判定結果になること(hour/minute/strftimeの
        扱いはaware/naiveで変わらないことの確認)。"""
        monkeypatch.setattr(config, "TV_PLUG_DEVICE_ID", "tv-plug-1", raising=False)
        last_run_file = str(tmp_path / "last_tv_lock.txt")
        monkeypatch.setattr(tv_lock_monitor, "LAST_RUN_FILE", last_run_file)
        import pytz
        aware_in_window = pytz.timezone("Asia/Tokyo").localize(datetime(2026, 9, 5, 2, 3, 0))

        with patch.object(tv_lock_monitor, "get_now_jst", lambda: aware_in_window), \
             patch.object(
                 tv_lock_monitor.switchbot_service,
                 "send_device_command",
                 return_value={"statusCode": 100},
             ) as mock_cmd:
            tv_lock_monitor.main()

        mock_cmd.assert_called_once_with("tv-plug-1", "turnOff")


class TestHolidaySlots:
    """休日(土日祝)は12:00にオフ・14:00にオン・20:00にオフ(平日は何もしない)。"""

    SATURDAY = datetime(2026, 9, 19, tzinfo=timezone(timedelta(hours=9)))
    FRIDAY = datetime(2026, 9, 18, tzinfo=timezone(timedelta(hours=9)))

    def _run(self, tmp_path, monkeypatch, now, result=None):
        monkeypatch.setattr(config, "TV_PLUG_DEVICE_ID", "tv-plug-1", raising=False)
        monkeypatch.setattr(tv_lock_monitor, "LAST_RUN_FILE", str(tmp_path / "last_tv_lock.txt"))
        monkeypatch.setattr(
            tv_lock_monitor, "HOLIDAY_RUN_FILE_TEMPLATE", str(tmp_path / "last_tv_lock_{hhmm}.txt")
        )
        with patch.object(tv_lock_monitor, "get_now_jst", lambda: now), \
             patch.object(
                 tv_lock_monitor.switchbot_service,
                 "send_device_command",
                 return_value=result or {"statusCode": 100},
             ) as mock_cmd:
            tv_lock_monitor.main()
        return mock_cmd

    def test_turns_off_at_noon_on_saturday(self, tmp_path, monkeypatch):
        mock_cmd = self._run(tmp_path, monkeypatch, self.SATURDAY.replace(hour=12, minute=2))
        mock_cmd.assert_called_once_with("tv-plug-1", "turnOff")

    def test_turns_on_at_14_on_saturday(self, tmp_path, monkeypatch):
        mock_cmd = self._run(tmp_path, monkeypatch, self.SATURDAY.replace(hour=14, minute=1))
        mock_cmd.assert_called_once_with("tv-plug-1", "turnOn")

    def test_turns_off_at_20_on_saturday(self, tmp_path, monkeypatch):
        mock_cmd = self._run(tmp_path, monkeypatch, self.SATURDAY.replace(hour=20, minute=0))
        mock_cmd.assert_called_once_with("tv-plug-1", "turnOff")

    def test_does_nothing_at_noon_on_a_weekday(self, tmp_path, monkeypatch):
        mock_cmd = self._run(tmp_path, monkeypatch, self.FRIDAY.replace(hour=12, minute=2))
        mock_cmd.assert_not_called()

    def test_does_nothing_at_20_on_a_weekday(self, tmp_path, monkeypatch):
        mock_cmd = self._run(tmp_path, monkeypatch, self.FRIDAY.replace(hour=20, minute=2))
        mock_cmd.assert_not_called()

    def test_each_slot_runs_once_per_day(self, tmp_path, monkeypatch):
        now = self.SATURDAY.replace(hour=12, minute=1)
        self._run(tmp_path, monkeypatch, now)
        mock_cmd = self._run(tmp_path, monkeypatch, now.replace(minute=4))
        mock_cmd.assert_not_called()
        # 別スロット(14:00)は独立して実行される
        mock_cmd = self._run(tmp_path, monkeypatch, self.SATURDAY.replace(hour=14, minute=1))
        mock_cmd.assert_called_once_with("tv-plug-1", "turnOn")

    def test_holiday_slot_uses_a_separate_record_file(self, tmp_path, monkeypatch):
        self._run(tmp_path, monkeypatch, self.SATURDAY.replace(hour=12, minute=1))
        assert (tmp_path / "last_tv_lock_1200.txt").read_text().strip() == "2026-09-19"
        assert not (tmp_path / "last_tv_lock.txt").exists()


class TestGracefulTurnOffIsUsed:
    def test_turn_off_slots_go_through_turn_off_tv_gracefully(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "TV_PLUG_DEVICE_ID", "tv-plug-1", raising=False)
        monkeypatch.setattr(tv_lock_monitor, "LAST_RUN_FILE", str(tmp_path / "last_tv_lock.txt"))
        now = datetime(2026, 9, 5, 2, 1, 0, tzinfo=timezone(timedelta(hours=9)))

        with patch.object(tv_lock_monitor, "get_now_jst", lambda: now), \
             patch.object(
                 tv_lock_monitor.switchbot_service,
                 "turn_off_tv_gracefully",
                 return_value={"statusCode": 100},
             ) as mock_off, \
             patch.object(tv_lock_monitor.switchbot_service, "send_device_command") as mock_cmd:
            tv_lock_monitor.main()

        mock_off.assert_called_once_with()
        mock_cmd.assert_not_called()


class TestRecutWhileBlocked:
    """Issue #900: 休日の禁止時間帯に入れ直されたら、次の5分の起動で再度切る。"""

    SATURDAY = datetime(2026, 9, 5)  # 土曜

    def _run(self, monkeypatch, now, watts, tmp_path=None):
        monkeypatch.setattr(config, "TV_PLUG_DEVICE_ID", "tv-plug-1", raising=False)
        monkeypatch.setattr(config, "TV_POWER_ON_THRESHOLD_WATTS", 10.0, raising=False)
        if tmp_path is not None:
            monkeypatch.setattr(
                tv_lock_monitor, "HOLIDAY_RUN_FILE_TEMPLATE", str(tmp_path / "run_{hhmm}.txt")
            )
        sw = tv_lock_monitor.switchbot_service
        with patch.object(tv_lock_monitor, "get_now_jst", lambda: now), \
             patch.object(sw, "get_tv_power_watts", return_value=watts), \
             patch.object(sw, "turn_off_tv_gracefully", return_value={"statusCode": 100}) as cut, \
             patch.object(sw, "send_device_command", return_value={"statusCode": 100}):
            tv_lock_monitor.main()
        return cut

    def test_cuts_when_tv_is_on_in_blocked_hours(self, monkeypatch):
        assert self._run(monkeypatch, self.SATURDAY.replace(hour=12, minute=30), 80.0).call_count == 1
        assert self._run(monkeypatch, self.SATURDAY.replace(hour=21, minute=10), 80.0).call_count == 1

    def test_does_not_cut_when_tv_is_off(self, monkeypatch):
        assert self._run(monkeypatch, self.SATURDAY.replace(hour=12, minute=30), 0.5).call_count == 0

    def test_does_not_cut_when_power_unavailable(self, monkeypatch):
        assert self._run(monkeypatch, self.SATURDAY.replace(hour=12, minute=30), None).call_count == 0

    def test_does_not_cut_outside_blocked_hours(self, monkeypatch):
        assert self._run(monkeypatch, self.SATURDAY.replace(hour=15, minute=0), 80.0).call_count == 0
        assert self._run(monkeypatch, self.SATURDAY.replace(hour=14, minute=30), 80.0).call_count == 0

    def test_does_not_cut_on_weekday(self, monkeypatch):
        weekday = datetime(2026, 9, 7, 12, 30)  # 月曜(祝日でない)
        assert self._run(monkeypatch, weekday, 80.0).call_count == 0

    def test_no_extra_cut_in_the_run_that_executes_the_slot(self, monkeypatch, tmp_path):
        cut = self._run(monkeypatch, self.SATURDAY.replace(hour=12, minute=2), 80.0, tmp_path)
        assert cut.call_count == 1  # スロットのオフ1回のみ(見張りは重ねない)

    def test_cuts_after_slot_already_done_within_slot_window(self, monkeypatch, tmp_path):
        (tmp_path / "run_1200.txt").write_text("2026-09-05")
        cut = self._run(monkeypatch, self.SATURDAY.replace(hour=12, minute=3), 80.0, tmp_path)
        assert cut.call_count == 1
