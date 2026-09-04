# MY_HOME_SYSTEM/tests/test_camera_router_extra.py
"""
routers/camera_router.py の追加テスト。

既存の test_camera_router.py は _resolve_segment_path 単体のパストラバーサル対策を
カバーしているが、本ファイルはエンドポイント経由での存在しないcamera_id・
非対応拡張子・追加のtraversalパターンを補う(CODE_REVIEW_REPORT.md 2.5関連)。
"""
import os
import sys
import tempfile

import pytest
from fastapi import HTTPException

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from services import camera_service
from routers.camera_router import _resolve_segment_path, get_record_file, get_live_segment


class TestUnknownCameraIsRejectedBeforePathResolution:
    def test_get_record_file_unknown_camera_returns_404(self, monkeypatch):
        monkeypatch.setattr(config, "CAMERAS", [{"id": "cam1", "name": "Cam1"}])
        with pytest.raises(HTTPException) as exc_info:
            get_record_file("nonexistent_camera", "20260101", "seg1.ts")
        assert exc_info.value.status_code == 404

    def test_get_live_segment_unknown_camera_returns_404(self, monkeypatch):
        monkeypatch.setattr(config, "CAMERAS", [{"id": "cam1", "name": "Cam1"}])
        with pytest.raises(HTTPException) as exc_info:
            get_live_segment("nonexistent_camera", "seg1.ts")
        assert exc_info.value.status_code == 404

    def test_traversal_via_camera_id_is_rejected_even_if_matching_config_entry_exists(self, monkeypatch):
        """config.CAMERASに '..' というidが万一存在しても、パス解決自体が400で拒否すること"""
        monkeypatch.setattr(config, "CAMERAS", [{"id": "..", "name": "Evil"}])
        with pytest.raises(HTTPException) as exc_info:
            get_live_segment("..", "seg1.ts")
        assert exc_info.value.status_code == 400


class TestUnsupportedExtension:
    def test_unsupported_extension_returns_400(self, monkeypatch):
        monkeypatch.setattr(config, "CAMERAS", [{"id": "cam1", "name": "Cam1"}])
        with pytest.raises(HTTPException) as exc_info:
            get_record_file("cam1", "2026-01-01", "video.mp4")
        assert exc_info.value.status_code == 400


class TestLiveSegmentRejectsNonTsExtension:
    """Issue #172の回帰テスト: get_live_segmentは_resolve_segment_pathで
    パストラバーサルのみをチェックしており、拡張子は一切検証していなかった。
    そのため、start_hls_streamがffmpeg.logをchmod 600で保護していても、
    /api/cameras/live/{camera_id}/ffmpeg.log を直接リクエストすれば、
    アプリケーションプロセス自身がファイル所有者としてそのまま配信してしまい、
    RTSPの認証情報等を含みうるffmpegのエラーログが漏洩し得た。
    get_record_fileが.m3u8/.ts以外を400で拒否しているのと非対称な実装だった。"""

    def test_ffmpeg_log_request_is_rejected_with_400(self, monkeypatch):
        monkeypatch.setattr(config, "CAMERAS", [{"id": "cam1", "name": "Cam1"}])
        with tempfile.TemporaryDirectory() as base_dir:
            monkeypatch.setattr(camera_service, "HLS_LIVE_DIR", base_dir)
            cam_dir = os.path.join(base_dir, "cam1")
            os.makedirs(cam_dir)
            log_path = os.path.join(cam_dir, "ffmpeg.log")
            with open(log_path, "w") as f:
                f.write("rtsp://user:secret@camera.local/stream\n")
            os.chmod(log_path, 0o600)

            with pytest.raises(HTTPException) as exc_info:
                get_live_segment("cam1", "ffmpeg.log")
            assert exc_info.value.status_code == 400

    def test_ts_segment_request_still_succeeds(self, monkeypatch):
        monkeypatch.setattr(config, "CAMERAS", [{"id": "cam1", "name": "Cam1"}])
        with tempfile.TemporaryDirectory() as base_dir:
            monkeypatch.setattr(camera_service, "HLS_LIVE_DIR", base_dir)
            cam_dir = os.path.join(base_dir, "cam1")
            os.makedirs(cam_dir)
            seg_path = os.path.join(cam_dir, "seg1.ts")
            with open(seg_path, "w") as f:
                f.write("dummy")

            response = get_live_segment("cam1", "seg1.ts")
            assert response.path == os.path.realpath(seg_path)


class TestResolveSegmentPathAdditionalTraversalPatterns:
    @pytest.mark.parametrize(
        "camera_id,filename",
        [
            ("cam1", "..%2f..%2fetc%2fpasswd"),  # URLエンコードされた文字はデコードされず単なる文字列として扱われる想定
            ("cam1", "....//....//etc/passwd"),
            ("cam1", "..\\..\\etc\\passwd"),
            ("....", "seg.ts"),
            ("cam1", "/etc/passwd"),
        ],
    )
    def test_various_traversal_attempts_stay_within_base_dir_or_are_rejected(self, camera_id, filename):
        with tempfile.TemporaryDirectory() as base_dir:
            try:
                resolved = _resolve_segment_path(base_dir, camera_id, filename)
            except HTTPException as e:
                assert e.status_code == 400
                return
            # 例外にならなかった場合は、必ずbase_dir配下に収まっていること
            assert os.path.commonpath([os.path.realpath(base_dir), resolved]) == os.path.realpath(base_dir)
