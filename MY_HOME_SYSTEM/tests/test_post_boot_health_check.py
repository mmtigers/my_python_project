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
    def test_high_temp_is_err_not_warn(self, monkeypatch):
        """危険域(85℃以上)の温度でもWARNどまりだったが、ERRに昇格させる"""
        checker = PostBootHealthCheck()
        with patch.object(
            health_check_module.subprocess,
            "check_output",
            return_value=b"temp=87.0'C\n",
        ):
            with patch.object(health_check_module.shutil, "disk_usage", return_value=(100, 10, 90)):
                checker.check_system_resources()

        result = checker.results[-1]
        assert result.name == "System Resource"
        assert result.status == STATUS_ERR
        assert "87.0" in result.message

    def test_high_disk_usage_is_err_not_warn(self, monkeypatch):
        """危険域(95%超)のディスク使用率でもWARNどまりだったが、ERRに昇格させる"""
        checker = PostBootHealthCheck()
        with patch.object(
            health_check_module.subprocess,
            "check_output",
            return_value=b"temp=50.0'C\n",
        ):
            with patch.object(health_check_module.shutil, "disk_usage", return_value=(100, 96, 4)):
                checker.check_system_resources()

        result = checker.results[-1]
        assert result.status == STATUS_ERR
        assert "96.0" in result.message

    def test_normal_temp_and_disk_is_ok(self, monkeypatch):
        checker = PostBootHealthCheck()
        with patch.object(
            health_check_module.subprocess,
            "check_output",
            return_value=b"temp=50.0'C\n",
        ):
            with patch.object(health_check_module.shutil, "disk_usage", return_value=(100, 10, 90)):
                checker.check_system_resources()

        result = checker.results[-1]
        assert result.status == STATUS_OK


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

        with patch.object(health_check_module.time, "sleep"):
            with patch.object(checker, "_check_port", return_value=False):
                with patch.object(checker, "_check_http", return_value=False):
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
        assert (
            health_check_module.resolve_target_bluetooth_mac()
            == config.SPEAKER_BLUETOOTH_MAC
        )
        assert health_check_module.resolve_target_bluetooth_mac()

    def test_module_default_matches_current_config(self):
        """import時に解決されるTARGET_BLUETOOTH_MACは現在のconfigと整合する"""
        expected = (
            config.SPEAKER_BLUETOOTH_MAC if config.ENABLE_BLUETOOTH else None
        )
        assert health_check_module.TARGET_BLUETOOTH_MAC == expected
