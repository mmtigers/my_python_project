# MY_HOME_SYSTEM/tests/test_post_boot_health_check.py
"""
post_boot_health_check.py の回帰テスト。

以前は以下の4項目が「異常があっても緑(OK)として報告されてしまう」バグを
抱えていた。各テストはその異常系を再現し、正しくWARN/ERRに倒れることを確認する。

- check_network_and_apis: SwitchBot/NatureRemoへのリクエストが未認証・
  ステータスコード未検証だったため、401等のAPI障害を検知できなかった
- check_recent_logs: tail実行が例外を出すと例外が握り潰され、
  ログを読めていないのに"Clean"として報告されていた
- check_peripherals (Cameras): config.CAMERASが空の場合に
  STATUS_OK "No Config"となり、devices.json読み込み失敗などが
  正常として埋もれてしまっていた
- TARGET_BLUETOOTH_MAC: ハードコードされたNoneによりBluetooth確認ロジックが
  デッドコード化し、無関係なオンボード音声デバイスの存在だけで緑になっていた
"""
import os
import subprocess
import sys
import time
from unittest.mock import MagicMock, patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
import post_boot_health_check as health_check_module
from post_boot_health_check import PostBootHealthCheck, STATUS_ERR, STATUS_OK, STATUS_WARN


class TestCheckNetworkAndApis:
    def test_switchbot_401_is_reported_as_ng_not_connected(self, monkeypatch):
        """SwitchBot APIキー失効/未設定(401)でも従来は緑になっていた"""
        monkeypatch.setattr(config, "SWITCHBOT_API_TOKEN", None)
        monkeypatch.setattr(config, "SWITCHBOT_API_SECRET", None)
        monkeypatch.setattr(config, "NATURE_REMO_ACCESS_TOKEN", "dummy-token")

        def fake_get(url, headers=None, timeout=5):
            res = MagicMock()
            res.status_code = 401 if "switch-bot" in url else 200
            return res

        checker = PostBootHealthCheck()
        with patch.object(health_check_module.subprocess, "check_call"):
            with patch.object(health_check_module.requests, "get", side_effect=fake_get):
                checker.check_network_and_apis()

        result = checker.results[-1]
        assert result.name == "Network & API"
        assert result.status == STATUS_WARN
        assert "SwitchBot" in result.message

    def test_all_apis_reachable_and_authenticated_is_ok(self, monkeypatch):
        monkeypatch.setattr(config, "NATURE_REMO_ACCESS_TOKEN", "dummy-token")

        def fake_get(url, headers=None, timeout=5):
            res = MagicMock()
            res.status_code = 200
            return res

        checker = PostBootHealthCheck()
        with patch.object(health_check_module.subprocess, "check_call"):
            with patch.object(health_check_module.requests, "get", side_effect=fake_get):
                checker.check_network_and_apis()

        result = checker.results[-1]
        assert result.status == STATUS_OK
        assert result.message == "All Connected"

    def test_nature_remo_check_is_skipped_when_token_not_configured(self, monkeypatch):
        """#411 品質: NATURE_REMO_ACCESS_TOKEN未設定時、以前は"Bearer None"という
        実在しないトークンをそのまま送信し「未設定」ではなく「API NG」と誤報告していた。
        未設定ならチェック自体をスキップし、他のAPIが正常ならOKになることを確認する。"""
        monkeypatch.setattr(config, "NATURE_REMO_ACCESS_TOKEN", None)
        called_urls = []

        def fake_get(url, headers=None, timeout=5):
            called_urls.append((url, headers))
            res = MagicMock()
            res.status_code = 200
            return res

        checker = PostBootHealthCheck()
        with patch.object(health_check_module.subprocess, "check_call"):
            with patch.object(health_check_module.requests, "get", side_effect=fake_get):
                checker.check_network_and_apis()

        assert not any("nature.global" in url for url, _ in called_urls)
        result = checker.results[-1]
        assert result.status == STATUS_OK
        assert result.message == "All Connected"


