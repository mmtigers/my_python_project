# MY_HOME_SYSTEM/tests/test_nas_monitor.py
import unittest
import sys
import os
import threading
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


class TestNasMonitorStatePersistence(unittest.TestCase):
    """M-4-6の回帰テスト(状態ファイルの/tmp配置分のみ):
    状態ファイルが/tmp配下にあると、プロセス/コンテナ再起動(/tmpがtmpfs等で
    消える環境)のたびにヘルス状態が既定値のTrueへ戻ってしまい、実際には
    NAS障害中でも「正常」とみなされ、真の復旧時にフォールバックデータの
    同期が行われなくなる。"""

    def test_state_file_is_not_under_tmp(self):
        monitor = NasMonitor()
        self.assertFalse(
            monitor.state_file.startswith("/tmp"),
            "状態ファイルが/tmpにあると再起動でヘルス状態がリセットされる"
        )

    def test_state_round_trips_across_new_instances(self):
        """プロセス再起動を模して、新しいNasMonitorインスタンスでも
        直前に保存した状態を読み込めること。"""
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(config, "BASE_DIR", tmp):
                m1 = NasMonitor()
                m1._save_state({"is_healthy": False})

                m2 = NasMonitor()
                self.assertEqual(m2._load_state(), {"is_healthy": False})


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


class TestNasMonitorWritePermissionTimeout(unittest.TestCase):
    """M-4-6残り: check_write_permission()の書き込みI/O(open/write/remove)に
    タイムアウトが無く、CIFSマウントがストールした場合にkillできず監視プロセス
    ごとハングしてしまう不具合。実CIFS/NAS環境の代わりに、名前付きパイプ(FIFO)を
    書き込み対象ファイルとして使い、open()がリーダー不在でブロックし続ける
    状況を再現する。"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_check_write_permission_times_out_instead_of_hanging(self):
        monitor = NasMonitor()
        monitor.mount_point = self.tmp_dir
        monitor.timeout = 1
        monitor.write_check_retries = 2  # リトライ待機を短縮してテストを高速化
        fifo_path = os.path.join(self.tmp_dir, ".write_test")
        os.mkfifo(fifo_path)

        result = {}

        def run():
            result["value"] = monitor.check_write_permission()

        t = threading.Thread(target=run, daemon=True)
        start = time.monotonic()
        t.start()
        t.join(timeout=10)
        elapsed = time.monotonic() - start

        self.assertFalse(
            t.is_alive(),
            "check_write_permission()がNASストール(FIFOブロック)でハングした"
        )
        self.assertLess(elapsed, 10, "タイムアウトが機能せず長時間ブロックした")
        self.assertEqual(result.get("value"), False)

    def test_check_write_permission_recovers_after_transient_timeout(self):
        """一過性のストール(autofsの再トリガー遅延やNASのディスクスピンアップ想定)は
        単発のタイムアウトで即座に異常とせず、リトライで復旧を検知できること。"""
        monitor = NasMonitor()
        monitor.mount_point = self.tmp_dir
        monitor.timeout = 1
        monitor.write_check_retries = 3
        fifo_path = os.path.join(self.tmp_dir, ".write_test")
        os.mkfifo(fifo_path)

        def clear_stall_after_delay():
            time.sleep(1.5)
            os.remove(fifo_path)

        threading.Thread(target=clear_stall_after_delay, daemon=True).start()

        start = time.monotonic()
        result = monitor.check_write_permission()
        elapsed = time.monotonic() - start

        self.assertTrue(
            result,
            "一過性のストールが解消した後は書き込みチェックが成功として扱われるべき"
        )
        self.assertLess(elapsed, 10)


if __name__ == "__main__":
    unittest.main()
