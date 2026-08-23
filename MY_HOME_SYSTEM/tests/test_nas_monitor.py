# MY_HOME_SYSTEM/tests/test_nas_monitor.py
import unittest
import sys
import os
import time
import tempfile
import shutil
from unittest.mock import patch

# プロジェクトルートにパスを通す
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from monitors.nas_monitor import NasMonitor


class TestNasMonitorCleanup(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.monitor = NasMonitor()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _make_file(self, name: str, age_days: int) -> str:
        path = os.path.join(self.tmp_dir, name)
        with open(path, "w") as f:
            f.write("dummy")
        old_time = time.time() - (age_days * 86400)
        os.utime(path, (old_time, old_time))
        return path

    def test_deletes_only_files_older_than_retention(self):
        old_file = self._make_file("old.mp4", age_days=40)
        new_file = self._make_file("new.mp4", age_days=5)

        result = self.monitor.cleanup_old_files(self.tmp_dir, retention_days=30, extensions=(".mp4",))

        self.assertEqual(result["deleted_count"], 1)
        self.assertFalse(os.path.exists(old_file))
        self.assertTrue(os.path.exists(new_file))

    def test_missing_directory_returns_empty_result(self):
        missing_dir = os.path.join(self.tmp_dir, "does_not_exist")

        result = self.monitor.cleanup_old_files(missing_dir, retention_days=30, extensions=(".mp4",))

        self.assertEqual(result, {"deleted_count": 0, "freed_gb": 0.0})

    def test_extension_filter_skips_other_files(self):
        old_other = self._make_file("old.txt", age_days=40)

        result = self.monitor.cleanup_old_files(self.tmp_dir, retention_days=30, extensions=(".mp4",))

        self.assertEqual(result["deleted_count"], 0)
        self.assertTrue(os.path.exists(old_other))


class TestNasMonitorRetentionTargets(unittest.TestCase):
    """M-4-4の回帰テスト: assets/timelapse配下の生成物が保持期間クリーンアップの
    対象に含まれておらず無限に蓄積してしまう不具合。"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.monitor = NasMonitor()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _make_file(self, path: str, age_days: int) -> str:
        with open(path, "w") as f:
            f.write("dummy")
        old_time = time.time() - (age_days * 86400)
        os.utime(path, (old_time, old_time))
        return path

    def test_old_timelapse_outputs_are_cleaned_up(self):
        timelapse_dir = os.path.join(self.tmp_dir, "timelapse")
        os.makedirs(timelapse_dir, exist_ok=True)
        old_summary = self._make_file(
            os.path.join(timelapse_dir, "20250101_summary.mp4"), age_days=40
        )
        old_part = self._make_file(
            os.path.join(timelapse_dir, "20250101_summary_part_001.mp4"), age_days=40
        )
        new_summary = self._make_file(
            os.path.join(timelapse_dir, "20260101_summary.mp4"), age_days=5
        )

        with patch.object(config, "ASSETS_DIR", self.tmp_dir), \
             patch.object(self.monitor, "cleanup_old_files", wraps=self.monitor.cleanup_old_files) as spy, \
             patch("monitors.nas_monitor.send_push"):
            self.monitor.run_retention_cleanup()

        # cleanup_old_filesが timelapse ディレクトリに対しても呼ばれていること
        called_dirs = [call.args[0] for call in spy.call_args_list]
        self.assertIn(timelapse_dir, called_dirs)

        self.assertFalse(os.path.exists(old_summary))
        self.assertFalse(os.path.exists(old_part))
        self.assertTrue(os.path.exists(new_summary))


if __name__ == "__main__":
    unittest.main()