class TestCheckRecentLogs:
    def test_tail_failure_is_warn_not_silently_clean(self, tmp_path):
        """tailの実行失敗は以前は握り潰されOK "Clean"扱いになっていた"""
        checker = PostBootHealthCheck()
        checker.log_file_path = str(tmp_path / "home_system.log")
        (tmp_path / "home_system.log").write_text("dummy")

        with patch.object(
            health_check_module.subprocess,
            "check_output",
            side_effect=subprocess.CalledProcessError(1, "tail"),
        ):
            checker.check_recent_logs()

        result = checker.results[-1]
        assert result.name == "Logs"
        assert result.status == STATUS_WARN
        assert "Check Failed" in result.message

    def test_no_recent_errors_is_clean(self, tmp_path):
        log_file = tmp_path / "home_system.log"
        log_file.write_text("2026-01-01 00:00:00 [INFO] all good\n")

        checker = PostBootHealthCheck()
        checker.log_file_path = str(log_file)
        checker.check_recent_logs()

        result = checker.results[-1]
        assert result.status == STATUS_OK
        assert result.message == "Clean (Last 10min)"


class TestCheckPeripheralsCameras:
    def test_empty_camera_config_is_warn_not_ok(self, monkeypatch, tmp_path):
        """devices.json読込失敗等でCAMERASが空でも以前はOK "No Config"だった"""
        monkeypatch.setattr(config, "CAMERAS", [])
        monkeypatch.setattr(config, "NAS_MOUNT_POINT", str(tmp_path / "no_such_mount"))

        checker = PostBootHealthCheck()
        with patch.object(health_check_module.subprocess, "check_output", side_effect=Exception("no aplay")):
            checker.check_peripherals()

        cam_result = next(r for r in checker.results if r.name == "Cameras")
        assert cam_result.status == STATUS_WARN
        assert cam_result.message == "No Config"


class TestCheckSystemResources:
    def test_high_temp_is_err_not_warn(self, monkeypatch, tmp_path):
        """危険域(85℃以上)の温度でもWARNどまりだったが、ERRに昇格させる"""
        checker = PostBootHealthCheck()
        temp_file = tmp_path / "temp"
        temp_file.write_text("87000\n")
        with patch.object(health_check_module, "CPU_TEMP_FILE", str(temp_file)):
            with patch.object(health_check_module.shutil, "disk_usage", return_value=(100, 10, 90)):
                checker.check_system_resources()

        result = checker.results[-1]
        assert result.name == "System Resource"
        assert result.status == STATUS_ERR
        assert "87.0" in result.message

    def test_high_disk_usage_is_err_not_warn(self, monkeypatch, tmp_path):
        """危険域(95%超)のディスク使用率でもWARNどまりだったが、ERRに昇格させる"""
        checker = PostBootHealthCheck()
        temp_file = tmp_path / "temp"
        temp_file.write_text("50000\n")
        with patch.object(health_check_module, "CPU_TEMP_FILE", str(temp_file)):
            with patch.object(health_check_module.shutil, "disk_usage", return_value=(100, 96, 4)):
                checker.check_system_resources()

        result = checker.results[-1]
        assert result.status == STATUS_ERR
        assert "96.0" in result.message

    def test_normal_temp_and_disk_is_ok(self, monkeypatch, tmp_path):
        checker = PostBootHealthCheck()
        temp_file = tmp_path / "temp"
        temp_file.write_text("50000\n")
        with patch.object(health_check_module, "CPU_TEMP_FILE", str(temp_file)):
            with patch.object(health_check_module.shutil, "disk_usage", return_value=(100, 10, 90)):
                checker.check_system_resources()

        result = checker.results[-1]
        assert result.status == STATUS_OK

    def test_unreadable_temp_is_warn_unknown(self, tmp_path):
        """温度が読めない環境では WARN の Unknown に倒す(チェック全体は止めない)"""
        checker = PostBootHealthCheck()
        with patch.object(health_check_module, "CPU_TEMP_FILE", str(tmp_path / "missing")):
            with patch.object(health_check_module.shutil, "disk_usage", return_value=(100, 10, 90)):
                checker.check_system_resources()

        result = checker.results[-1]
        assert result.status == STATUS_WARN
        assert "Unknown" in result.message


