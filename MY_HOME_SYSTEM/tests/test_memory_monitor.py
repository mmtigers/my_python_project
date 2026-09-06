# MY_HOME_SYSTEM/tests/test_memory_monitor.py
"""
monitors/memory_monitor.py のテスト。

Issue #490: このスクリプトは scheduler_boot.py により10分間隔(1日144回)で
実行される高頻度ジョブだが、本番コードカバレッジが0%だった。
プロセス判定(is_target_process)、クールダウン判定(check_cooldown/
record_notification、読み取りエラー時のfail-open挙動を含む)、上位メモリ
消費プロセスの整形(get_top_memory_processes)、および閾値超過時の
通知フロー(main)をカバーする。
"""
import os
import sys
import time
from unittest.mock import MagicMock, patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from monitors import memory_monitor


class TestIsTargetProcess:
    def test_empty_cmdline_is_not_a_target(self):
        assert memory_monitor.is_target_process([]) is False

    def test_python_process_running_unified_server_is_a_target(self):
        assert memory_monitor.is_target_process(["python3", "unified_server.py"]) is True

    def test_python_process_under_monitors_dir_is_a_target(self):
        assert memory_monitor.is_target_process(["python3", "monitors/nas_monitor.py"]) is True

    def test_python_process_with_project_dir_in_path_is_a_target(self):
        assert memory_monitor.is_target_process(
            ["python3", "/home/pi/develop/MY_HOME_SYSTEM/scheduler_boot.py"]
        ) is True

    def test_non_python_process_is_not_a_target(self):
        assert memory_monitor.is_target_process(["nginx", "-g", "daemon off;"]) is False

    def test_unrelated_python_process_is_not_a_target(self):
        assert memory_monitor.is_target_process(["python3", "some_other_script.py"]) is False


class TestCooldown:
    def test_no_previous_notification_allows_notify(self, tmp_path, monkeypatch):
        last_notify_file = str(tmp_path / "last_memory_alert.txt")
        monkeypatch.setattr(config, "MEMORY_ALERT_LAST_NOTIFY_FILE", last_notify_file, raising=False)

        assert memory_monitor.check_cooldown() is True

    def test_recent_notification_blocks_notify(self, tmp_path, monkeypatch):
        last_notify_file = str(tmp_path / "last_memory_alert.txt")
        monkeypatch.setattr(config, "MEMORY_ALERT_LAST_NOTIFY_FILE", last_notify_file, raising=False)
        monkeypatch.setattr(config, "MEMORY_ALERT_COOLDOWN_SEC", 7200, raising=False)
        with open(last_notify_file, "w") as f:
            f.write(str(time.time()))

        assert memory_monitor.check_cooldown() is False

    def test_expired_cooldown_allows_notify(self, tmp_path, monkeypatch):
        last_notify_file = str(tmp_path / "last_memory_alert.txt")
        monkeypatch.setattr(config, "MEMORY_ALERT_LAST_NOTIFY_FILE", last_notify_file, raising=False)
        monkeypatch.setattr(config, "MEMORY_ALERT_COOLDOWN_SEC", 7200, raising=False)
        with open(last_notify_file, "w") as f:
            f.write(str(time.time() - 7300))

        assert memory_monitor.check_cooldown() is True

    def test_empty_file_allows_notify(self, tmp_path, monkeypatch):
        last_notify_file = str(tmp_path / "last_memory_alert.txt")
        monkeypatch.setattr(config, "MEMORY_ALERT_LAST_NOTIFY_FILE", last_notify_file, raising=False)
        with open(last_notify_file, "w") as f:
            f.write("")

        assert memory_monitor.check_cooldown() is True

    def test_corrupted_file_fails_open_and_allows_notify(self, tmp_path, monkeypatch):
        last_notify_file = str(tmp_path / "last_memory_alert.txt")
        monkeypatch.setattr(config, "MEMORY_ALERT_LAST_NOTIFY_FILE", last_notify_file, raising=False)
        with open(last_notify_file, "w") as f:
            f.write("not-a-timestamp")

        assert memory_monitor.check_cooldown() is True

    def test_record_notification_writes_current_timestamp(self, tmp_path, monkeypatch):
        last_notify_file = str(tmp_path / "last_memory_alert.txt")
        monkeypatch.setattr(config, "MEMORY_ALERT_LAST_NOTIFY_FILE", last_notify_file, raising=False)

        before = time.time()
        memory_monitor.record_notification()
        after = time.time()

        with open(last_notify_file, "r") as f:
            recorded = float(f.read().strip())
        assert before <= recorded <= after

    def test_record_notification_failure_is_swallowed(self, tmp_path, monkeypatch):
        last_notify_file = str(tmp_path / "no_such_dir" / "last_memory_alert.txt")
        monkeypatch.setattr(config, "MEMORY_ALERT_LAST_NOTIFY_FILE", last_notify_file, raising=False)

        with patch.object(memory_monitor.os, "makedirs", side_effect=OSError("boom")):
            memory_monitor.record_notification()  # 例外を送出しないこと


