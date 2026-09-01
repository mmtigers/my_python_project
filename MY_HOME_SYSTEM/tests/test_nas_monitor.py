# MY_HOME_SYSTEM/tests/test_nas_monitor.py
import unittest
import sys
import os
import subprocess
import threading
import time
import tempfile
import shutil
from unittest.mock import patch

# プロジェクトルートにパスを通す
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import common
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
        """タイムラプス動画の実際の生成先(monitors/smart_timelapse_generator.pyの
        setup_directories)はNAS(config.ASSETS_DIR)ではなくローカルの
        config.BASE_DIR/assets/timelapse であるため、リテンション対象も
        同じローカルパスを見る必要がある(Issue #171)。"""
        timelapse_dir = os.path.join(self.tmp_dir, "assets", "timelapse")
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

        with patch.object(config, "BASE_DIR", self.tmp_dir), \
             patch.object(self.monitor, "cleanup_old_files", wraps=self.monitor.cleanup_old_files) as spy, \
             patch("monitors.nas_monitor.send_push"):
            self.monitor.run_retention_cleanup()

        # cleanup_old_filesが timelapse ディレクトリに対しても呼ばれていること
        called_dirs = [call.args[0] for call in spy.call_args_list]
        self.assertIn(timelapse_dir, called_dirs)

        self.assertFalse(os.path.exists(old_summary))
        self.assertFalse(os.path.exists(old_part))
        self.assertTrue(os.path.exists(new_summary))

    def test_old_config_file_backups_are_cleaned_up(self):
        """Issue #191の回帰テスト: services/backup_service.py の _backup_config_files が
        config.BACKUP_FILES 中のDB以外のファイル(config.py, .env, devices.json)を
        DB_BACKUPS_DIR へ拡張子付き/なしでコピーするが、以前のリテンションは
        DBバックアップ対象の拡張子を ".db" のみに限定していたため、設定ファイルの
        バックアップコピーは一切削除されず無限蓄積していた。"""
        db_backups_dir = os.path.join(self.tmp_dir, "db_backups")
        os.makedirs(db_backups_dir, exist_ok=True)
        old_db = self._make_file(os.path.join(db_backups_dir, "home_system_20250101_000000.db"), age_days=40)
        old_config = self._make_file(os.path.join(db_backups_dir, "config_20250101_000000.py"), age_days=40)
        old_devices = self._make_file(os.path.join(db_backups_dir, "devices_20250101_000000.json"), age_days=40)
        # Path(".env").stem == ".env", Path(".env").suffix == "" のため
        # 実際のコピー結果は拡張子なしのファイル名になる
        old_env = self._make_file(os.path.join(db_backups_dir, ".env_20250101_000000"), age_days=40)
        new_db = self._make_file(os.path.join(db_backups_dir, "home_system_20260101_000000.db"), age_days=5)

        with patch.object(config, "DB_BACKUPS_DIR", db_backups_dir), \
             patch("monitors.nas_monitor.send_push"):
            self.monitor.run_retention_cleanup()

        self.assertFalse(os.path.exists(old_db))
        self.assertFalse(os.path.exists(old_config))
        self.assertFalse(os.path.exists(old_devices))
        self.assertFalse(os.path.exists(old_env))
        self.assertTrue(os.path.exists(new_db))