class TestCheckServicesParallelism:
    def test_service_checks_run_in_parallel_not_sequentially(self):
        """check_servicesはBackend/Family Quest/Dashboardを順番にリトライして
        おり、全滅時は最悪ケースで(サービス数)x(1サービスの最大待ち時間)だけ
        通知が遅延しえた(実運用値では最大6分)。並列化により、遅延を単一サービス
        のリトライ時間程度に抑える。
        """
        checker = PostBootHealthCheck()
        checker.max_retries = 3
        checker.retry_interval = 0.1

        with patch.object(checker, "_check_port", return_value=False):
            with patch.object(checker, "_check_http", return_value=False):
                start = time.monotonic()
                checker.check_services()
                elapsed = time.monotonic() - start

        single_target_worst_case = checker.max_retries * checker.retry_interval
        assert elapsed < single_target_worst_case * 2

        names = {r.name for r in checker.results}
        assert names == {"Backend Server", "Family Quest", "Dashboard"}
        assert all(r.status == STATUS_ERR for r in checker.results)


class TestCheckServicesDashboard:
    def test_dashboard_down_is_err_not_warn(self, monkeypatch):
        """Dashboardは以前はcritical=FalseでWARNどまりだったが、ERRに昇格させる"""
        checker = PostBootHealthCheck()
        checker.max_retries = 1
        checker.retry_interval = 0

        with patch.object(health_check_module.time, "sleep"), \
                patch.object(checker, "_check_port", return_value=False), \
                patch.object(checker, "_check_http", return_value=False):
            checker.check_services()

        dashboard_result = next(r for r in checker.results if r.name == "Dashboard")
        assert dashboard_result.status == STATUS_ERR


class TestTargetBluetoothMac:
    def test_none_when_bluetooth_disabled(self, monkeypatch):
        """ENABLE_BLUETOOTH=Falseの間はBTチェックをスキップする(bluetooth.service停止
        環境で毎回BT WARNが出るノイズを防ぐ)"""
        monkeypatch.setattr(config, "ENABLE_BLUETOOTH", False, raising=False)
        assert health_check_module.resolve_target_bluetooth_mac() is None

    def test_uses_configured_speaker_mac_when_enabled(self, monkeypatch):
        """再有効化時は設定済みMACでBT接続を確認する"""
        monkeypatch.setattr(config, "ENABLE_BLUETOOTH", True, raising=False)
        monkeypatch.setattr(config, "SPEAKER_BLUETOOTH_MAC", "AA:BB:CC:DD:EE:FF", raising=False)
        assert health_check_module.resolve_target_bluetooth_mac() == "AA:BB:CC:DD:EE:FF"

    def test_returns_none_when_speaker_mac_is_empty(self, monkeypatch):
        """#665: SPEAKER_BLUETOOTH_MAC 未設定(既定の空文字)ならBT有効でもNone(チェックをスキップ)"""
        monkeypatch.setattr(config, "ENABLE_BLUETOOTH", True, raising=False)
        monkeypatch.setattr(config, "SPEAKER_BLUETOOTH_MAC", "", raising=False)
        assert health_check_module.resolve_target_bluetooth_mac() is None

    def test_module_default_matches_current_config(self):
        """import時に解決されるTARGET_BLUETOOTH_MACは現在のconfigと整合する"""
        expected = (
            config.SPEAKER_BLUETOOTH_MAC if config.ENABLE_BLUETOOTH else None
        )
        assert health_check_module.TARGET_BLUETOOTH_MAC == expected


# ---------------------------------------------------------------------------
# Issue #758 (AUDIT-029): 各チェックの正常・異常経路。
#
# 「起動後の健全性確認そのもの」が52%しかカバーされておらず、チェックが誤判定
# しても気づけない状態だった。ユーティリティ(ポート/HTTP/uptime)・DB整合性・
# 周辺機器(NAS/カメラ/スピーカー)・ログ解析・レポート送信の各分岐を埋める。
# ---------------------------------------------------------------------------
import pytest
from freezegun import freeze_time


