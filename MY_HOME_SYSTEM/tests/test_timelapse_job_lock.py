# MY_HOME_SYSTEM/tests/test_timelapse_job_lock.py
"""
timelapse_job_lock() のテスト (M-4-1の回帰テスト)。

work/timelapse ディレクトリは run_smart_timelapse_job() /
daily_timelapse_job.run_daily_timelapse() の複数エントリポイントから共有され、
setup_directories() で全消去されるため、複数ジョブが同時実行されると
互いの作業ファイル(motion.csv等)を破壊し合っていた。
"""
import os
import sys
from unittest.mock import patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from monitors import smart_timelapse_generator as stg
from monitors import daily_timelapse_job as dtj


class TestTimelapseJobLock:
    def test_second_acquisition_fails_while_first_holds_lock(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))

        with stg.timelapse_job_lock() as acquired1:
            assert acquired1 is True
            with stg.timelapse_job_lock() as acquired2:
                assert acquired2 is False, (
                    "同じロックを別ジョブが保持中でも取得できてしまっている"
                    "(並列実行によるwork/timelapseディレクトリの破壊を防げない)"
                )

    def test_lock_is_released_after_context_exits(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))

        with stg.timelapse_job_lock() as acquired1:
            assert acquired1 is True

        with stg.timelapse_job_lock() as acquired2:
            assert acquired2 is True


class TestRunSmartTimelapseJobSkipsWhenLocked:
    def test_skips_setup_directories_when_lock_held(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))

        with stg.timelapse_job_lock():
            with patch.object(stg, "setup_directories") as mock_setup, \
                 patch.object(stg, "logger") as mock_logger:
                stg.run_smart_timelapse_job("dummy.mp4")

        mock_setup.assert_not_called()
        mock_logger.warning.assert_called_once()

    def test_proceeds_when_lock_is_free(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))

        with patch.object(stg, "_run_smart_timelapse_job_locked") as mock_impl:
            stg.run_smart_timelapse_job("dummy.mp4")

        mock_impl.assert_called_once_with("dummy.mp4")


class TestRunDailyTimelapseSkipsWhenLocked:
    def test_skips_setup_directories_when_lock_held(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
        monkeypatch.setattr(config, "NVR_RECORD_DIR", str(tmp_path))

        cam_dir = tmp_path / "garden"
        cam_dir.mkdir()
        (cam_dir / "20260101_080000.mp4").write_bytes(b"dummy")

        with stg.timelapse_job_lock():
            with patch.object(dtj, "check_dependencies", return_value=True), \
                 patch.object(dtj, "setup_directories") as mock_setup, \
                 patch.object(dtj, "logger") as mock_logger:
                dtj.run_daily_timelapse("garden", target_date_str="2026-01-01")

        mock_setup.assert_not_called()
        mock_logger.warning.assert_called_once()