def _make_process(pid, name, rss_mb):
    p = MagicMock()
    p.info = {"pid": pid, "name": name, "memory_info": MagicMock(rss=int(rss_mb * 1024 * 1024))}
    return p


class TestGetTopMemoryProcesses:
    def test_sorts_by_memory_descending_and_respects_limit(self, monkeypatch):
        processes = [
            _make_process(1, "small", 10.0),
            _make_process(2, "big", 500.0),
            _make_process(3, "medium", 100.0),
        ]
        monkeypatch.setattr(memory_monitor.psutil, "process_iter", lambda fields: iter(processes))

        result = memory_monitor.get_top_memory_processes(limit=2)

        lines = result.splitlines()
        assert lines[0] == "【Top Memory Consuming Processes】"
        assert "big" in lines[1]
        assert "medium" in lines[2]
        assert len(lines) == 3  # header + limit(2)

    def test_skips_processes_that_disappear_during_iteration(self, monkeypatch):
        ok_process = _make_process(1, "ok", 50.0)

        class _RaisingInfo(dict):
            def __getitem__(self, key):
                if key == "memory_info":
                    raise memory_monitor.psutil.NoSuchProcess(2)
                return super().__getitem__(key)

        gone_process = MagicMock()
        gone_process.info = _RaisingInfo(pid=2, name="gone")

        monkeypatch.setattr(memory_monitor.psutil, "process_iter", lambda fields: iter([ok_process, gone_process]))

        result = memory_monitor.get_top_memory_processes(limit=5)

        assert "ok" in result
        assert "gone" not in result