class TestUtilityHelpers:
    def test_check_port_true_when_connection_succeeds(self, monkeypatch):
        class _Sock:
            def __enter__(self): return self
            def __exit__(self, *exc): return False

        monkeypatch.setattr(health_check_module.socket, "create_connection", lambda addr, timeout=3: _Sock())
        assert PostBootHealthCheck()._check_port("localhost", 8000) is True

    @pytest.mark.parametrize("exc", [TimeoutError(), ConnectionRefusedError(), OSError()])
    def test_check_port_false_on_connection_errors(self, monkeypatch, exc):
        def _boom(addr, timeout=3):
            raise exc

        monkeypatch.setattr(health_check_module.socket, "create_connection", _boom)
        assert PostBootHealthCheck()._check_port("localhost", 8000) is False

    def test_check_http_false_on_server_error_status(self, monkeypatch):
        res = MagicMock()
        res.status_code = 500
        monkeypatch.setattr(health_check_module.requests, "get", lambda *a, **k: res)
        assert PostBootHealthCheck()._check_http("http://x") is False

    def test_check_http_false_when_request_raises(self, monkeypatch):
        def _boom(*args, **kwargs):
            raise RuntimeError("connection reset")

        monkeypatch.setattr(health_check_module.requests, "get", _boom)
        assert PostBootHealthCheck()._check_http("http://x") is False

    @pytest.mark.parametrize(
        "uptime_seconds,expected",
        [("45.0 0.0", "45秒"), ("300.0 0.0", "5分"), ("7380.0 0.0", "2時間3分")],
    )
    def test_get_uptime_formats(self, monkeypatch, tmp_path, uptime_seconds, expected):
        proc_uptime = tmp_path / "uptime"
        proc_uptime.write_text(uptime_seconds, encoding="utf-8")
        real_open = open
        monkeypatch.setattr(
            "builtins.open",
            lambda path, *a, **k: real_open(proc_uptime if path == "/proc/uptime" else path, *a, **k),
        )
        assert PostBootHealthCheck()._get_uptime() == expected

    def test_get_uptime_unknown_when_proc_is_unreadable(self, monkeypatch):
        def _boom(*args, **kwargs):
            raise OSError("no /proc")

        monkeypatch.setattr("builtins.open", _boom)
        assert PostBootHealthCheck()._get_uptime() == "不明"


class TestCheckNetworkOffline:
    def test_ping_failure_short_circuits_to_err(self, monkeypatch):
        def _boom(*args, **kwargs):
            raise subprocess.CalledProcessError(1, "ping")

        monkeypatch.setattr(health_check_module.subprocess, "check_call", _boom)
        called = []
        monkeypatch.setattr(health_check_module.requests, "get", lambda *a, **k: called.append(1))

        checker = PostBootHealthCheck()
        checker.check_network_and_apis()

        assert checker.results[-1].status == STATUS_ERR
        assert checker.results[-1].message == "Offline (Ping NG)"
        assert called == [], "Ping NG の時点で API チェックまで進まないこと"


class TestCheckDatabase:
    def test_missing_db_file_is_err(self, monkeypatch, tmp_path):
        monkeypatch.setattr(config, "SQLITE_DB_PATH", str(tmp_path / "missing.db"))
        checker = PostBootHealthCheck()
        checker.check_database()
        assert checker.results[-1].status == STATUS_ERR
        assert checker.results[-1].message == "File Not Found"

    def test_healthy_db_is_ok(self, isolated_db):
        checker = PostBootHealthCheck()
        checker.check_database()
        assert checker.results[-1].status == STATUS_OK
        assert checker.results[-1].message == "Integrity OK"

    def test_quick_check_reporting_corruption_is_err(self, isolated_db, monkeypatch):
        class _Cursor:
            def execute(self, sql): return self
            def fetchone(self): return ("*** in database main ***",)

        class _Conn:
            def __enter__(self): return self
            def __exit__(self, *exc): return False
            def cursor(self): return _Cursor()

        monkeypatch.setattr(health_check_module, "get_ro_connection", lambda db_path=None: _Conn())
        checker = PostBootHealthCheck()
        checker.check_database()
        assert checker.results[-1].status == STATUS_ERR
        assert "Corrupt" in checker.results[-1].message

    def test_connection_error_is_err_not_silently_ok(self, isolated_db, monkeypatch):
        def _boom(db_path=None):
            raise RuntimeError("database is locked")

        monkeypatch.setattr(health_check_module, "get_ro_connection", _boom)
        checker = PostBootHealthCheck()
        checker.check_database()
        assert checker.results[-1].status == STATUS_ERR
        assert "database is locked" in checker.results[-1].message


