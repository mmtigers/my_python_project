# MY_HOME_SYSTEM/tests/test_camera_monitor_rtsp_snapshot.py
"""
Issue #703: 動体検知スナップショットの取得方式を「NVR録画セグメントの切り出し」から
「RTSPストリームからの直接1フレーム取得」へ切り替えたことのテスト。

元のバグ:
    capture_snapshot_from_nvr() は当日分の *.mp4 を mtime 降順に並べて先頭を選ぶが、
    NVR が書き込み中のセグメントは mtime が更新され続けるため必ず先頭に来る。
    moov atom が未完成のため `-sseof -1` のシークができず ffmpeg が終了コード 69/183 で
    失敗し、実機では動体検知 316 件中 94 件(約30%)のスナップショットが落ちていた。
    その WARNING を health_watch/log_analyzer がエラーとして数えるため、一次監視が
    常時「異常」状態になり、他の異常通知が抑制で埋もれる実害が出ていた。

ffmpeg の起動は全てモック化しているため、実機・CI いずれでもカメラ/NASを必要としない。
"""
import os
import subprocess
import sys
import time
import uuid
from unittest.mock import patch

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from monitors import camera_monitor

RTSP_URL_WITH_SECRET = "rtsp://admin:SuperSecretPass@192.168.1.50:554/profile1"


@pytest.fixture
def isolated_tmpdir(tmp_path, monkeypatch):
    """snapshot_*.jpg の出力先をテスト専用ディレクトリへ隔離する。

    実 /tmp を覗くと他プロセス・並列テストの残骸で偽陽性になるため。
    """
    fake_tmpdir = tmp_path / "fake_tmp"
    fake_tmpdir.mkdir()
    monkeypatch.setattr(camera_monitor.tempfile, "gettempdir", lambda: str(fake_tmpdir))
    return fake_tmpdir


def _make_nvr_folder(tmp_path, monkeypatch, cam_name, segments):
    """NVR録画フォルダを作る。segments は (ファイル名, 「何秒前に更新されたか」) のリスト。"""
    nas_folder = tmp_path / cam_name
    nas_folder.mkdir()
    now = time.time()
    for filename, age_sec in segments:
        path = nas_folder / filename
        path.write_bytes(b"fake video")
        os.utime(path, (now - age_sec, now - age_sec))
    monkeypatch.setattr(camera_monitor.config, "NVR_RECORD_DIR", str(tmp_path), raising=False)
    return nas_folder


class TestNvrSegmentSelectionRegression:
    """回帰テスト: 書き込み中(mtime が更新され続けている)セグメントを選ばないこと。"""

    def test_in_progress_segment_is_not_selected(self, tmp_path, monkeypatch, isolated_tmpdir):
        cam_name = f"TestCam_{uuid.uuid4().hex[:8]}"
        today = camera_monitor.dt_class.now().strftime("%Y%m%d")
        # 0秒前に更新された = NVR が今まさに書き込んでいるセグメント(mtime 最新)
        in_progress = f"{today}_121814.mp4"
        completed = f"{today}_120814.mp4"
        nas_folder = _make_nvr_folder(
            tmp_path, monkeypatch, cam_name,
            [(in_progress, 0), (completed, 600)],
        )

        used_inputs = []

        def fake_run(cmd, stdout=None, stderr=None, timeout=None, check=None):
            used_inputs.append(cmd[cmd.index("-i") + 1])
            with open(cmd[-1], "wb") as f:
                f.write(b"\xff\xd8jpeg")
            return subprocess.CompletedProcess(cmd, 0)

        cam_conf = {"name": cam_name, "nas_folder": cam_name}
        with patch("subprocess.run", side_effect=fake_run):
            result = camera_monitor.capture_snapshot_from_nvr(cam_conf)

        assert result == b"\xff\xd8jpeg"
        # 修正前はここが in_progress だった(=ffmpeg exit 69/183 で失敗していた)
        assert used_inputs == [str(nas_folder / completed)]

    def test_returns_none_when_only_in_progress_segment_exists(self, tmp_path, monkeypatch, isolated_tmpdir):
        """完成済みセグメントが無い場合は、失敗確実な ffmpeg を起動せずに諦める。"""
        cam_name = f"TestCam_{uuid.uuid4().hex[:8]}"
        today = camera_monitor.dt_class.now().strftime("%Y%m%d")
        _make_nvr_folder(tmp_path, monkeypatch, cam_name, [(f"{today}_121814.mp4", 0)])

        cam_conf = {"name": cam_name, "nas_folder": cam_name}
        with patch("subprocess.run") as mocked_run:
            result = camera_monitor.capture_snapshot_from_nvr(cam_conf)

        assert result is None
        assert mocked_run.call_count == 0

    def test_select_latest_completed_segment_skips_missing_files(self, tmp_path):
        """glob 直後にローテーションで消えたファイルは黙って読み飛ばす。"""
        existing = tmp_path / "20260919_120000.mp4"
        existing.write_bytes(b"x")
        os.utime(existing, (time.time() - 600, time.time() - 600))
        vanished = str(tmp_path / "20260919_121000.mp4")

        selected = camera_monitor._select_latest_completed_segment([vanished, str(existing)])
        assert selected == str(existing)


