# MY_HOME_SYSTEM/tests/test_camera_monitor_enabled_reload.py
"""
Issue #652: devices.json の enabled 変更が camera_monitor(別プロセス)に伝播することのテスト。

`services/camera_service.set_camera_enabled` は devices.json を書き換えて
サーバープロセス内の `config.CAMERAS` を更新するが、`monitors/camera_monitor.py` は
`unified_server.py` から別プロセスとして起動され、起動時の `config.CAMERAS` を
保持し続ける。以前は UI でカメラを無効化してもサーバー再起動まで ONVIF 購読・
スナップショット・通知が続いていた。
"""
import json
import os
import sys

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from monitors import camera_monitor


class _StopLoop(Exception):
    """monitor_single_camera の無限ループをテストで打ち切るための番兵例外。"""


@pytest.fixture(autouse=True)
def _reset_enabled_cache(monkeypatch):
    """モジュールグローバルの mtime キャッシュをテストごとに初期化する。"""
    monkeypatch.setattr(
        camera_monitor,
        "_enabled_cache",
        {"mtime_ns": None, "checked_at": 0.0, "flags": None},
        raising=False,
    )
    # mtime の stat 間隔(既定5秒)をテストでは毎回確認させる
    monkeypatch.setattr(camera_monitor, "ENABLED_RECHECK_INTERVAL_SEC", 0.0, raising=False)


def _write_devices_json(path, cameras):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"cameras": cameras}, f, ensure_ascii=False)


class TestIsCameraEnabled:
    def test_reflects_devices_json_update_without_restart(self, tmp_path, monkeypatch):
        """devices.json の enabled を false にすると、再起動なしで False を返す。"""
        devices_path = tmp_path / "devices.json"
        _write_devices_json(devices_path, [{"id": "cam1", "name": "玄関カメラ", "enabled": True}])
        monkeypatch.setattr(camera_monitor.config, "DEVICES_JSON_PATH", str(devices_path), raising=False)

        cam_conf = {"id": "cam1", "name": "玄関カメラ", "enabled": True}
        assert camera_monitor.is_camera_enabled(cam_conf) is True

        # set_camera_enabled 相当の書き換え(mtime が変わる)
        _write_devices_json(devices_path, [{"id": "cam1", "name": "玄関カメラ", "enabled": False}])
        os.utime(devices_path, ns=(1, 2))  # mtime を確実に変える
        assert camera_monitor.is_camera_enabled(cam_conf) is False

        # 再有効化も同様に伝播する
        _write_devices_json(devices_path, [{"id": "cam1", "name": "玄関カメラ", "enabled": True}])
        os.utime(devices_path, ns=(3, 4))
        assert camera_monitor.is_camera_enabled(cam_conf) is True

    def test_falls_back_to_startup_value_when_devices_json_missing(self, tmp_path, monkeypatch):
        """devices.json が無い環境では起動時の cam_conf の値に倒す。"""
        monkeypatch.setattr(
            camera_monitor.config, "DEVICES_JSON_PATH", str(tmp_path / "absent.json"), raising=False
        )
        assert camera_monitor.is_camera_enabled({"id": "cam1", "enabled": True}) is True
        assert camera_monitor.is_camera_enabled({"id": "cam1", "enabled": False}) is False

    def test_falls_back_when_devices_json_is_corrupt(self, tmp_path, monkeypatch):
        """壊れた devices.json でも例外を投げず、起動時の値に倒す。"""
        devices_path = tmp_path / "devices.json"
        devices_path.write_text("{ broken json", encoding="utf-8")
        monkeypatch.setattr(camera_monitor.config, "DEVICES_JSON_PATH", str(devices_path), raising=False)
        assert camera_monitor.is_camera_enabled({"id": "cam1", "enabled": True}) is True

    def test_unknown_camera_id_falls_back(self, tmp_path, monkeypatch):
        """devices.json に該当 id が無い場合も起動時の値に倒す。"""
        devices_path = tmp_path / "devices.json"
        _write_devices_json(devices_path, [{"id": "other", "enabled": False}])
        monkeypatch.setattr(camera_monitor.config, "DEVICES_JSON_PATH", str(devices_path), raising=False)
        assert camera_monitor.is_camera_enabled({"id": "cam1", "enabled": True}) is True


class TestMonitorSingleCameraSkipsDisabledCamera:
    def test_does_not_connect_while_disabled(self, tmp_path, monkeypatch):
        """enabled=false の間は到達性チェックにもONVIF接続にも進まず待機する。"""
        devices_path = tmp_path / "devices.json"
        _write_devices_json(devices_path, [{"id": "cam1", "enabled": False}])
        monkeypatch.setattr(camera_monitor.config, "DEVICES_JSON_PATH", str(devices_path), raising=False)

        def _fail_if_called(*args, **kwargs):
            raise AssertionError("無効化されたカメラで is_host_reachable が呼ばれた")

        monkeypatch.setattr(camera_monitor, "is_host_reachable", _fail_if_called)

        slept = []

        def _fake_sleep(seconds):
            slept.append(seconds)
            raise _StopLoop()

        monkeypatch.setattr(camera_monitor.time, "sleep", _fake_sleep)

        cam_conf = {"id": "cam1", "name": "テストカメラ", "ip": "192.0.2.10", "user": "u", "pass": "p", "enabled": True}
        with pytest.raises(_StopLoop):
            camera_monitor.monitor_single_camera(cam_conf)

        assert slept == [camera_monitor.DISABLED_POLL_INTERVAL_SEC]

    def test_proceeds_to_reachability_check_when_enabled(self, tmp_path, monkeypatch):
        """enabled=true なら従来どおり到達性チェックへ進む。"""
        devices_path = tmp_path / "devices.json"
        _write_devices_json(devices_path, [{"id": "cam1", "enabled": True}])
        monkeypatch.setattr(camera_monitor.config, "DEVICES_JSON_PATH", str(devices_path), raising=False)

        called = []

        def _reachable(ip):
            called.append(ip)
            raise _StopLoop()

        monkeypatch.setattr(camera_monitor, "is_host_reachable", _reachable)

        cam_conf = {"id": "cam1", "name": "テストカメラ", "ip": "192.0.2.10", "user": "u", "pass": "p", "enabled": True}
        with pytest.raises(_StopLoop):
            camera_monitor.monitor_single_camera(cam_conf)
        assert called == ["192.0.2.10"]
