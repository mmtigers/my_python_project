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
from unittest.mock import MagicMock, patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
import post_boot_health_check as health_check_module
from post_boot_health_check import PostBootHealthCheck, STATUS_OK, STATUS_WARN


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


class TestTargetBluetoothMac:
    def test_defaults_to_configured_speaker_mac_not_none(self):
        """以前はNoneがハードコードされ、BT確認ロジックが常にデッドコードだった"""
        assert health_check_module.TARGET_BLUETOOTH_MAC == config.SPEAKER_BLUETOOTH_MAC
        assert health_check_module.TARGET_BLUETOOTH_MAC
