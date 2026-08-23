# DDD/test_newface_monitor_lock.py
"""
M-7-4: newface_monitor.py の多重起動防止ロックの回帰テスト。

DDDにはpytest基盤(conftest.py等)が無いため、本ファイルは
`pytest DDD/test_newface_monitor_lock.py` のように直接指定して実行する
(MY_HOME_SYSTEM/pytest.ini の testpaths=tests のスコープ外)。

batch_download_discord.py は既にflockによる多重起動防止ロックを持つが、
newface_monitor.py には無く、cronの1回が想定より長引く(1時間超)と
新旧プロセスが並行実行され、既知キャストリスト・サマリファイルの
読み書きが競合しうる問題があった。
"""
import fcntl
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

DDD_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DDD_DIR))

import newface_monitor as module  # noqa: E402


class TestRunMonitorLock:
    def test_second_instance_is_skipped_while_lock_is_held(self, tmp_path, monkeypatch):
        lock_path = tmp_path / ".newface_monitor.lock"
        monkeypatch.setattr(module, "_MONITOR_LOCK_FILE_PATH", lock_path)

        # 1つ目の"インスタンス"としてロックを保持したまま、
        # 2つ目の run_monitor() 呼び出しが即座にスキップされることを検証する。
        holder_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
        fcntl.flock(holder_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with patch.object(module, "_run_monitor_locked") as mock_run:
                module.run_monitor()
            mock_run.assert_not_called()
        finally:
            fcntl.flock(holder_fd, fcntl.LOCK_UN)
            os.close(holder_fd)

    def test_runs_normally_when_lock_is_free(self, tmp_path, monkeypatch):
        lock_path = tmp_path / ".newface_monitor.lock"
        monkeypatch.setattr(module, "_MONITOR_LOCK_FILE_PATH", lock_path)

        with patch.object(module, "_run_monitor_locked") as mock_run:
            module.run_monitor()

        mock_run.assert_called_once()

    def test_lock_is_released_after_run_so_a_later_call_can_proceed(self, tmp_path, monkeypatch):
        lock_path = tmp_path / ".newface_monitor.lock"
        monkeypatch.setattr(module, "_MONITOR_LOCK_FILE_PATH", lock_path)

        with patch.object(module, "_run_monitor_locked"):
            module.run_monitor()

        with patch.object(module, "_run_monitor_locked") as mock_run_second:
            module.run_monitor()
        mock_run_second.assert_called_once()

    def test_lock_is_released_even_if_run_raises(self, tmp_path, monkeypatch):
        lock_path = tmp_path / ".newface_monitor.lock"
        monkeypatch.setattr(module, "_MONITOR_LOCK_FILE_PATH", lock_path)

        with patch.object(module, "_run_monitor_locked", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError):
                module.run_monitor()

        with patch.object(module, "_run_monitor_locked") as mock_run_second:
            module.run_monitor()
        mock_run_second.assert_called_once()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