class TestCaptureSnapshotFromRtsp:
    """新方式: RTSP から直接1フレーム取得する。"""

    def test_returns_frame_without_touching_nas(self, monkeypatch, isolated_tmpdir):
        """NVR録画ディレクトリが存在しなくてもスナップショットが取れる。

        = 書き込み中セグメントを選んでしまう問題が構造的に起こり得ないことの確認。
        """
        cam_conf = {"id": "garden", "name": "庭カメラ"}
        monkeypatch.setattr(camera_monitor.config, "NVR_RECORD_DIR", "/nonexistent-nas", raising=False)
        monkeypatch.setattr(camera_monitor, "get_rtsp_url", lambda conf: RTSP_URL_WITH_SECRET)

        captured_cmd = {}

        def fake_run(cmd, stdout=None, stderr=None, timeout=None, check=None):
            captured_cmd["cmd"] = cmd
            captured_cmd["timeout"] = timeout
            with open(cmd[-1], "wb") as f:
                f.write(b"\xff\xd8rtsp-frame")
            return subprocess.CompletedProcess(cmd, 0)

        with patch("subprocess.run", side_effect=fake_run):
            result = camera_monitor.capture_snapshot_from_rtsp(cam_conf)

        assert result == b"\xff\xd8rtsp-frame"
        cmd = captured_cmd["cmd"]
        assert cmd[0] == "ffmpeg"
        assert "-rtsp_transport" in cmd and cmd[cmd.index("-rtsp_transport") + 1] == "tcp"
        assert cmd[cmd.index("-i") + 1] == RTSP_URL_WITH_SECRET
        assert "-frames:v" in cmd and cmd[cmd.index("-frames:v") + 1] == "1"
        # NVR 方式に固有のシーク指定を引きずっていないこと
        assert "-sseof" not in cmd
        assert captured_cmd["timeout"] == camera_monitor.RTSP_SNAPSHOT_TIMEOUT_SEC

    def test_zero_byte_output_is_treated_as_failure(self, monkeypatch, isolated_tmpdir):
        cam_conf = {"id": "garden", "name": "庭カメラ"}
        monkeypatch.setattr(camera_monitor, "get_rtsp_url", lambda conf: RTSP_URL_WITH_SECRET)

        def fake_run(cmd, stdout=None, stderr=None, timeout=None, check=None):
            open(cmd[-1], "wb").close()  # 0バイトファイルを残して正常終了する ffmpeg
            return subprocess.CompletedProcess(cmd, 0)

        with patch("subprocess.run", side_effect=fake_run), patch("time.sleep"):
            result = camera_monitor.capture_snapshot_from_rtsp(cam_conf)

        assert result is None

    def test_rtsp_url_resolution_failure_is_fail_soft(self, monkeypatch, isolated_tmpdir):
        """get_rtsp_url は ONVIF 失敗時に例外を投げるが、呼び出し元へは伝播させない。"""
        cam_conf = {"id": "garden", "name": "庭カメラ"}

        def boom(conf):
            raise RuntimeError("ONVIF unreachable")

        monkeypatch.setattr(camera_monitor, "get_rtsp_url", boom)
        with patch("subprocess.run") as mocked_run:
            result = camera_monitor.capture_snapshot_from_rtsp(cam_conf)

        assert result is None
        assert mocked_run.call_count == 0

    def test_retries_then_fails_soft_and_cleans_up_tmp_file(self, monkeypatch, isolated_tmpdir):
        """全リトライ失敗しても例外を投げず、一時ファイルの残骸も残さない。"""
        cam_conf = {"id": "garden", "name": "庭カメラ"}
        monkeypatch.setattr(camera_monitor, "get_rtsp_url", lambda conf: RTSP_URL_WITH_SECRET)

        def fake_run(cmd, stdout=None, stderr=None, timeout=None, check=None):
            with open(cmd[-1], "wb") as f:
                f.write(b"partial")
            raise subprocess.CalledProcessError(returncode=69, cmd=cmd)

        with patch("subprocess.run", side_effect=fake_run) as mocked_run, patch("time.sleep"):
            result = camera_monitor.capture_snapshot_from_rtsp(cam_conf)

        assert result is None
        assert mocked_run.call_count == camera_monitor.RTSP_SNAPSHOT_MAX_RETRIES
        assert os.listdir(isolated_tmpdir) == []

    @pytest.mark.parametrize("failure", ["timeout", "exit_code"])
    def test_credentials_are_never_written_to_logs(self, monkeypatch, isolated_tmpdir, failure):
        """CalledProcessError/TimeoutExpired の str() には cmd(=認証情報入りURL)が含まれる。

        そのままログへ出すとアプリログに平文パスワードが残るため、マスク済みURLだけを
        出していることを固定する。
        """
        cam_conf = {"id": "garden", "name": "庭カメラ"}
        monkeypatch.setattr(camera_monitor, "get_rtsp_url", lambda conf: RTSP_URL_WITH_SECRET)

        messages = []
        # core.logger.setup_logging のロガーは propagate=False のため caplog では拾えない
        # (test_camera_monitor_backoff.py と同じ差し替え方式にする)。
        for level in ("warning", "error"):
            monkeypatch.setattr(
                camera_monitor.logger, level,
                lambda message, *a, **k: messages.append(str(message)),
            )

        def fake_run(cmd, stdout=None, stderr=None, timeout=None, check=None):
            if failure == "timeout":
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)
            raise subprocess.CalledProcessError(returncode=183, cmd=cmd)

        with patch("subprocess.run", side_effect=fake_run), patch("time.sleep"):
            camera_monitor.capture_snapshot_from_rtsp(cam_conf)

        logged = "\n".join(messages)
        assert messages, "失敗はログに残すこと(無言で握りつぶさない)"
        assert "SuperSecretPass" not in logged
        assert "admin" not in logged
        assert "192.168.1.50" in logged  # ホストは追跡のため残す


