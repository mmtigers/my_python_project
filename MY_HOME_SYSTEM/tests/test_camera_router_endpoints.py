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

    async def _no_wait(seconds):
        return None

    # #457: get_live_streamはtime.sleepからasyncio.sleepに変更されたため、
    # 実時間の待機をスキップするパッチ対象もそれに合わせる。
    monkeypatch.setattr(camera_router.asyncio, "sleep", _no_wait)

    res = api_client.get("/api/cameras/live/cam1/stream.m3u8")
    assert res.status_code == 503


def test_get_record_info_returns_offset_and_timeline(api_client, one_camera, monkeypatch):
    monkeypatch.setattr(camera_router.camera_service, "get_record_start_offset", lambda cam_conf, date: 42)
    timeline = [{"start_seconds": 42, "position": 0.0, "duration": 600.0}]
    monkeypatch.setattr(camera_router.record_playlist_service, "get_record_timeline", lambda cam_conf, date: timeline)
    res = api_client.get("/api/cameras/record/cam1/20260101/info")
    assert res.status_code == 200
    assert res.json() == {"offset_seconds": 42, "files": timeline}


def test_get_record_info_unknown_camera_returns_404(api_client, one_camera):
    res = api_client.get("/api/cameras/record/unknown_cam/20260101/info")
    assert res.status_code == 404


def test_get_record_file_playlist_success(api_client, one_camera, monkeypatch):
    monkeypatch.setattr(
        camera_router.record_playlist_service, "get_record_playlist", lambda cam_conf, date: "#EXTM3U\n"
    )
    res = api_client.get("/api/cameras/record/cam1/20260101/record_20260101.m3u8")
    assert res.status_code == 200
    assert res.text == "#EXTM3U\n"
    assert res.headers["content-type"].startswith("application/vnd.apple.mpegurl")
    assert res.headers["cache-control"] == "no-cache"


def test_get_record_file_playlist_not_found_returns_404(api_client, one_camera, monkeypatch):
    monkeypatch.setattr(camera_router.record_playlist_service, "get_record_playlist", lambda cam_conf, date: None)
    res = api_client.get("/api/cameras/record/cam1/20260101/record.m3u8")
    assert res.status_code == 404


# Issue #1091: 録画 MP4 はプレイリストが示す範囲(?part=開始-長さ)だけを返す
def test_get_record_mp4_part_returns_service_bytes(api_client, one_camera, monkeypatch):
    calls = []

    def _read(cam_conf, date, filename, offset, length):
        calls.append((cam_conf["id"], date, filename, offset, length))
        return b"segment-bytes"
    monkeypatch.setattr(camera_router.record_playlist_service, "read_record_part", _read)
    res = api_client.get("/api/cameras/record/cam1/20260101/20260101_100000.mp4?part=1203-62102")
    assert res.status_code == 200
    assert res.content == b"segment-bytes"
    assert res.headers["content-type"] == "video/mp4"
    assert calls == [("cam1", "20260101", "20260101_100000.mp4", 1203, 62102)]


@pytest.mark.parametrize("query", ["", "?part=", "?part=abc", "?part=1-", "?part=-5", "?part=1-2-3"])
def test_get_record_mp4_without_valid_part_is_400(api_client, one_camera, monkeypatch, query):
    monkeypatch.setattr(camera_router.record_playlist_service, "read_record_part",
                        lambda *a: pytest.fail("サービスまで到達している"))
    res = api_client.get(f"/api/cameras/record/cam1/20260101/20260101_100000.mp4{query}")
    assert res.status_code == 400


def test_get_record_mp4_unknown_part_is_404(api_client, one_camera, monkeypatch):
    monkeypatch.setattr(camera_router.record_playlist_service, "read_record_part", lambda *a: None)
    res = api_client.get("/api/cameras/record/cam1/20260101/20260101_100000.mp4?part=0-10")
    assert res.status_code == 404


def test_get_record_mp4_end_to_end_with_real_service(api_client, one_camera, monkeypatch, tmp_path):
    """実際の record_playlist_service を通し、ファイル名の検証が効くことを確かめる。"""
    from services import record_playlist_service as rps
    nvr = tmp_path / "nvr"
    (nvr / one_camera["name"]).mkdir(parents=True)
    (tmp_path / "secret.mp4").write_bytes(b"secret")
    monkeypatch.setattr(config, "NVR_RECORD_DIR", str(nvr))
    monkeypatch.setattr(rps, "INDEX_DIR", str(tmp_path / "index"))
    for name in ["..%2Fsecret.mp4", "20260102_100000.mp4", "20260101_250000.mp4", "20260101_100000.mp4"]:
        res = api_client.get(f"/api/cameras/record/cam1/20260101/{name}?part=0-6")
        assert res.status_code in (400, 404), name


def test_get_record_ts_is_no_longer_served(api_client, one_camera):
    res = api_client.get("/api/cameras/record/cam1/20260101/record_20260101000.ts")
    assert res.status_code == 400


# #386: target_date は glob パターン/ファイル名に埋め込まれるため YYYYMMDD 以外は 400 で拒否する
@pytest.mark.parametrize("bad_date", ["*", "2026-01-01", "2026010", "20260101x", "[0-9]*"])
def test_record_endpoints_reject_non_yyyymmdd_target_date(api_client, one_camera, monkeypatch, bad_date):
    called = []
    monkeypatch.setattr(camera_router.camera_service, "get_record_start_offset", lambda cam_conf, date: called.append(date) or 0)
    monkeypatch.setattr(camera_router.record_playlist_service, "get_record_playlist", lambda cam_conf, date: called.append(date) or None)
    monkeypatch.setattr(camera_router.record_playlist_service, "get_record_timeline", lambda cam_conf, date: called.append(date) or [])

    res_info = api_client.get(f"/api/cameras/record/cam1/{bad_date}/info")
    res_pl = api_client.get(f"/api/cameras/record/cam1/{bad_date}/record.m3u8")

    assert res_info.status_code == 400
    assert res_pl.status_code == 400
    assert called == [], "不正な target_date がサービス層まで到達している"