class TestCheckPeripheralsNas:
    @pytest.fixture(autouse=True)
    def _no_notifications(self, monkeypatch):
        self.sent = []
        monkeypatch.setattr(
            health_check_module, "send_push",
            lambda messages, target=None, channel=None: self.sent.append(messages[0]["text"]),
        )
        monkeypatch.setattr(config, "CAMERAS", [])
        monkeypatch.setattr(
            health_check_module.subprocess, "check_output", lambda *a, **k: b"card 0: bcm2835\n"
        )

    def test_unmounted_nas_is_err(self, monkeypatch, tmp_path):
        monkeypatch.setattr(config, "NAS_MOUNT_POINT", str(tmp_path / "nas"))
        monkeypatch.setattr(health_check_module.os.path, "ismount", lambda p: False)

        checker = PostBootHealthCheck()
        checker.check_peripherals()

        nas = next(r for r in checker.results if r.name == "NAS")
        assert (nas.status, nas.message) == (STATUS_ERR, "Disconnected")
        assert self.sent == []

    def test_mounted_and_writable_is_ok_and_cleans_up_the_probe_file(self, monkeypatch, tmp_path):
        mount = tmp_path / "nas"
        mount.mkdir()
        monkeypatch.setattr(config, "NAS_MOUNT_POINT", str(mount))
        monkeypatch.setattr(config, "NAS_IP", "192.168.0.10", raising=False)
        monkeypatch.setattr(health_check_module.os.path, "ismount", lambda p: True)

        checker = PostBootHealthCheck()
        checker.check_peripherals()

        nas = next(r for r in checker.results if r.name == "NAS")
        assert nas.status == STATUS_OK
        assert "192.168.0.10" in nas.message
        assert list(mount.iterdir()) == [], "書き込みテスト用ファイルが残っていない"

    def test_permission_denied_is_err_and_notifies(self, monkeypatch, tmp_path):
        mount = tmp_path / "nas"
        mount.mkdir()
        monkeypatch.setattr(config, "NAS_MOUNT_POINT", str(mount))
        monkeypatch.setattr(health_check_module.os.path, "ismount", lambda p: True)

        real_open = open

        def _deny(path, *args, **kwargs):
            if str(path).endswith(".health_check_rw"):
                raise PermissionError("read-only")
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr("builtins.open", _deny)

        checker = PostBootHealthCheck()
        checker.check_peripherals()

        nas = next(r for r in checker.results if r.name == "NAS")
        assert (nas.status, nas.message) == (STATUS_ERR, "Permission Denied")
        assert len(self.sent) == 1
        assert "NAS権限エラー" in self.sent[0]


