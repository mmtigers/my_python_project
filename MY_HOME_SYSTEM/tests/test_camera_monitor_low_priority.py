# MY_HOME_SYSTEM/tests/test_camera_monitor_low_priority.py
"""
monitors/camera_monitor.py のLow優先度指摘のテスト。

- capture_snapshot_from_nvr: リトライ失敗時に /tmp/snapshot_*.jpg の残骸ファイルが
  クリーンアップされずに残り続ける問題。
- monitor_single_camera: 玄関カメラの全イベントペイロード(dir(events)含む)が
  デバッグ目的のまま INFO レベルで本番ログに出力され続けていた問題。
"""
import datetime
import glob
import inspect
import os
import subprocess
import sys
import uuid
from unittest.mock import patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from monitors import camera_monitor


class TestCaptureSnapshotCleansUpOnFailure:
    """
    capture_snapshot_from_nvr は output_tmp(常に /tmp/snapshot_<camera>_<uuid>.jpg)を
    全リトライで使い回すため、ffmpeg がタイムアウト等で部分書き込みしたファイルを
    残したまま関数を抜けると、/tmp に残骸が蓄積し続けていた。
    """

    def test_leftover_tmp_file_is_removed_when_all_retries_fail(self, tmp_path, monkeypatch):
        # 他のテスト・実プロセスの /tmp/snapshot_*.jpg と衝突しないよう一意なカメラ名にする。
        cam_name = f"TestCam_{uuid.uuid4().hex[:8]}"
        nas_folder = tmp_path / cam_name
        nas_folder.mkdir()
        # #411 S-L10 で当日分("{YYYYMMDD}_*.mp4")に検索を絞ったため、録画ファイル名も
        # 実際のNVR命名規則に合わせる。
        today_str = datetime.datetime.now().strftime("%Y%m%d")
        (nas_folder / f"{today_str}_000000.mp4").write_bytes(b"fake video")

        cam_conf = {"name": cam_name, "nas_folder": cam_name}
        monkeypatch.setattr(camera_monitor.config, "NVR_RECORD_DIR", str(tmp_path), raising=False)

        def fake_run(cmd, stdout=None, stderr=None, timeout=None, check=None):
            # ffmpegがkillされる前に部分書き込みしたファイルを残す状況を再現する。
            output_path = cmd[-1]
            with open(output_path, "wb") as f:
                f.write(b"partial-data-from-a-killed-ffmpeg-process")
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=10)

        leftover_files = []
        try:
            with patch("subprocess.run", side_effect=fake_run), \
                 patch("time.sleep"):  # exponential backoffの待機をスキップ
                result = camera_monitor.capture_snapshot_from_nvr(cam_conf)

            assert result is None
            leftover_files = glob.glob(f"/tmp/snapshot_{cam_name}_*.jpg")
            assert leftover_files == [], (
                f"capture_snapshot_from_nvr should clean up its temp file on every exit "
                f"path, but found leftovers: {leftover_files}"
            )
        finally:
            for f in leftover_files:
                os.remove(f)


class TestEntranceCameraPayloadLoggingIsNotInfoLevel:
    """
    玄関カメラの全イベントペイロード(dir(events)含む)がデバッグ目的のまま
    INFO レベルで出力され続けていた(ログのノイズ・情報量ともに大きい)。
    monitor_single_camera はONVIF接続を含む長い状態機械のため実行はせず、
    該当箇所のソースを直接検証する(静的回帰テスト)。
    """

    def test_raw_events_payload_logging_uses_debug_not_info(self):
        source = inspect.getsource(camera_monitor.monitor_single_camera)

        assert "logger.info(f\"🔬 [RAW EVENTS]" not in source
        assert "logger.info(f\"📦 [EVENT PAYLOAD]" not in source
        assert "logger.info(f\"📝 [PAYLOAD DETAIL]" not in source

        assert "logger.debug(f\"🔬 [RAW EVENTS]" in source
        assert "logger.debug(f\"📦 [EVENT PAYLOAD]" in source
        assert "logger.debug(f\"📝 [PAYLOAD DETAIL]" in source