class TestSaveImageFromStreamPrefersRtsp:
    def test_rtsp_is_tried_first_and_nvr_is_not_used_on_success(self, monkeypatch, tmp_path):
        cam_conf = {"id": "garden", "name": "庭カメラ"}
        monkeypatch.setattr(camera_monitor.config, "CAMERAS", [cam_conf], raising=False)
        monkeypatch.setattr(camera_monitor, "ASSETS_DIR", str(tmp_path), raising=False)
        monkeypatch.setattr(camera_monitor, "capture_snapshot_from_rtsp", lambda conf: b"\xff\xd8ok")

        def nvr_must_not_be_called(conf, target_time=None):
            raise AssertionError("RTSP 成功時に NVR 切り出しへフォールバックしてはいけない")

        monkeypatch.setattr(camera_monitor, "capture_snapshot_from_nvr", nvr_must_not_be_called)

        path = camera_monitor.save_image_from_stream("庭カメラ")
        assert path is not None
        with open(path, "rb") as f:
            assert f.read() == b"\xff\xd8ok"

    def test_falls_back_to_nvr_when_rtsp_fails(self, monkeypatch, tmp_path):
        cam_conf = {"id": "garden", "name": "庭カメラ"}
        monkeypatch.setattr(camera_monitor.config, "CAMERAS", [cam_conf], raising=False)
        monkeypatch.setattr(camera_monitor, "ASSETS_DIR", str(tmp_path), raising=False)
        monkeypatch.setattr(camera_monitor, "capture_snapshot_from_rtsp", lambda conf: None)
        monkeypatch.setattr(
            camera_monitor, "capture_snapshot_from_nvr",
            lambda conf, target_time=None: b"\xff\xd8from-nvr",
        )

        path = camera_monitor.save_image_from_stream("庭カメラ")
        assert path is not None
        with open(path, "rb") as f:
            assert f.read() == b"\xff\xd8from-nvr"

    def test_returns_none_when_both_paths_fail(self, monkeypatch, tmp_path):
        """Fail-Soft 契約: 両方失敗しても例外ではなく None を返す。"""
        cam_conf = {"id": "garden", "name": "庭カメラ"}
        monkeypatch.setattr(camera_monitor.config, "CAMERAS", [cam_conf], raising=False)
        monkeypatch.setattr(camera_monitor, "ASSETS_DIR", str(tmp_path), raising=False)
        monkeypatch.setattr(camera_monitor, "capture_snapshot_from_rtsp", lambda conf: None)
        monkeypatch.setattr(camera_monitor, "capture_snapshot_from_nvr", lambda conf, target_time=None: None)

        assert camera_monitor.save_image_from_stream("庭カメラ") is None