class TestMainThresholdAndNotification:
    def _mock_virtual_memory(self, percent):
        mem = MagicMock()
        mem.percent = percent
        return mem

    def test_below_threshold_and_below_process_limit_sends_no_notification(self, monkeypatch):
        monkeypatch.setattr(config, "MEMORY_ALERT_PERCENT", 85.0, raising=False)
        monkeypatch.setattr(config, "PROCESS_MEMORY_LIMIT_MB", 500.0, raising=False)
        monkeypatch.setattr(memory_monitor.psutil, "virtual_memory", lambda: self._mock_virtual_memory(50.0))
        monkeypatch.setattr(memory_monitor.psutil, "process_iter", lambda fields: iter([]))

        with patch.object(memory_monitor, "send_push") as mock_send:
            memory_monitor.main()

        mock_send.assert_not_called()

    def test_system_memory_at_threshold_sends_notification(self, monkeypatch):
        monkeypatch.setattr(config, "MEMORY_ALERT_PERCENT", 85.0, raising=False)
        monkeypatch.setattr(config, "PROCESS_MEMORY_LIMIT_MB", 500.0, raising=False)
        monkeypatch.setattr(memory_monitor.psutil, "virtual_memory", lambda: self._mock_virtual_memory(85.0))
        monkeypatch.setattr(memory_monitor.psutil, "process_iter", lambda fields: iter([]))

        with patch.object(memory_monitor, "check_cooldown", return_value=True), \
             patch.object(memory_monitor, "record_notification") as mock_record, \
             patch.object(memory_monitor, "send_push", return_value=True) as mock_send:
            memory_monitor.main()

        mock_send.assert_called_once()
        mock_record.assert_called_once()

    def test_system_memory_just_below_threshold_sends_no_notification(self, monkeypatch):
        monkeypatch.setattr(config, "MEMORY_ALERT_PERCENT", 85.0, raising=False)
        monkeypatch.setattr(config, "PROCESS_MEMORY_LIMIT_MB", 500.0, raising=False)
        monkeypatch.setattr(memory_monitor.psutil, "virtual_memory", lambda: self._mock_virtual_memory(84.9))
        monkeypatch.setattr(memory_monitor.psutil, "process_iter", lambda fields: iter([]))

        with patch.object(memory_monitor, "send_push") as mock_send:
            memory_monitor.main()

        mock_send.assert_not_called()

    def test_notification_suppressed_during_cooldown(self, monkeypatch):
        monkeypatch.setattr(config, "MEMORY_ALERT_PERCENT", 85.0, raising=False)
        monkeypatch.setattr(config, "PROCESS_MEMORY_LIMIT_MB", 500.0, raising=False)
        monkeypatch.setattr(memory_monitor.psutil, "virtual_memory", lambda: self._mock_virtual_memory(90.0))
        monkeypatch.setattr(memory_monitor.psutil, "process_iter", lambda fields: iter([]))

        with patch.object(memory_monitor, "check_cooldown", return_value=False), \
             patch.object(memory_monitor, "record_notification") as mock_record, \
             patch.object(memory_monitor, "send_push") as mock_send:
            memory_monitor.main()

        mock_send.assert_not_called()
        mock_record.assert_not_called()

    def test_notification_not_recorded_when_send_fails(self, monkeypatch):
        monkeypatch.setattr(config, "MEMORY_ALERT_PERCENT", 85.0, raising=False)
        monkeypatch.setattr(config, "PROCESS_MEMORY_LIMIT_MB", 500.0, raising=False)
        monkeypatch.setattr(memory_monitor.psutil, "virtual_memory", lambda: self._mock_virtual_memory(90.0))
        monkeypatch.setattr(memory_monitor.psutil, "process_iter", lambda fields: iter([]))

        with patch.object(memory_monitor, "check_cooldown", return_value=True), \
             patch.object(memory_monitor, "record_notification") as mock_record, \
             patch.object(memory_monitor, "send_push", return_value=False):
            memory_monitor.main()

        mock_record.assert_not_called()

    def test_oversized_target_process_triggers_notification(self, monkeypatch):
        monkeypatch.setattr(config, "MEMORY_ALERT_PERCENT", 85.0, raising=False)
        monkeypatch.setattr(config, "PROCESS_MEMORY_LIMIT_MB", 500.0, raising=False)
        monkeypatch.setattr(memory_monitor.psutil, "virtual_memory", lambda: self._mock_virtual_memory(10.0))

        bloated = MagicMock()
        bloated.info = {"pid": 42, "name": "python3", "cmdline": ["python3", "unified_server.py"]}
        bloated.memory_info.return_value = MagicMock(rss=600 * 1024 * 1024)

        monkeypatch.setattr(memory_monitor.psutil, "process_iter", lambda fields: iter([bloated]))

        with patch.object(memory_monitor, "check_cooldown", return_value=True), \
             patch.object(memory_monitor, "record_notification") as mock_record, \
             patch.object(memory_monitor, "send_push", return_value=True) as mock_send:
            memory_monitor.main()

        mock_send.assert_called_once()
        mock_record.assert_called_once()

    def test_process_iteration_error_is_caught_and_does_not_crash(self, monkeypatch):
        monkeypatch.setattr(config, "MEMORY_ALERT_PERCENT", 85.0, raising=False)
        monkeypatch.setattr(config, "PROCESS_MEMORY_LIMIT_MB", 500.0, raising=False)
        monkeypatch.setattr(memory_monitor.psutil, "virtual_memory", lambda: self._mock_virtual_memory(10.0))

        def _raise(fields):
            raise RuntimeError("boom")

        monkeypatch.setattr(memory_monitor.psutil, "process_iter", _raise)

        with patch.object(memory_monitor, "send_push") as mock_send:
            memory_monitor.main()  # 例外を送出しないこと

        mock_send.assert_not_called()

    def test_disappearing_process_during_scan_is_skipped(self, monkeypatch):
        monkeypatch.setattr(config, "MEMORY_ALERT_PERCENT", 85.0, raising=False)
        monkeypatch.setattr(config, "PROCESS_MEMORY_LIMIT_MB", 500.0, raising=False)
        monkeypatch.setattr(memory_monitor.psutil, "virtual_memory", lambda: self._mock_virtual_memory(10.0))

        gone = MagicMock()
        gone.info = {"pid": 99, "name": "python3", "cmdline": ["python3", "unified_server.py"]}
        gone.memory_info.side_effect = memory_monitor.psutil.NoSuchProcess(99)

        monkeypatch.setattr(memory_monitor.psutil, "process_iter", lambda fields: iter([gone]))

        with patch.object(memory_monitor, "send_push") as mock_send:
            memory_monitor.main()

        mock_send.assert_not_called()