class TestCheckPeripheralsCamerasAndSpeaker:
    @pytest.fixture(autouse=True)
    def _base(self, monkeypatch, tmp_path):
        monkeypatch.setattr(config, "NAS_MOUNT_POINT", str(tmp_path / "nas"))
        monkeypatch.setattr(health_check_module.os.path, "ismount", lambda p: False)
        monkeypatch.setattr(health_check_module, "send_push", lambda **kwargs: None)

    def _cameras(self, monkeypatch, reachable: dict):
        monkeypatch.setattr(config, "CAMERAS", [{"ip": ip} for ip in reachable])
        checker = PostBootHealthCheck()
        monkeypatch.setattr(
            checker, "_check_port", lambda host, port, timeout=2: reachable.get(host, False)
        )
        return checker

    def test_all_cameras_online_is_ok(self, monkeypatch):
        checker = self._cameras(monkeypatch, {"10.0.0.1": True, "10.0.0.2": True})
        with patch.object(health_check_module.subprocess, "check_output", return_value=b"card 0\n"):
            checker.check_peripherals()
        cam = next(r for r in checker.results if r.name == "Cameras")
        assert (cam.status, cam.message) == (STATUS_OK, "2/2 Online")

    def test_partially_reachable_cameras_is_warn(self, monkeypatch):
        checker = self._cameras(monkeypatch, {"10.0.0.1": True, "10.0.0.2": False})
        with patch.object(health_check_module.subprocess, "check_output", return_value=b"card 0\n"):
            checker.check_peripherals()
        cam = next(r for r in checker.results if r.name == "Cameras")
        assert (cam.status, cam.message) == (STATUS_WARN, "1/2 Online")

    def test_all_cameras_offline_is_err(self, monkeypatch):
        checker = self._cameras(monkeypatch, {"10.0.0.1": False})
        with patch.object(health_check_module.subprocess, "check_output", return_value=b"card 0\n"):
            checker.check_peripherals()
        cam = next(r for r in checker.results if r.name == "Cameras")
        assert (cam.status, cam.message) == (STATUS_ERR, "0/1 Online")

    def test_speaker_falls_back_to_sound_card_when_bt_disabled(self, monkeypatch):
        monkeypatch.setattr(health_check_module, "TARGET_BLUETOOTH_MAC", None)
        checker = self._cameras(monkeypatch, {})
        with patch.object(health_check_module.subprocess, "check_output", return_value=b"card 0: bcm2835\n"):
            checker.check_peripherals()
        spk = next(r for r in checker.results if r.name == "Speaker")
        assert (spk.status, spk.message) == (STATUS_OK, "Sound Card OK")

    def test_speaker_warns_when_no_sound_card_and_no_bt(self, monkeypatch):
        monkeypatch.setattr(health_check_module, "TARGET_BLUETOOTH_MAC", None)
        checker = self._cameras(monkeypatch, {})
        with patch.object(health_check_module.subprocess, "check_output", side_effect=Exception("no aplay")):
            checker.check_peripherals()
        spk = next(r for r in checker.results if r.name == "Speaker")
        assert (spk.status, spk.message) == (STATUS_WARN, "No Device")

    def test_speaker_connected_over_bluetooth(self, monkeypatch):
        monkeypatch.setattr(health_check_module, "TARGET_BLUETOOTH_MAC", "AA:BB:CC:DD:EE:FF")
        checker = self._cameras(monkeypatch, {})
        with patch.object(
            health_check_module.subprocess, "check_output",
            side_effect=[b"card 0\n", b"Connected: yes\n"],
        ):
            checker.check_peripherals()
        spk = next(r for r in checker.results if r.name == "Speaker")
        assert (spk.status, spk.message) == (STATUS_OK, "Connected (BT)")

    def test_speaker_disconnected_over_bluetooth_is_warn(self, monkeypatch):
        monkeypatch.setattr(health_check_module, "TARGET_BLUETOOTH_MAC", "AA:BB:CC:DD:EE:FF")
        checker = self._cameras(monkeypatch, {})
        with patch.object(
            health_check_module.subprocess, "check_output",
            side_effect=[b"card 0\n", b"Connected: no\n"],
        ):
            checker.check_peripherals()
        spk = next(r for r in checker.results if r.name == "Speaker")
        assert (spk.status, spk.message) == (STATUS_WARN, "Disconnected (BT)")

    def test_bluetoothctl_timeout_is_bt_error(self, monkeypatch):
        """bluetoothctl は Bluetooth デーモン不調時に応答を返さないことがあるため、
        timeout 超過は WARN(BT Error)として扱う。"""
        monkeypatch.setattr(health_check_module, "TARGET_BLUETOOTH_MAC", "AA:BB:CC:DD:EE:FF")
        checker = self._cameras(monkeypatch, {})
        with patch.object(
            health_check_module.subprocess, "check_output",
            side_effect=[b"card 0\n", subprocess.TimeoutExpired(cmd="bluetoothctl", timeout=15)],
        ):
            checker.check_peripherals()
        spk = next(r for r in checker.results if r.name == "Speaker")
        assert (spk.status, spk.message) == (STATUS_WARN, "BT Error")