class TestNasMonitorFallbackSync(unittest.TestCase):
    """Issue #162 の回帰テスト: sync_fallback_dataの同期先がmount_point直下になっており、
    本来の NAS_PROJECT_ROOT/assets ではなく誤った場所へ移動されていた不具合。
    また、同期対象を assets サブディレクトリに限定せず fallback_dir 全体を対象と
    していたため、last_memory_alert.txt 等の無関係なローカル状態ファイルまで
    巻き込んで移動・削除されていた不具合。"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.mount_point = os.path.join(self.tmp_dir, "mnt_nas")
        self.fallback_dir = os.path.join(self.tmp_dir, "temp_fallback")
        self.nas_project_root = os.path.join(self.mount_point, "home_system")
        os.makedirs(self.mount_point, exist_ok=True)
        os.makedirs(self.fallback_dir, exist_ok=True)

        with patch.object(config, "NAS_MOUNT_POINT", self.mount_point), \
             patch.object(config, "FALLBACK_ROOT", self.fallback_dir), \
             patch.object(config, "NAS_PROJECT_ROOT", self.nas_project_root):
            self.monitor = NasMonitor()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_sync_targets_nas_project_root_assets_not_mount_root(self):
        assets_dir = os.path.join(self.fallback_dir, "assets")
        os.makedirs(assets_dir, exist_ok=True)
        with open(os.path.join(assets_dir, "snapshot.jpg"), "w") as f:
            f.write("dummy")

        with patch("monitors.nas_monitor.subprocess.run") as mock_run, \
             patch("monitors.nas_monitor.send_push"):
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            self.monitor.sync_fallback_data()

        cmd = mock_run.call_args.args[0]
        src, dst = cmd[-2], cmd[-1]
        expected_dst = os.path.join(self.nas_project_root, "assets") + "/"
        self.assertEqual(src, assets_dir + "/")
        self.assertEqual(dst, expected_dst)

    def test_sync_does_not_touch_unrelated_fallback_state_files(self):
        assets_dir = os.path.join(self.fallback_dir, "assets")
        os.makedirs(assets_dir, exist_ok=True)
        with open(os.path.join(assets_dir, "snapshot.jpg"), "w") as f:
            f.write("dummy")

        unrelated_state_file = os.path.join(self.fallback_dir, "last_memory_alert.txt")
        with open(unrelated_state_file, "w") as f:
            f.write("2026-08-30T00:00:00")

        with patch("monitors.nas_monitor.subprocess.run") as mock_run, \
             patch("monitors.nas_monitor.send_push"):
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            self.monitor.sync_fallback_data()

        # rsyncの対象パスが assets サブディレクトリに限定され、
        # fallback_dir直下の無関係な状態ファイルを含んでいないこと
        cmd = mock_run.call_args.args[0]
        src = cmd[-2]
        self.assertEqual(src, assets_dir + "/")
        self.assertTrue(os.path.exists(unrelated_state_file), "無関係な状態ファイルは移動・削除されないこと")

    def test_sync_skipped_when_only_unrelated_state_files_exist(self):
        """assetsサブディレクトリが存在しない(=同期すべきNASデータがない)場合は、
        fallback_dir直下の状態ファイルの有無に関わらず同期処理自体を行わないこと。"""
        with open(os.path.join(self.fallback_dir, "last_tv_lock.txt"), "w") as f:
            f.write("2026-08-30")

        with patch("monitors.nas_monitor.subprocess.run") as mock_run, \
             patch("monitors.nas_monitor.send_push"):
            self.monitor.sync_fallback_data()

        mock_run.assert_not_called()


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
        # 各試行は本来ファイル名を毎回変えるが(自己永続的な失敗ループ回避のため)、
        # このテストは「同じ箇所が全リトライを通じて塞がり続けるケース」を
        # 検証したいので、あえて固定名に差し替える。
        fixed_name = ".write_test_fixed_for_test"
        monitor._write_test_filename = lambda: fixed_name
        fifo_path = os.path.join(self.tmp_dir, fixed_name)
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
        fixed_name = ".write_test_fixed_for_test"
        monitor._write_test_filename = lambda: fixed_name
        fifo_path = os.path.join(self.tmp_dir, fixed_name)
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

    def test_final_timeout_logs_ping_and_mount_diagnostic(self):
        """リトライを使い切って異常確定する際、起床待ちか本当に無応答かを
        切り分けられるよう、ping/mountの結果がログに残ること。"""
        monitor = NasMonitor()
        monitor.mount_point = self.tmp_dir
        monitor.timeout = 1
        monitor.write_check_retries = 1
        fixed_name = ".write_test_fixed_for_test"
        monitor._write_test_filename = lambda: fixed_name
        fifo_path = os.path.join(self.tmp_dir, fixed_name)
        os.mkfifo(fifo_path)

        with patch.object(monitor, "check_ping", return_value=True) as mock_ping, \
             patch.object(monitor, "check_mount", return_value=True) as mock_mount, \
             self.assertLogs("nas_monitor", level="ERROR") as cm:
            result = monitor.check_write_permission()

        self.assertFalse(result)
        mock_ping.assert_called_once()
        mock_mount.assert_called_once()
        self.assertTrue(
            any("ping=True, mount=True" in message for message in cm.output),
            f"診断結果がログに含まれていない: {cm.output}"
        )


class TestNasMonitorWriteTestFilenameUniqueness(unittest.TestCase):
    """本番調査(2026-08-23)で判明した不具合の回帰テスト:
    固定ファイル名だと、タイムアウトでkillされた際の残留ファイル/CIFSハンドル
    不整合が原因で、以降のチェックが毎回同じ理由で失敗し続けるループに陥っていた。
    呼び出しごとに異なるファイル名を使うことでこれを回避する。"""

    def test_filename_differs_between_calls(self):
        monitor = NasMonitor()
        name1 = monitor._write_test_filename()
        name2 = monitor._write_test_filename()
        self.assertNotEqual(name1, name2)

    def test_write_permission_check_does_not_reuse_stale_file(self):
        tmp_dir = tempfile.mkdtemp()
        try:
            monitor = NasMonitor()
            monitor.mount_point = tmp_dir
            # 過去に(タイムアウトでkillされて)取り残された残留ファイルを再現
            stale_path = os.path.join(tmp_dir, ".write_test.99999.123")
            with open(stale_path, "w") as f:
                f.write("")

            self.assertTrue(monitor.check_write_permission())
            # 残留ファイルには触れず、そのまま残っているはず
            self.assertTrue(os.path.exists(stale_path))
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_each_retry_attempt_uses_a_different_filename(self):
        """リトライ機構(write_check_retries)と一意ファイル名は組み合わさって
        初めて効果がある。同一呼び出し内の各試行が同じファイル名を使い回すと、
        1回目のkillで残った不整合を2回目以降も踏み続け、結局リトライしても
        毎回失敗し続けてしまうため、試行ごとにファイル名が変わることを確認する。"""
        tmp_dir = tempfile.mkdtemp()
        try:
            monitor = NasMonitor()
            monitor.mount_point = tmp_dir
            monitor.timeout = 1
            monitor.write_check_retries = 3
            used_paths = []

            def spy_run(cmd, **kwargs):
                # 最終失敗時の診断用ping呼び出し等、書き込みテスト以外のsubprocess.run
                # 呼び出しも同じモックを通るため、書き込みテストの呼び出しだけを対象にする。
                if cmd[0] == sys.executable:
                    used_paths.append(cmd[-1])
                raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))

            with patch("monitors.nas_monitor.subprocess.run", side_effect=spy_run):
                monitor.check_write_permission()

            self.assertEqual(len(used_paths), monitor.write_check_retries)
            self.assertEqual(len(set(used_paths)), len(used_paths))
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


class TestNasMonitorSaveToDbWritesNasRecords:
    """Issue #168の回帰テスト: save_to_dbは以前device_recordsにしか書き込んで
    おらず、ダッシュボードのNASステータスカード(views/dashboard/summary.py)・
    NAS状態パネル(views/dashboard/log_tab.py)が読むconfig.SQLITE_TABLE_NAS
    (=nas_records)には何も書き込まれず、常に「データなし」表示のままだった。"""

    def test_healthy_state_is_recorded_in_nas_records(self, isolated_db):
        monitor = NasMonitor()
        usage = {"total_gb": 100.0, "used_gb": 40.0, "free_gb": 60.0, "percent": 40.0}

        monitor.save_to_db(ping_ok=True, mount_ok=True, usage=usage)

        with common.get_db_cursor() as cur:
            row = cur.execute(
                "SELECT * FROM nas_records ORDER BY id DESC LIMIT 1"
            ).fetchone()

        assert row is not None, "nas_recordsに何も書き込まれていない"
        # views/dashboard/summary.py・log_tab.pyはstatus_ping/status_mountを
        # 文字列 'OK' と直接比較するため、真偽値ではなくこの文字列である必要がある。
        assert row["status_ping"] == "OK"
        assert row["status_mount"] == "OK"
        assert row["total_gb"] == 100.0
        assert row["used_gb"] == 40.0
        assert row["free_gb"] == 60.0
        assert row["percent"] == 40.0

    def test_unhealthy_state_is_recorded_as_ng(self, isolated_db):
        monitor = NasMonitor()

        monitor.save_to_db(ping_ok=False, mount_ok=False, usage=None)

        with common.get_db_cursor() as cur:
            row = cur.execute(
                "SELECT * FROM nas_records ORDER BY id DESC LIMIT 1"
            ).fetchone()

        assert row is not None
        assert row["status_ping"] == "NG"
        assert row["status_mount"] == "NG"

    def test_device_records_write_is_unaffected(self, isolated_db):
        """既存のdevice_records書き込み(他のNAS使用率グラフ等が依存する可能性が
        あるため)は、nas_records書き込みの追加によって壊れていないこと。"""
        monitor = NasMonitor()
        usage = {"total_gb": 100.0, "used_gb": 40.0, "free_gb": 60.0, "percent": 40.0}

        monitor.save_to_db(ping_ok=True, mount_ok=True, usage=usage)

        with common.get_db_cursor() as cur:
            row = cur.execute(
                "SELECT * FROM device_records WHERE device_name='NAS_Monitor' ORDER BY id DESC LIMIT 1"
            ).fetchone()

        assert row is not None
        assert row["contact_state"] == "mounted"
        assert row["nas_usage_percent"] == 40.0


if __name__ == "__main__":
    unittest.main()
