# MY_HOME_SYSTEM/tests/test_nas_monitor.py
import unittest
import sys
import os
import time
import tempfile
import shutil

# プロジェクトルートにパスを通す
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

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


if __name__ == "__main__":
    unittest.main()