class TestCheckRecentLogsFiltering:
    def test_missing_log_file_is_warn(self, tmp_path):
        checker = PostBootHealthCheck()
        checker.log_file_path = str(tmp_path / "nope.log")
        checker.check_recent_logs()
        assert (checker.results[-1].status, checker.results[-1].message) == (STATUS_WARN, "No log file yet")

    @freeze_time("2026-09-19 12:00:00")
    def test_recent_errors_are_counted_and_the_last_two_are_shown(self, tmp_path, monkeypatch):
        log_file = tmp_path / "home_system.log"
        log_file.write_text("dummy", encoding="utf-8")
        lines = "\n".join(
            f"2026-09-19 11:55:00 [ERROR] failure number {i}" for i in range(3)
        )
        monkeypatch.setattr(
            health_check_module.subprocess, "check_output", lambda *a, **k: lines.encode()
        )

        checker = PostBootHealthCheck()
        checker.log_file_path = str(log_file)
        checker.check_recent_logs()

        result = checker.results[-1]
        assert result.status == STATUS_WARN
        assert "3 Errors in last 10min" in result.message
        assert "failure number 2" in result.message
        assert "failure number 0" not in result.message

    @freeze_time("2026-09-19 12:00:00")
    def test_errors_older_than_ten_minutes_are_ignored(self, tmp_path, monkeypatch):
        log_file = tmp_path / "home_system.log"
        log_file.write_text("dummy", encoding="utf-8")
        monkeypatch.setattr(
            health_check_module.subprocess, "check_output",
            lambda *a, **k: b"2026-09-19 11:30:00 [ERROR] stale failure",
        )

        checker = PostBootHealthCheck()
        checker.log_file_path = str(log_file)
        checker.check_recent_logs()

        assert checker.results[-1].status == STATUS_OK

    def test_lines_without_a_parsable_timestamp_are_skipped(self, tmp_path, monkeypatch):
        """ノイズ低減のため、日付をパースできない ERROR 行は数えない。"""
        log_file = tmp_path / "home_system.log"
        log_file.write_text("dummy", encoding="utf-8")
        monkeypatch.setattr(
            health_check_module.subprocess, "check_output",
            lambda *a, **k: b"Traceback ERROR without timestamp",
        )

        checker = PostBootHealthCheck()
        checker.log_file_path = str(log_file)
        checker.check_recent_logs()

        assert checker.results[-1].status == STATUS_OK


class TestSendReport:
    def _report(self, monkeypatch, statuses):
        sent = []
        monkeypatch.setattr(
            health_check_module, "send_push",
            lambda messages, target=None, channel=None: sent.append(messages[0]["text"]),
        )
        checker = PostBootHealthCheck()
        monkeypatch.setattr(checker, "_get_uptime", lambda: "1時間0分")
        checker.results = [
            health_check_module.CheckResult(f"check{i}", status, "msg") for i, status in enumerate(statuses)
        ]
        checker._send_report()
        return sent[0]

    def test_all_ok_is_green(self, monkeypatch):
        assert self._report(monkeypatch, [STATUS_OK, STATUS_OK]).startswith("🟢")

    def test_any_warn_is_yellow(self, monkeypatch):
        assert self._report(monkeypatch, [STATUS_OK, STATUS_WARN]).startswith("🟡")

    def test_any_err_is_red_even_with_warns(self, monkeypatch):
        body = self._report(monkeypatch, [STATUS_WARN, STATUS_ERR])
        assert body.startswith("🔴")
        assert "🔴 **check1**: msg" in body


class TestRun:
    def test_run_executes_every_check_and_sends_one_report(self, monkeypatch):
        """run() が全チェックを呼び、最後にレポートを1回だけ送ること。"""
        checker = PostBootHealthCheck()
        called = []
        for name in (
            "check_network_and_apis", "check_system_resources", "check_database",
            "check_peripherals", "check_services", "check_recent_logs", "_send_report",
        ):
            monkeypatch.setattr(checker, name, lambda _n=name: called.append(_n))

        checker.run()

        assert called == [
            "check_network_and_apis", "check_system_resources", "check_database",
            "check_peripherals", "check_services", "check_recent_logs", "_send_report",
        ]
