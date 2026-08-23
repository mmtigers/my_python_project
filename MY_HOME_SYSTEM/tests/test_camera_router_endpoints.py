# MY_HOME_SYSTEM/tests/test_camera_router_endpoints.py
"""
routers/camera_router.py の成功パス(TestClient経由)。
既存 test_camera_router.py / test_camera_router_extra.py はパストラバーサル対策・
存在しないカメラの異常系を中心にカバーしているため、本ファイルは正常系を補う。
実際のffmpeg実行は行わず、services.camera_service の関数をモックする。
"""
import json
import os
import sys

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from routers import camera_router


@pytest.fixture
def one_camera(monkeypatch):
    cam = {"id": "cam1", "name": "玄関カメラ", "location": "玄関", "ip": "192.168.1.50"}
    monkeypatch.setattr(config, "CAMERAS", [cam])
    return cam


@pytest.fixture
def devices_json_camera(monkeypatch, tmp_path, one_camera):
    """devices.json 経由の永続化をテストするため、config.DEVICES_JSON_PATH を
    tmp_path 上のファイルに差し替え、one_camera と同じ内容を書き込んでおく。"""
    devices_path = tmp_path / "devices.json"
    devices_path.write_text(
        json.dumps({"cameras": [one_camera]}, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr(config, "DEVICES_JSON_PATH", str(devices_path))
    return devices_path


def test_get_camera_settings_lists_configured_cameras(api_client, one_camera):
    res = api_client.get("/api/cameras/settings")
    assert res.status_code == 200
    body = res.json()
    assert len(body) == 1
    assert body[0]["id"] == "cam1"
    assert body[0]["order"] == 1
    assert body[0]["enabled"] is True


def test_update_camera_settings_persists_enabled_to_devices_json(api_client, devices_json_camera):
    res = api_client.put("/api/cameras/settings/cam1", json={"enabled": False})
    assert res.status_code == 200
    assert res.json() == {"id": "cam1", "enabled": False}

    # config.CAMERAS (in-memory) にも反映されていること
    assert config.CAMERAS[0]["enabled"] is False

    # devices.json (ファイル) にも永続化されていること
    persisted = json.loads(devices_json_camera.read_text(encoding="utf-8"))
    assert persisted["cameras"][0]["enabled"] is False

    # GET /settings が更新後の値を反映すること
    res = api_client.get("/api/cameras/settings")
    assert res.json()[0]["enabled"] is False


def test_update_camera_settings_unknown_camera_returns_404(api_client, one_camera):
    res = api_client.put("/api/cameras/settings/unknown_cam", json={"enabled": False})
    assert res.status_code == 404


def test_get_live_stream_returns_playlist_once_ready(api_client, one_camera, tmp_path, monkeypatch):
    playlist = tmp_path / "stream.m3u8"
    playlist.write_text("#EXTM3U\n")

    monkeypatch.setattr(camera_router.camera_service, "start_hls_stream", lambda cam_conf: str(playlist))

    res = api_client.get("/api/cameras/live/cam1/stream.m3u8")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/vnd.apple.mpegurl"


def test_get_live_stream_unknown_camera_returns_404(api_client, one_camera):
    res = api_client.get("/api/cameras/live/unknown_cam/stream.m3u8")
    assert res.status_code == 404


def test_get_live_stream_failed_initialization_returns_500(api_client, one_camera, monkeypatch):
    monkeypatch.setattr(camera_router.camera_service, "start_hls_stream", lambda cam_conf: None)
    res = api_client.get("/api/cameras/live/cam1/stream.m3u8")
    assert res.status_code == 500


def test_get_live_stream_times_out_if_playlist_never_appears(api_client, one_camera, tmp_path, monkeypatch):
    never_created = tmp_path / "never.m3u8"
    monkeypatch.setattr(camera_router.camera_service, "start_hls_stream", lambda cam_conf: str(never_created))
    monkeypatch.setattr(camera_router.time, "sleep", lambda s: None)  # 実時間の待機をスキップ

    res = api_client.get("/api/cameras/live/cam1/stream.m3u8")
    assert res.status_code == 503


def test_get_record_info_returns_offset(api_client, one_camera, monkeypatch):
    monkeypatch.setattr(camera_router.camera_service, "get_record_start_offset", lambda cam_conf, date: 42)
    res = api_client.get("/api/cameras/record/cam1/2026-01-01/info")
    assert res.status_code == 200
    assert res.json()["offset_seconds"] == 42


def test_get_record_info_unknown_camera_returns_404(api_client, one_camera):
    res = api_client.get("/api/cameras/record/unknown_cam/2026-01-01/info")
    assert res.status_code == 404


def test_get_record_file_playlist_success(api_client, one_camera, tmp_path, monkeypatch):
    playlist = tmp_path / "record.m3u8"
    playlist.write_text("#EXTM3U\n")
    monkeypatch.setattr(
        camera_router.camera_service, "generate_record_playlist", lambda cam_conf, date: str(playlist)
    )
    res = api_client.get("/api/cameras/record/cam1/2026-01-01/record.m3u8")
    assert res.status_code == 200


def test_get_record_file_playlist_not_found_returns_404(api_client, one_camera, monkeypatch):
    monkeypatch.setattr(camera_router.camera_service, "generate_record_playlist", lambda cam_conf, date: None)
    res = api_client.get("/api/cameras/record/cam1/2026-01-01/record.m3u8")
    assert res.status_code == 404
